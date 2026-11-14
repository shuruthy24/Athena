"""Precompute trips with OpenTripPlanner once, store the raw answers, and never call OTP live in the app.

  OTP_URL=http://localhost:8080 python3 -m pipeline.precompute_trips
    -> data/raw/otp_trips_<YYYY-MM-DD>.json  (every place pair x 3 bands x 3 modes, up to 4 itineraries)

Setting up OTP (a human runs this once, on a machine with about 10 GB of RAM and Java 21):
  1. Put the TfNSW GTFS zip (pipeline/ingest.py gtfs) and an OSM extract for Sydney (.osm.pbf from a
     Geofabrik or BBBike extract, ODbL) in one folder.
  2. java -Xmx8G -jar otp-shaded-2.x.jar --build --save <folder>   then   --load --serve <folder>
  3. Run this script with OTP_URL pointing at it. Times are scheduled timetable times, not live.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from athena.data.otp import OTPError, OTPSource

from .ingest import snapshot_path

ROOT = Path(__file__).resolve().parents[1]
BANDS = ("18", "22", "01")
MODES = ("walk", "bike", "transit")


class _NoStreets:
    def streets(self):
        return [], {}


def precompute(otp_url: str, places: dict[str, dict], out: Path | None = None, session=None) -> Path:
    out = out or snapshot_path("otp_trips", "json")
    if out.exists():
        raise FileExistsError(f"{out.name} already exists; raw snapshots are never overwritten")
    otp = OTPSource(otp_url, _NoStreets(), places, session=session)
    trips, failures = [], []
    for (o, d), band, mode in itertools.product(itertools.permutations(sorted(places), 2), BANDS, MODES):
        try:
            legs, steps = otp._plan(o, d, band, mode)
        except OTPError as exc:
            failures.append({"origin": o, "destination": d, "band": band, "mode": mode, "error": str(exc)[:160]})
            legs, steps = [], []
        trips.append({"origin": o, "destination": d, "band": band, "mode": mode, "legs": legs, "steps": steps})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": "OpenTripPlanner precompute", "otp_url": otp_url,
                               "fetched": datetime.now(timezone.utc).isoformat(),
                               "places": sorted(places), "trips": trips, "failures": failures}) + "\n")
    return out


def main() -> int:
    places = {p["id"]: p for p in json.loads((ROOT / "data/places.json").read_text())["rows"]}
    path = precompute(os.environ["OTP_URL"], places)
    body = json.loads(path.read_text())
    routed = sum(1 for t in body["trips"] if t["legs"])
    print(f"wrote {path.relative_to(ROOT)}: {routed}/{len(body['trips'])} trips routed, "
          f"{len(body['failures'])} failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
