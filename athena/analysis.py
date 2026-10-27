"""Route comparison: turns Zetaris rows into the facts every explanation must be grounded in.

Scoring mirrors the pre-existing Athena web app so the agent and the map agree:
  route risk = 0.55 x distance-weighted mean street risk + 0.45 x worst stretch
Only street-level (walk/bike) distance is scored; in-vehicle transit legs are reported but not scored.
Unknown lighting stays unknown: it is excluded from the lit share, reported as unknown_share and lowers
confidence. It is never treated as unlit (ACC-3 crit decision).
Approved user reports add a temporary penalty that halves every 7 days (capped at +0.3).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

REPORT_QUEUE = ROOT / "runs" / "reports_queue.jsonl"
HALF_LIFE_DAYS = 7.0
PENALTY = 0.10
PENALTY_CAP = 0.30


def report_penalties(band: str, now: datetime | None = None, path: Path | None = None) -> dict[str, float]:
    now = now or datetime.now(timezone.utc)
    path = path or REPORT_QUEUE
    out: dict[str, float] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("action") not in ("approve", "redact") or r.get("band") != band or not r.get("street_resolved"):
            continue
        age = (now - datetime.fromisoformat(r["at"])).total_seconds() / 86400
        out[r["street"]] = min(PENALTY_CAP, out.get(r["street"], 0.0) + PENALTY * 0.5 ** (age / HALF_LIFE_DAYS))
    return out


def variant_metrics(steps: list[dict], streets: dict[str, dict], band: str, penalties: dict[str, float]) -> dict:
    if not steps:
        return {}
    first = steps[0]
    scored_m = lit_m = unknown_m = 0.0
    weighted = 0.0
    worst = None
    legs, names = [], []
    for s in sorted(steps, key=lambda r: r["step"]):
        if not s.get("street"):
            legs.append({"mode": s["leg_mode"], "line": s.get("line"), "distance_m": s["distance_m"]})
            continue
        names.append(s["street"])
        d = float(s["distance_m"])
        st = streets.get(s["street"])
        risk = None if not st or st.get(f"risk_{band}") is None else float(st[f"risk_{band}"])
        if risk is not None:
            risk = min(1.0, risk + penalties.get(s["street"], 0.0))
        lit = st.get("lit") if st else None
        scored_m += d
        if lit == "yes":
            lit_m += d
        elif lit is None:
            unknown_m += d
        if risk is None:
            continue
        weighted += d * risk
        if worst is None or risk > worst["risk"]:
            worst = {"name": s["street"], "risk": round(risk, 2), "lit": lit, "distance_m": round(d),
                     "tunnel": bool(st.get("tunnel")) if st else False,
                     "report_penalty": round(penalties.get(s["street"], 0.0), 3)}
    known_m = scored_m - sum(float(s["distance_m"]) for s in steps
                             if s.get("street") and (s["street"] not in streets or streets[s["street"]].get(f"risk_{band}") is None))
    mean = weighted / known_m if known_m else None
    risk = None if mean is None else round(0.55 * mean + 0.45 * (worst["risk"] if worst else mean), 3)
    return {
        "duration_min": round(float(first["duration_min"]), 1),
        "walk_m": round(float(first.get("walk_m") or 0)),
        "transfers": int(first.get("transfers") or 0),
        "scored_m": round(scored_m),
        "lit_share_pct": round(100 * lit_m / scored_m, 1) if scored_m else None,
        "unknown_share_pct": round(100 * unknown_m / scored_m, 1) if scored_m else None,
        "mean_risk": None if mean is None else round(mean, 3),
        "risk": risk,
        "worst": worst,
        "streets": list(dict.fromkeys(names)),
        "transit_legs": legs,
    }


def compare(steps: list[dict], street_rows: list[dict], band: str, use_reports: bool = True) -> dict:
    streets = {s["name"]: s for s in street_rows}
    penalties = report_penalties(band) if use_reports else {}
    by_variant = {v: [s for s in steps if s["variant"] == v] for v in ("fastest", "athena")}
    fastest = variant_metrics(by_variant["fastest"], streets, band, penalties)
    athena = variant_metrics(by_variant["athena"], streets, band, penalties)
    if not fastest or not athena:
        return {"available": False}
    extra = round(athena["duration_min"] - fastest["duration_min"], 1)
    reduction = None
    if fastest["risk"] and athena["risk"] is not None:
        reduction = round(100 * (fastest["risk"] - athena["risk"]) / fastest["risk"], 1)
    unknown = max(fastest["unknown_share_pct"] or 0, athena["unknown_share_pct"] or 0)
    confidence = "high" if unknown < 10 else "medium" if unknown < 30 else "low"
    same = fastest["streets"] == athena["streets"] and fastest["transit_legs"] == athena["transit_legs"]
    return {
        "available": True, "band": band,
        "fastest": fastest, "athena": athena,
        "extra_min": extra, "risk_reduction_pct": reduction,
        "avoided_streets": [s for s in fastest["streets"] if s not in athena["streets"]],
        "added_streets": [s for s in athena["streets"] if s not in fastest["streets"]],
        "same_route": same,
        "confidence": confidence,
        "report_penalties": {k: round(v, 3) for k, v in penalties.items()},
    }


def flatten_facts(cmp: dict) -> dict:
    """Flat key -> value map. Claims must cite these keys; the critic checks numbers against them."""
    f: dict = {}
    for v in ("fastest", "athena"):
        m = cmp[v]
        for k in ("duration_min", "walk_m", "transfers", "lit_share_pct", "unknown_share_pct", "risk", "mean_risk"):
            f[f"{v}.{k}"] = m.get(k)
        if m.get("worst"):
            for k in ("name", "risk", "lit", "distance_m", "tunnel"):
                f[f"{v}.worst.{k}"] = m["worst"][k]
        f[f"{v}.streets"] = m["streets"]
        f[f"{v}.transit_lines"] = [leg["line"] for leg in m["transit_legs"] if leg.get("line")]
    f["extra_min"] = cmp["extra_min"]
    f["risk_reduction_pct"] = cmp["risk_reduction_pct"]
    f["avoided_streets"] = cmp["avoided_streets"]
    f["added_streets"] = cmp["added_streets"]
    f["confidence"] = cmp["confidence"]
    return f


def fallback_explanation(cmp: dict) -> tuple[str, list[dict]]:
    """Deterministic explanation built only from facts. Used when the model is unavailable or the critic
    rejects every draft. It cannot over-claim because every sentence is a formatted fact."""
    f, a = cmp["fastest"], cmp["athena"]
    claims = []
    if cmp["same_route"]:
        return ("Athena and the fastest option are the same route for this trip and time.", [
            {"text": f"Both options take {a['duration_min']} min.", "facts": ["athena.duration_min"]}])
    if cmp["risk_reduction_pct"] is not None:
        claims.append({"text": f"Athena's route takes {cmp['extra_min']:+} min and has {cmp['risk_reduction_pct']}% lower route risk than the fastest route.",
                       "facts": ["extra_min", "risk_reduction_pct"]})
    if f.get("worst") and cmp["avoided_streets"] and f["worst"]["name"] in cmp["avoided_streets"]:
        lit = {"yes": "lit", "no": "unlit", None: "of unknown lighting"}[f["worst"]["lit"]]
        claims.append({"text": f"It avoids {f['worst']['name']}, the highest-risk stretch of the fastest route ({lit}, risk {f['worst']['risk']}).",
                       "facts": ["fastest.worst.name", "fastest.worst.lit", "fastest.worst.risk"]})
    if a.get("lit_share_pct") is not None and f.get("lit_share_pct") is not None \
            and a["lit_share_pct"] != f["lit_share_pct"]:
        claims.append({"text": f"{a['lit_share_pct']}% of Athena's street distance is lit, versus {f['lit_share_pct']}% on the fastest route.",
                       "facts": ["athena.lit_share_pct", "fastest.lit_share_pct"]})
    head = "Athena's route is lower risk" if (cmp["risk_reduction_pct"] or 0) > 0 else "Athena's route is not lower risk"
    return f"{head} for this trip.", claims


def numbers_in(text: str) -> list[float]:
    import re
    # ignore line names and times like T2, L3, 10pm, 1am
    cleaned = re.sub(r"\b[A-Z]+\d+\b|\b\d{1,2}\s?(am|pm)\b|\b\d{1,2}:\d{2}\b", " ", text, flags=re.I)
    return [float(x) for x in re.findall(r"(?<![\w.])[+-]?\d+(?:\.\d+)?", cleaned)]


def close(a: float, b: float) -> bool:
    if b is None or (isinstance(b, float) and math.isnan(b)):
        return False
    return abs(abs(a) - abs(b)) <= max(0.6, 0.02 * abs(b))
