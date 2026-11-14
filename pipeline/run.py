"""`make data`: rebuild data/processed/ from the newest raw snapshots, with no manual steps.

  data/raw/osm_<date>.json        -> streets.parquet       (pipeline.features)
  data/raw/otp_trips_<date>.json  -> route_legs / leg_streets / route_steps .parquet  (pipeline.trips)
  data/places.json                -> places.parquet        (configuration, not a source snapshot)
  manifest.json                   which raw files (with sha256) built which tables, and the row counts

Storage format on purpose (week 2): Parquet for tables queried on every request. Without an OSM snapshot
there is nothing to build: run `python3 -m pipeline.ingest osm` first. Without an OTP snapshot the streets
are still rebuilt and the app keeps using the hand-made sample routes in data/*.json, and says so.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .features import build_streets
from .trips import build_trip_tables

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"


def latest(prefix: str, raw: Path = RAW) -> Path | None:
    files = sorted(raw.glob(f"{prefix}_*.json"))
    return files[-1] if files else None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_parquet(rows: list[dict], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def build(raw: Path = RAW, out: Path = PROCESSED, places_file: Path = ROOT / "data/places.json") -> dict:
    osm = latest("osm", raw)
    if osm is None:
        raise FileNotFoundError("no data/raw/osm_<date>.json snapshot: run `python3 -m pipeline.ingest osm`")
    snapshot = json.loads(osm.read_text())
    streets, street_stats = build_streets(snapshot)
    write_parquet(streets, out / "streets.parquet")
    places = json.loads(places_file.read_text())["rows"]
    write_parquet(places, out / "places.parquet")
    manifest = {"built_at": datetime.now(timezone.utc).isoformat(),
                "sources": {"osm": {"file": osm.name, "sha256": sha256(osm), "fetched": snapshot.get("fetched"),
                                    "licence": snapshot.get("licence"), "attribution": snapshot.get("attribution")}},
                "tables": {"streets": len(streets), "places": len(places)}, "stats": {"streets": street_stats},
                "routes": "sample"}
    otp = latest("otp_trips", raw)
    if otp is not None:
        legs, leg_streets, steps, trip_stats = build_trip_tables(json.loads(otp.read_text()), streets)
        write_parquet(legs, out / "route_legs.parquet")
        write_parquet(leg_streets, out / "leg_streets.parquet")
        write_parquet(steps, out / "route_steps.parquet")
        manifest["sources"]["otp"] = {"file": otp.name, "sha256": sha256(otp)}
        manifest["tables"].update(route_legs=len(legs), leg_streets=len(leg_streets), route_steps=len(steps))
        manifest["stats"]["trips"] = trip_stats
        manifest["routes"] = "otp"
    else:
        for name in ("route_legs", "leg_streets", "route_steps"):  # never mix new streets with stale routes
            (out / f"{name}.parquet").unlink(missing_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main() -> int:
    m = build()
    print(json.dumps({"routes": m["routes"], "tables": m["tables"], "stats": m["stats"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
