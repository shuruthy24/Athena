# Athena: project status

What the system does, how to verify each feature yourself, what is not implemented, the known limitations and
issues, and how the system differs from what was demonstrated. Evaluation results are in
[evaluation.md](evaluation.md); readiness is in [design.md](design.md) §7.

## Verify everything in three commands

```bash
python -m pip install -r requirements-lock.txt     # Python 3.12 or 3.13, inside a virtual environment (README)
python scripts/check.py                            # tests, reproduce every offline result, data checks, docs in sync,
                                                   # and each demonstrated feature (PASS/FAIL, no key, no server)
```

Against a running or deployed server: `python scripts/verify_demo.py --base-url http://127.0.0.1:8001`. The same
checks run on Linux and Windows for every push (`.github/workflows/check.yml`).

## Features

| # | Feature | Where in the app | Code | Tests | `verify_demo` check | Status |
| --- | --- | --- | --- | --- | --- | --- |
| F0 | Says which data and which model are answering | box above the results on `/` and `/map`; `/api/health` | `LocalSource.provenance`, `server.health` | `test_data_lineage.py::test_provenance_*` | F0 | done |
| F1 | Planner, street map and prototype pages | `/`, `/map`, `/prototype` | `web/` | `test_pages_share_navigation` | F1 | done |
| F2 | Trip planner: options across walk, cycle and public transport, ranked five ways, each labelled lower / similar / higher risk than the fastest, any departure time | `/` | `athena/journeys.py` | `test_journeys.py` (36) | F2 ×4 | done |
| F3 | Street map: every scored street coloured by risk for a time | `/map` | `server.street_safety` | `test_street_safety_overlay*` | F3 | done |
| F4 | Help near the route (police, hospital, station) and 000, with no model call | route detail, top bar | `web/plan.html`, `/api/safety-points` | `test_safety_points_absent_then_present` | F4 | done |
| F5 | "Explain this trade-off": a model drafts claims citing facts; rules and a model critic check them; up to 2 revisions, then fact-only text; cached; outage → fact-only, confidence low | route detail | `athena/agents.py` | `test_agents.py`, `test_cache.py`, `test_leakage.py` | F5 ×4 | done (live model needs the gateway key) |
| F6 | Street report: injection blocked before any model, PII removed, classifier, verifier against street data, approved reports add a fading penalty | route detail → "Report a street condition" | `athena/agents.py`, `athena/guard.py`, `athena/classify/` | `test_report_*`, `test_instruction_in_a_report_*`, `test_privacy.py` | F6 ×4 | done; uses `moderator-v1`. Six classifiers were compared on the dev split, which could not separate them (`classifier_dev.json`) |
| F7 | Per-client rate limit, no IP stored | all POST endpoints | `server.rate_limit` | `test_rate_limit_is_per_client_not_shared` | F7 | done |
| F8 | Earlier prototype map, kept for comparison | `/prototype` | `web/index.html` (pre-existing, unchanged) | page loads (F1) | F1 | pre-existing work, labelled as such |
| F9 | Live OpenTripPlanner (plan any trip) | only when `OTP_URL` points at a running OTP | `athena/data/otp.py` | `test_live_otp_rows_are_scored_like_precomputed` (mock) | none | **never run against a live OTP**; off by default |
| F10 | Deployed URL | none | `Dockerfile` | none | `--base-url` once deployed | **not deployed** |

## Not implemented and future work

Everything that is not finished is listed here, and nowhere else. Rows that depend on a missing result file show as
"not run yet" in the generated tables in [evaluation.md](evaluation.md) and the README. Three of them are scoping
decisions; evaluation.md, "Evaluations deliberately not run", gives the reason for each.

| Item | State | What finishing takes |
| --- | --- | --- |
| Labelled reports with hold cases | 49 labelled (train 35, dev 5, test 9). Dev and test contain only `approve`, so the classifier's main metric (TNR on hold) is undefined | more reports, including `review` and `reject`, collected with consent (`data/labelling/guide.md`); then `make labels`, `python evals/eval_classifier.py` on dev, choose, score the test split once |
| Report classifier test split | held back on purpose (no decision depends on it) | as above |
| Judge audit with FAIL cases | the primary audit (`judge_audit.json`) has no held-out FAIL labels, so TNR is undefined; the secondary audit's result file was not saved | `python evals/judge.py audit evals/traces/2026-11-14-live-sample --labels evals/labels-sample` (gateway key, 6 calls) |
| Larger blind A/B | 6 pairs, tied 3-3 (`ab.json`) | more pairs from a new trace run (`python evals/ab.py prepare ...`) |
| Red-team held-out set | not collected | attempts by people outside the team (`evals/guard/README.md`) |
| Task study (outcome metric) | not conducted | sessions with participants (`docs/user_study.md`), then `python evals/eval_user_study.py` |
| Deployment and load test | not deployed | build and run the container (`Dockerfile` header), then `make load-test BASE_URL=...` and `python scripts/verify_demo.py --base-url ...` |
| Critic, moderator and judge model choice | only the explainer was compared (`model_comparison.json`) | `python evals/compare_models.py` with labelled reports |
| Public mirror of the raw snapshots | `data/raw_manifest.json` has no download URLs yet, so `make data-verify` runs only where `data/raw/` exists (DVC) | upload the raw files, add their URLs to the manifest, `make fetch-raw && make data-verify` |
| Route baselines on real data | measured on the sample only (`route_baselines.json`) | `python evals/eval_route_baselines.py` on `data/processed` |

## Known limitations

- **Risk is a rule, not a measurement of safety.** Its weights are a team judgement (no risk labels exist), and the
  route evaluation scores Athena with that same rule ([evaluation.md](evaluation.md)).
- **Injected reports can still get through.** The text guard alone misses most held-out injection probes; with
  the live moderator, 4 of 24 were admitted end to end (`injection_e2e.json`).
- **Names are removed only when introduced** ("my name is", "my friend", "I'm"); a bare name passes PII
  redaction. The report form asks people to leave names out, and labellers read every report.
- **Lighting data is thin:** most real streets have no OSM lighting tag. Unknown lighting is scored halfway and
  always shown as unknown.
- **Few real alternatives:** stock OpenTripPlanner gives one walking route per trip, so most differences are
  between public transport options. Times come from the timetable, not live data.
- **The evaluation sets are small** (tens of cases; 95% intervals are shown). Several were written by the team or
  by an AI assistant; each set says who wrote it ([evaluation.md](evaluation.md)).
- **Route coverage:** 7 places, 42 pairs (real data) or 2 trips (sample). Other trips need `OTP_URL`.
- **Report storage** is the container's `runs/` folder: lost on redeploy, by design (privacy), so penalties reset.
- **Basemap and fonts load from the internet** (OpenFreeMap, Google Fonts). Offline, the map shows a plain
  background with a note, and the system fonts are used.

## Known issues

- `evals/assertions.py <dir>` and `evals/judge.py audit <dir>` overwrite committed results and read the shell's
  `.env`. Run directly under `SAMPLE_MODE=false`, `assertions.py` has produced spurious `streets_on_routes`
  failures. Check results with `python scripts/reproduce.py` instead ([evaluation.md](evaluation.md), "Results that
  are written to fixed paths").
- `python scripts/verify_demo.py --base-url ...` saves an untracked `evals/results/verify_<host>.json`. Without
  `--base-url` or `--out` it rewrites the committed `evals/results/verify.json`, so the next `make check` fails
  ("docs in sync") until `make report` is run. To check the app, use `make check`.
- The report test split has 9 rows; a 60-row split would need about 300 labelled reports.

Fixed, each with a regression test: `make check` failed outside a git checkout (secrets scan); the app's PII
redaction missed addresses, URLs, card numbers and bracketed landlines (one policy now, regex fixed); the "no
routes" message named a fixed corridor whatever the data; the sample was labelled "precomputed with
OpenTripPlanner"; file reads and writes used the platform encoding, so Windows could garble or crash on non-ASCII
data.

## How this system differs from the demonstration

| Aspect | Demonstrated | This repository | Why |
| --- | --- | --- | --- |
| Data | real OSM + OTP data (`data/processed`) | the same tables, committed; the hand-made sample remains the offline regression set | — |
| Models | live gateway (explainer `scx-gemma-4-31B-it`, critic and moderator `scx-gpt-oss-120b`) | identical with a key in `.env`; without one, `SAMPLE_MODE` uses an offline stand-in, labelled in the UI and in every response (`drafted_by`) | a reader may not have a gateway key |
| Prototype map with a "Live OTP" toggle | shown | unchanged, under `/prototype`, labelled pre-existing | live OTP needs a local OTP server |
| Deployment | — | not deployed; runs locally | see "Not implemented" |

## External dependencies and what happens without them

| Dependency | Used for | Without it |
| --- | --- | --- |
| LiteLLM gateway (`COMP3110_MODEL_BASE_URL`) | explainer, critic, report classifier (prompted) | explanations fall back to fact-only text (confidence low); reports go to human review; `SAMPLE_MODE` runs offline |
| OpenFreeMap tiles, Google Fonts | basemap, typefaces | plain map background with a note; system fonts |
| OpenTripPlanner (`OTP_URL`) | optional live routing | precomputed routes (default) |
| Overpass API, TfNSW Open Data Hub | rebuilding raw data only | not needed at runtime; `data/processed/` is committed |

## Where things are documented

| Topic | Where |
| --- | --- |
| Evaluation sets, metrics, baselines, results and methodology | [evaluation.md](evaluation.md), [metrics.md](metrics.md), `evals/guard/README.md`, [user_study.md](user_study.md) |
| Reproducing every result | `python scripts/reproduce.py`; locked dependencies (`requirements-lock.txt`); CI on Linux and Windows |
| Data sources, licences, cleaning and the repeatable rebuild | [data_sources.md](../data_sources.md), `data/raw_manifest.json`, `data/processed/manifest.json`, `data/processed/LICENCE.md`, `NOTICE` |
| Tuning vs held-back data | group split with a frozen test split; guard dev vs held-out; trace dev vs held-out assignment; `evals/checksums.txt` |
| Privacy | PII removed at ingest and before every model call, measured (`pii.json`); consent texts; retention; no accounts; hashed rate-limit ids ([data_sources.md](../data_sources.md)) |
| Data quality | `pipeline.check` (schema, keys, area, references) and `data_quality.json` (missing values, duplicates, snapshot age) |
| Architecture, design decisions and readiness | [design.md](design.md), [model_card.md](model_card.md) |
| Container | `Dockerfile` (build and run instructions in its header) |
