"""Route comparison: turns route and street rows into the facts every explanation must be grounded in.

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
import re
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
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("action") not in ("approve", "redact") or r.get("band") != band or not r.get("street_resolved"):
            continue
        age = (now - datetime.fromisoformat(r["at"])).total_seconds() / 86400
        out[r["street"]] = min(PENALTY_CAP, out.get(r["street"], 0.0) + PENALTY * 0.5 ** (age / HALF_LIFE_DAYS))
    return out


STREET_MODES = ("WALK", "BICYCLE")
# Exposure while riding a tram, bus, train or ferry: a fixed assumption (team decision, 15 Nov 2026), below every
# street score the rule can give (the lowest is about 0.15, a lit main road at 6 pm). Not measured.
IN_VEHICLE_RISK = 0.10
MIN_WAIT_S = 30             # shorter gaps between legs are transfers on foot, not waits at a stop


def _row_attrs(s: dict, streets: dict[str, dict], band: str, penalties: dict[str, float]):
    """(label, street row or seg_* attributes, report penalty) for a street step or a wait at a stop."""
    if s.get("street"):
        return s["street"], streets.get(s["street"]), penalties.get(s["street"], 0.0)
    if s.get("seg_label"):
        return s["seg_label"], {"lit": s.get("seg_lit"), f"risk_{band}": s.get(f"seg_risk_{band}"),
                                "tunnel": s.get("seg_tunnel")}, 0.0
    return None, None, 0.0


def variant_metrics(steps: list[dict], streets: dict[str, dict], band: str, penalties: dict[str, float]) -> dict:
    """Route risk = 0.55 x mean + 0.45 x worst stretch.

    With timed rows (the processed OTP data: every row has duration_s) the mean is weighted by TIME and covers the
    whole trip: walking and cycling at their street's score, waiting at a stop (leg_mode WAIT) at the nearest
    street's score, and time on board at IN_VEHICLE_RISK. Without times (the hand-made sample) it is the
    distance-weighted mean over walk/bike streets only, as before. Lit and unknown-lighting shares are always
    over walk/bike distance only: we have no lighting data for vehicles or stops' surroundings beyond the street."""
    if not steps:
        return {}
    first = steps[0]
    timed = all(s.get("duration_s") is not None for s in steps)
    scored_m = lit_m = unknown_m = 0.0
    weighted = known_w = 0.0
    wait_s = vehicle_s = 0.0
    worst = None
    legs, names = [], []
    for s in sorted(steps, key=lambda r: r["step"]):
        d = float(s.get("distance_m") or 0)
        w = float(s["duration_s"]) if timed else d
        mode = s.get("leg_mode")
        if mode == "WAIT":
            wait_s += float(s.get("duration_s") or 0)
            label, st, penalty = _row_attrs(s, streets, band, penalties)
            risk = None if not st or st.get(f"risk_{band}") is None else min(1.0, float(st[f"risk_{band}"]) + penalty)
            if timed and risk is not None:
                weighted += w * risk
                known_w += w
                if worst is None or risk > worst["risk"]:
                    worst = {"name": f"waiting at {label}", "risk": round(risk, 2), "lit": st.get("lit"),
                             "distance_m": 0, "tunnel": False, "report_penalty": round(penalty, 3)}
            continue
        if mode not in STREET_MODES and not s.get("street") and not s.get("seg_label"):
            legs.append({"mode": mode, "line": s.get("line"), "distance_m": s["distance_m"]})
            if timed:                                  # time on board counts at the fixed in-vehicle value
                vehicle_s += w
                weighted += w * IN_VEHICLE_RISK
                known_w += w
            continue
        label, st, penalty = _row_attrs(s, streets, band, penalties)
        scored_m += d
        if label is None:                              # walk/bike with no data at all: unknown
            unknown_m += d
            continue
        if s.get("street"):
            names.append(s["street"])
        risk = None if not st or st.get(f"risk_{band}") is None else min(1.0, float(st[f"risk_{band}"]) + penalty)
        lit = st.get("lit") if st else None
        if lit == "yes":
            lit_m += d
        elif lit is None:
            unknown_m += d
        if risk is None:
            continue
        weighted += w * risk
        known_w += w
        if worst is None or risk > worst["risk"]:
            worst = {"name": label, "risk": round(risk, 2), "lit": lit, "distance_m": round(d),
                     "tunnel": bool(st.get("tunnel")) if st else False, "report_penalty": round(penalty, 3)}
    mean = weighted / known_w if known_w else None
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
        "wait_min": round(wait_s / 60, 1) if timed else None,
        "on_board_min": round(vehicle_s / 60, 1) if timed else None,
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
    red = cmp["risk_reduction_pct"]
    if red is not None and red > 0:
        claims.append({"text": f"Athena's route takes {cmp['extra_min']:+} min and has {red}% lower route risk than the fastest route.",
                       "facts": ["extra_min", "risk_reduction_pct"]})
    elif red is not None:
        # Not lower risk: say so plainly. (It used to print "-0.8% lower route risk".)
        risk = (f"{abs(red)}% higher than" if red < 0 else "the same as")
        claims.append({"text": f"Athena's route takes {cmp['extra_min']:+} min and its route risk is {risk} the fastest route.",
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


# Says outright that Athena's route is not lower risk (or is higher, or the same). Used by the in-loop rule
# that must not flag such a sentence as "claims lower risk", and by the omission assertion.
NEGATED_LOWER_RISK = re.compile(
    r"\b(not|isn'?t|no|never)\b[^.]{0,25}\b(lower|less|reduc\w*)\b[^.]{0,15}\brisk"
    r"|\brisk\b[^.]{0,25}\b(not|isn'?t|no)\b[^.]{0,10}\b(lower|less|reduced)\b", re.I)
NO_RISK_BENEFIT = re.compile(
    NEGATED_LOWER_RISK.pattern
    + r"|\b(no|not any)\b[^.]{0,15}\brisk (reduction|benefit|advantage|saving)"
    + r"|\b(higher|greater|increased)\b[^.]{0,12}\brisk\b"
    + r"|\brisk\b[^.]{0,25}\b(higher|greater|the same|unchanged)\b"
    + r"|\bsame (route|risk)\b", re.I)


def risk_benefit_missing(facts: dict) -> bool:
    """True when Athena's route is NOT lower risk, so any explanation has to say so."""
    red = facts.get("risk_reduction_pct")
    return red is not None and red <= 0


def says_no_risk_benefit(headline: str, claims: list[dict]) -> bool:
    texts = [headline or ""] + [str(c.get("text", "")) for c in claims]
    return any(NO_RISK_BENEFIT.search(t) for t in texts)


def numbers_in(text: str) -> list[float]:
    # ignore line names and times like T2, L3, 10pm, 1am
    cleaned = re.sub(r"\b[A-Z]+\d+\b|\b\d{1,2}\s?(am|pm)\b|\b\d{1,2}:\d{2}\b", " ", text, flags=re.I)
    return [float(x) for x in re.findall(r"(?<![\w.])[+-]?\d+(?:\.\d+)?", cleaned)]


def close(a: float, b: float) -> bool:
    if b is None or (isinstance(b, float) and math.isnan(b)):
        return False
    return abs(abs(a) - abs(b)) <= max(0.6, 0.02 * abs(b))
