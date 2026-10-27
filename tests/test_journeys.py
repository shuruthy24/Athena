"""Journey planner: option building, safety bands, ranking, dedupe, coverage, endpoints, live OTP shape."""
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from athena.data.otp import OTPSource
from athena.data.source import LocalSource
from athena.journeys import LABELS, SORTS, JourneyPlanner, band_label, dedupe, rank, score_streets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def plan(mode="transit", sort="balanced", band="22", o="unsw", d="townhall"):
    return JourneyPlanner(LocalSource()).plan(o, d, band, mode, sort, use_reports=False)


def decode(poly):
    """Reference decoder for Google encoded polylines (precision 5), mirroring web/plan.html."""
    i = lat = lon = 0
    out = []
    while i < len(poly):
        vals = []
        for _ in range(2):
            shift = res = 0
            while True:
                b = ord(poly[i]) - 63
                i += 1
                res |= (b & 31) << shift
                shift += 5
                if b < 32:
                    break
            vals.append(~(res >> 1) if res & 1 else res >> 1)
        lat += vals[0]
        lon += vals[1]
        out.append((lat / 1e5, lon / 1e5))
    return out


def test_band_labels_use_validated_thresholds_and_never_say_safe():
    assert band_label(0.10, 0)[0] == "low"
    assert band_label(0.33, 0)[0] == "medium"
    assert band_label(0.549, 0)[0] == "medium"
    assert band_label(0.55, 0)[0] == "high"
    assert band_label(0.20, 31)[0] == "unknown"     # too much missing lighting data to rank on risk
    assert band_label(None, 0)[0] == "unknown"
    assert not any("safe" in v.lower().split() for v in LABELS.values())


def test_unknown_lighting_lowers_confidence_not_risk():
    streets = {"A": {"name": "A", "lit": "yes", "risk_22": 0.2}, "B": {"name": "B", "lit": None, "risk_22": None}}
    s = score_streets([{"street": "A", "distance_m": 100}, {"street": "B", "distance_m": 100}], streets, "22", {})
    assert s["risk"] == 0.2                          # B is excluded from risk, not counted as dark
    assert s["unknown_share_pct"] == 50.0 and s["known_m"] == 100


@pytest.mark.parametrize("mode", ["walk", "bike", "transit"])
def test_every_mode_returns_comparable_options_with_real_geometry(mode):
    r = plan(mode)
    assert r["coverage"] and len(r["journeys"]) >= 2
    assert r["freshness"]["live"] is False and "precomputed" in r["freshness"]["note"].lower()
    for j in r["journeys"]:
        assert j["safety"]["label"] in LABELS.values()
        assert j["explanation"] and j["legs"]
        for leg in j["legs"]:
            pts = decode(leg["polyline"])
            assert len(pts) >= 2
            # Decoded geometry starts and ends at the leg's own stops (within ~150 m).
            assert abs(pts[0][0] - leg["from"]["lat"]) < 0.0015 and abs(pts[-1][1] - leg["to"]["lon"]) < 0.0015
    assert sum(j["is_fastest"] for j in r["journeys"]) == 1
    assert sum(j["is_lowest_risk"] for j in r["journeys"]) == 1


def test_transit_options_match_verified_output():
    r = plan("transit", "fastest")
    fast, alt = r["journeys"]
    assert (fast["total_min"], fast["transfers"], fast["safety"]["risk"]) == (36.5, 1, 0.451)
    assert (alt["total_min"], alt["transfers"], alt["safety"]["risk"]) == (39.4, 0, 0.272)
    assert alt["is_lowest_risk"] and "Tram L3" in alt["transit_lines"]
    assert any("lower risk indicators" in x for x in alt["explanation"])


@pytest.mark.parametrize("sort", SORTS)
def test_ranking_orders_without_inventing_routes(sort):
    base = {j["id"] for j in plan("transit")["journeys"]}
    r = plan("transit", sort)
    assert {j["id"] for j in r["journeys"]} == base
    js = r["journeys"]
    if sort == "fastest":
        assert js[0]["is_fastest"]
    if sort == "lower_risk":
        assert js[0]["is_lowest_risk"]
    if sort == "fewest_transfers":
        assert js[0]["transfers"] == min(j["transfers"] for j in js)
    if sort == "least_walking":
        assert js[0]["walk_m"] == min(j["walk_m"] for j in js)


def test_rank_puts_unscored_routes_last():
    a = {"id": "a", "total_min": 10, "transfers": 0, "walk_m": 1, "safety": {"risk": None}}
    b = {"id": "b", "total_min": 20, "transfers": 0, "walk_m": 1, "safety": {"risk": 0.5}}
    assert [j["id"] for j in rank([a, b], "lower_risk")] == ["b", "a"]
    assert [j["id"] for j in rank([a, b], "balanced")] == ["b", "a"]


def test_dedupe_merges_identical_itineraries_and_records_both_preferences():
    leg = {"mode": "WALK", "line": None, "from": {"name": "X"}, "to": {"name": "Y"}, "start": "t", "polyline": "p"}
    a = {"variant": "fastest", "itinerary": 0, "legs": [leg]}
    b = {"variant": "athena", "itinerary": 0, "legs": [dict(leg)]}
    c = {"variant": "athena", "itinerary": 1, "legs": [{**leg, "polyline": "q"}]}   # different path
    out = dedupe([b, a, c])
    assert len(out) == 2 and out[0]["from_preferences"] == ["fastest", "athena"]


def test_uncovered_trip_reports_no_coverage():
    r = plan(o="unsw", d="quay")
    assert r["coverage"] is False and r["journeys"] == []


def test_invalid_trip_is_rejected():
    with pytest.raises(ValueError):
        JourneyPlanner(LocalSource()).plan("unsw", "townhall'; DROP TABLE x;--", "22", "walk")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.delenv("OTP_URL", raising=False)
    import athena.server as server
    importlib.reload(server)
    server.athena.cache_clear()
    return TestClient(server.app), server


def test_journey_endpoints(client):
    c, _ = client
    r = c.get("/api/journeys", params={"origin": "unsw", "destination": "townhall", "band": "01", "mode": "walk"})
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"] and body["origin"]["name"] == "UNSW Library" and len(body["journeys"]) == 2
    none = c.get("/api/journeys", params={"origin": "quay", "destination": "unsw"}).json()
    assert none["coverage"] is False and "UNSW Library and Town Hall" in none["message"]
    assert c.get("/api/journeys", params={"origin": "unsw", "destination": "townhall", "sort": "nope"}).status_code == 422
    assert c.get("/api/journeys", params={"origin": "mars", "destination": "townhall"}).status_code == 422
    assert len(c.get("/api/places").json()["places"]) == 7


def test_street_safety_overlay(client):
    c, _ = client
    g = c.get("/api/street-safety", params={"band": "01"}).json()
    assert g["type"] == "FeatureCollection" and g["features"]
    names = [f["properties"]["name"] for f in g["features"]]
    assert len(names) == len(set(names))
    assert "not an incident count" in g["note"]
    assert c.get("/api/street-safety", params={"band": "99"}).status_code == 422


def test_safety_points_absent_then_present(client, tmp_path, monkeypatch):
    c, server = client
    monkeypatch.setattr(server, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    assert c.get("/api/safety-points").json()["available"] is False
    (tmp_path / "data" / "safety_points.json").write_text(json.dumps(
        {"rows": [{"id": "osm:node/1", "name": "P", "category": "police", "lat": -33.9, "lon": 151.2}],
         "attribution": "© OpenStreetMap contributors, ODbL", "fetched": "2026-10-28T00:00:00+00:00"}))
    body = c.get("/api/safety-points").json()
    assert body["available"] and body["points"][0]["category"] == "police" and "ODbL" in body["attribution"]


def test_planner_page_served(client):
    c, _ = client
    html = c.get("/").text
    assert "Athena Journey Planner" in html and "/web/vendor/maplibre-gl.js" in html and "000" in html
    assert c.get("/web/vendor/maplibre-gl.js").status_code == 200


def test_fetch_safety_points_parses_overpass():
    from fetch_safety_points import overpass_query, to_rows
    rows = to_rows([
        {"type": "node", "id": 1, "lat": -33.89, "lon": 151.20, "tags": {"amenity": "police", "name": "Redfern"}},
        {"type": "way", "id": 2, "center": {"lat": -33.88, "lon": 151.22}, "tags": {"amenity": "hospital", "name": "H", "emergency": "yes"}},
        {"type": "way", "id": 3, "center": {"lat": -33.88, "lon": 151.22}, "tags": {"amenity": "hospital", "name": "H"}},
        {"type": "node", "id": 4, "lat": -33.90, "lon": 151.23, "tags": {"emergency": "phone"}},
        {"type": "node", "id": 5, "lat": -33.88, "lon": 151.20, "tags": {"railway": "station", "name": "Old", "disused": "yes"}},
        {"type": "node", "id": 6, "tags": {"amenity": "police"}},   # no coordinates
    ])
    assert [r["category"] for r in rows] == ["police", "hospital", "phone"]
    assert rows[1]["emergency_department"] is True
    q = overpass_query()
    assert '"amenity"="police"' in q and "-33.93,151.17,-33.85,151.26" in q and "out center" in q


class FakeResp:
    def __init__(self, body, code=200):
        self.body, self.status_code, self.text = body, code, json.dumps(body)

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, body):
        self.body, self.calls = body, []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return FakeResp(self.body)


def test_live_otp_rows_are_scored_like_precomputed():
    node = {"start": "s", "end": "e", "duration": 600, "numberOfTransfers": 0, "walkDistance": 800, "legs": [{
        "mode": "WALK", "distance": 800, "duration": 600, "headsign": None,
        "from": {"name": "UNSW Library", "lat": -33.9175, "lon": 151.2333},
        "to": {"name": "Town Hall station", "lat": -33.8731, "lon": 151.2068},
        "legGeometry": {"points": "_p~iF~ps|U_ulLnnqC"}, "route": None,
        "start": {"scheduledTime": "s"}, "end": {"scheduledTime": "e"},
        "steps": [{"streetName": "George Street", "distance": 800}]}]}
    session = FakeSession({"data": {"planConnection": {"edges": [{"node": node}]}}})
    places = {p["id"]: p for p in json.loads((ROOT / "data" / "places.json").read_text())["rows"]}
    otp = OTPSource("http://otp:8080", LocalSource(), places, session=session)
    r = JourneyPlanner(otp).plan("unsw", "townhall", "22", "walk", use_reports=False)
    assert r["freshness"]["live"] is True and len(r["journeys"]) == 1
    assert r["journeys"][0]["safety"]["risk"] is not None
    assert session.calls[0][0] == "http://otp:8080/otp/gtfs/v1"
    assert session.calls[0][1]["variables"]["modes"] == {"direct": ["WALK"]}


def test_journeys_over_live_zetaris(monkeypatch, mock_server):
    from conftest import API_KEY, RUNTIME_TOKEN
    for k, v in dict(SAMPLE_MODE="false", ZETARIS_REST_URL=f"{mock_server}/api/v1.0", ZETARIS_BASE_URL=mock_server,
                     ZETARIS_ORG_ID="1", ZETARIS_API_KEY=API_KEY, ZETARIS_USERNAME="tester@example.com",
                     ZETARIS_PASSWORD="Correct1!", METERLESS_BASE_URL=f"{mock_server}/v1",
                     METERLESS_RUNTIME_TOKEN=RUNTIME_TOKEN, OTP_URL="").items():
        monkeypatch.setenv(k, v)
    import athena.server as server
    importlib.reload(server)
    server.athena.cache_clear()
    r = TestClient(server.app).get("/api/journeys", params={"origin": "unsw", "destination": "townhall",
                                                            "band": "22", "mode": "transit", "sort": "fastest"}).json()
    assert r["coverage"] and "Zetaris" in r["freshness"]["note"]
    assert all("Zetaris" in e["executed_by"] for e in r["evidence"])
    sample = plan("transit", "fastest")
    assert [(j["total_min"], j["safety"]["risk"]) for j in r["journeys"]] == \
           [(j["total_min"], j["safety"]["risk"]) for j in sample["journeys"]]
