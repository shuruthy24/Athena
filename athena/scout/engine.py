"""Scout Intent engine for Athena (Python implementation of the Meterless Scout Intent spec).

Spec source: https://github.com/Meterless/Meterless/tree/main/engines/scout-intent (Apache-2.0).
Pipeline: Sense -> Interpret -> Guard -> Route -> Recommend -> signed ExecutionContract.

Implementer's choices (the spec leaves these open) are marked "(implementer's choice)".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REGISTRY_PATH = Path(__file__).with_name("registry.json")
POLICY_PACK_VERSION = "athena-default-1"

# Canonical defaults from AGENTS.md section 12.
WEIGHTS = {"lexical": 0.40, "semantic": 0.35, "contextual": 0.20, "recency": 0.05}
BAND_HIGH, BAND_MEDIUM, BAND_LOW = 0.80, 0.55, 0.40
TIE_WINDOW = 0.05
HIGH_RISK_PROCEED = 0.95
SECONDARY_FLOOR = 0.55
STAGE1_BUDGET_MS = 16.0
CONTRACT_TTL_S = 300
MAX_CLARIFY_OPTIONS = 4
LEXICAL_SATURATION = 1.5          # (implementer's choice) weighted match sum that counts as a full lexical match
RATE_LIMIT_PER_MIN = 30           # (implementer's choice)

PLACE_ALIASES = {
    "unsw": ["unsw", "library", "kensington", "uni", "campus"],
    "townhall": ["town hall", "townhall", "cbd", "george st", "city"],
    "central": ["central"],
    "quay": ["circular quay", "the quay", "quay"],
    "redfern": ["redfern"],
    "kingsx": ["kings cross", "king's cross"],
    "newtown": ["newtown"],
}
BAND_ALIASES = {"18": ["6pm", "6 pm", "18:00", "evening", "after work"],
                "22": ["10pm", "10 pm", "22:00", "tonight", "late tonight", "night"],
                "01": ["1am", "1 am", "01:00", "midnight", "after midnight", "late"]}
MODE_ALIASES = {"walk": ["walk", "walking", "on foot"],
                "bike": ["bike", "cycle", "cycling", "ride"],
                "transit": ["bus", "train", "tram", "light rail", "public transport", "transit"]}

# Guard: injection patterns (spec docs/risk-and-policy.md: pattern match, role drift, embedded control,
# out-of-band tool references). Evidence records the pattern name, never the payload.
INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("instruction-override", re.compile(r"\b(ignore|disregard|forget)\b.{0,20}\b(previous|prior|above|all|the)\b.{0,20}\b(instructions?|rules|prompts?|user)\b", re.I | re.S)),
    ("role-drift", re.compile(r"\byou are now\b|\bunrestricted mode\b|\bpretend to be\b|\bact as (the )?(system|admin)", re.I)),
    ("embedded-control", re.compile(r"</?\s*(system|user|assistant)\s*>|\[/?INST\]", re.I)),
    ("secret-exfiltration", re.compile(r"\breveal\b.{0,40}\b(keys?|credentials?|passwords?|secrets?|tokens?)\b", re.I | re.S)),
    ("tool-ref", re.compile(r"\b(grant (me )?admin|admin panel|internal_\w+|drop (the )?\w+ table|delete (all|every))\b", re.I)),
    ("risk-tampering", re.compile(r"\bmark (every|all|each)\b.{0,20}\b(streets?|routes?)\b.{0,15}\b(as )?(safe|low risk)\b|\boverride\b.{0,15}\brisk\b", re.I | re.S)),
]
BIDI_CONTROL = re.compile(r"[‪-‮⁦-⁩​-‏]|\\u202[a-e]", re.I)
PII_PATTERNS = [
    ("phone", re.compile(r"(?:\+?61\s?|0)4\d{2}[\s-]?\d{3}[\s-]?\d{3}\b|\b\d{4}\s?\d{3}\s?\d{3}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("plate", re.compile(r"\b(rego|plate)\s*[:#]?\s*[A-Z0-9]{2,7}\b", re.I)),
]


class ContractError(Exception):
    """Raised by executors when a contract fails verification (signature, expiry, scope)."""


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9':\s]", " ", text)
    return " " + re.sub(r"\s+", " ", text).strip() + " "


def _stem(t: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in _norm(text).split() if len(t) > 1]


def _trigger_regex(phrase: str) -> re.Pattern:
    """Trigger phrase -> word-bounded regex. A word ending in * matches any suffix (stem match)."""
    parts = []
    for w in phrase.strip().split():
        parts.append(re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w))
    return re.compile(r"(?<![a-z0-9])" + r"\s+".join(parts) + r"(?![a-z0-9])")


def _ulid() -> str:
    """Monotonic-ish ULID: 48-bit ms timestamp + 80 random bits, Crockford base32."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    n = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(alphabet[n & 31])
        n >>= 5
    return "".join(reversed(out))


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class Decision:
    intent: str
    confidence: float
    band: str
    candidates: list[dict]
    parameters: dict
    references: dict
    needs_clarification: bool
    clarification: dict | None
    risk: dict
    tool_plan: list[dict]
    model_profile: dict
    contract: dict | None
    secondary: list[dict] = field(default_factory=list)
    redacted_prompt: str = ""
    latency_ms: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"intent": self.intent, "confidence": round(self.confidence, 3), "band": self.band,
                "candidates": self.candidates[:3], "parameters": self.parameters,
                "needsClarification": self.needs_clarification, "clarification": self.clarification,
                "risk": self.risk, "toolPlan": self.tool_plan, "modelProfile": self.model_profile,
                "secondary": self.secondary,
                "contract": None if not self.contract else {
                    "contractId": self.contract["contractId"], "traceId": self.contract["traceId"],
                    "expiresAt": self.contract["expiresAt"], "capabilities": self.contract["scope"]["capabilities"],
                    "signature": self.contract["signature"][:16] + "…"},
                "latencyMs": self.latency_ms}


class Scout:
    def __init__(self, intent_registry: str | Path | dict = REGISTRY_PATH, secret: str | None = None,
                 model_profiles: list[dict] | None = None, tool_availability: dict[str, Callable[[], bool]] | None = None,
                 known_streets: list[str] | None = None, self_test: bool = True):
        reg = intent_registry if isinstance(intent_registry, dict) else json.loads(Path(intent_registry).read_text())
        self.registry_version = reg["registryVersion"]
        self.intents = {i["id"]: i for i in reg["intents"]}
        self._validate_registry()
        self.secret = (secret or os.environ.get("SCOUT_SECRET") or "dev-only-change-me").encode()
        self.model_profiles = model_profiles or default_model_profiles()
        self.availability = tool_availability or {}
        self.known_streets = known_streets or []
        self._idf, self._example_vecs = self._build_semantic_index()
        self._triggers = {iid: [(_trigger_regex(ph), w) for ph, w in i.get("triggers", {}).items() if ph.strip()]
                          for iid, i in self.intents.items()}
        self._rate: dict[str, deque] = defaultdict(deque)
        if self_test:
            self.startup_self_test()

    # ------------------------------------------------------------------ registry
    def _validate_registry(self) -> None:
        if "GENERAL" not in self.intents:
            raise ValueError("registry must define the GENERAL fallback intent")
        for i in self.intents.values():
            for key in ("id", "category", "description", "capabilities", "riskClass", "surfaces", "examples", "version"):
                if key not in i:
                    raise ValueError(f"intent {i.get('id')} missing {key}")
            if not i["examples"]:
                raise ValueError(f"intent {i['id']} has no examples (mandatory)")

    # ------------------------------------------------------------------ Sense
    def _build_semantic_index(self):
        """Stage-2 semantic term: TF-IDF cosine against registry examples, computed locally.
        (implementer's choice: the spec suggests a 50-200 MB local embedding model; TF-IDF keeps the
        dependency footprint at zero and runs in well under a millisecond.)"""
        docs = {iid: [_tokens(e) for e in i["examples"]] for iid, i in self.intents.items()}
        df = Counter()
        all_docs = [d for ds in docs.values() for d in ds]
        for d in all_docs:
            df.update(set(d))
        n = len(all_docs)
        idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        vecs = {iid: [self._vec(d, idf) for d in ds] for iid, ds in docs.items()}
        return idf, vecs

    @staticmethod
    def _vec(tokens: list[str], idf: dict) -> dict:
        tf = Counter(tokens)
        v = {t: c * idf.get(t, 0.0) for t, c in tf.items() if t in idf}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def _lexical(self, prompt_n: str) -> dict[str, tuple[float, list[tuple[int, int]]]]:
        out = {}
        for iid, triggers in self._triggers.items():
            total, spans = 0.0, []
            for rx, w in triggers:
                m = rx.search(prompt_n)
                if m:
                    total += w
                    spans.append((m.start(), m.end()))
            out[iid] = (min(1.0, total / LEXICAL_SATURATION), spans)
        return out

    def classify(self, prompt: str, surface: str = "web", context: dict | None = None,
                 stage2: bool = True) -> dict:
        context = context or {}
        t0 = time.perf_counter()
        prompt_n = _norm(prompt)
        lex = self._lexical(prompt_n)
        stage1_ms = (time.perf_counter() - t0) * 1000
        qv = self._vec(_tokens(prompt), self._idf) if stage2 else {}
        last = context.get("last_intent")
        recent = set(context.get("recent_intents", []))
        has_ctx = bool(last) or bool(context.get("trip"))
        # (implementer's choice) terms with no available signal are excluded and the remaining weights
        # renormalised, so a stateless request can still reach the High band.
        avail = {"lexical": True, "semantic": stage2, "contextual": has_ctx, "recency": bool(recent)}
        denom = sum(WEIGHTS[k] for k, on in avail.items() if on)
        cands = []
        for iid in self.intents:
            sem = max((sum(qv.get(t, 0) * x for t, x in ev.items()) for ev in self._example_vecs[iid]), default=0.0) if stage2 else 0.0
            ctx = 0.0
            if has_ctx:
                if iid == last:
                    ctx = 1.0
                elif context.get("trip") and iid.startswith("route."):
                    ctx = 0.5
            rec = 1.0 if iid in recent else 0.0
            raw = (WEIGHTS["lexical"] * lex[iid][0] + WEIGHTS["semantic"] * sem
                   + WEIGHTS["contextual"] * ctx + WEIGHTS["recency"] * rec)
            score = raw / denom if denom else 0.0
            # riskPenalty: down-rank higher-risk intents that only matched weakly (implementer's choice)
            if self.intents[iid]["riskClass"] == "high" and lex[iid][0] < 0.5:
                score -= 0.1
            if surface not in self.intents[iid]["surfaces"]:
                score = 0.0
            cands.append({"intent": iid, "score": round(max(0.0, min(1.0, score)), 4),
                          "lexical": round(lex[iid][0], 3), "semantic": round(sem, 3), "span": lex[iid][1][:1]})
        cands.sort(key=lambda c: c["score"], reverse=True)
        return {"candidates": cands, "stage1_ms": stage1_ms, "stage2": "used" if stage2 else "skipped"}

    def startup_self_test(self) -> None:
        probes = ["someone is following me", "why is the athena route better", "plan a route from unsw to town hall",
                  "report broken light on anzac parade", "hello"] * 4
        worst = 0.0
        for p in probes:
            t0 = time.perf_counter()
            self._lexical(_norm(p))
            worst = max(worst, (time.perf_counter() - t0) * 1000)
        if worst > STAGE1_BUDGET_MS:
            raise RuntimeError(f"Scout Stage-1 self-test failed: {worst:.2f} ms > {STAGE1_BUDGET_MS} ms budget")
        self.self_test_ms = worst

    # ------------------------------------------------------------------ Interpret
    def interpret(self, intent_id: str, prompt: str, context: dict) -> tuple[dict, dict, list[str]]:
        intent = self.intents[intent_id]
        props = intent.get("parameters", {}).get("properties", {})
        p = _norm(prompt)
        params, refs = {}, {}
        if "origin" in props or "destination" in props:
            found = []
            for pid, aliases in PLACE_ALIASES.items():
                for a in aliases:
                    i = p.find(f" {a} ")
                    if i >= 0:
                        found.append((i, pid))
                        break
            found.sort()
            from_m = re.search(r" from ([a-z' ]+?) (to|at|by|on|tonight|$)", p + " $")
            to_m = re.search(r" to ([a-z' ]+?) (from|at|by|on|tonight|\$)", p + " $")
            def match(s):
                for pid, aliases in PLACE_ALIASES.items():
                    if any(f" {a} " in f" {s} " for a in aliases):
                        return pid
                return None
            o = match(from_m.group(1)) if from_m else None
            d = match(to_m.group(1)) if to_m else None
            if not d and " home" in p and context.get("home"):
                d = context["home"]
            ordered = [pid for _, pid in found]
            if not o and not d and len(ordered) >= 2:
                o, d = ordered[0], ordered[1]
            elif o and not d:
                d = next((x for x in ordered if x != o), None)
            elif d and not o:
                o = next((x for x in ordered if x != d), None)
            trip = context.get("trip") or {}
            params["origin"] = o or trip.get("origin")
            params["destination"] = d or trip.get("destination")
        if "band" in props:
            band = None
            for b, aliases in BAND_ALIASES.items():
                if any(f" {a} " in p for a in aliases):
                    band = b
                    break
            params["band"] = band or (context.get("trip") or {}).get("band") or props["band"].get("default")
        if "mode" in props:
            mode = None
            for m, aliases in MODE_ALIASES.items():
                if any(f" {a} " in p for a in aliases):
                    mode = m
                    break
            params["mode"] = mode or (context.get("trip") or {}).get("mode") or props["mode"].get("default")
        if "street" in props:
            street = next((s for s in sorted(self.known_streets, key=len, reverse=True)
                           if f" {_norm(s).strip()} " in p), None)
            if not street:
                m = re.search(r"\b(on|at|near|along|in) ([A-Z][\w']+(?: [A-Z][\w']+){0,3})", prompt)
                street = m.group(2) if m else None
            params["street"] = street
            refs["street"] = {"name": street, "resolved": street in self.known_streets} if street else None
        if "text" in props:
            params["text"] = prompt.strip()[: props["text"].get("maxLength", 500)]
        missing = [r for r in intent.get("parameters", {}).get("required", []) if not params.get(r)]
        # schema validation of enums
        for k, spec in props.items():
            if "enum" in spec and params.get(k) is not None and params[k] not in spec["enum"]:
                missing.append(k)
        return params, refs, missing

    # ------------------------------------------------------------------ Guard
    def guard(self, intent_id: str, prompt: str, user: dict, surface: str) -> tuple[dict, str]:
        flags, redactions, evidence = [], [], []
        layers = {}
        # 1. injection
        inj = [name for name, rx in INJECTION_PATTERNS if rx.search(prompt)]
        if BIDI_CONTROL.search(prompt):
            inj.append("embedded-control-unicode")
        layers["injection"] = "block" if inj else "pass"
        if inj:
            flags.append("injection")
            evidence = inj
        # 2. policy (RBAC)
        role = (user or {}).get("role", "traveller")
        roles = self.intents[intent_id].get("roles", ["*"])
        layers["policy"] = "pass" if ("*" in roles or role in roles) else "block"
        # 3. scope (surface)
        layers["scope"] = "pass" if surface in self.intents[intent_id]["surfaces"] else "block"
        # 4. PII / sensitivity: redact before any model call
        redacted = prompt
        for kind, rx in PII_PATTERNS:
            if rx.search(redacted):
                redacted = rx.sub(f"[{kind} removed]", redacted)
                redactions.append({"kind": kind})
        layers["pii"] = "warn" if redactions else "pass"
        if redactions:
            flags.append("pii-redacted")
        # 5. rate limit
        uid = (user or {}).get("id", "anon")
        q = self._rate[uid]
        now = time.time()
        while q and now - q[0] > 60:
            q.popleft()
        q.append(now)
        layers["rate"] = "block" if len(q) > RATE_LIMIT_PER_MIN else "pass"
        floor = self.intents[intent_id]["riskClass"]
        order = ["low", "medium", "high"]
        level = floor
        if any(v == "warn" for v in layers.values()):
            level = order[min(2, order.index(level) + 1)]
        reason = None
        if any(v == "block" for v in layers.values()):
            level = "block"
            reason = ", ".join(k for k, v in layers.items() if v == "block")
        return {"level": level, "flags": flags, "redactions": redactions, "layers": layers,
                "injection": {"detected": bool(inj), "kind": inj[0] if inj else None, "evidence": evidence},
                "reason": reason}, redacted

    # ------------------------------------------------------------------ Route
    CAPABILITY_GRAPH = {
        "zetaris.query": [{"tool": "zetaris.sql", "local": False, "latency_ms": 800},
                          {"tool": "sample.json", "local": True, "latency_ms": 2}],
        "route.compare": [{"tool": "athena.route_metrics", "local": True, "latency_ms": 2}],
        "llm.draft": [{"tool": "nightshift.chat", "local": False, "latency_ms": 2500, "profile": "nemotron-fast"},
                      {"tool": "sample.llm", "local": True, "latency_ms": 1, "profile": "sample-offline"}],
        "llm.critique": [{"tool": "critic.rules", "local": True, "latency_ms": 1, "profile": "local-rules"},
                         {"tool": "nightshift.chat", "local": False, "latency_ms": 2500, "profile": "nemotron-critic"},
                         {"tool": "sample.llm", "local": True, "latency_ms": 1, "profile": "sample-offline"}],
        "llm.moderate": [{"tool": "nightshift.chat", "local": False, "latency_ms": 2500, "profile": "nemotron-fast"},
                         {"tool": "sample.llm", "local": True, "latency_ms": 1, "profile": "sample-offline"}],
        "guard.redact": [{"tool": "scout.redactor", "local": True, "latency_ms": 1}],
        "report.queue": [{"tool": "reports.jsonl", "local": True, "latency_ms": 1}],
        "emergency.card": [{"tool": "emergency.card", "local": True, "latency_ms": 1}],
    }

    def route(self, capabilities: list[str]) -> list[dict]:
        """Selection rules: availability, then latency, then local-first tie-break.
        llm.critique keeps the deterministic rules step AND the best available model step."""
        plan = []
        for cap in capabilities:
            options = [o for o in self.CAPABILITY_GRAPH.get(cap, []) if self.availability.get(o["tool"], lambda: True)()]
            if not options:
                raise ContractError(f"no available tool for capability {cap}")
            if cap == "llm.critique":
                rules = [o for o in options if o["tool"] == "critic.rules"]
                models = sorted([o for o in options if o["tool"] != "critic.rules"],
                                key=lambda o: (o["latency_ms"], not o["local"]))
                chosen = rules + models[:1]
            else:
                chosen = [sorted(options, key=lambda o: (o["latency_ms"], not o["local"]))[0]] \
                    if cap not in ("zetaris.query", "llm.draft", "llm.moderate") else [options[0]]
            for o in chosen:
                plan.append({"capability": cap, "tool": o["tool"], "profile": o.get("profile")})
        return plan

    # ------------------------------------------------------------------ Recommend + contract
    def recommend(self, tool_plan: list[dict], data_class: str) -> dict:
        wanted = [s["profile"] for s in tool_plan if s.get("profile")]
        for pid in wanted:
            prof = next((p for p in self.model_profiles if p["id"] == pid), None)
            if prof and data_class in prof["dataClasses"]:
                return {"id": prof["id"], "model": prof["model"], "provider": prof["provider"]}
        return {"id": "local-rules", "model": None, "provider": "local"}

    def sign(self, contract: dict) -> dict:
        body = {k: v for k, v in contract.items() if k not in ("signature", "signedBy")}
        contract["signature"] = hmac.new(self.secret, canonical_json(body).encode(), hashlib.sha256).hexdigest()
        contract["signedBy"] = "scout"
        return contract

    def verify(self, contract: dict, capability: str, engine: str = "executor") -> None:
        """Executors call this before acting. Refuses unsigned, tampered, expired or out-of-scope use."""
        if not contract or "signature" not in contract:
            raise ContractError("unsigned contract")
        body = {k: v for k, v in contract.items() if k not in ("signature", "signedBy")}
        expected = hmac.new(self.secret, canonical_json(body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, contract["signature"]):
            raise ContractError("signature mismatch (contract tampered)")
        if datetime.fromisoformat(contract["expiresAt"]) < datetime.now(timezone.utc):
            raise ContractError("contract expired")
        if capability not in contract["scope"]["capabilities"]:
            raise ContractError(f"capability {capability} out of contract scope (reason=scope-mismatch)")

    def issue_contract(self, *, trace_id: str, intent: dict, risk: dict, tool_plan: list[dict],
                       model_profile: dict, user: dict, surface: str, capabilities: list[str],
                       parent_id: str | None = None) -> dict:
        now = datetime.now(timezone.utc)
        contract = {
            "contractId": _ulid(), "parentId": parent_id, "traceId": trace_id,
            "intent": intent,
            "risk": {"level": risk["level"], "flags": risk["flags"], "redactions": risk["redactions"], "approvals": []},
            "toolPlan": tool_plan, "modelProfile": model_profile,
            "scope": {"user": user, "surface": surface, "capabilities": capabilities},
            "issuedAt": now.isoformat(), "expiresAt": (now + timedelta(seconds=CONTRACT_TTL_S)).isoformat(),
            "registryVersion": self.registry_version, "policyPackVersion": POLICY_PACK_VERSION,
        }
        return self.sign(contract)

    # ------------------------------------------------------------------ full pipeline
    def decide(self, prompt: str, user: dict | None = None, surface: str = "web", context: dict | None = None,
               trace=None, parent_id: str | None = None) -> Decision:
        user = user or {"id": "anon", "role": "traveller"}
        context = context or {}
        t0 = time.perf_counter()
        sensed = self.classify(prompt, surface, context)
        cands = sensed["candidates"]
        top = cands[0]
        band = band_of(top["score"])
        intent_id = top["intent"] if band != "reject" else "GENERAL"
        conf = top["score"]
        params, refs, missing = self.interpret(intent_id, prompt, context)
        clar = None
        triggers = []
        if band == "low":
            triggers.append("low-band")
        if band == "medium" and len(cands) > 1 and cands[0]["score"] - cands[1]["score"] < TIE_WINDOW \
                and cands[1]["intent"] != "GENERAL":
            triggers.append("tie")
        if missing and intent_id != "GENERAL":
            triggers.append("params")
        if self.intents[intent_id]["riskClass"] == "high" and conf < HIGH_RISK_PROCEED:
            triggers.append("high-risk")
        # (implementer's choice) an emergency is never delayed by clarification, and GENERAL, the safe
        # conversational fallback, has nothing to clarify.
        if intent_id in ("safety.emergency", "GENERAL"):
            triggers = []
        if triggers:
            opts = [c["intent"] for c in cands if c["intent"] != "GENERAL"][:MAX_CLARIFY_OPTIONS]
            question = ("Which trip do you mean? Pick a start and destination." if triggers == ["params"]
                        else "Do you want to plan a route, ask why a route was chosen, report a street, or get help now?")
            clar = {"question": question, "options": opts, "trigger": triggers[0], "missing": missing}
            if trace:
                trace.event("scout.clarification", trigger=triggers[0])
        risk, redacted = self.guard(intent_id, prompt, user, surface)
        secondary = [c for c in cands[1:] if c["score"] >= BAND_HIGH and c["intent"] not in ("GENERAL", intent_id)
                     and c["span"] and top["span"] and c["span"][0][0] != top["span"][0][0]]
        tool_plan, profile, contract = [], {"id": "local-rules", "model": None, "provider": "local"}, None
        caps = self.intents[intent_id]["capabilities"]
        if risk["level"] != "block" and not clar:
            tool_plan = self.route(caps)
            data_class = "redacted-user-text" if intent_id == "report.submit" else "public"
            profile = self.recommend(tool_plan, data_class)
            trace_id = getattr(trace, "trace_id", None) or _ulid()
            contract = self.issue_contract(
                trace_id=trace_id,
                intent={"primary": intent_id, "parameters": params, "references": refs,
                        "confidence": conf, "alternates": cands[1:4]},
                risk=risk, tool_plan=tool_plan, model_profile=profile, user=user, surface=surface,
                capabilities=caps, parent_id=parent_id)
        total_ms = (time.perf_counter() - t0) * 1000
        if trace:
            trace.event("scout.decision", intent=intent_id, band=band, latencyMs=round(total_ms, 2),
                        stage2=sensed["stage2"], risk=risk["level"])
            if contract:
                trace.event("contract.issued", contractId=contract["contractId"], intent=intent_id,
                            risk=risk["level"], profile=profile["id"])
        return Decision(intent=intent_id, confidence=conf, band=band, candidates=cands, parameters=params,
                        references=refs, needs_clarification=bool(clar), clarification=clar, risk=risk,
                        tool_plan=tool_plan, model_profile=profile, contract=contract, secondary=secondary,
                        redacted_prompt=redacted,
                        latency_ms={"stage1": round(sensed["stage1_ms"], 3), "total": round(total_ms, 3)})

    def replan(self, parent: dict, reason: str, prompt: str, user: dict | None = None, surface: str = "web",
               context: dict | None = None, trace=None) -> Decision:
        if trace:
            trace.event("scout.replan", parent=parent["contractId"], reason=reason)
        return self.decide(prompt, user, surface, context, trace, parent_id=parent["contractId"])


def band_of(score: float) -> str:
    if score >= BAND_HIGH:
        return "high"
    if score >= BAND_MEDIUM:
        return "medium"
    if score >= BAND_LOW:
        return "low"
    return "reject"


def default_model_profiles() -> list[dict]:
    fast = os.environ.get("FAST_MODEL", os.environ.get("METERLESS_MODEL", "meterless/auto"))
    critic = os.environ.get("CRITIC_MODEL", os.environ.get("METERLESS_MODEL", "meterless/auto"))
    return [
        {"id": "nemotron-fast", "provider": "meterless-nightshift", "model": fast,
         "capabilities": ["json"], "dataClasses": ["public", "redacted-user-text"]},
        {"id": "nemotron-critic", "provider": "meterless-nightshift", "model": critic,
         "capabilities": ["json"], "dataClasses": ["public", "redacted-user-text"]},
        {"id": "sample-offline", "provider": "local", "model": "sample-offline-stub",
         "capabilities": ["json"], "dataClasses": ["public", "redacted-user-text", "pii"]},
        {"id": "local-rules", "provider": "local", "model": None,
         "capabilities": [], "dataClasses": ["public", "redacted-user-text", "pii"]},
    ]
