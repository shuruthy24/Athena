"""Data lineage and repeatability: content hashes, the rebuild check, the raw-file fetcher, data quality, and the
provenance the app reports (Data practices; Completion: the UI never passes the sample off as real data)."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from athena.data.source import LocalSource
from pipeline import fetch_raw
from pipeline.check import quality, run_checks
from pipeline.lineage import file_sha256, rows_sha256
from pipeline.run import build
from pipeline.verify import verify
from tests.test_pipeline import _otp_snapshot

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture()
def built(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copy(FIX / "osm_tiny.json", raw / "osm_2026-11-14.json")
    (raw / "otp_trips_2026-11-14.json").write_text(json.dumps(_otp_snapshot("George Street", "Moore Park path")), encoding="utf-8")
    places = tmp_path / "places.json"
    places.write_text(json.dumps({"rows": [
        {"id": "unsw", "name": "UNSW Library", "lat": -33.9175, "lon": 151.2333},
        {"id": "townhall", "name": "Town Hall station", "lat": -33.8731, "lon": 151.2068}]}), encoding="utf-8")
    out = tmp_path / "processed"
    manifest = build(raw=raw, out=out, places_file=places)
    return raw, out, places, manifest


def test_content_hash_ignores_row_order_and_float_noise_but_not_values():
    a = [{"x": 1, "y": 0.1 + 0.2}, {"x": 2, "y": None}]
    assert rows_sha256(a) == rows_sha256(list(reversed(a))) == rows_sha256([{"y": 0.3, "x": 1}, {"x": 2, "y": None}])
    assert rows_sha256(a) != rows_sha256([{"x": 1, "y": 0.31}, {"x": 2, "y": None}])


def test_build_records_content_hashes_and_licence(built):
    _, out, _, m = built
    assert set(m["content_sha256"]) == {"streets", "places", "route_legs", "leg_streets", "route_steps"}
    assert "ODbL" in (out / "LICENCE.md").read_text(encoding="utf-8") and "CC BY 4.0" in (out / "LICENCE.md").read_text(encoding="utf-8")


def test_rebuild_from_the_same_raw_is_identical(built):
    raw, out, places, _ = built
    res = verify(processed=out, raw=raw, places_file=places)
    assert res["status"] == "identical", res
    assert all(c["ok"] for c in res["checks"])


def test_rebuild_detects_a_changed_table(built):
    raw, out, places, _ = built
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    m["content_sha256"]["streets"] = "0" * 64      # as if the committed streets differed from what raw produces
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    res = verify(processed=out, raw=raw, places_file=places)
    assert res["status"] == "differs"
    assert [c for c in res["checks"] if not c["ok"]][0]["check"].startswith("streets")


def test_rebuild_detects_a_changed_raw_snapshot(built):
    raw, out, places, _ = built
    (raw / "osm_2026-11-14.json").write_text((raw / "osm_2026-11-14.json").read_text(encoding="utf-8").replace("George", "Georg"))
    res = verify(processed=out, raw=raw, places_file=places)
    assert res["status"] == "differs"
    assert not [c for c in res["checks"] if "raw osm" in c["check"]][0]["ok"]


def test_rebuild_cannot_run_without_raw_or_hashes(built, tmp_path):
    raw, out, places, _ = built
    assert verify(processed=out, raw=tmp_path / "empty", places_file=places)["status"] == "cannot-run"
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    m.pop("content_sha256")
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert "content hashes" in verify(processed=out, raw=raw, places_file=places)["reason"]


def test_fetch_raw_accepts_matching_hash_and_rejects_a_tampered_file(tmp_path):
    good = tmp_path / "source.json"
    good.write_text('{"ok": true}', encoding="utf-8")
    manifest = tmp_path / "raw_manifest.json"
    manifest.write_text(json.dumps({"files": {
        "a.json": {"needed_by_make_data": True, "sha256": file_sha256(good), "url": "https://mirror/a.json"},
        "b.json": {"needed_by_make_data": True, "sha256": file_sha256(good), "url": "https://mirror/b.json"},
        "c.zip": {"needed_by_make_data": False, "sha256": None, "url": None}}}), encoding="utf-8")

    def fake_download(url, path):
        path.write_text('{"ok": true}' if url.endswith("a.json") else '{"ok": false}', encoding="utf-8")

    raw = tmp_path / "raw"
    status = dict(fetch_raw.fetch(raw=raw, manifest=manifest, download=fake_download))
    assert status["a.json"] == "downloaded" and (raw / "a.json").exists()
    assert status["b.json"].startswith("BAD-HASH") and not (raw / "b.json").exists()
    assert status["c.zip"] == "not needed by make data"


def test_fetch_raw_write_records_size_and_hash(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "osm_x.json").write_text("{}", encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"files": {"osm_x.json": {"source": "kept"}}}), encoding="utf-8")
    body = fetch_raw.write(raw=raw, manifest=manifest)
    assert body["files"]["osm_x.json"] == {"source": "kept", "bytes": 2, "sha256": file_sha256(raw / "osm_x.json")}


def test_committed_raw_manifest_names_the_snapshots_make_data_uses():
    body = json.loads((ROOT / "data" / "raw_manifest.json").read_text(encoding="utf-8"))
    needed = {n for n, e in body["files"].items() if e["needed_by_make_data"]}
    assert any(n.startswith("osm_") for n in needed) and any(n.startswith("otp_trips_") for n in needed)
    assert all(e.get("licence") for e in body["files"].values())


def test_quality_reports_missing_values_and_fails_duplicate_keys_and_out_of_area_points():
    tables = {"streets": [{"name": "A", "lit": None}, {"name": "A", "lit": "yes"}],
              "places": [{"id": "x", "name": "X", "lat": -33.9, "lon": 151.2},
                         {"id": "y", "name": "Y", "lat": 51.5, "lon": -0.1}]}
    q = quality(tables, Path("/nonexistent"), now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    assert q["tables"]["streets"]["missing_share"]["lit"] == 0.5
    assert any("streets: 1 duplicate keys" in f for f in q["failures"])
    assert any("places: 1 coordinates outside" in f for f in q["failures"])
    clean = quality({"places": [{"id": "x", "name": "X", "lat": -33.9, "lon": 151.2}]}, Path("/nonexistent"))
    assert clean["failures"] == []


def test_quality_fails_a_repeated_leg_streets_key():
    step = {"journey": "j1", "leg": 0, "step": 0, "street": "A", "distance_m": 10}
    q = quality({"leg_streets": [step, {**step, "street": "B"}]}, Path("/nonexistent"))
    assert any("leg_streets: 1 duplicate keys" in f for f in q["failures"])


def test_quality_passes_distinct_leg_streets_keys():
    step = {"journey": "j1", "leg": 0, "step": 0, "street": "A", "distance_m": 10}
    q = quality({"leg_streets": [step, {**step, "step": 1}]}, Path("/nonexistent"))
    assert q["failures"] == [] and q["tables"]["leg_streets"]["duplicate_keys"] == 0


def test_quality_warns_on_a_stale_snapshot(built):
    _, out, _, _ = built
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    m["sources"]["osm"]["fetched"] = "2025-01-01T00:00:00+00:00"
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    q = quality({}, out, now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    assert any("days old" in w for w in q["warnings"])


def test_check_passes_on_a_fresh_build_including_quality(built):
    _, out, _, _ = built
    results = run_checks(out)
    assert all(ok for _, ok, _ in results), [r for r in results if not r[1]]
    assert any(name.startswith("no duplicate keys") for name, _, _ in results)


def test_provenance_says_sample_or_names_the_snapshots(built):
    _, out, _, _ = built
    sample = LocalSource(data_dir=ROOT / "data").provenance()
    assert sample["mode"] == "sample" and "not real measurements" in sample["label"]
    assert "unsw|townhall" in sample["routable_pairs"]
    real = LocalSource(data_dir=out).provenance()
    assert real["mode"] == "processed" and "OpenStreetMap" in real["label"]
    assert real["sources"]["osm"]["sha256"] and real["content_sha256"]["streets"]
