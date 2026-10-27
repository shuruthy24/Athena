"""Fetch help locations near the corridor from OpenStreetMap (Overpass API) for the journey planner map.

Writes data/safety_points.json, which GET /api/safety-points serves. Run on a machine with internet access:

    python3 scripts/fetch_safety_points.py

Categories: police stations (amenity=police), hospitals (amenity=hospital) and railway stations
(railway=station). Emergency phones are left out: OpenStreetMap lists hundreds of them around the corridor
(lift and bus-stop intercoms among them), which buried the places people look for. OpenStreetMap does not
record staffing hours, so stations are labelled as stations, not as staffed. Data © OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "safety_points.json"
BBOX = (-33.93, 151.17, -33.85, 151.26)            # south, west, north, east
ENDPOINTS = ("https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter")
ATTRIBUTION = "© OpenStreetMap contributors, ODbL"


def overpass_query(bbox=BBOX) -> str:
    b = ",".join(str(x) for x in bbox)
    return (f"[out:json][timeout:60];\n(\n"
            f'  nwr["amenity"="police"]({b});\n'
            f'  nwr["amenity"="hospital"]({b});\n'
            f'  nwr["railway"="station"]({b});\n'
            f");\nout center tags;")


def category(tags: dict) -> str | None:
    if tags.get("amenity") == "police":
        return "police"
    if tags.get("amenity") == "hospital":
        return "hospital"
    if tags.get("railway") == "station":
        # Skip disused, proposed and tourist/heritage lines; keep train and metro stations.
        if tags.get("disused") == "yes" or tags.get("usage") == "tourism" or tags.get("station") == "light_rail":
            return None
        return "station"
    return None


def to_rows(elements: list[dict]) -> list[dict]:
    """Overpass elements -> one row per place, deduplicated by category and name."""
    rows, seen = [], set()
    for el in elements:
        tags = el.get("tags") or {}
        cat = category(tags)
        if not cat:
            continue
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        name = tags.get("name") or tags.get("official_name")
        # Stations and hospitals are mapped as several objects (building, platform area, node);
        # keep one per name. Unnamed places are kept individually.
        key = (cat, name) if name else (cat, el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        row = {"id": f"osm:{el.get('type')}/{el.get('id')}", "name": name, "category": cat,
               "lat": round(float(lat), 6), "lon": round(float(lon), 6)}
        phone = tags.get("phone") or tags.get("contact:phone")
        if phone:
            row["phone"] = phone
        if tags.get("opening_hours"):
            row["opening_hours"] = tags["opening_hours"]
        if cat == "hospital" and tags.get("emergency"):
            row["emergency_department"] = tags["emergency"] == "yes"
        rows.append(row)
    order = {"police": 0, "hospital": 1, "station": 2}
    return sorted(rows, key=lambda r: (order[r["category"]], r["name"] or "~", r["id"]))


def fetch(endpoints=ENDPOINTS, timeout=90) -> list[dict]:
    q = overpass_query()
    last = None
    for url in endpoints:
        for attempt in range(2):
            try:
                r = requests.post(url, data={"data": q}, timeout=timeout,
                                  headers={"User-Agent": "athena-journey-planner/1.0 (hackathon project)"})
                if r.status_code == 200:
                    return r.json().get("elements", [])
                last = f"{url}: HTTP {r.status_code}"
            except (requests.RequestException, ValueError) as exc:
                last = f"{url}: {exc.__class__.__name__}"
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"Could not reach any Overpass endpoint (last error: {last})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-file", type=Path, help="parse a saved Overpass JSON response instead of fetching")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    elements = json.loads(args.from_file.read_text())["elements"] if args.from_file else fetch()
    rows = to_rows(elements)
    if not rows:
        print("Overpass returned no help locations; not overwriting the existing file.", file=sys.stderr)
        return 1
    body = {"rows": rows, "attribution": ATTRIBUTION, "bbox": list(BBOX),
            "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "query": overpass_query()}
    args.out.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    counts = {c: sum(1 for r in rows if r["category"] == c) for c in ("police", "hospital", "station")}
    print(f"wrote {len(rows)} help locations to {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}: "
          + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
