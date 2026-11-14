"""Street features from an OSM snapshot, and the street risk rule.

Per named street (all OSM ways sharing a name are combined, weighted by length):
  highway         the class covering the most length
  lit             "yes" / "no" when at least half the length has a lit tag, else None (unknown)
  tunnel          any way tagged tunnel=yes or building_passage
  lights_per_100m highway=street_lamp nodes within LAMP_RADIUS_M of the street, per 100 m
  venues_<band>   venues within VENUE_RADIUS_M whose opening_hours say they are open at that band
                  (Friday 6 pm, 10 pm, and 1 am Saturday); venues with unknown hours are not counted

Street risk rule (a rule, not a model: there are no risk labels, Rules of ML #1). The hackathon's hand-made
streets had risk values but no written formula, so this rule is new and the team should review its weights.
  risk_<band> = highway base + unlit term + tunnel term + quiet term(band) + late term(band) + lamp bonus
  unknown lighting -> risk None: the street stays unknown and lowers confidence; it is never scored as unlit
"""
from __future__ import annotations

import json

from . import opening_hours
from .clean import drop_missing, fix_encoding
from .geo import PointGrid, line_length_m

BANDS = ("18", "22", "01")
LAMP_RADIUS_M = 20.0
VENUE_RADIUS_M = 50.0

HIGHWAY_BASE = {  # busier main roads score lower than paths away from traffic
    "primary": 0.15, "secondary": 0.15, "tertiary": 0.15,
    "primary_link": 0.15, "secondary_link": 0.15, "tertiary_link": 0.15,
    "pedestrian": 0.25,
    "residential": 0.30, "unclassified": 0.30, "living_street": 0.30, "service": 0.30,
    "footway": 0.40, "path": 0.40, "cycleway": 0.40, "steps": 0.40, "track": 0.40,
}
UNLIT_TERM = 0.30
TUNNEL_TERM = 0.15
LATE_TERM = {"18": 0.0, "22": 0.05, "01": 0.10}
LAMP_BONUS = -0.05          # applied when lit and at least LAMP_DENSE lamps per 100 m
LAMP_DENSE = 2.0
LIT_YES = {"yes", "24/7", "automatic", "sunset-sunrise", "interval"}
LIT_NO = {"no", "disused"}


def quiet_term(venues_open: int) -> float:
    return 0.0 if venues_open >= 5 else 0.07 if venues_open >= 1 else 0.15


def street_risk(highway: str | None, lit: str | None, tunnel: bool, venues_open: int, band: str,
                lights_per_100m: float | None = None) -> float | None:
    if lit is None:
        return None
    risk = HIGHWAY_BASE.get(highway or "", 0.30)
    risk += UNLIT_TERM if lit == "no" else 0.0
    risk += TUNNEL_TERM if tunnel else 0.0
    risk += quiet_term(venues_open) + LATE_TERM[band]
    if lit == "yes" and lights_per_100m is not None and lights_per_100m >= LAMP_DENSE:
        risk += LAMP_BONUS
    return round(min(1.0, max(0.0, risk)), 2)


def _lit_value(tag: str | None) -> str | None:
    tag = (tag or "").strip().lower()
    return "yes" if tag in LIT_YES else "no" if tag in LIT_NO else None


def split_elements(snapshot: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(street ways, lamp nodes, venues) from an Overpass snapshot."""
    ways, lamps, venues = [], [], []
    for el in snapshot.get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "way" and tags.get("highway") and el.get("geometry"):
            ways.append({"id": el["id"], "name": tags.get("name"), "highway": tags.get("highway"),
                         "lit": tags.get("lit"), "tunnel": tags.get("tunnel"),
                         "coords": [(g["lat"], g["lon"]) for g in el["geometry"]]})
        elif el.get("type") == "node" and tags.get("highway") == "street_lamp" or \
                (el.get("type") == "node" and not tags and "lat" in el):
            lamps.append((el["lat"], el["lon"]))
        elif tags.get("amenity") or tags.get("shop"):
            c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
            if c:
                venues.append({"lat": c["lat"], "lon": c["lon"], "opening_hours": tags.get("opening_hours")})
    return ways, lamps, venues


def build_streets(snapshot: dict) -> tuple[list[dict], dict]:
    """Street rows plus a stats dict for the run report."""
    ways, lamps, venues = split_elements(snapshot)
    ways, missing = drop_missing(ways, ["name", "highway"])
    ways, enc_fixed = fix_encoding(ways, ["name"])
    ways = list({w["id"]: w for w in ways}.values())  # exact duplicate ways by OSM id
    lamp_grid = PointGrid(lamps)
    venue_grid = PointGrid([(v["lat"], v["lon"]) for v in venues])
    open_by_band = {b: [opening_hours.open_at(v["opening_hours"], b) is True for v in venues] for b in BANDS}

    by_name: dict[str, list[dict]] = {}
    for w in ways:
        by_name.setdefault(" ".join(w["name"].split()), []).append(w)

    rows = []
    for i, (name, group) in enumerate(sorted(by_name.items()), 1):
        lengths = [line_length_m(w["coords"]) for w in group]
        total = sum(lengths) or 1.0
        by_hw: dict[str, float] = {}
        lit_len = {"yes": 0.0, "no": 0.0}
        for w, length in zip(group, lengths):
            by_hw[w["highway"]] = by_hw.get(w["highway"], 0.0) + length
            lv = _lit_value(w["lit"])
            if lv:
                lit_len[lv] += length
        known = lit_len["yes"] + lit_len["no"]
        lit = None if known < 0.5 * total else ("yes" if lit_len["yes"] >= lit_len["no"] else "no")
        highway = max(by_hw, key=by_hw.get)
        tunnel = any((w["tunnel"] or "").lower() in ("yes", "building_passage") for w in group)
        lamp_ids, venue_ids = set(), set()
        for w in group:
            lamp_ids |= lamp_grid.near_line(w["coords"], LAMP_RADIUS_M)
            venue_ids |= venue_grid.near_line(w["coords"], VENUE_RADIUS_M)
        lights = round(100 * len(lamp_ids) / total, 2) if lamps else None
        row = {"id": i, "name": name, "highway": highway, "lit": lit, "tunnel": tunnel,
               "lights_per_100m": lights, "length_m": round(total, 1),
               "lit_known_share": round(known / total, 3), "osm_ways": len(group),
               "geometry": json.dumps([[[lon, lat] for lat, lon in w["coords"]] for w in group]),
               "source": "osm"}
        for b in BANDS:
            n_open = sum(1 for j in venue_ids if open_by_band[b][j])
            row[f"venues_{b}"] = n_open
            row[f"risk_{b}"] = street_risk(highway, lit, tunnel, n_open, b, lights)
        rows.append(row)
    stats = {"ways": len(ways), "ways_missing_name": missing, "names_encoding_fixed": enc_fixed,
             "lamps": len(lamps), "venues": len(venues),
             "venues_unknown_hours": sum(1 for v in venues if opening_hours.parse(v["opening_hours"]) is None),
             "streets": len(rows), "streets_lit_unknown": sum(1 for r in rows if r["lit"] is None)}
    return rows, stats
