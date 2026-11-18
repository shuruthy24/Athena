# Data sources

Every dataset Athena uses, with its licence and how it was cleaned. Raw snapshots
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

Cleaning (`pipeline/features.py`, cleaning helpers in `pipeline/clean.py`):
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

### Hand-made sample

`data/streets.json` (12), `data/route_steps.json` (186), `data/route_legs.json` (108),
`data/leg_streets.json` (204), `data/places.json` (7) and `data/reports.json` (14) were made by the team
from a pre-existing prototype route map (`web/routes.json`, `web/streets.geojson`). The street
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

## Provenance and a repeatable rebuild

Every derived number can be traced to raw files with known hashes, and anyone can rebuild them:

```
data/raw_manifest.json  ->  python -m pipeline.fetch_raw   download each raw snapshot from its mirror, check sha256
data/raw/               ->  make data (pipeline/run.py)    clean, match, score; write data/processed/*.parquet
data/processed/manifest.json                               raw file names + sha256, row counts, content hash per table
make data-verify (pipeline/verify.py)                      rebuild into a temp folder, compare every content hash
python -m pipeline.check --write-quality                   schema, keys, area, references -> data_quality.json
```

- **Raw snapshots** are never edited (`pipeline/ingest.py` refuses to overwrite). `data/raw_manifest.json` lists
  each one with its source, licence, retrieval time, size, sha256 and a mirror URL. `fetch_raw` rejects a file whose
  hash differs.
- **Derived tables** (`data/processed/`) are committed, so the submitted app serves the real data without any
  download. Each table's content hash (sha256 of its rows as canonical JSON, independent of Parquet metadata and row
  order) is in the manifest; `make data-verify` proves the committed tables are exactly what the code produces from
  the raw snapshots.
- **Results** name their inputs: `data_coverage.json` and `route_baselines.json` carry the manifest's sources and
  hashes; every result file has `generated_at` and `git_sha`.
- **DVC** still versions `data/raw/` (`data/raw.dvc`). Its remote was a folder on one laptop, so the mirror URLs in
  `raw_manifest.json` are the route for everyone else; a shared DVC remote (`dvc remote add -d ...`) works too.
- **Environment:** `requirements-lock.txt` pins every package behind the committed results.

## Evaluation datasets

Roles: *tuning* data may be used to change the system; *held-out* data is frozen by sha256 in
`evals/checksums.txt`, is never used for tuning, and `make check` fails if it changes.

| Dataset | Origin | Licence / consent | Role | Size |
| --- | --- | --- | --- | --- |
| `evals/guard/injection.jsonl` | 10 rows from the Meterless v1 adversarial set, 16 written by the team | Apache-2.0 (`NOTICE`) / team | tuning (guard dev) | 26 |
| `evals/guard/heldout_probes_v1.jsonl` | synthetic, written by Claude on 18 Nov 2026 after reading the guard's patterns; labels by construction | project | held-out, frozen | 50 |
| `evals/guard/redteam.csv` | attempts by people outside the team | consent, pseudonymous `author_group` | held-out (to collect) | – |
| `evals/privacy/pii_cases_v1.jsonl` | synthetic, fictional people and numbers | project | regression set (it motivated the 18 Nov PII fix) | 29 |
| `data/labelling/reports_to_label.csv` → `data/reports/{train,dev,test}.jsonl` | team-written (`seed`) and collected (`collected`) street reports | consent text in `data/labelling/guide.md` | train / dev / **test frozen, touched once**; group split by street and writer | target 150+ |
| `evals/traces/2026-11-14-live-scx-gemma-4-31B-it/` | live explainer outputs on the sample routes | LiteLLM gateway | error analysis, assertions | 36 |
| `evals/labels/{dev,heldout}.json` | human PASS/FAIL, split by `assignment.json` (seeded, written before labelling) | team | judge dev / held-out | 36 |
| `evals/user_study/sessions.csv` | task-study answers | consent text in `docs/user_study.md` | outcome | – |
| `data/reports.json` | 14 synthetic reports with expected actions | project | regression set | 14 |

## Synthetic data: how it was made and what it cannot show

| Set | How it was generated | Represents | Limits |
| --- | --- | --- | --- |
| Hand-made routes and street risks (`data/*.json`) | drawn by the team from a pre-existing prototype route map; risks assigned by hand | the app's data shapes and a few plausible trade-offs | not measurements; regression use only |
| Critic corruptions | fact-only claims corrupted five ways in `evals/run_evals.py` | wrong numbers, "safe", streets on neither route, missing citations, reversed direction | tests the rules, not how real models fail |
| Guard held-out probes | written to imitate realistic attempts (instructions to the moderator, JSON payloads, other languages, obfuscation) and realistic benign reports with trigger words | how the guard generalises beyond its dev set | the writer had read the patterns; real attempts (red-team) are better evidence |
| PII cases | fictional names, numbers and addresses in report-like sentences | phone, email, address, plate, URL, card, name, student id | covers formats we thought of; real reports will contain others |
| Synthetic report variants (`pipeline/synth.py`) | model paraphrases of train-split reports only | training volume | never in dev or test |

## Privacy

- **Street reports** can say where someone felt unsafe. No accounts; text is redacted (phones, emails, house
  addresses, links, card numbers, plates) before any model call and before storage, with one policy for the app and
  for ingest (`athena/guard.py`). It was measured on `evals/results/pii.json`: names and student ids are not found,
  so the report form asks people to leave them out and labellers read every report.
- **Collected reports and study answers** are given with consent (texts in `data/labelling/guide.md` and
  `docs/user_study.md`), stored under pseudonymous group ids, never names, and deleted after marking.
- **App storage:** report text and traces go to the container's `runs/` folder (git-ignored, lost on redeploy).
  Rate limiting uses a keyed hash of the client address, so no IP address is stored.
- **Model hosting:** report text is sent only to models behind the LiteLLM gateway; check where each model is
  hosted before using it for user text (docs/model_card.md).
- **Secrets** come only from the environment; `scripts/scan_secrets.py` runs in `make check` and as a pre-commit
  hook.

## Licences when redistributing

| Material | Licence | What we must do | Where we do it |
| --- | --- | --- | --- |
| OpenStreetMap data and anything derived from it (`data/processed/`, `data/safety_points.json`) | ODbL 1.0 | attribute; offer derived databases under ODbL | map attribution; `data/processed/LICENCE.md` |
| TfNSW GTFS (behind the precomputed trips) | CC BY 4.0 | attribute Transport for NSW | map attribution ("Timetables: Transport for NSW (CC BY 4.0)"); this file |
| Meterless adversarial prompts and guard patterns | Apache-2.0 | keep the notice | `NOTICE` |
| MapLibre GL JS (`web/vendor/`) | BSD-3-Clause | keep the licence | vendored file header |
| OpenFreeMap tiles | attribution | attribute | map attribution |
| City of Sydney light poles, BOCSAR, Free to Be | unknown or unsuitable | **not ingested** | "Excluded" above |
