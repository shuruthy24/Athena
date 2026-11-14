# Data sources

Every dataset Athena uses, with its licence and how it was cleaned (AGENTS.md section 2). Raw snapshots
live in `data/raw/` (versioned with DVC, never edited); derived tables in `data/processed/` are rebuilt by
`make data`, which also writes `data/processed/manifest.json` with each raw file's name, sha256, fetch
time and row counts. Numbers below come from files in this repo; "after `make data`" means the manifest
holds them once the snapshot exists.

## In use

### OpenStreetMap streets, street lamps and night venues

| | |
| --- | --- |
| Source | OpenStreetMap via the Overpass API (`pipeline/ingest.py`, query in the snapshot's `query` field) |
| URL | https://overpass-api.de/api/interpreter |
| Licence | ODbL 1.0, attribution "© OpenStreetMap contributors" (shown on the map pages) |
| Area | bbox -33.93, 151.17, -33.85, 151.26 (UNSW, CBD, inner west) |
| Retrieved | written into `data/raw/osm_<date>.json` and the manifest by `make ingest` |
| Rows kept | streets, lamps, venues and unknown-hours counts in `manifest.json` → `stats.streets` after `make data` |

Cleaning (`pipeline/features.py`, Tech 4 helpers in `pipeline/clean.py`):
- Unnamed ways dropped (`drop_missing`); names NFC-normalised and stripped of control characters
  (`fix_encoding`); duplicate OSM way IDs dropped; ways that share a name after whitespace normalisation
  are combined into one street, weighted by length.
- `lit` is read as yes/no only when at least half the street's length is tagged; `lit=limited` and
  missing tags stay unknown and are never scored as unlit.
- Opening hours that the small parser does not understand stay unknown, and those venues are not counted
  as open.
- PII: OSM street, lamp and venue data describes places, not people. Free-text tags are not used.

### TfNSW Timetables Complete GTFS (for precomputed trips)

| | |
| --- | --- |
| Source | Transport for NSW Open Data Hub, "Timetables Complete GTFS" |
| URL | https://opendata.transport.nsw.gov.au/ (free account needed) |
| Licence | CC BY 4.0 |
| Retrieved | by a team member, saved unchanged as `data/raw/gtfs_<date>.zip` (`make ingest` prints the steps) |
| Rows kept | trips routed and failures in `data/raw/otp_trips_<date>.json` and the manifest's `stats.trips` |

Trips are computed once with OpenTripPlanner (`pipeline/precompute_trips.py`) for every place pair, three
time bands (6 pm, 10 pm, 1 am) and three modes. The app never calls OTP live. Times are timetable times.

### OpenStreetMap help locations

| | |
| --- | --- |
| Source | OpenStreetMap via Overpass (`scripts/fetch_safety_points.py`) |
| Licence | ODbL 1.0 |
| Retrieved | 27 Oct 2026 (`fetched` field in `data/safety_points.json`) |
| Rows kept | 265 points (police, hospitals, stations, emergency phones); `/api/safety-points` serves police, hospitals and stations |

### Hand-made sample (hackathon)

`data/streets.json` (12), `data/route_steps.json` (186), `data/route_legs.json` (108),
`data/leg_streets.json` (204), `data/places.json` (7) and `data/reports.json` (14) were made by the team
during the hackathon from a pre-existing route map (`web/routes.json`, `web/streets.geojson`). The street
risk values were assigned by hand. They are test fixtures and the fallback the app serves until `make
data` has built both streets and trips; they are not evidence about real streets. The 14 reports are
synthetic.

### Meterless Scout Intent adversarial prompts

10 of the 18 prompts in `evals/guard/injection.jsonl` come from the Meterless v1 adversarial set
(Apache-2.0, see `NOTICE`); the other 8 were written by the team.

## Excluded

| Dataset | Why |
| --- | --- |
| City of Sydney street light poles (Data.NSW) | Listed as "License Not Specified". Not ingested until a human confirms the licence on the City's portal; OSM `highway=street_lamp` nodes are used instead. |
| BOCSAR crime counts | Suburb-level counts are too coarse for street routing and would steer routes away from poorer and more diverse areas. |
| Free to Be (Plan International, Monash XYX Lab) | No download or licence stated. Used only as a reference for report categories until permission is granted. |

## Versioning with DVC

```bash
pip install dvc
dvc init
make ingest
make dvc-track
dvc remote add -d storage <your remote, e.g. a UNSW OneDrive folder or a local path outside the repo>
dvc push
```

Commit `data/raw.dvc`, never the raw files. To restore on another machine: `dvc pull`, then `make data`.
