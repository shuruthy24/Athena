"""Athena agents and the orchestrator.

Every request: Scout Intent decides (signed execution contract) -> executors verify the contract for the
capability they are about to use -> agents run -> Reporter assembles the answer with a full trace.

Route questions run a draft-and-check loop:
  Route Analyst (Nemotron fast lane) drafts 2-4 claims, each citing fact keys computed from Zetaris rows
  Evidence Critic = deterministic rules (numbers, banned wording, street names, direction)
                    + Nemotron critic lane (semantic support)
  revise up to MAX_REVISIONS times, then fall back to a deterministic explanation built only from facts.

Reports run Moderator (Nemotron) + Verifier (Zetaris street data, corroboration) and are queued with a
fading risk penalty when approved.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

from . import analysis as _analysis
from .analysis import close, compare, fallback_explanation, flatten_facts, numbers_in
from .config import Settings
from .data.source import PLACES, LocalSource, ZetarisSource
from .llm import LLMError, parse_json
from .scout import ContractError, Scout
from .telemetry import Trace

log = logging.getLogger("athena.agents")

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

ANALYST_SYSTEM = """TASK: analyst
You are Athena's Route Analyst. You explain the trade-off between the FASTEST route and ATHENA's lower-risk
route for a night trip in Sydney, using ONLY the facts provided.
Rules:
- Output ONE JSON object: {"headline": string, "claims": [{"text": string, "facts": [fact keys]}]}
- 2 to 4 claims. Every claim cites the exact fact keys it relies on. Every number must come from a cited fact.
- Never say "safe", "safer", "safest" or "guaranteed". Say "lower risk". Do not invent streets or numbers.
- If any unknown_share_pct is above 15, add a claim saying part of the route has unknown lighting.
- Plain English for a woman deciding how to get home; short sentences."""

CRITIC_SYSTEM = """TASK: critic
You are Athena's Evidence Critic. For each claim, decide whether the cited facts fully support it.
Output ONE JSON object: {"verdict": "approve" | "revise", "unsupported": [claim indexes], "notes": string}
A claim is unsupported if it states anything not in its cited facts, exaggerates, implies the route is safe,
or reverses a direction (for example says lower risk when risk_reduction_pct is not positive)."""

MODERATOR_SYSTEM = """TASK: moderator
You moderate anonymous night-safety reports about Sydney streets before they can affect route scores.
Output ONE JSON object: {"prejudiced": bool, "category": "lighting|isolation|harassment|infrastructure|positive|other",
"plausible": bool, "reason": string}
prejudiced = the report targets people by ethnicity, race, religion, gender identity, disability or similar.
plausible = the report describes a concrete, observable street condition."""


class Athena:
    def __init__(self, settings: Settings, llm, source=None, scout: Scout | None = None):
        self.s = settings
        self.llm = llm
        self.live_source = source if isinstance(source, ZetarisSource) else None
        self.local_source = source if isinstance(source, LocalSource) else LocalSource(schema=settings.zetaris_schema)
        self._zetaris_ok = self.live_source is not None
        street_rows, _ = self.local_source.streets()
        self.scout = scout or Scout(
            secret=settings.scout_secret,
            known_streets=[r["name"] for r in street_rows],
            tool_availability={
                "zetaris.sql": lambda: self.live_source is not None and self._zetaris_ok,
                "sample.json": lambda: self.live_source is None or not self._zetaris_ok,
                "nightshift.chat": lambda: getattr(self.llm, "provider", "") == "meterless-nightshift",
                "sample.llm": lambda: getattr(self.llm, "provider", "") != "meterless-nightshift",
            })

    # ------------------------------------------------------------------ helpers
    def _model_for(self, decision, capability: str) -> str:
        step = next((t for t in decision.tool_plan if t["capability"] == capability and t["tool"] != "critic.rules"), None)
        if step and step.get("profile") == "nemotron-critic":
            return self.s.critic_model
        return self.s.fast_model

    def _verify(self, decision, capability: str, trace: Trace) -> None:
        self.scout.verify(decision.contract, capability)
        trace.event("contract.verified", contractId=decision.contract["contractId"], engine=capability)

    def _source(self, decision):
        tool = next((t["tool"] for t in decision.tool_plan if t["capability"] == "zetaris.query"), "sample.json")
        return self.live_source if tool == "zetaris.sql" else self.local_source

    # ------------------------------------------------------------------ entry point
    def handle(self, message: str, trip: dict | None = None, user: dict | None = None,
               session: dict | None = None) -> dict:
        trace = Trace()
        user = user or {"id": "anon", "role": "traveller"}
        session = session or {}
        context = {"trip": trip or {}, "last_intent": session.get("last_intent"),
                   "recent_intents": session.get("recent_intents", []), "home": session.get("home")}
        t0 = time.perf_counter()
        decision = self.scout.decide(message, user=user, surface="web", context=context, trace=trace)
        trace.step("scout-intent", f"intent={decision.intent} band={decision.band} confidence={decision.confidence:.2f} "
                   f"risk={decision.risk['level']}" + (f" clarify={decision.clarification['trigger']}" if decision.clarification else ""),
                   model=None, ms=(time.perf_counter() - t0) * 1000)
        base = {"traceId": trace.trace_id, "intent": decision.intent, "scout": decision.summary(),
                "help": HELP, "data_mode": "sample" if self.live_source is None else "zetaris"}

        if decision.risk["level"] == "block":
            reason = decision.risk.get("reason") or "policy"
            trace.step("guard", f"blocked: {reason}; evidence={decision.risk['injection']['evidence']}")
            resp = {**base, "intent": "blocked", "confidence": None,
                    "answer": "I can't act on that request. It looks like an attempt to change how Athena works, "
                              "so nothing was changed. You can ask about a route, report a street, or get help now."}
        elif decision.clarification:
            resp = {**base, "confidence": None, "answer": decision.clarification["question"],
                    "clarification": decision.clarification}
        elif decision.intent == "safety.emergency":
            self._verify(decision, "emergency.card", trace)
            trace.step("emergency", "emergency card shown immediately; no model call")
            resp = {**base, "confidence": None, "emergency": True,
                    "answer": "If you are in danger, call 000 now. Move towards people and light. "
                              "Staffed stations nearby: " + ", ".join(STAFFED) + "."}
        elif decision.intent == "GENERAL":
            resp = {**base, "confidence": None,
                    "answer": "I compare the fastest route with a lower-risk route for night trips between UNSW and "
                              "the CBD, explain why using street lighting data, take street safety reports, and show "
                              "help options. Try: “Why is the Athena walk better at 10pm?”"}
        elif decision.intent in ("route.plan", "route.explain"):
            resp = {**base, **self._route(decision, message, trace, user, context)}
        elif decision.intent == "report.submit":
            resp = {**base, **self._report(decision, trace)}
        else:  # pragma: no cover - registry and handlers are kept in sync by tests
            resp = {**base, "answer": "Unsupported intent."}

        for sec in decision.secondary:  # multi-intent: e.g. a report plus a route request
            trace.step("scout-intent", f"secondary intent={sec['intent']} score={sec['score']}")
        resp["trace"] = trace.steps
        resp["events"] = trace.events
        resp["total_ms"] = trace.total_ms()
        trace.persist({"message": decision.redacted_prompt, "trip": trip}, resp)
        return resp

    # ------------------------------------------------------------------ routes
    def _route(self, decision, message: str, trace: Trace, user: dict, context: dict) -> dict:
        p = decision.parameters
        if p["origin"] not in PLACES or p["destination"] not in PLACES:
            return {"answer": "I don't recognise that trip yet.", "confidence": None}
        self._verify(decision, "zetaris.query", trace)
        source = self._source(decision)
        try:
            steps, ev1 = source.route_steps(p["origin"], p["destination"], p["band"], p["mode"])
            streets, ev2 = source.streets()
            trace.step("data", f"{len(steps)} route rows + {len(streets)} street rows via {ev1['executed_by']}",
                       ms=ev1["ms"] + ev2["ms"], tool=source.name)
        except Exception as exc:  # noqa: BLE001 - degrade with a re-plan, never crash the request
            trace.step("data", f"Zetaris query failed ({exc.__class__.__name__}); re-planning on sample data", tool=source.name)
            self._zetaris_ok = False
            decision = self.scout.replan(decision.contract, "zetaris.sql unavailable", message, user=user,
                                         surface="web", context=context, trace=trace)
            self._verify(decision, "zetaris.query", trace)
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
        self._verify(decision, "route.compare", trace)
        cmp = compare(steps, streets, p["band"])
        facts = flatten_facts(cmp)
        trace.step("route-metrics", f"fastest risk={cmp['fastest']['risk']} athena risk={cmp['athena']['risk']} "
                   f"extra={cmp['extra_min']} min reduction={cmp['risk_reduction_pct']}%")

        headline, claims, verdict = self._draft_and_check(decision, message, cmp, facts, trace)
        confidence = cmp["confidence"] if verdict != "fallback-error" else "low"
        if self.live_source is None:
            confidence_note = "Sample data (pre-existing hand-made routes); not live OTP results."
        else:
            confidence_note = "Route data served by Zetaris."
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
        all_known = set(self.scout.known_streets)
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

    def _draft_and_check(self, decision, message, cmp, facts, trace) -> tuple[str, list[dict], str]:
        feedback = None
        attempts = self.s.max_revisions + 1
        self._verify(decision, "llm.draft", trace)
        self._verify(decision, "llm.critique", trace)
        for attempt in range(1, attempts + 1):
            payload = {"question": message, "facts": facts, "comparison": cmp, "previous_feedback": feedback}
            try:
                r = self.llm.chat(self._model_for(decision, "llm.draft"), ANALYST_SYSTEM, json.dumps(payload))
                draft = parse_json(r.text)
                claims = [c for c in draft.get("claims", []) if isinstance(c, dict)][:4]
                headline = str(draft.get("headline", ""))[:200]
                trace.step("route-analyst", f"draft {attempt}: {len(claims)} claims", model=r.model, ms=r.ms)
            except (LLMError, ValueError) as exc:
                trace.step("route-analyst", f"draft {attempt} failed: {exc}; using fact-only explanation")
                headline, claims = fallback_explanation(cmp)
                return headline, [{**c, "supported": True, "problems": []} for c in claims], "fallback-error"
            rule_results = self.check_claims(claims, facts)
            if BANNED.search(headline):
                rule_results.append({"index": -1, "supported": False, "problems": ["headline uses banned wording"]})
            llm_unsupported, llm_note = [], ""
            try:
                cr = self.llm.chat(self._model_for(decision, "llm.critique"), CRITIC_SYSTEM,
                                   json.dumps({"facts": facts, "claims": claims}))
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
                           model=critic_model, ms=critic_ms, verdict="approve")
                checked = [{**c, **{k: v for k, v in rule_results[i].items() if k != "index"}} for i, c in enumerate(claims)]
                return headline, checked, "approve"
            summary = "; ".join(f"claim {r['index']}: {', '.join(r['problems'])}" for r in failed)[:300]
            trace.step("evidence-critic", f"revise draft {attempt}: {summary}", model=critic_model, ms=critic_ms,
                       verdict="revise")
            feedback = summary
        headline, claims = fallback_explanation(cmp)
        trace.step("reporter", "critic rejected every draft; using fact-only explanation", verdict="fallback")
        return headline, [{**c, "supported": True, "problems": []} for c in claims], "fallback"

    # ------------------------------------------------------------------ reports
    def _report(self, decision, trace: Trace) -> dict:
        p = decision.parameters
        self._verify(decision, "guard.redact", trace)
        text = decision.redacted_prompt
        if decision.risk["redactions"]:
            trace.step("guard", f"redacted {[r['kind'] for r in decision.risk['redactions']]} before any model call")
        self._verify(decision, "llm.moderate", trace)
        try:
            r = self.llm.chat(self._model_for(decision, "llm.moderate"), MODERATOR_SYSTEM,
                              json.dumps({"text": text, "street": p.get("street")}))
            mod = parse_json(r.text)
            trace.step("report-moderator", f"category={mod.get('category')} prejudiced={mod.get('prejudiced')}",
                       model=r.model, ms=r.ms)
        except (LLMError, ValueError) as exc:
            mod = {"prejudiced": False, "category": "other", "plausible": True, "reason": "moderator unavailable"}
            trace.step("report-moderator", f"model unavailable ({exc}); sent to human review")
            mod["force_review"] = True
        self._verify(decision, "zetaris.query", trace)
        source = self._source(decision)
        try:
            streets, ev = source.streets()
        except Exception:  # noqa: BLE001
            streets, ev = self.local_source.streets()
        verdict = verify_report(text, p.get("street"), mod, {s["name"]: s for s in streets})
        trace.step("report-verifier", f"action={verdict['action']} reason={verdict['reason']}")
        self._verify(decision, "report.queue", trace)
        record = {"at": datetime.now(timezone.utc).isoformat(), "street": p.get("street"), "band": p.get("band"),
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
    """Report Verifier: combine moderation with Zetaris street data. Order matters: reject > review > redact > approve."""
    resolved = bool(street and street in streets)
    if mod.get("prejudiced"):
        return {"action": "reject", "reason": "prejudiced", "street_resolved": resolved}
    if mod.get("force_review"):
        return {"action": "review", "reason": "moderator_unavailable", "street_resolved": resolved}
    if not resolved:
        return {"action": "review", "reason": "unknown_street", "street_resolved": False}
    if CONTRADICTS_LIT.search(text) and streets[street].get("lit") == "yes":
        return {"action": "review", "reason": "contradicts_data", "street_resolved": True}
    if not mod.get("plausible", True):
        return {"action": "review", "reason": "implausible", "street_resolved": True}
    if "removed]" in text:
        return {"action": "redact", "reason": "personal_info", "street_resolved": True}
    return {"action": "approve", "reason": mod.get("category") or "other", "street_resolved": True}
