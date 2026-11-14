"""Turn a raw OpenTripPlanner precompute snapshot into the three route tables the app reads.

Input  data/raw/otp_trips_<date>.json   written by pipeline/precompute_trips.py, never edited
Output route_legs     one row per leg of every itinerary (journey planner)
       leg_streets    one row per street step of every itinerary
       route_steps    the "fastest" and "athena" itinerary per trip/band/mode (Ask Athena explanations)

Variant choice uses the existing route rule unchanged (athena.analysis.variant_metrics: 0.55 x
distance-weighted mean street risk + 0.45 x worst stretch): "fastest" is the shortest itinerary and
"athena" the one with the lowest route risk, ties broken by time. When they are the same itinerary both
labels point at it, as in the hackathon data, and the explanation says the routes are the same.
"""
from __future__ import annotations

from athena.analysis import variant_metrics

STEP_FIELDS = ["trip", "origin", "destination", "band", "mode", "variant", "duration_min", "walk_m",
               "transfers", "leg", "leg_mode", "line", "step", "street", "distance_m"]
STREET_MODES = {"WALK", "BICYCLE"}


def itinerary_steps(legs: list[dict], streets: list[dict], trip: dict, variant: str) -> list[dict]:
    """route_steps rows for one itinerary: one row per street step of walk/bike legs, one row per transit leg."""
    head = legs[0]
    out, n = [], 0
    by_leg: dict[int, list[dict]] = {}
    for s in streets:
        by_leg.setdefault(int(s["leg"]), []).append(s)
    for leg in sorted(legs, key=lambda r: int(r["leg"])):
        base = {"trip": f"{trip['origin']}|{trip['destination']}", "origin": trip["origin"],
                "destination": trip["destination"], "band": trip["band"], "mode": trip["mode"],
                "variant": variant, "duration_min": head["total_min"], "walk_m": head["walk_m"],
                "transfers": head["transfers"], "leg": int(leg["leg"]), "leg_mode": leg["leg_mode"]}
        if leg["leg_mode"] in STREET_MODES:
            for st in sorted(by_leg.get(int(leg["leg"]), []), key=lambda r: int(r["step"])):
                out.append({**base, "line": None, "step": n, "street": st.get("street") or None,
                            "distance_m": float(st["distance_m"])})
                n += 1
        else:
            out.append({**base, "line": leg.get("line"), "step": n, "street": None,
                        "distance_m": float(leg["distance_m"])})
            n += 1
    return out


def build_trip_tables(snapshot: dict, street_rows: list[dict]) -> tuple[list[dict], list[dict], list[dict], dict]:
    """(route_legs, leg_streets, route_steps, stats)."""
    streets = {s["name"]: s for s in street_rows}
    route_legs, leg_streets, route_steps = [], [], []
    same, trips_without_routes = 0, 0
    for trip in snapshot.get("trips", []):
        legs, steps = trip.get("legs") or [], trip.get("steps") or []
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
             "fastest_is_athena": same, "route_steps": len(route_steps), "route_legs": len(route_legs),
             "leg_streets": len(leg_streets)}
    return route_legs, leg_streets, route_steps, stats
