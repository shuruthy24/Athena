"""Journey planning: build comparable route options and score each one for safety.

Two sources of candidate journeys:

* **Precomputed (default).** Route alternatives our OpenTripPlanner instance produced for the covered
  corridor, stored as the tables `route_legs` and `leg_streets`. The geometry, stop names
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

from .analysis import IN_VEHICLE_RISK, MIN_WAIT_S
from dataclasses import dataclass

from .analysis import report_penalties

log = logging.getLogger("athena.journeys")

# Validated band thresholds from the Athena street-tagging pipeline (pipeline/tag_streets.py).
BAND_LOW, BAND_MEDIUM = 0.33, 0.55
UNKNOWN_LIMIT = 30.0          # % of scored distance with unknown lighting above which confidence is "low"
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
# Journeys are labelled against the fastest option (team decision): the rule's absolute scores have
# no ground truth, and on the real data most streets land in "high", so only the comparison is meaningful.
RELATIVE_LABELS = {
    "reference": "Fastest option",
    "lower": "Lower risk than fastest",
    "similar": "Similar risk to fastest",
    "higher": "Higher risk than fastest",
    "unknown": "Not enough data to compare",
}
SIMILAR_PCT = 5.0             # within 5% of the fastest option's score counts as similar


def relative_band(risk: float | None, ref_risk: float | None, is_ref: bool) -> tuple[str, str]:
    if is_ref and risk is not None:
        return "reference", RELATIVE_LABELS["reference"]
    if risk is None or not ref_risk:
        return "unknown", RELATIVE_LABELS["unknown"]
    change = 100 * (risk - ref_risk) / ref_risk
    key = "lower" if change < -SIMILAR_PCT else "higher" if change > SIMILAR_PCT else "similar"
    return key, RELATIVE_LABELS[key]
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


def band_label(risk: float | None) -> tuple[str, str]:
    """Band from the risk score. Missing lighting doesn't hide the band (unknown lighting is scored halfway);
    it lowers the journey's `confidence` instead, which the planner shows."""
    if risk is None:
        return "unknown", LABELS["unknown"]
    key = "low" if risk < BAND_LOW else "medium" if risk < BAND_MEDIUM else "high"
    return key, LABELS[key]


def score_streets(rows: list[dict], streets: dict[str, dict], band: str,
                  penalties: dict[str, float], extra: list[dict] | None = None) -> dict:
    """Risk over one leg or journey: 0.55 x mean + 0.45 x worst stretch.

    With `extra` (a whole journey whose rows carry duration_s) the mean is weighted by time and also covers waits
    at stops and time on board (extra items: {"kind": "wait"|"vehicle", "seconds", "risk", "label", "lit"});
    see athena.analysis.variant_metrics. Otherwise it is distance-weighted over the street steps. Lit and
    unknown-lighting shares are always over walk/bike distance only."""
    timed = extra is not None and all(r.get("duration_s") is not None for r in rows)
    scored = lit = unknown = known_d = known_w = weighted = 0.0
    worst = None
    detail = []
    for r in rows:
        name = r.get("street")
        d = float(r.get("distance_m") or 0)
        w = float(r["duration_s"]) if timed else d
        if not name and not r.get("seg_label"):  # no way matched, or a name not in the streets table: unknown
            scored += d
            unknown += d
            continue
        if name:
            st, penalty = streets.get(name), penalties.get(name, 0.0)
        else:  # an unnamed way matched by location (pipeline/match.py): its own data, labelled by type
            name, penalty = r["seg_label"], 0.0
            st = {"lit": r.get("seg_lit"), f"risk_{band}": r.get(f"seg_risk_{band}"), "tunnel": r.get("seg_tunnel")}
        raw = None if not st or st.get(f"risk_{band}") is None else float(st[f"risk_{band}"])
        risk = None if raw is None else min(1.0, raw + penalty)
        lit_tag = st.get("lit") if st else None
        scored += d
        if lit_tag == "yes":
            lit += d
        elif lit_tag is None:
            unknown += d
        detail.append({"street": name, "named": bool(r.get("street")), "distance_m": round(d), "risk": risk, "lit": lit_tag,
                       "report_penalty": round(penalty, 3) or None,
                       "tunnel": bool(st.get("tunnel")) if st else None})
        if risk is None:
            continue
        known_d += d
        known_w += w
        weighted += w * risk
        if worst is None or risk > worst["risk"]:
            worst = {"street": name, "risk": round(risk, 3), "lit": lit_tag, "distance_m": round(d)}
    wait_s = vehicle_s = 0.0
    for e in (extra or []) if timed else []:
        if e["kind"] == "wait":
            wait_s += e["seconds"]
        else:
            vehicle_s += e["seconds"]
        if e["risk"] is None:              # a stop with no street data nearby: unknown, left out of the mean
            continue
        known_w += e["seconds"]
        weighted += e["seconds"] * e["risk"]
        if e["kind"] == "wait" and (worst is None or e["risk"] > worst["risk"]):
            worst = {"street": f"waiting at {e['label']}", "risk": round(e["risk"], 3), "lit": e.get("lit"),
                     "distance_m": 0, "wait_min": round(e["seconds"] / 60, 1)}
    mean = weighted / known_w if known_w else None
    risk = None if mean is None else round((1 - WORST_WEIGHT) * mean + WORST_WEIGHT * (worst["risk"] if worst else mean), 3)
    return {
        "risk": risk, "mean_risk": None if mean is None else round(mean, 3), "worst": worst,
        "scored_m": round(scored), "known_m": round(known_d),
        "lit_share_pct": round(100 * lit / scored, 1) if scored else None,
        "unknown_share_pct": round(100 * unknown / scored, 1) if scored else None,
        "wait_min": round(wait_s / 60, 1) if timed else None,
        "on_board_min": round(vehicle_s / 60, 1) if timed else None,
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
        parts = [f"{_dist(s['scored_m'])} of {on}"]
        if s.get("on_board_min"):
            parts.append(f"{_pct(s['on_board_min'])} min on board")
        if s.get("wait_min"):
            parts.append(f"{_pct(s['wait_min'])} min waiting at stops")
        out.append(f"Risk indicator {s['risk']} ({s['label'].lower()}) over " + ", ".join(parts) + ".")
    if s.get("lit_share_pct") is not None:
        out.append(f"{_pct(s['lit_share_pct'])}% of that distance is lit"
                   + (f", and {_pct(s['unknown_share_pct'])}% has no lighting data." if s.get("unknown_share_pct") else "."))
    if s.get("worst"):
        w = s["worst"]
        lit = {"yes": "lit", "no": "unlit", None: "no lighting data"}[w["lit"]]
        size = f"{_pct(w['wait_min'])} min" if w.get("wait_min") else f"{w['distance_m']} m"
        out.append(f"Highest-risk stretch: {w['street']} ({lit}, {w['risk']}, {size}).")
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
    source: object                 # LocalSource or OTPSource
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
            j["safety"]["band"], j["safety"]["label"] = relative_band(
                j["safety"]["risk"], fastest["safety"]["risk"], j["is_fastest"])
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

    @staticmethod
    def _wait(r: dict, by_name: dict, band: str, penalties: dict) -> dict:
        """The wait before a public transport leg, scored from the street or path nearest the stop."""
        if r.get("wait_street"):
            st, label = by_name.get(r["wait_street"]), r["wait_street"]
            penalty = penalties.get(label, 0.0)
        else:
            st = {"lit": r.get("wait_seg_lit"), f"risk_{band}": r.get(f"wait_seg_risk_{band}")} \
                if r.get("wait_seg_label") else None
            label, penalty = r.get("wait_seg_label") or r.get("from_name") or "the stop", 0.0
        raw = None if not st or st.get(f"risk_{band}") is None else float(st[f"risk_{band}"])
        return {"kind": "wait", "seconds": float(r["wait_s"]), "label": label,
                "risk": None if raw is None else min(1.0, raw + penalty), "lit": st.get("lit") if st else None}

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
            legs, all_steps, lines, extra = [], [], [], []
            timed = all(r.get("duration_s") is not None for r in rows)
            for r in rows:
                steps = sorted(steps_by_leg.get((jid, int(r["leg"])), []), key=lambda s: int(s["step"]))
                if timed and r["leg_mode"] in STREET_MODES:
                    total = sum(float(x.get("distance_m") or 0) for x in steps)
                    steps = [{**x, "duration_s": float(r["duration_s"]) * (float(x.get("distance_m") or 0) / total
                                                                           if total else 1 / len(steps))}
                             for x in steps]
                all_steps.extend(steps)
                if timed and r["leg_mode"] not in STREET_MODES:
                    extra.append({"kind": "vehicle", "seconds": float(r["duration_s"]), "risk": IN_VEHICLE_RISK})
                    if (r.get("wait_s") or 0) >= MIN_WAIT_S:
                        extra.append(self._wait(r, by_name, band, penalties))
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
            safety = score_streets(all_steps, by_name, band, penalties, extra=extra if timed else None)
            key, label = band_label(safety["risk"])
            safety["band"] = key           # replaced by a label relative to the fastest option in plan()
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
