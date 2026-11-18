"""Turn a raw OpenTripPlanner precompute snapshot into the three route tables the app reads.

Input  data/raw/otp_trips_<date>.json   written by pipeline/precompute_trips.py, never edited
Output route_legs     one row per leg of every itinerary (journey planner)
       leg_streets    one row per street step of every itinerary
       route_steps    the "fastest" and "athena" itinerary per trip/band/mode (Ask Athena explanations)

Variant choice uses the app's route rule (athena.analysis.variant_metrics: 0.55 x time-weighted mean + 0.45 x worst
stretch, with waits at stops and time on board included, see there): "fastest" is the shortest itinerary and
"athena" the one with the lowest route risk, ties broken by time. When they are the same itinerary both
labels point at it, as in the hackathon data, and the explanation says the routes are the same.
"""
from __future__ import annotations

from datetime import datetime

from athena.analysis import MIN_WAIT_S, variant_metrics

from .clean import normalise
from .match import WayIndex, decode_polyline, match_line

SEG_FIELDS = ("label", "lit", "risk_18", "risk_22", "risk_01", "highway", "tunnel")

STREET_MODES = {"WALK", "BICYCLE"}
STOP_RADIUS_M = 25.0        # a stop is scored from the nearest street or path within this distance


def _seconds_between(a: str | None, b: str | None) -> float | None:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except (TypeError, ValueError):
        return None


def annotate_waits(legs: list[dict], index: WayIndex | None, segments: dict) -> None:
    """Add wait_s (time at the stop before each public transport leg) and where that wait happens: wait_street
    (a named street) or wait_seg_* (an unnamed way), from the nearest way to the stop. In place."""
    by_it: dict[int, list[dict]] = {}
    for leg in legs:
        by_it.setdefault(int(leg["itinerary"]), []).append(leg)
    for its in by_it.values():
        its.sort(key=lambda r: int(r["leg"]))
        prev_end = its[0].get("start_time")
        for leg in its:
            if leg["leg_mode"] not in STREET_MODES:
                wait = _seconds_between(prev_end, leg.get("leg_start"))
                leg["wait_s"] = None if wait is None else max(0.0, wait)
                near = index.distances(float(leg["from_lat"]), float(leg["from_lon"]), STOP_RADIUS_M) \
                    if index is not None and leg.get("from_lat") is not None else {}
                seg = segments.get(min(near, key=near.get)) if near else None
                if seg and seg["street"]:
                    leg["wait_street"] = seg["street"]
                elif seg:
                    leg.update({f"wait_seg_{k}": seg.get(k) for k in SEG_FIELDS})
            prev_end = leg.get("leg_end") or prev_end


def itinerary_steps(legs: list[dict], streets: list[dict], trip: dict, variant: str) -> list[dict]:
    """route_steps rows for one itinerary: one row per street step of walk/bike legs, one row per transit leg, and
    a WAIT row before a transit leg when the wait at the stop is at least MIN_WAIT_S. When every leg has a
    duration, every row gets duration_s (street steps share their leg's time by distance), so the route score can
    weight by time."""
    head = legs[0]
    out, n = [], 0
    by_leg: dict[int, list[dict]] = {}
    for s in streets:
        by_leg.setdefault(int(s["leg"]), []).append(s)
    timed = all(leg.get("duration_s") is not None for leg in legs)
    for leg in sorted(legs, key=lambda r: int(r["leg"])):
        base = {"trip": f"{trip['origin']}|{trip['destination']}", "origin": trip["origin"],
                "destination": trip["destination"], "band": trip["band"], "mode": trip["mode"],
                "variant": variant, "duration_min": head["total_min"], "walk_m": head["walk_m"],
                "transfers": head["transfers"], "leg": int(leg["leg"]), "leg_mode": leg["leg_mode"]}
        if leg["leg_mode"] in STREET_MODES:
            leg_steps = sorted(by_leg.get(int(leg["leg"]), []), key=lambda r: int(r["step"]))
            total = sum(float(st["distance_m"]) for st in leg_steps)
            for st in leg_steps:
                row = {**base, "line": None, "step": n, "street": st.get("street") or None,
                       "distance_m": float(st["distance_m"]),
                       **{k: v for k, v in st.items() if k.startswith("seg_")}}
                if timed:
                    share = float(st["distance_m"]) / total if total else 1 / len(leg_steps)
                    row["duration_s"] = round(float(leg["duration_s"]) * share, 1)
                out.append(row)
                n += 1
        else:
            if timed and (leg.get("wait_s") or 0) >= MIN_WAIT_S:
                out.append({**base, "leg_mode": "WAIT", "line": None, "step": n, "street": leg.get("wait_street"),
                            "distance_m": 0.0, "duration_s": float(leg["wait_s"]),
                            **{k[5:]: v for k, v in leg.items() if k.startswith("wait_seg_")}})
                n += 1
            row = {**base, "line": leg.get("line"), "step": n, "street": None, "distance_m": float(leg["distance_m"])}
            if timed:
                row["duration_s"] = float(leg["duration_s"])
            out.append(row)
            n += 1
    return out


def matched_steps(leg: dict, index: WayIndex, segments: dict, cache: dict) -> list[dict]:
    """Street steps for one walk/bike leg, matched by location. Named ways carry their street name; unnamed ways
    carry their own data in seg_* fields (and no street name); stretches with no way nearby carry nothing."""
    key = leg["polyline"]
    if key not in cache:
        cache[key] = match_line(decode_polyline(key), index, total_m=float(leg.get("distance_m") or 0) or None)
    rows = []
    for step, (way, m) in enumerate(cache[key]):
        seg = segments.get(way) if way is not None else None
        row = {"journey": leg["journey"], "variant": leg.get("variant"), "itinerary": int(leg["itinerary"]),
               "leg": int(leg["leg"]), "step": step, "street": seg["street"] if seg else None,
               "distance_m": round(m, 1)}
        if seg and seg["street"] is None:
            row.update({f"seg_{k}": seg.get(k) for k in SEG_FIELDS})
        rows.append(row)
    return rows


def build_trip_tables(snapshot: dict, street_rows: list[dict],
                      segment_rows: list[dict] | None = None) -> tuple[list[dict], list[dict], list[dict], dict]:
    """(route_legs, leg_streets, route_steps, stats). With segment_rows (pipeline.features.build_segments), walk and
    bike legs are matched to OSM ways by location; otherwise OTP's step names are matched to street names."""
    streets = {s["name"]: s for s in street_rows}
    canonical = {normalise(name): name for name in streets}
    index = WayIndex(segment_rows) if segment_rows else None
    segments = {r["way_id"]: r for r in segment_rows or []}
    cache: dict = {}
    by_location = {"named_m": 0.0, "unnamed_m": 0.0, "unmatched_m": 0.0}
    matched_m = unmatched_m = 0.0
    unmatched: dict[str, float] = {}
    route_legs, leg_streets, route_steps = [], [], []
    same, trips_without_routes, dropped_transit = 0, 0, 0
    for trip in snapshot.get("trips", []):
        legs = [dict(r) for r in trip.get("legs") or []]
        if trip["mode"] in ("walk", "bike"):
            # Older snapshots asked OTP for walk/bike without directOnly and got tram and bus trips back.
            with_transit = {int(r["itinerary"]) for r in legs if r["leg_mode"] not in STREET_MODES}
            dropped_transit += len(with_transit)
            legs = [r for r in legs if int(r["itinerary"]) not in with_transit]
        kept = {int(r["itinerary"]) for r in legs}
        annotate_waits(legs, index, segments)
        located = set()
        located_steps = []
        if index is not None:
            for leg in legs:
                if leg.get("leg_mode") in STREET_MODES and leg.get("polyline"):
                    rows = matched_steps(leg, index, segments, cache)
                    located.add((int(leg["itinerary"]), int(leg["leg"])))
                    located_steps += rows
                    for r in rows:
                        k = "named_m" if r["street"] else "unnamed_m" if r.get("seg_label") else "unmatched_m"
                        by_location[k] += r["distance_m"]
        # OTP names unnamed ways "path" or "sidewalk", and sometimes uses a cycle-route name. Only names in the
        # streets table carry data; anything else becomes an unnamed step, which is scored as unknown.
        steps = []
        for r in trip.get("steps") or []:
            if int(r["itinerary"]) not in kept or (int(r["itinerary"]), int(r["leg"])) in located:
                continue                                  # replaced by the location match above
            name = canonical.get(normalise(r.get("street"))) if r.get("street") else None
            d = float(r.get("distance_m") or 0)
            if name:
                matched_m += d
            else:
                unmatched_m += d
                if r.get("street"):
                    unmatched[r["street"]] = unmatched.get(r["street"], 0.0) + d
            steps.append({**r, "street": name})
        steps += located_steps
        itineraries = sorted({int(r["itinerary"]) for r in legs})
        if not itineraries:
            trips_without_routes += 1
            continue
        ctx = {k: trip[k] for k in ("origin", "destination", "band", "mode")}
        scored = []
        for it in itineraries:
            it_legs = [r for r in legs if int(r["itinerary"]) == it]
            it_streets = [r for r in steps if int(r["itinerary"]) == it]
            rs = itinerary_steps(it_legs, it_streets, ctx, "candidate")
            m = variant_metrics(rs, streets, trip["band"], {})
            scored.append((it, it_legs, it_streets, rs, m))
        fastest = min(scored, key=lambda x: (x[4]["duration_min"], x[0]))
        athena = min(scored, key=lambda x: (x[4]["risk"] if x[4]["risk"] is not None else 9.0,
                                            x[4]["duration_min"], x[0]))
        same += fastest[0] == athena[0]
        for variant, chosen in (("fastest", fastest), ("athena", athena)):
            it, it_legs, it_streets, rs, _ = chosen
            journey = f"{trip['origin']}|{trip['destination']}|{trip['band']}|{trip['mode']}|{variant}|{it}"
            route_steps += [{**r, "variant": variant} for r in rs]
            route_legs += [{**r, **ctx, "trip": f"{ctx['origin']}|{ctx['destination']}", "journey": journey,
                            "variant": variant} for r in it_legs]
            leg_streets += [{**r, "trip": f"{ctx['origin']}|{ctx['destination']}", "band": ctx["band"],
                             "mode": ctx["mode"], "journey": journey, "variant": variant} for r in it_streets]
    stats = {"trips": len(snapshot.get("trips", [])), "trips_without_routes": trips_without_routes,
             "fastest_is_athena": same, "walk_bike_itineraries_with_transit_dropped": dropped_transit,
             "route_steps": len(route_steps), "route_legs": len(route_legs),
             "leg_streets": len(leg_streets),
             "step_distance_matched_pct": round(100 * matched_m / ((matched_m + unmatched_m) or 1), 1),
             "top_unmatched_names": [n for n, _ in sorted(unmatched.items(), key=lambda x: -x[1])[:10]]}
    located_total = sum(by_location.values())
    if located_total:
        stats["by_location"] = {"named_pct": round(100 * by_location["named_m"] / located_total, 1),
                                "unnamed_pct": round(100 * by_location["unnamed_m"] / located_total, 1),
                                "unmatched_pct": round(100 * by_location["unmatched_m"] / located_total, 1)}
    return route_legs, leg_streets, route_steps, stats
