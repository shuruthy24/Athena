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
| Retrieved | `data/raw/osm_2026-11-15T0130.json`, fetched 2026-11-15 01:30 UTC (the snapshot in use). An earlier snapshot the same day, `osm_2026-11-15.json`, has named streets only and is kept for history. |
| Rows kept | 11,196 named ways → 2,926 streets; 25,656 unnamed ways (footways, paths, cycleways, service lanes, steps); 1,840 street lamps; 3,666 venues, 2,868 with opening hours the parser cannot read (`manifest.json` → `stats`) |

Cleaning (`pipeline/features.py`, Tech 4 helpers in `pipeline/clean.py`):
- Names NFC-normalised and stripped of control characters (`fix_encoding`); duplicate OSM way IDs dropped;
  named ways that share a name after case and whitespace normalisation are combined into one street,
  weighted by length, keeping the most common spelling ("Gadigal Avenue" / "Gadigal avenue").
- Unnamed ways are not streets: they are kept as segments, scored on their own and labelled by type
  ("unnamed footway"), so routes along footpaths have data without being named in explanations.
- `lit` is read as yes/no only when at least half the length is tagged; `lit=limited` and missing tags
  stay unknown and are never scored as unlit.
- Lamp inference: a street with no lit tag but at least 2 mapped lamps and at least 1 per 100 m gets
  `lit=yes` (`lit_source=lamps`). No lamps never means unlit. Checked against OSM's own tags before use
  (`evals/results/lit_inference.json`): 43/43 agree, but only 11 tagged streets are unlit, so the check is
  weak. 79 streets gained a value; 2,445 of 2,926 streets and 22,173 of 25,656 unnamed ways still have
  unknown lighting.
- Unknown lighting is scored halfway between lit and unlit (+0.15 vs +0.30; team decision, 15 Nov 2026),
  and is still reported as unknown in every explanation.
- Opening hours that the small parser does not understand stay unknown, and those venues are not counted
  as open.
- PII: OSM street, lamp and venue data describes places, not people. Free-text tags are not used.

### TfNSW Timetables Complete GTFS (for precomputed trips)

| | |
| --- | --- |
| Source | Transport for NSW Open Data Hub, "Timetables Complete GTFS" |
| URL | https://opendata.transport.nsw.gov.au/ (free account needed) |
| Licence | CC BY 4.0 |
| Retrieved | 15 Nov 2026 by a team member through the Open Data Hub API (personal API token, kept in the git-ignored `.env`), saved unchanged as `data/raw/gtfs_2026-11-15.zip` (278 MB, all of NSW) |
| Rows kept | used only to build the OpenTripPlanner graph; nothing from it is stored in `data/processed/` except the planned trips |

### Precomputed trips (OpenTripPlanner)

| | |
| --- | --- |
| Tool | OpenTripPlanner 2.7.0 (stock release jar from Maven Central), Java 21, run locally once |
| Inputs | the GTFS above plus a Geofabrik Australia OSM extract (ODbL) clipped with osmium to 151.15, -33.95, 151.28, -33.83 |
| Output | `data/raw/otp_trips_2026-11-15T0638.json`, planned 2026-11-15 06:38 UTC by `pipeline/precompute_trips.py` (in use). The earlier `otp_trips_2026-11-15.json` asked for walk and bike trips without "direct only" and got tram and bus itineraries back; it is kept for history. |
| Rows kept | 378 trips (42 place pairs × 3 bands × 3 modes); 377 routed, 1 with no route, 0 errors |

The app never calls OTP live; times are timetable times. Public transport trips ask OTP once (up to 4 itineraries).
Walk and bike trips ask for direct trips only, twice: fastest, and with OTP's own safety preference (walk
`safetyFactor` 1.0; cycling triangle safety 0.7, flatness 0.1, time 0.2), keeping distinct routes. Stock OTP does
not read lighting, and its preference changed no walking route: walk trips have one route each.
`pipeline/trips.py` picks, per trip, the fastest itinerary and the one with the lowest route risk (the same rule as
the app: time-weighted, time on board at 0.10, waits at stops scored from the nearest street); in 309 of 378 they
are the same itinerary. Walk and bike legs are matched to OSM ways by location, not by OTP's step names
(`pipeline/match.py`: a point every 10 m snapped to the nearest way within 15 m, staying on the current way while it
is within range): 57.2% of walk/bike distance lands on named streets, 42.7% on unnamed ways, 0.1% on nothing.
Coverage per data version: `evals/results/data_coverage.json`.

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

`data/raw/` (4 files, 324 MB: the GTFS zip, both OSM snapshots, the OTP precompute) is tracked by DVC. Git holds
only the pointer `data/raw.dvc` (directory hash, size, file count); the data itself is git-ignored. The remote
is a local directory outside the repository, configured with `--local` so its path stays out of git; a pull from
it was checked with the working copy and the DVC cache moved away.

```bash
dvc remote add --local -d localstore <a folder outside the repo>   # once per machine
dvc pull                                                           # restore data/raw/
make data                                                          # rebuild data/processed/
```

The remote is on one team member's laptop, so other machines need their own remote (or a shared one, added the
same way) before `dvc pull` works there.
