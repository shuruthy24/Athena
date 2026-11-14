"""Athena agents and the orchestrator.

Two direct entry points, no chat router: Athena.explain(trip) for a route explanation and
Athena.submit_report(street, band, text) for a street report. Each returns the answer with a full trace.

Route explanations run a draft-and-check loop (evaluator-optimiser):
  Route Analyst (EXPLAINER_MODEL) drafts 2-4 claims, each citing fact keys computed from street data
  Evidence Critic = deterministic rules (numbers, banned wording, street names, direction)
                    + model critic (CRITIC_MODEL, semantic support, per-claim indexes)
  revise up to MAX_REVISIONS times, then fall back to a deterministic explanation built only from facts.

Reports run Moderator (MODERATOR_MODEL) + Verifier (street data, corroboration) and are queued with a
fading risk penalty when approved. Every model call goes through the COMP3110 gateway and its trace step
records the served model, tokens and estimated cost.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

from . import analysis as _analysis
from .analysis import close, compare, fallback_explanation, flatten_facts, numbers_in
from .cache import ExplanationCache, cache_key
from .config import Settings
from .data.source import PLACES, LocalSource, ZetarisSource, validate_trip
from .llm import LLMError, parse_json
from .prompts import load as load_prompt
from .guard import inspect_text
from .telemetry import Trace

log = logging.getLogger("athena.agents")
DEFAULT_QUESTION = "Why is the Athena route better tonight?"

PLACE_NAMES = {"unsw": "UNSW Library", "townhall": "Town Hall station", "central": "Central station",
               "quay": "Circular Quay", "redfern": "Redfern station", "kingsx": "Kings Cross station",
               "newtown": "Newtown station"}
BAND_NAMES = {"18": "6 pm", "22": "10 pm", "01": "1 am"}
STAFFED = ["Town Hall station", "Central station"]
BANNED = re.compile(r"\b(safe|safely|safer|safest|safety guaranteed|guarantee[sd]?|risk[- ]free|no risk|100% (safe|secure))\b", re.I)
LOWER_RISK = re.compile(r"\b(lower|less|reduced|reduces?)\b.{0,12}\brisk\b", re.I)
FASTEST_LOWER = re.compile(r"\b(fastest|quickest|faster)\b[^.]{0,40}\b(lower|less|reduced)\b[^.]{0,12}\brisk", re.I)

HELP = {"emergency": "000", "emergency_note": "Call 000 for police, fire or ambulance.",
        "staffed_stations": STAFFED,
        "note": "Station staffing hours vary; check before relying on a station being staffed."}


PROMPT_ROLES = ("explainer", "critic", "moderator")
LEAK_WINDOW = 40  # characters of normalised system-prompt text that may not appear in user-facing output


def _norm_text(text: str) -> str:
    return " ".join(str(text).lower().split())


def prompt_windows(bodies: list[str], size: int = LEAK_WINDOW, stride: int = 10) -> set[str]:
    out: set[str] = set()
    for body in bodies:
        t = _norm_text(body)
        out.update(t[i:i + size] for i in range(0, max(1, len(t) - size + 1), stride))
    return out


def leaks_prompt(text: str, windows: set[str], size: int = LEAK_WINDOW) -> bool:
    """True when output repeats a stretch of a system prompt. Prompts are public (week 8: assume leakage),
    but the app still never echoes them back as an answer."""
    t = _norm_text(text)
    return any(t[i:i + size] in windows for i in range(0, max(0, len(t) - size + 1)))


class Athena:
    def __init__(self, settings: Settings, llm, source=None):
        self.s = settings
        self.llm = llm
        # Prompts are versioned files (prompts/<role>.v<N>.md); traces record which version ran. The
        # moderator is pinned to v1 for the default path: v2 has a different output shape and is only used
        # through a prompted classifier chosen with REPORT_CLASSIFIER.
        self.prompts = {role: load_prompt(role) for role in PROMPT_ROLES}
        self.prompts["moderator"] = load_prompt("moderator", 1)
        self._prompt_windows = prompt_windows([p.body for p in self.prompts.values()])
        self.cache = ExplanationCache()
        self.classifier = None
        if settings.report_classifier != "moderator-v1":
            from .classify import build_classifier
            from .classify.tfidf import train_rows
            self.classifier = build_classifier(settings.report_classifier, llm=llm,
                                               model=settings.moderator_model, train_rows=train_rows())
        self.live_source = source if isinstance(source, ZetarisSource) else None
        self.local_source = source if isinstance(source, LocalSource) else LocalSource(schema=settings.zetaris_schema)
        self._zetaris_ok = self.live_source is not None
        street_rows, _ = self.local_source.streets()
        self.known_streets = [r["name"] for r in street_rows]

    # ------------------------------------------------------------------ helpers
    def _prompt_tag(self, role: str) -> str:
        p = self.prompts[role]
        return f"{p.id}@{p.sha}"

    def _data_mode(self) -> str:
        if self.live_source is not None:
            return "zetaris"
        return "files" if getattr(self.local_source, "processed", False) else "sample"

    def _source(self):
        return self.live_source if self.live_source is not None and self._zetaris_ok else self.local_source

    @staticmethod
    def _finish(trace: Trace, resp: dict, request: dict) -> dict:
        model_steps = [t for t in trace.steps if t.get("model")]
        costs = [t.get("cost_usd") for t in model_steps]
        trace.step("usage", f"{len(model_steps)} model calls this request",
                   calls=len(model_steps), tokens_in=sum(t.get("tokens_in") or 0 for t in model_steps),
                   tokens_out=sum(t.get("tokens_out") or 0 for t in model_steps),
                   cost_usd=None if any(c is None for c in costs) else round(sum(costs), 8))
        resp["trace"] = trace.steps
        resp["events"] = trace.events
        resp["total_ms"] = trace.total_ms()
        trace.persist(request, resp)
        return resp

    # ------------------------------------------------------------------ entry points
    def explain(self, trip: dict, question: str = DEFAULT_QUESTION) -> dict:
        """Explain the trade-off between the fastest route and Athena's lower-risk route for one trip."""
        trace = Trace()
        p = {k: trip.get(k) for k in ("origin", "destination", "band", "mode")}
        validate_trip(p["origin"], p["destination"], p["band"], p["mode"])  # ValueError on anything unknown
        base = {"traceId": trace.trace_id, "help": HELP, "data_mode": self._data_mode()}
        resp = {**base, **self._route(p, question, trace)}
        return self._finish(trace, resp, {"trip": p})

    # ------------------------------------------------------------------ routes
    def _route(self, p: dict, message: str, trace: Trace) -> dict:
        if p["origin"] not in PLACES or p["destination"] not in PLACES:
            return {"answer": "I don't recognise that trip yet.", "confidence": None}
        source = self._source()
        try:
            steps, ev1 = source.route_steps(p["origin"], p["destination"], p["band"], p["mode"])
            streets, ev2 = source.streets()
            trace.step("data", f"{len(steps)} route rows + {len(streets)} street rows via {ev1['executed_by']}",
                       ms=ev1["ms"] + ev2["ms"], tool=source.name)
        except Exception as exc:  # noqa: BLE001 - degrade with a re-plan, never crash the request
            trace.step("data", f"Zetaris query failed ({exc.__class__.__name__}); falling back to local files", tool=source.name)
            trace.event("data.fallback", reason="zetaris unavailable")
            self._zetaris_ok = False
            steps, ev1 = self.local_source.route_steps(p["origin"], p["destination"], p["band"], p["mode"])
            streets, ev2 = self.local_source.streets()
            trace.step("data", f"fallback: {len(steps)} route rows from sample data", ms=ev1["ms"] + ev2["ms"],
                       tool="sample.json")
            self._zetaris_ok = True  # try Zetaris again on the next request
        evidence = [ev1, ev2]
        if not steps:
            return {"answer": f"Athena has no routes for {PLACE_NAMES[p['origin']]} to {PLACE_NAMES[p['destination']]} "
                              "yet. The current data covers UNSW Library and Town Hall in both directions.",
                    "confidence": None, "evidence": evidence, "trip": p}
        cmp = compare(steps, streets, p["band"])
        facts = flatten_facts(cmp)
        trace.step("route-metrics", f"fastest risk={cmp['fastest']['risk']} athena risk={cmp['athena']['risk']} "
                   f"extra={cmp['extra_min']} min reduction={cmp['risk_reduction_pct']}%")

        key = cache_key([self._prompt_tag("explainer"), self._prompt_tag("critic")],
                        [self.s.model_for("explainer"), self.s.model_for("critic")],
                        self.s.max_revisions, facts)
        hit = self.cache.get(key)
        if hit:
            headline, claims, verdict = hit["headline"], json.loads(json.dumps(hit["claims"])), hit["verdict"]
            trace.step("cache", f"explanation cache hit ({verdict}); no model call", key=key[:12])
        else:
            headline, claims, verdict = self._draft_and_check(message, cmp, facts, trace)
            self.cache.put(key, {"headline": headline, "claims": claims, "verdict": verdict})
        confidence = cmp["confidence"] if verdict != "fallback-error" else "low"
        confidence_note = {
            "sample": "Sample data (pre-existing hand-made routes); not live OTP results.",
            "files": "Routes precomputed with OpenTripPlanner and streets from OpenStreetMap (data/processed); "
                     "times are scheduled, not live.",
            "zetaris": "Route data served by Zetaris."}[self._data_mode()]
        trip_label = f"{PLACE_NAMES[p['origin']]} → {PLACE_NAMES[p['destination']]}, {BAND_NAMES[p['band']]}, {p['mode']}"
        return {
            "answer": headline, "claims": claims, "confidence": confidence, "confidence_note": confidence_note,
            "trip": {**p, "label": trip_label},
            "routes": [{"variant": v, **{k: cmp[v][k] for k in ("duration_min", "lit_share_pct", "unknown_share_pct",
                                                                    "risk", "worst", "streets", "transit_legs")}}
                       for v in ("fastest", "athena")],
            "comparison": {k: cmp[k] for k in ("extra_min", "risk_reduction_pct", "avoided_streets", "added_streets",
                                               "report_penalties")},
            "critic_verdict": verdict, "evidence": evidence,
        }

    def check_claims(self, claims: list[dict], facts: dict) -> list[dict]:
        """Deterministic part of the Evidence Critic. Returns one result per claim."""
        known_streets = set(facts.get("fastest.streets", [])) | set(facts.get("athena.streets", []))
        all_known = set(self.known_streets)
        out = []
        for i, c in enumerate(claims):
            problems = []
            text, keys = str(c.get("text", "")), [k for k in c.get("facts", []) if isinstance(k, str)]
            if not keys:
                problems.append("cites no facts")
            bad_keys = [k for k in keys if k not in facts]
            if bad_keys:
                problems.append(f"unknown fact keys {bad_keys}")
            if BANNED.search(text):
                problems.append("uses banned wording (safe/guaranteed)")
            cited_nums = [facts[k] for k in keys if k in facts and isinstance(facts[k], (int, float)) and not isinstance(facts[k], bool)]
            for n in numbers_in(text):
                if not any(close(n, v) for v in cited_nums):
                    problems.append(f"number {n:g} not in cited facts")
            for name in all_known:
                if name.lower() in text.lower() and name not in known_streets:
                    problems.append(f"mentions {name}, which is on neither route")
            red = facts.get("risk_reduction_pct") or 0
            if FASTEST_LOWER.search(text) and red > 0:
                problems.append("says the fastest route is lower risk, but Athena's route is lower risk")
            elif LOWER_RISK.search(text) and not FASTEST_LOWER.search(text) and red <= 0:
                problems.append("claims lower risk but risk is not lower")
            out.append({"index": i, "supported": not problems, "problems": problems})
        return out

    def _draft_once(self, payload: dict):
        """One analyst call. The client owns the single permitted retry (AGENTS.md section 2), so a failure
        here goes straight to the fact-only fallback."""
        return self.llm.chat(self.s.model_for("explainer"), self.prompts["explainer"].body,
                             json.dumps(payload), role="explainer")

    def _draft_and_check(self, message, cmp, facts, trace) -> tuple[str, list[dict], str]:
        feedback = None
        attempts = self.s.max_revisions + 1
        for attempt in range(1, attempts + 1):
            # Keep the prompt small: the analyst needs the flat facts, not the whole comparison object.
            # The offline stub builds its claims from the comparison, so it still receives it.
            payload = {"question": message, "facts": facts, "previous_feedback": feedback}
            if getattr(self.llm, "provider", "") == "local":
                payload["comparison"] = cmp
            try:
                r = self._draft_once(payload)
                draft = parse_json(r.text)
                claims = [c for c in draft.get("claims", []) if isinstance(c, dict)][:4]
                headline = str(draft.get("headline", ""))[:200]
                trace.step("route-analyst", f"draft {attempt}: {len(claims)} claims", model=r.model, ms=r.ms,
                           prompt=self._prompt_tag("explainer"), **r.usage())
            except (LLMError, ValueError) as exc:
                trace.step("route-analyst", f"draft {attempt} failed: {exc}; using fact-only explanation")
                headline, claims = fallback_explanation(cmp)
                return headline, [{**c, "supported": True, "problems": []} for c in claims], "fallback-error"
            rule_results = self.check_claims(claims, facts)
            if BANNED.search(headline):
                rule_results.append({"index": -1, "supported": False, "problems": ["headline uses banned wording"]})
            for i, t in [(-1, headline)] + [(i, str(c.get("text", ""))) for i, c in enumerate(claims)]:
                if leaks_prompt(t, self._prompt_windows):
                    rule_results.append({"index": i, "supported": False, "problems": ["repeats system prompt text"]})
            llm_unsupported, llm_note, critic_usage = [], "", {}
            try:
                cr = self.llm.chat(self.s.model_for("critic"), self.prompts["critic"].body,
                                   json.dumps({"facts": facts, "claims": claims}), role="critic")
                critic_usage = {"prompt": self._prompt_tag("critic"), **cr.usage()}
                cv = parse_json(cr.text)
                llm_unsupported = [int(i) for i in cv.get("unsupported", []) if str(i).lstrip("-").isdigit()]
                llm_note = str(cv.get("notes", ""))[:160]
                critic_model, critic_ms = cr.model, cr.ms
            except (LLMError, ValueError) as exc:
                llm_note, critic_model, critic_ms = f"model critic unavailable ({exc}); rules only", None, None
            failed = [r for r in rule_results if not r["supported"]] + \
                     [{"index": i, "supported": False, "problems": ["model critic: not supported by cited facts"]}
                      for i in llm_unsupported if 0 <= i < len(claims)]
            if not failed:
                trace.step("evidence-critic", f"approve draft {attempt}" + (f" ({llm_note})" if llm_note else ""),
                           model=critic_model, ms=critic_ms, verdict="approve", **critic_usage)
                checked = [{**c, **{k: v for k, v in rule_results[i].items() if k != "index"}} for i, c in enumerate(claims)]
                return headline, checked, "approve"
            summary = "; ".join(f"claim {r['index']}: {', '.join(r['problems'])}" for r in failed)[:300]
            trace.step("evidence-critic", f"revise draft {attempt}: {summary}", model=critic_model, ms=critic_ms,
                       verdict="revise", **critic_usage)
            feedback = summary
        headline, claims = fallback_explanation(cmp)
        trace.step("reporter", "critic rejected every draft; using fact-only explanation", verdict="fallback")
        return headline, [{**c, "supported": True, "problems": []} for c in claims], "fallback"

    # ------------------------------------------------------------------ reports
    def submit_report(self, street: str | None, band: str, text: str) -> dict:
        """Moderate one anonymous street report: redact PII, block instructions, classify, verify against
        street data, queue. The report text reaches the model only as a quoted JSON field."""
        trace = Trace()
        base = {"traceId": trace.trace_id, "help": HELP, "data_mode": self._data_mode()}
        redacted, injection, redactions = inspect_text(text)
        if injection:
            trace.step("guard", f"blocked: injection; evidence={injection}")
            resp = {**base, "blocked": True, "answer": "This report can't be used because it looks like an attempt "
                    "to give Athena instructions. Describe what you saw on the street instead."}
            return self._finish(trace, resp, {"street": street, "band": band})
        if redactions:
            trace.step("guard", f"redacted {[r['kind'] for r in redactions]} before any model call")
        if self.classifier is not None:
            mod = self._classify(redacted, street, trace)
        else:
            mod = self._moderate_v1(redacted, street, trace)
        try:
            streets, ev = self._source().streets()
        except Exception:  # noqa: BLE001
            streets, ev = self.local_source.streets()
        resp = {**base, **self._finish_report(street, band, redacted, mod, streets, ev, trace)}
        return self._finish(trace, resp, {"street": street, "band": band})

    def _classify(self, text: str, street: str | None, trace: Trace) -> dict:
        """A chosen ReportClassifier (athena/classify). Any failure sends the report to human review."""
        from .classify import ClassifierError
        c = self.classifier
        try:
            out = c.predict(text, street)
        except (ClassifierError, LLMError, ValueError) as exc:
            trace.step("report-moderator", f"classifier={c.name} failed ({exc}); sent to human review")
            return {"action": "review", "category": "other", "reason": "classifier unavailable", "force_review": True}
        reply = getattr(c, "last_reply", None)
        extra = {"prompt": f"{c.prompt.id}@{c.prompt.sha}", **reply.usage()} if reply is not None else {}
        trace.step("report-moderator", f"classifier={c.name} action={out['action']} category={out['category']}",
                   model=reply.model if reply is not None else None, ms=reply.ms if reply is not None else None, **extra)
        return out

    def _moderate_v1(self, text: str, street: str | None, trace: Trace) -> dict:
        try:
            r = self.llm.chat(self.s.model_for("moderator"), self.prompts["moderator"].body,
                              json.dumps({"text": text, "street": street}), role="moderator")
            mod = parse_json(r.text)
            trace.step("report-moderator", f"category={mod.get('category')} prejudiced={mod.get('prejudiced')}",
                       model=r.model, ms=r.ms, prompt=self._prompt_tag("moderator"), **r.usage())
        except (LLMError, ValueError) as exc:
            mod = {"prejudiced": False, "category": "other", "plausible": True, "reason": "moderator unavailable"}
            trace.step("report-moderator", f"model unavailable ({exc}); sent to human review")
            mod["force_review"] = True
        return mod

    def _finish_report(self, street: str | None, band: str, text: str, mod: dict, streets: list[dict], ev: dict,
                       trace: Trace) -> dict:
        verdict = verify_report(text, street, mod, {s["name"]: s for s in streets})
        trace.step("report-verifier", f"action={verdict['action']} reason={verdict['reason']}")
        record = {"at": datetime.now(timezone.utc).isoformat(), "street": street, "band": band,
                  "text": text, "action": verdict["action"], "reason": verdict["reason"],
                  "street_resolved": verdict["street_resolved"], "category": mod.get("category"),
                  "traceId": trace.trace_id}
        queue = _analysis.REPORT_QUEUE
        queue.parent.mkdir(exist_ok=True)
        with queue.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        msg = {"approve": "Thanks. Your report was accepted and adds a temporary risk penalty to that street that fades over two weeks.",
               "redact": "Thanks. Personal details were removed, and the rest of your report was accepted.",
               "review": "Thanks. Your report needs a person to review it before it can affect routes.",
               "reject": "This report can't be used because it targets people rather than a street condition."}[verdict["action"]]
        return {"answer": msg, "confidence": None, "report": {k: record[k] for k in ("street", "band", "text", "action", "reason", "category")},
                "evidence": [ev]}


CONTRADICTS_LIT = re.compile(r"\bno (street )?lights?\b.{0,30}\b(at all|anywhere|everywhere)\b|\bcompletely dark\b.{0,20}\beverywhere\b|\bno street ?lights at all\b", re.I)


def verify_report(text: str, street: str | None, mod: dict, streets: dict[str, dict]) -> dict:
    """Report Verifier: combine the classifier or moderator output with street data.
    Order matters: reject > review > redact > approve. Accepts the classifier shape {action, category, reason}
    (AGENTS.md section 6) and the hackathon moderator shape {prejudiced, category, plausible, reason}."""
    resolved = bool(street and street in streets)
    if mod.get("force_review"):
        return {"action": "review", "reason": "moderator_unavailable", "street_resolved": resolved}
    if mod.get("action") == "reject" or mod.get("prejudiced"):
        return {"action": "reject", "reason": "prejudiced", "street_resolved": resolved}
    if mod.get("action") == "review":
        return {"action": "review", "reason": "classifier_review", "street_resolved": resolved}
    if not resolved:
        return {"action": "review", "reason": "unknown_street", "street_resolved": False}
    if CONTRADICTS_LIT.search(text) and streets[street].get("lit") == "yes":
        return {"action": "review", "reason": "contradicts_data", "street_resolved": True}
    if not mod.get("plausible", True):
        return {"action": "review", "reason": "implausible", "street_resolved": True}
    if "removed]" in text:
        return {"action": "redact", "reason": "personal_info", "street_resolved": True}
    return {"action": "approve", "reason": mod.get("category") or "other", "street_resolved": True}
