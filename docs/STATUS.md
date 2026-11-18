# Project status

What works, how to check each feature, what isn't finished, and how this version differs from the demo.
Evaluation results are in [evaluation.md](evaluation.md).

## Checking everything

```bash
python -m pip install -r requirements-lock.txt     # inside a virtual environment (see the README)
python scripts/check.py                            # tests, results, data checks and every feature check
python scripts/verify_demo.py --base-url http://127.0.0.1:8001   # the feature checks against a running server
```

The same checks run on Linux and Windows for every push (`.github/workflows/check.yml`).

## Features

| # | Feature | Where | Code | Check | Status |
| --- | --- | --- | --- | --- | --- |
| F0 | Shows which data and which model are answering | `/`, `/map`, `/api/health` | `LocalSource.provenance`, `server.health` | F0 | done |
| F1 | Planner, street map and prototype pages | `/`, `/map`, `/prototype` | `web/` | F1 | done |
| F2 | Trip planner: walk, cycle and public transport options, ranked five ways, labelled against the fastest, any departure time | `/` | `athena/journeys.py` | F2 | done |
| F3 | Street map coloured by risk for a chosen time | `/map` | `server.street_safety` | F3 | done |
| F4 | Help near the route (police, hospital, station, 000), no model call | route detail | `/api/safety-points` | F4 | done |
| F5 | "Explain this trade-off": model draft, rule and model checks, up to 2 revisions, fact-only fallback, cache | route detail | `athena/agents.py` | F5 | done (the live model needs the gateway key) |
| F6 | Street reports: injection blocked and PII removed before any model, then classifier and street-data check | route detail | `athena/agents.py`, `athena/guard.py`, `athena/classify/` | F6 | done; uses `moderator-v1` |
| F7 | Per-client rate limit, no IP address stored | all POST endpoints | `server.rate_limit` | F7 | done |
| F8 | Earlier prototype map, kept for comparison | `/prototype` | `web/index.html` (pre-existing) | F1 | pre-existing work |
| F9 | Live OpenTripPlanner for any trip | only with `OTP_URL` set | `athena/data/otp.py` | mock test only | never run against a live OTP; off by default |
| F10 | Deployed URL | none | `Dockerfile` | none | not deployed |

## Not finished

| Item | State | What finishing takes |
| --- | --- | --- |
| Reports that should be held | 49 labelled, but dev and test have only `approve`, so the classifier's main metric can't be computed | collect and label more reports, including vague, off-topic and prejudiced ones (`data/labelling/guide.md`), then rerun the split and `evals/eval_classifier.py` |
| Report test split | held back on purpose; no decision depends on it yet | as above, then score it once |
| Judge audit with failing examples | the held-out labels contain no FAIL; the second audit's result file wasn't saved | `python evals/judge.py audit evals/traces/2026-11-14-live-sample --labels evals/labels-sample` (gateway key) |
| Larger blind A/B | 6 pairs, tied 3–3 | more pairs (`python evals/ab.py prepare ...`) |
| Red-team set | not collected | injection attempts from people outside the team in `evals/guard/redteam.csv`; `make eval` scores it |
| User study | not run, so we don't know whether people pick the lower-risk option | a task study with people outside the team |
| Deployment and load test | not deployed | run the container (see the `Dockerfile` header), then `make load-test BASE_URL=...` |
| Critic, moderator and judge model choice | only the explainer model was compared | `python evals/compare_models.py` |
| Raw data download | `data/raw_manifest.json` has no mirror URLs yet, so `make data-verify` only runs where `data/raw/` exists | upload the raw snapshots, add their URLs, then `make fetch-raw && make data-verify` |

## Known limitations

- **The risk score is a rule we chose,** not a measure of safety, and on real data it doesn't favour lit streets
  ([evaluation.md](evaluation.md#findings)).
- **Injected reports can still get through:** 4 of 24 held-out attempts with the live classifier.
- **Names are only removed when introduced** ("my name is ..."). The report form asks people not to include names.
- **Lighting data is thin:** most streets have no lighting tag. Unknown lighting is scored halfway and shown as
  unknown.
- **Few real alternatives:** OpenTripPlanner gives one walking route per trip, so most alternatives are public
  transport. Times come from the timetable, not live data.
- **Small evaluation sets,** mostly written by us (some by an AI tool; each set says who wrote it).
- **Coverage:** 7 places and 42 trip pairs. Other trips need a live OpenTripPlanner (`OTP_URL`).
- **Reports are stored only in the running container** (`runs/`), so they're lost on restart. This is on purpose,
  for privacy.
- **The map tiles and fonts load from the internet.** Offline, the map shows a plain background.

## Known issues

- `evals/assertions.py`, `evals/judge.py audit` and `scripts/verify_demo.py` (without `--base-url`) overwrite
  committed results. Use `scripts/reproduce.py` or `scripts/check.py` to check results instead
  ([evaluation.md](evaluation.md#a-warning-about-three-scripts)).
- `verify_demo.py --base-url` saves an untracked `evals/results/verify_<host>.json`; delete it after use.
- The report test split has 9 rows. A 60-row split would need about 300 labelled reports.

## Differences from the demo

| | Demo | This repository |
| --- | --- | --- |
| Data | real OSM + OTP data | the same data, committed in `data/processed/` |
| Models | live gateway (explainer `scx-gemma-4-31B-it`, critic and classifier `scx-gpt-oss-120b`) | the same with a key in `.env`; without one, `SAMPLE_MODE` uses an offline stand-in, which the app labels on screen |
| Prototype map | shown | unchanged at `/prototype`; its "Live OTP" toggle needs a local OpenTripPlanner |
| Hosting | — | runs locally; not deployed |

## What happens without the external services

| Service | Used for | Without it |
| --- | --- | --- |
| LiteLLM gateway | explainer, critic, prompted classifier | fact-only explanations; reports go to human review; `SAMPLE_MODE` works fully offline |
| OpenFreeMap tiles, Google Fonts | map background, fonts | plain background; system fonts |
| OpenTripPlanner (`OTP_URL`) | optional live routing | precomputed routes (the default) |
| Overpass API, TfNSW Open Data Hub | rebuilding the raw data only | not needed to run the app |
