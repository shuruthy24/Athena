"""Data pipeline (AGENTS.md Step 2) on a tiny Overpass-shaped fixture: opening hours, cleaning, street features
and the risk rule, trip variant selection, Parquet export, LocalSource and pipeline.check."""
import json
import shutil
from pathlib import Path

import pytest

from athena.data.source import LocalSource, read_table
from pipeline import opening_hours
from pipeline.check import run_checks
from pipeline.clean import drop_duplicates, find_conflicts, fix_encoding, remove_pii
from pipeline.features import build_streets, street_risk
from pipeline.ingest import fetch_osm, overpass_query
from pipeline.run import build
from pipeline.trips import build_trip_tables

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def streets():
    rows, stats = build_streets(json.loads((FIX / "osm_tiny.json").read_text()))
    return {r["name"]: r for r in rows}, stats


# ---------------------------------------------------------------- opening hours
@pytest.mark.parametrize("value,band,expected", [
    ("24/7", "01", True),
    ("Mo-Su 16:00-02:00", "01", True),          # Friday's span runs past midnight
    ("Mo-Su 16:00-02:00", "18", True),
    ("Mo-Fr 11:00-21:00; Sa,Su off", "18", True),
    ("Mo-Fr 11:00-21:00; Sa,Su off", "22", False),
    ("Mo-Th 17:00-23:00", "22", False),         # Friday not listed
    ("by appointment", "22", None),             # not understood -> unknown, never "open"
    (None, "18", None),
])
def test_opening_hours(value, band, expected):
    assert opening_hours.open_at(value, band) is expected


# ---------------------------------------------------------------- cleaning (Tech 4 helpers)
def test_remove_pii_marks_each_kind():
    text, kinds = remove_pii("Call 0412 345 678 or mail a.b@example.com, I live at 12 Smith Street, see www.x.com")
    assert "0412" not in text and "@" not in text and "12 Smith" not in text and "www." not in text
    assert {"phone", "email", "address", "url"} <= set(kinds)
    assert remove_pii("Two lights out on Anzac Parade near the oval")[1] == []


def test_duplicates_and_conflicts():
    rows = [{"t": "Dark path", "y": "approve"}, {"t": "dark  PATH", "y": "approve"}, {"t": "Dark path", "y": "review"}]
    kept, dropped = drop_duplicates(rows, ["t"])
    assert len(kept) == 1 and dropped == 2
    conflicts = find_conflicts(rows, ["t"], "y")
    assert len(conflicts) == 1 and conflicts[0]["labels"] == ["approve", "review"]
    fixed, n = fix_encoding([{"t": "a​b\r\nc"}], ["t"])
    assert fixed[0]["t"] == "ab\nc" and n == 1


# ---------------------------------------------------------------- features and risk rule
def test_street_features(streets):
    s, stats = streets
    assert set(s) == {"George Street", "Moore Park path", "Quiet Lane", "Station Tunnel Walk", "Market Lane"}
    assert stats["ways_missing_name"] == 1
    g = s["George Street"]
    assert g["osm_ways"] == 2 and g["lit"] == "yes" and g["highway"] == "primary"
    assert g["lights_per_100m"] > 0 and s["Moore Park path"]["lights_per_100m"] == 0
    # bar (to 02:00) + 24/7 shop at 1 am; restaurant shuts at 21:00; cafe hours unknown -> never counted
    assert (g["venues_18"], g["venues_22"], g["venues_01"]) == (3, 2, 2)
    assert s["Station Tunnel Walk"]["tunnel"] is True


def test_unknown_lighting_stays_unknown(streets):
    s, _ = streets
    assert s["Quiet Lane"]["lit"] is None and s["Quiet Lane"]["risk_22"] is None
    assert s["Market Lane"]["lit"] is None  # lit=limited is not read as either yes or no


def test_risk_rule_orders_streets_sensibly(streets):
    s, _ = streets
    assert s["Moore Park path"]["risk_22"] > s["George Street"]["risk_22"]
    assert s["Moore Park path"]["risk_01"] > s["Moore Park path"]["risk_18"]
    assert street_risk("footway", "yes", True, 0, "22") > street_risk("footway", "yes", False, 0, "22")
    assert street_risk("primary", "no", False, 10, "18") > street_risk("primary", "yes", False, 10, "18")
    assert street_risk("primary", None, False, 10, "18") is None


# ---------------------------------------------------------------- trips
def _otp_snapshot(slow_street: str, fast_street: str) -> dict:
    def itin(i, minutes, street):
        legs = [{"itinerary": i, "leg": 0, "leg_mode": "WALK", "line": None, "total_min": minutes,
                 "walk_m": 1000.0, "transfers": 0, "distance_m": 1000.0}]
        steps = [{"itinerary": i, "leg": 0, "step": 0, "street": street, "distance_m": 1000.0}]
        return legs, steps
    l0, s0 = itin(0, 12.0, fast_street)
    l1, s1 = itin(1, 15.0, slow_street)
    return {"trips": [{"origin": "unsw", "destination": "townhall", "band": "22", "mode": "walk",
                       "legs": l0 + l1, "steps": s0 + s1},
                      {"origin": "townhall", "destination": "unsw", "band": "22", "mode": "walk",
                       "legs": [], "steps": []}]}


def test_fastest_and_lower_risk_itineraries_are_labelled(streets):
    rows = list(streets[0].values())
    legs, leg_streets, steps, stats = build_trip_tables(_otp_snapshot("George Street", "Moore Park path"), rows)
    variants = {(r["variant"], r["street"]) for r in steps}
    assert variants == {("fastest", "Moore Park path"), ("athena", "George Street")}
    assert stats["trips_without_routes"] == 1 and stats["fastest_is_athena"] == 0
    assert {r["journey"].split("|")[4] for r in legs} == {"fastest", "athena"}


def test_same_itinerary_gets_both_labels(streets):
    rows = list(streets[0].values())
    _, _, steps, stats = build_trip_tables(_otp_snapshot("Moore Park path", "George Street"), rows)
    assert {r["variant"] for r in steps} == {"fastest", "athena"} and stats["fastest_is_athena"] == 1


# ---------------------------------------------------------------- export, LocalSource, check
@pytest.fixture()
def raw_dir(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copy(FIX / "osm_tiny.json", raw / "osm_2026-11-14.json")
    places = tmp_path / "places.json"
    places.write_text(json.dumps({"rows": [
        {"id": "unsw", "name": "UNSW Library", "lat": -33.9175, "lon": 151.2333},
        {"id": "townhall", "name": "Town Hall station", "lat": -33.8731, "lon": 151.2068}]}))
    return raw, places


def test_build_without_routes_keeps_serving_the_sample(raw_dir, tmp_path):
    raw, places = raw_dir
    m = build(raw=raw, out=tmp_path / "processed", places_file=places)
    assert m["routes"] == "sample" and m["tables"]["streets"] == 5
    assert m["sources"]["osm"]["file"] == "osm_2026-11-14.json" and len(m["sources"]["osm"]["sha256"]) == 64
    assert not (tmp_path / "processed" / "route_steps.parquet").exists()


def test_full_build_passes_check_and_is_served(raw_dir, tmp_path):
    raw, places = raw_dir
    (raw / "otp_trips_2026-11-14.json").write_text(json.dumps(_otp_snapshot("George Street", "Moore Park path")))
    out = tmp_path / "processed"
    m = build(raw=raw, out=out, places_file=places)
    assert m["routes"] == "otp"
    results = run_checks(out)
    assert all(ok for _, ok, _ in results), [r for r in results if not r[1]]
    src = LocalSource(data_dir=out)
    steps, ev = src.route_steps("unsw", "townhall", "22", "walk")
    assert src.processed and len(steps) == 2 and "Parquet" in ev["executed_by"]


def test_check_fails_when_a_route_street_is_missing(raw_dir, tmp_path):
    raw, places = raw_dir
    (raw / "otp_trips_2026-11-14.json").write_text(json.dumps(_otp_snapshot("George Street", "Nowhere Road")))
    out = tmp_path / "processed"
    build(raw=raw, out=out, places_file=places)
    failed = {name for name, ok, _ in run_checks(out) if not ok}
    assert "every route_steps street is in streets" in failed


def test_sample_data_passes_check():
    results = run_checks(ROOT / "data")
    assert read_table(ROOT / "data", "streets")
    assert all(ok for _, ok, _ in results), [r for r in results if not r[1]]


# ---------------------------------------------------------------- ingest
def test_overpass_query_and_raw_files_are_never_overwritten(tmp_path):
    q = overpass_query()
    assert "-33.93,151.17,-33.85,151.26" in q and "street_lamp" in q and "out geom" in q
    existing = tmp_path / "osm_2026-11-14.json"
    existing.write_text("{}")
    with pytest.raises(FileExistsError):
        fetch_osm(out=existing, session=object())
