"""Street features from an OSM snapshot, and the street risk rule.

Per named street (all OSM ways sharing a name are combined, weighted by length):
  highway         the class covering the most length
  lit_osm         "yes" / "no" when at least half the length has an OSM lit tag, else None (unknown)
  lit             lit_osm; or "yes" inferred from lamps (see infer_lit) when lit_osm is unknown and
                  INFER_LIT_FROM_LAMPS is on. Lamps only ever add "yes": no lamps mapped stays unknown,
                  because OSM lamp mapping is incomplete (absence of a mapped lamp is not darkness).
  lit_source      "osm", "lamps" or None
  tunnel          any way tagged tunnel=yes or building_passage
  lights_per_100m highway=street_lamp nodes within LAMP_RADIUS_M of the street, per 100 m
  venues_<band>   venues within VENUE_RADIUS_M whose opening_hours say they are open at that band
                  (Friday 6 pm, 10 pm, and 1 am Saturday); venues with unknown hours are not counted

Street risk rule (a rule, not a model: there are no risk labels, Rules of ML #1). The hackathon's hand-made
streets had risk values but no written formula, so this rule is new and the team should review its weights.
  risk_<band> = highway base + lighting term + tunnel term + quiet term(band) + late term(band) + lamp bonus
  lighting term: lit 0, unknown UNKNOWN_LIT_TERM (0.15), unlit UNLIT_TERM (0.30). Unknown is scored halfway
  (Option 2, team decision 15 Nov 2026): never as unlit, but not free either, so routes are not steered onto
  streets we know nothing about. The street's lit value stays None, so explanations still report it as unknown
  lighting and confidence stays low.
"""
from __future__ import annotations

import json
from collections import Counter

from . import opening_hours
from .clean import drop_missing, fix_encoding, normalise
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
UNKNOWN_LIT_TERM = 0.15     # halfway between lit (0) and unlit (UNLIT_TERM); team decision 15 Nov 2026
TUNNEL_TERM = 0.15
LATE_TERM = {"18": 0.0, "22": 0.05, "01": 0.10}
LAMP_BONUS = -0.05          # applied when lit and at least LAMP_DENSE lamps per 100 m
LAMP_DENSE = 2.0
# Lamp inference (Option 1, 15 Nov 2026). Acceptance criteria were fixed before the agreement was measured
# (evals/eval_lit_inference.py): on streets that have an OSM lit tag, at least 90% of lamp-inferred "yes" must be
# OSM "yes", on at least 30 such streets. The thresholds below are set from street-light spacing, not tuned.
INFER_LIT_FROM_LAMPS = True   # passed: evals/results/lit_inference.json
LAMP_INFER_MIN = 2          # at least this many mapped lamps along the street
LAMP_INFER_PER_100M = 1.0   # and at least one per 100 m (street lights are usually 30-50 m apart)


def infer_lit(lamps: int, lights_per_100m: float | None) -> str | None:
    """'yes' when enough lamps are mapped along the street, otherwise None. Never returns 'no'."""
    if lights_per_100m is None:
        return None
    return "yes" if lamps >= LAMP_INFER_MIN and lights_per_100m >= LAMP_INFER_PER_100M else None


LIT_YES = {"yes", "24/7", "automatic", "sunset-sunrise", "interval"}
LIT_NO = {"no", "disused"}


def quiet_term(venues_open: int) -> float:
    return 0.0 if venues_open >= 5 else 0.07 if venues_open >= 1 else 0.15


def street_risk(highway: str | None, lit: str | None, tunnel: bool, venues_open: int, band: str,
                lights_per_100m: float | None = None) -> float | None:
    risk = HIGHWAY_BASE.get(highway or "", 0.30)
    risk += UNLIT_TERM if lit == "no" else UNKNOWN_LIT_TERM if lit is None else 0.0
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


class _Context:
    """Lamp and venue indexes, and the scoring of one group of OSM ways (one named street, or one unnamed way)."""

    def __init__(self, lamps: list, venues: list):
        self.has_lamps = bool(lamps)
        self.lamp_grid = PointGrid(lamps)
        self.venue_grid = PointGrid([(v["lat"], v["lon"]) for v in venues])
        self.open_by_band = {b: [opening_hours.open_at(v["opening_hours"], b) is True for v in venues] for b in BANDS}

    def score(self, group: list[dict]) -> dict:
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
        lit_osm = None if known < 0.5 * total else ("yes" if lit_len["yes"] >= lit_len["no"] else "no")
        highway = max(by_hw, key=by_hw.get)
        tunnel = any((w["tunnel"] or "").lower() in ("yes", "building_passage") for w in group)
        lamp_ids, venue_ids = set(), set()
        for w in group:
            lamp_ids |= self.lamp_grid.near_line(w["coords"], LAMP_RADIUS_M)
            venue_ids |= self.venue_grid.near_line(w["coords"], VENUE_RADIUS_M)
        lights = round(100 * len(lamp_ids) / total, 2) if self.has_lamps else None
        inferred = infer_lit(len(lamp_ids), lights) if INFER_LIT_FROM_LAMPS and lit_osm is None else None
        lit = lit_osm or inferred
        row = {"highway": highway, "lit": lit, "tunnel": tunnel,
               "lit_osm": lit_osm, "lit_source": "osm" if lit_osm else "lamps" if inferred else None,
               "lamps": len(lamp_ids), "lights_per_100m": lights, "length_m": round(total, 1),
               "lit_known_share": round(known / total, 3), "osm_ways": len(group), "source": "osm"}
        for b in BANDS:
            n_open = sum(1 for j in venue_ids if self.open_by_band[b][j])
            row[f"venues_{b}"] = n_open
            row[f"risk_{b}"] = street_risk(highway, lit, tunnel, n_open, b, lights)
        return row


def build_streets(snapshot: dict) -> tuple[list[dict], dict]:
    """Street rows plus a stats dict for the run report."""
    ways, lamps, venues = split_elements(snapshot)
    ways, missing = drop_missing(ways, ["name", "highway"])
    ways, enc_fixed = fix_encoding(ways, ["name"])
    ways = list({w["id"]: w for w in ways}.values())  # exact duplicate ways by OSM id

    # Group case-insensitively ("Gadigal Avenue" and "Gadigal avenue" are one street) and keep the most
    # common spelling as the display name.
    groups: dict[str, list[dict]] = {}
    for w in ways:
        groups.setdefault(normalise(w["name"]), []).append(w)
    by_name: dict[str, list[dict]] = {}
    for group in groups.values():
        spellings = Counter(" ".join(w["name"].split()) for w in group)
        by_name[min(spellings, key=lambda n: (-spellings[n], n))] = group

    ctx = _Context(lamps, venues)
    rows = []
    for i, (name, group) in enumerate(sorted(by_name.items()), 1):
        rows.append({"id": i, "name": name, **ctx.score(group),
                     "geometry": json.dumps([[[lon, lat] for lat, lon in w["coords"]] for w in group])})
    stats = {"ways": len(ways), "ways_missing_name": missing, "names_encoding_fixed": enc_fixed,
             "lamps": len(lamps), "venues": len(venues),
             "venues_unknown_hours": sum(1 for v in venues if opening_hours.parse(v["opening_hours"]) is None),
             "streets": len(rows), "streets_lit_unknown": sum(1 for r in rows if r["lit"] is None),
             "streets_lit_from_lamps": sum(1 for r in rows if r["lit_source"] == "lamps"),
             "infer_lit_from_lamps": INFER_LIT_FROM_LAMPS}
    return rows, stats


SEGMENT_KEYS = ("highway", "lit", "lit_source", "tunnel", "risk_18", "risk_22", "risk_01")


def build_segments(snapshot: dict, street_rows: list[dict]) -> tuple[list[dict], dict]:
    """One row per OSM way, for matching routes by location (pipeline/match.py). Named ways take their street's
    values (so a route and its explanation agree with the streets table); unnamed ways are scored on their own
    with the same rule, and are labelled by type ("unnamed footway"), never given a street name."""
    ways, lamps, venues = split_elements(snapshot)
    ways = list({w["id"]: w for w in ways}.values())
    streets = {normalise(r["name"]): r for r in street_rows}
    ctx = _Context(lamps, venues)
    rows, named, unnamed, no_street = [], 0, 0, 0
    for w in ways:
        if w.get("name"):
            st = streets.get(normalise(" ".join(w["name"].split())))
            if st is None:      # a named way dropped from the streets table (e.g. bad encoding): skip it
                no_street += 1
                continue
            rows.append({"way_id": w["id"], "street": st["name"], "label": st["name"], "coords": w["coords"],
                         **{k: st.get(k) for k in SEGMENT_KEYS}})
            named += 1
        else:
            sc = ctx.score([w])
            rows.append({"way_id": w["id"], "street": None, "label": f"unnamed {w['highway']}", "coords": w["coords"],
                         **{k: sc.get(k) for k in SEGMENT_KEYS}})
            unnamed += 1
    stats = {"segments": len(rows), "named": named, "unnamed": unnamed, "named_without_street": no_street,
             "unnamed_lit_unknown": sum(1 for r in rows if r["street"] is None and r["lit"] is None)}
    return rows, stats
