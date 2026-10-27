"""Journey planning: build comparable route options and score each one for safety.

Two sources of candidate journeys:

* **Precomputed (default).** Route alternatives our OpenTripPlanner instance produced for the covered
  corridor, stored in Zetaris as `athena.route_legs` and `athena.leg_streets`. The geometry, stop names
  and times are OTP's own output; they are NOT live, and every response says so in `freshness`.
* **Live OTP.** When `OTP_URL` is set, `OTPClient` queries a running OpenTripPlanner over its
  GraphQL `planConnection` API and the same scoring is applied to whatever it returns.

Safety scoring reuses the validated methodology in `analysis.py`: a distance-weighted mean street risk
plus the worst stretch, with approved user reports adding a fading penalty. Band thresholds are the ones
the Athena tagging pipeline already uses (low < 0.33, medium < 0.55), so labels mean the same thing here
as on the map. Unknown lighting is never treated as unlit: it lowers confidence instead.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from dataclasses import dataclass

from .analysis import report_penalties

log = logging.getLogger("athena.journeys")

# Validated band thresholds from the Athena street-tagging pipeline (pipeline/tag_streets.py).
BAND_LOW, BAND_MEDIUM = 0.33, 0.55
UNKNOWN_LIMIT = 30.0          # % of scored distance with unknown lighting before we stop ranking on risk
WORST_WEIGHT = 0.45           # risk = 0.55 x distance-weighted mean + 0.45 x worst stretch
MODES = ("transit", "walk", "bike")
BAND_TIMES = {"18": "18:00", "22": "22:00", "01": "01:00"}
SORTS = ("fastest", "lower_risk", "balanced", "fewest_transfers", "least_walking")
LABELS = {
    "low": "Lower risk indicators",
    "medium": "Moderate risk indicators",
    "high": "Higher risk indicators",
    "unknown": "Insufficient lighting data",
}
STREET_MODES = {"WALK", "BICYCLE", "SCOOTER"}


def band_for_time(hhmm: str) -> str:
    """Map any departure time to the closest street-risk profile Athena has (6 pm, 10 pm, 1 am).

    Daytime uses the 6 pm profile, the earliest one scored; 20:00-23:29 uses 10 pm; later uses 1 am.
    """
    h, m = parse_hhmm(hhmm)
    t = h * 60 + m
    if 5 * 60 <= t < 20 * 60:
        return "18"
    if 20 * 60 <= t < 23 * 60 + 30:
        return "22"
    return "01"


def parse_hhmm(hhmm: str) -> tuple[int, int]:
    if not isinstance(hhmm, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hhmm):
        raise ValueError("time must be HH:MM (24-hour)")
    return int(hhmm[:2]), int(hhmm[3:])


def shift_iso(iso: str | None, minutes: int) -> str | None:
    if not iso:
        return iso
    return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat()


def band_label(risk: float | None, unknown_pct: float) -> tuple[str, str]:
    if risk is None or unknown_pct > UNKNOWN_LIMIT:
        return "unknown", LABELS["unknown"]
    key = "low" if risk < BAND_LOW else "medium" if risk < BAND_MEDIUM else "high"
    return key, LABELS[key]


def score_streets(rows: list[dict], streets: dict[str, dict], band: str,
                  penalties: dict[str, float]) -> dict:
    """Distance-weighted risk over the street steps of one leg or journey."""
    scored = lit = unknown = known = weighted = 0.0
    worst = None
    detail = []
    for r in rows:
        name = r.get("street")
        if not name:
            continue
        d = float(r.get("distance_m") or 0)
        st = streets.get(name)
        raw = None if not st or st.get(f"risk_{band}") is None else float(st[f"risk_{band}"])
        penalty = penalties.get(name, 0.0)
        risk = None if raw is None else min(1.0, raw + penalty)
        lit_tag = st.get("lit") if st else None
        scored += d
        if lit_tag == "yes":
            lit += d
        elif lit_tag is None:
            unknown += d
        detail.append({"street": name, "distance_m": round(d), "risk": risk, "lit": lit_tag,
                       "report_penalty": round(penalty, 3) or None,
                       "tunnel": bool(st.get("tunnel")) if st else None})
        if risk is None:
            continue
        known += d
        weighted += d * risk
        if worst is None or risk > worst["risk"]:
            worst = {"street": name, "risk": round(risk, 3), "lit": lit_tag, "distance_m": round(d)}
    mean = weighted / known if known else None
    risk = None if mean is None else round((1 - WORST_WEIGHT) * mean + WORST_WEIGHT * worst["risk"], 3)
    return {
        "risk": risk, "mean_risk": None if mean is None else round(mean, 3), "worst": worst,
        "scored_m": round(scored), "known_m": round(known),
        "lit_share_pct": round(100 * lit / scored, 1) if scored else None,
        "unknown_share_pct": round(100 * unknown / scored, 1) if scored else None,
        "streets": detail,
    }


def _dist(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{round(m)} m"


def _pct(v: float) -> str:
    return f"{v:g}" if v != int(v) else str(int(v))


MODE_WORDS = {"walk": "walking", "bike": "cycling", "transit": "public transport"}


def explain(journey: dict, fastest: dict | None) -> list[str]:
    """Plain sentences a user can check against the numbers on screen. No safety promises."""
    s = journey["safety"]
    out = []
    if s["risk"] is None:
        out.append("Not enough lighting data on this route to compare its risk indicators.")
    else:
        on = "cycling" if journey.get("mode") == "bike" else "walking"
        out.append(f"{s['label']} ({s['risk']}) across {_dist(s['scored_m'])} of {on} on this route.")
    if s.get("lit_share_pct") is not None:
        out.append(f"{_pct(s['lit_share_pct'])}% of that distance is lit"
                   + (f", and {_pct(s['unknown_share_pct'])}% has no lighting data." if s.get("unknown_share_pct") else "."))
    if s.get("worst"):
        w = s["worst"]
        lit = {"yes": "lit", "no": "unlit", None: "no lighting data"}[w["lit"]]
        out.append(f"Highest-risk stretch: {w['street']} ({lit}, {w['risk']}, {w['distance_m']} m).")
    if fastest and journey["id"] != fastest["id"]:
        extra = round(journey["total_min"] - fastest["total_min"], 1)
        fr, jr = fastest["safety"]["risk"], s["risk"]
        if fr and jr is not None and fr > 0:
            delta = round(100 * (fr - jr) / fr, 1)
            direction = "lower" if delta > 0 else "higher"
            out.append(f"Compared with the fastest {MODE_WORDS.get(journey['mode'], '')} option: {extra:+} min, "
                       f"and {abs(delta)}% {direction} risk indicators.".replace("fastest  option", "fastest option"))
        else:
            out.append(f"Compared with the fastest {MODE_WORDS.get(journey['mode'], '')} option: {extra:+} min."
                       .replace("fastest  option", "fastest option"))
    if journey.get("transit_lines"):
        out.append("Uses " + ", ".join(journey["transit_lines"]) + ".")
    return out


def rank(journeys: list[dict], sort: str) -> list[dict]:
    """Ranking happens AFTER the router produced the candidates; it never invents a route."""
    if not journeys:
        return journeys
    if sort == "fastest":
        return sorted(journeys, key=lambda j: (j["total_min"], j["safety"]["risk"] is None, j["safety"]["risk"] or 0))
    if sort == "fewest_transfers":
        return sorted(journeys, key=lambda j: (j["transfers"], j["total_min"]))
    if sort == "least_walking":
        return sorted(journeys, key=lambda j: (j["walk_m"], j["total_min"]))
    scored = [j for j in journeys if j["safety"]["risk"] is not None]
    unscored = [j for j in journeys if j["safety"]["risk"] is None]
    if sort == "lower_risk":
        return sorted(scored, key=lambda j: (j["safety"]["risk"], j["total_min"])) + unscored
    # balanced: normalise duration and risk across the candidate set, then weight evenly
    if not scored:
        return journeys
    ds = [j["total_min"] for j in scored]
    rs = [j["safety"]["risk"] for j in scored]
    dlo, dhi, rlo, rhi = min(ds), max(ds), min(rs), max(rs)
    def norm(v, lo, hi):
        return 0.0 if hi - lo < 1e-9 else (v - lo) / (hi - lo)
    for j in scored:
        j["balance_score"] = round(0.5 * norm(j["total_min"], dlo, dhi)
                                   + 0.5 * norm(j["safety"]["risk"], rlo, rhi), 4)
    return sorted(scored, key=lambda j: j["balance_score"]) + unscored


def dedupe(journeys: list[dict]) -> list[dict]:
    """The fastest and Athena queries often return the same itinerary. Show it once, and record which
    preferences produced it so the interface can say so rather than listing a route twice."""
    seen: dict[tuple, dict] = {}
    for j in sorted(journeys, key=lambda x: (x["variant"] != "fastest", x["itinerary"])):
        # The path itself is part of the signature: two walking options can share endpoints and
        # departure time while following completely different streets.
        sig = tuple((l["mode"], l["line"], l["from"]["name"], l["to"]["name"], l["start"], l["polyline"])
                    for l in j["legs"])
        if sig in seen:
            kept = seen[sig]
            if j["variant"] not in kept["from_preferences"]:
                kept["from_preferences"].append(j["variant"])
            continue
        j["from_preferences"] = [j["variant"]]
        seen[sig] = j
    return list(seen.values())


@dataclass
class JourneyPlanner:
    source: object                 # ZetarisSource, LocalSource or OTPSource
    schema: str = "athena"

    def plan(self, origin: str, destination: str, band: str, mode: str | list[str], sort: str = "balanced",
             use_reports: bool = True, depart: str | None = None) -> dict:
        """Journey options for one or more modes ("walk", "walk,bike" or a list), ranked together.

        `depart` (HH:MM) is the user's departure time. Live OTP plans for it directly. Precomputed routes
        were planned for the band's time, so they are moved to the chosen departure: walking and cycling
        times stay exact (they don't depend on a timetable); transit times are marked as estimates.
        """
        if sort not in SORTS:
            raise ValueError(f"unknown sort {sort}")
        modes = [m.strip() for m in (mode.split(",") if isinstance(mode, str) else mode) if m.strip()]
        modes = list(dict.fromkeys(modes))
        if not modes or any(m not in MODES for m in modes):
            raise ValueError(f"mode must be one or more of {', '.join(MODES)}")
        if depart is not None:
            parse_hhmm(depart)
        live = bool(self.source.freshness.get("live"))
        streets, ev_st = self.source.streets()
        by_name = {s["name"]: s for s in streets}
        penalties = report_penalties(band) if use_reports else {}

        journeys, evidence = [], []
        for m in modes:
            kw = {"depart": depart} if (live and depart) else {}
            legs_rows, ev_legs = self.source.route_legs(origin, destination, band, m, **kw)
            evidence.append(ev_legs)
            if not legs_rows:
                continue
            street_rows, ev_streets = self.source.leg_streets(origin, destination, band, m, **kw)
            evidence.append(ev_streets)
            journeys.extend(dedupe(self._build(legs_rows, street_rows, by_name, band, m, penalties)))
        evidence.append(ev_st)
        if not journeys:
            return {"journeys": [], "evidence": evidence, "coverage": False, "modes": modes,
                    "freshness": self.source.freshness, "sort": sort, "band": band, "depart": depart}

        shifted = False
        if depart and not live:
            h, mi = parse_hhmm(depart)
            for j in journeys:
                start = datetime.fromisoformat(j["start_time"])
                delta = (h * 60 + mi) - (start.hour * 60 + start.minute)
                delta = (delta + 720) % 1440 - 720          # nearest direction across midnight
                if delta:
                    shifted = True
                    j["start_time"], j["end_time"] = shift_iso(j["start_time"], delta), shift_iso(j["end_time"], delta)
                    for leg in j["legs"]:
                        leg["start"], leg["end"] = shift_iso(leg["start"], delta), shift_iso(leg["end"], delta)
                    j["times_estimated"] = any(l["mode"] not in STREET_MODES for l in j["legs"])
        for j in journeys:
            j.setdefault("times_estimated", False)

        fastest = min(journeys, key=lambda j: j["total_min"])
        fastest_by_mode = {m: min((j for j in journeys if j["mode"] == m), key=lambda j: j["total_min"], default=None)
                           for m in modes}
        for j in journeys:
            j["is_fastest"] = j["id"] == fastest["id"]
            j["explanation"] = explain(j, fastest_by_mode.get(j["mode"]))
        # Ties (walking and cycling often share streets) go to the quicker trip, matching the lower_risk sort.
        lowest = min((j for j in journeys if j["safety"]["risk"] is not None),
                     key=lambda j: (j["safety"]["risk"], j["total_min"]), default=None)
        for j in journeys:
            j["is_lowest_risk"] = bool(lowest and j["id"] == lowest["id"])
        balanced = rank([dict(j) for j in journeys], "balanced")
        best = balanced[0]["id"] if balanced and balanced[0]["safety"]["risk"] is not None and len(journeys) > 2 else None
        for j in journeys:
            j["is_best_balance"] = j["id"] == best
        ordered = rank(journeys, sort)
        timing_note = None
        if shifted:
            timing_note = (f"Planned from routes precomputed for {BAND_TIMES[band]}, moved to your {depart} departure. "
                           "Walking and cycling times are exact; public transport times are estimates, so check live departures.")
        return {
            "journeys": ordered, "coverage": True, "sort": sort, "modes": modes, "band": band, "depart": depart,
            "evidence": evidence, "freshness": self.source.freshness, "timing_note": timing_note,
            "report_penalties": {k: round(v, 3) for k, v in penalties.items()},
        }

    def _build(self, legs_rows, street_rows, by_name, band, mode, penalties) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for r in legs_rows:
            groups.setdefault(r["journey"], []).append(r)
        steps_by_leg: dict[tuple[str, int], list[dict]] = {}
        for r in street_rows:
            steps_by_leg.setdefault((r["journey"], int(r["leg"])), []).append(r)
        journeys = []
        for jid, rows in groups.items():
            rows.sort(key=lambda r: int(r["leg"]))
            head = rows[0]
            legs, all_steps, lines = [], [], []
            for r in rows:
                steps = sorted(steps_by_leg.get((jid, int(r["leg"])), []), key=lambda s: int(s["step"]))
                all_steps.extend(steps)
                leg_safety = score_streets(steps, by_name, band, penalties) if r["leg_mode"] in STREET_MODES else None
                if r.get("line"):
                    kind = r["leg_mode"].title()
                    lines.append(r["line"] if r["line"].lower() == kind.lower() else f"{kind} {r['line']}")
                legs.append({
                    "leg": int(r["leg"]), "mode": r["leg_mode"], "line": r.get("line"),
                    "color": r.get("line_color"), "headsign": r.get("headsign"),
                    "from": {"name": r["from_name"], "lat": float(r["from_lat"]), "lon": float(r["from_lon"])},
                    "to": {"name": r["to_name"], "lat": float(r["to_lat"]), "lon": float(r["to_lon"])},
                    "distance_m": round(float(r["distance_m"] or 0)),
                    "duration_s": int(float(r["duration_s"] or 0)),
                    "start": r.get("leg_start"), "end": r.get("leg_end"),
                    "polyline": r.get("polyline"), "safety": leg_safety,
                })
            safety = score_streets(all_steps, by_name, band, penalties)
            key, label = band_label(safety["risk"], safety.get("unknown_share_pct") or 0)
            safety["band"] = key
            safety["label"] = label
            safety["confidence"] = ("high" if (safety.get("unknown_share_pct") or 0) < 10
                                    else "medium" if (safety.get("unknown_share_pct") or 0) < UNKNOWN_LIMIT else "low")
            journeys.append({
                "id": jid, "variant": head["variant"], "itinerary": int(head["itinerary"]),
                "mode": mode, "band": band,
                "total_min": round(float(head["total_min"]), 1),
                "walk_m": round(float(head["walk_m"] or 0)),
                "transfers": int(float(head["transfers"] or 0)),
                "start_time": head.get("start_time"), "end_time": head.get("end_time"),
                "transit_lines": lines, "legs": legs, "safety": safety,
            })
        return journeys
