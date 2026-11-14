"""Fetch raw source snapshots into data/raw/. Raw files are written once and never edited (AGENTS.md section 2).

  python3 -m pipeline.ingest osm          # OpenStreetMap via Overpass -> data/raw/osm_<YYYY-MM-DD>.json
  python3 -m pipeline.ingest gtfs         # prints how to download TfNSW GTFS (login required, done by a human)

Licences: OpenStreetMap data is ODbL (attribution "© OpenStreetMap contributors"). TfNSW Timetables
Complete GTFS is CC BY 4.0. City of Sydney light poles are NOT ingested until a human confirms the licence;
OSM highway=street_lamp nodes are used instead. BOCSAR crime counts stay excluded (too coarse, and they
would steer routes away from poorer and more diverse areas).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
BBOX = (-33.93, 151.17, -33.85, 151.26)  # south, west, north, east: UNSW, CBD and inner west
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"

# Named streets and paths people walk or ride on; motorways and construction are excluded.
STREET_HIGHWAYS = ("primary|secondary|tertiary|unclassified|residential|living_street|service|pedestrian|"
                   "footway|path|cycleway|steps|track|primary_link|secondary_link|tertiary_link")
# Places likely to be open and staffed at night: the "activity" signal for each time band.
VENUE_AMENITIES = "bar|pub|restaurant|fast_food|cafe|nightclub|cinema|theatre|fuel|pharmacy|police|hospital"


def overpass_query(bbox=BBOX) -> str:
    b = ",".join(str(v) for v in bbox)
    return (
        "[out:json][timeout:180];\n"
        f'way["highway"~"^({STREET_HIGHWAYS})$"]["name"]({b})->.streets;\n'
        f'node["highway"="street_lamp"]({b})->.lamps;\n'
        f'(nw["amenity"~"^({VENUE_AMENITIES})$"]({b}); nw["shop"="convenience"]({b});)->.venues;\n'
        ".streets out geom tags;\n.lamps out skel;\n.venues out center tags;"
    )


def snapshot_path(source: str, ext: str, day: str | None = None) -> Path:
    day = day or datetime.now(timezone.utc).date().isoformat()
    return RAW / f"{source}_{day}.{ext}"


def fetch_osm(out: Path | None = None, session=None) -> Path:
    import requests
    out = out or snapshot_path("osm", "json")
    if out.exists():
        raise FileExistsError(f"{out.name} already exists; raw snapshots are never overwritten")
    query = overpass_query()
    http = session or requests.Session()
    r = http.post(OVERPASS_URL, data={"data": query}, timeout=240,
                  headers={"User-Agent": "athena-comp3110-pipeline"})
    r.raise_for_status()
    body = r.json()
    snapshot = {"source": "OpenStreetMap via Overpass API", "url": OVERPASS_URL, "licence": "ODbL-1.0",
                "attribution": ATTRIBUTION, "fetched": datetime.now(timezone.utc).isoformat(), "bbox": list(BBOX),
                "query": query, "elements": body.get("elements", [])}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False) + "\n")
    return out


GTFS_HELP = """TfNSW Timetables Complete GTFS needs a (free) TfNSW Open Data account, so a human downloads it:
  1. Sign in at https://opendata.transport.nsw.gov.au/ and open "Timetables Complete GTFS".
  2. Download the zip and save it unchanged as data/raw/gtfs_<YYYY-MM-DD>.zip (the download date).
  3. Record the licence (CC BY 4.0) and date in data_sources.md, then run pipeline/precompute_trips.py
     against an OpenTripPlanner built from that GTFS plus the OSM extract (see its docstring)."""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["osm", "gtfs"])
    args = ap.parse_args(argv)
    if args.source == "gtfs":
        print(GTFS_HELP)
        return 0
    path = fetch_osm()
    n = len(json.loads(path.read_text())["elements"])
    print(f"wrote {path.relative_to(ROOT)} ({n} elements). Track it with: dvc add data/raw && git add data/raw.dvc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
