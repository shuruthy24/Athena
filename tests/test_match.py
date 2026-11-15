"""Option 3: matching OTP walk/bike legs to OSM ways by location."""
import pytest

from athena.analysis import variant_metrics
from pipeline.match import WayIndex, decode_polyline, match_line
from pipeline.trips import build_trip_tables


def test_decode_polyline_matches_the_reference_example():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def encode(coords):
    out = []
    plat = plon = 0
    for lat, lon in coords:
        for v, prev in ((round(lat * 1e5), plat), (round(lon * 1e5), plon)):
            d = v - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = round(lat * 1e5), round(lon * 1e5)
    return "".join(out)


# Two parallel east-west ways about 11 m apart (a road and its footpath), and a side path further north.
ROAD = {"way_id": 1, "coords": [(-33.9000, 151.2000), (-33.9000, 151.2100)]}
FOOTPATH = {"way_id": 2, "coords": [(-33.8999, 151.2000), (-33.8999, 151.2100)]}
FAR = {"way_id": 3, "coords": [(-33.8950, 151.2000), (-33.8950, 151.2100)]}


def test_route_is_split_by_nearest_way_and_scaled_to_the_leg_distance():
    idx = WayIndex([ROAD, FAR])
    line = [(-33.90001, 151.2010), (-33.90001, 151.2050),        # along the road
            (-33.8970, 151.2050), (-33.8950, 151.2050)]          # north, onto the far path
    runs = match_line(line, idx, total_m=1000.0)
    ways = [w for w, _ in runs]
    assert ways[0] == 1 and ways[-1] == 3 and None in ways      # the stretch between them matches nothing
    assert abs(sum(m for _, m in runs) - 1000.0) < 1e-6


def test_a_route_stays_on_the_way_it_is_following():
    idx = WayIndex([ROAD, FOOTPATH])
    line = [(-33.90000, 151.2010), (-33.89996, 151.2030), (-33.89994, 151.2060)]  # drifts towards the footpath
    assert [w for w, _ in match_line(line, idx)] == [1]          # no flip-flopping onto the parallel way


def test_nothing_nearby_is_unmatched():
    assert [w for w, _ in match_line([(-33.95, 151.30), (-33.95, 151.31)], WayIndex([ROAD]))] == [None]


def _snapshot(polyline):
    legs = [{"journey": "x", "itinerary": 0, "leg": 0, "leg_mode": "WALK", "line": None, "total_min": 10.0,
             "walk_m": 900.0, "transfers": 0, "distance_m": 900.0, "polyline": polyline}]
    steps = [{"itinerary": 0, "leg": 0, "step": 0, "street": "sidewalk", "distance_m": 900.0}]
    return {"trips": [{"origin": "unsw", "destination": "townhall", "band": "22", "mode": "walk",
                       "legs": legs, "steps": steps}]}


def test_trip_steps_come_from_the_location_match_and_unnamed_ways_keep_their_own_data():
    streets = [{"name": "Lit Road", "lit": "yes", "risk_18": 0.2, "risk_22": 0.2, "risk_01": 0.2, "tunnel": False}]
    segments = [{**ROAD, "street": "Lit Road", "label": "Lit Road", "lit": "yes", "risk_22": 0.2},
                {**FAR, "street": None, "label": "unnamed footway", "lit": "no", "risk_18": 0.8, "risk_22": 0.8,
                 "risk_01": 0.8, "highway": "footway", "tunnel": False}]
    line = encode([(-33.90001, 151.2010), (-33.90001, 151.2050), (-33.8970, 151.2050), (-33.8950, 151.2050)])
    _, leg_streets, steps, stats = build_trip_tables(_snapshot(line), streets, segments)
    named = [r for r in steps if r["street"] == "Lit Road"]
    unnamed = [r for r in steps if r.get("seg_label") == "unnamed footway"]
    assert named and unnamed and "sidewalk" not in {r["street"] for r in steps}   # OTP's name is not used
    assert set(stats["by_location"]) == {"named_pct", "unnamed_pct", "unmatched_pct"}
    m = variant_metrics([r for r in steps if r["variant"] == "athena"], {s["name"]: s for s in streets}, "22", {})
    assert m["worst"]["name"] == "unnamed footway" and m["worst"]["risk"] == 0.8     # unnamed data is scored
    assert "unnamed footway" not in m["streets"]                                      # but never named as a street
    assert 0 < m["unknown_share_pct"] < 100                                           # the gap stays unknown
    assert leg_streets and any(r.get("seg_label") for r in leg_streets)


def test_without_segments_the_name_match_is_still_used():
    streets = [{"name": "Lit Road", "lit": "yes", "risk_22": 0.2}]
    _, _, steps, stats = build_trip_tables(_snapshot(encode([(-33.9, 151.201), (-33.9, 151.205)])), streets)
    assert "by_location" not in stats and {r["street"] for r in steps} == {None}


def _transit_trip(mode="transit"):
    t0, t1, t2, t3 = ("2026-11-16T01:00:00+11:00", "2026-11-16T01:05:00+11:00",
                      "2026-11-16T01:15:00+11:00", "2026-11-16T01:30:00+11:00")
    walk = {"journey": "x", "itinerary": 0, "leg": 0, "leg_mode": "WALK", "line": None, "total_min": 30.0,
            "walk_m": 400.0, "transfers": 0, "distance_m": 400.0, "duration_s": 300, "start_time": t0,
            "leg_start": t0, "leg_end": t1, "from_lat": -33.90001, "from_lon": 151.2010, "polyline": None}
    tram = {**walk, "leg": 1, "leg_mode": "TRAM", "line": "L3", "distance_m": 5000.0, "duration_s": 900,
            "leg_start": t2, "leg_end": t3, "from_lat": -33.90001, "from_lon": 151.2050}   # the stop is on ROAD (Lit Road)
    steps = [{"itinerary": 0, "leg": 0, "step": 0, "street": "Lit Road", "distance_m": 400.0}]
    return {"origin": "unsw", "destination": "townhall", "band": "01", "mode": mode, "legs": [walk, tram], "steps": steps}


STREETS = [{"name": "Lit Road", "lit": "yes", "risk_18": 0.2, "risk_22": 0.2, "risk_01": 0.3, "tunnel": False}]
SEGMENTS = [{**ROAD, "street": "Lit Road", "label": "Lit Road", "lit": "yes", "risk_01": 0.3}]


def test_a_wait_at_the_stop_becomes_a_timed_row_scored_from_the_nearest_street():
    _, _, steps, _ = build_trip_tables({"trips": [_transit_trip()]}, STREETS, SEGMENTS)
    rows = [r for r in steps if r["variant"] == "fastest"]
    wait = next(r for r in rows if r["leg_mode"] == "WAIT")
    assert wait["duration_s"] == 600 and wait["street"] == "Lit Road"        # 01:05 -> 01:15 at the stop
    assert all(r.get("duration_s") is not None for r in rows)
    m = variant_metrics(rows, {s["name"]: s for s in STREETS}, "01", {})
    assert (m["wait_min"], m["on_board_min"]) == (10.0, 15.0)
    # time-weighted: (300 s walk + 600 s wait) x 0.3 + 900 s on board x 0.10, over 1800 s
    assert m["mean_risk"] == round((900 * 0.3 + 900 * 0.10) / 1800, 3)


def test_short_gaps_are_not_waits_and_untimed_rows_keep_the_old_rule():
    trip = _transit_trip()
    trip["legs"][1]["leg_start"] = "2026-11-16T01:05:20+11:00"                # 20 s: a transfer, not a wait
    _, _, steps, _ = build_trip_tables({"trips": [trip]}, STREETS, SEGMENTS)
    assert not any(r["leg_mode"] == "WAIT" for r in steps)
    for leg in trip["legs"]:
        leg.pop("duration_s")
    _, _, steps, _ = build_trip_tables({"trips": [trip]}, STREETS, SEGMENTS)
    m = variant_metrics([r for r in steps if r["variant"] == "fastest"], {s["name"]: s for s in STREETS}, "01", {})
    assert m["on_board_min"] is None and m["mean_risk"] == 0.3               # walking streets only, as before


def test_walk_and_bike_trips_drop_itineraries_that_use_transit():
    _, _, steps, stats = build_trip_tables({"trips": [_transit_trip("walk")]}, STREETS, SEGMENTS)
    assert steps == [] and stats["walk_bike_itineraries_with_transit_dropped"] == 1
    assert stats["trips_without_routes"] == 1
    _, _, steps, _ = build_trip_tables({"trips": [_transit_trip("transit")]}, STREETS, SEGMENTS)
    assert steps                                                              # transit trips keep them
