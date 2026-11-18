# Athena: status of the submission

What the submitted system does, how to verify each demonstrated feature yourself, what is not finished, the known
limitations and bugs, and how the demo differs from this submission. Last updated 18 Nov 2026. Update this page
whenever a feature, a dataset or a result changes. The evaluation results are in [evaluation.md](evaluation.md).

## Verify everything in three commands

```bash
python -m pip install -r requirements-lock.txt     # Python 3.12 or 3.13; on Windows use .venv\Scripts\python
python scripts/verify_demo.py                      # each demonstrated feature, PASS/FAIL, no key, no server
python scripts/check.py                            # tests, reproduce every offline result, data checks, docs in sync
```

Against a running or deployed server: `python scripts/verify_demo.py --base-url http://127.0.0.1:8001`. The same
checks run on Linux and Windows for every push (`.github/workflows/check.yml`).

## Demonstrated features

| # | Feature (as demonstrated) | Where in the app | Code | Tests | `verify_demo` check | Status |
| --- | --- | --- | --- | --- | --- | --- |
| F0 | Says which data and which model are answering | box above the results on `/` and `/map`; `/api/health` | `LocalSource.provenance`, `server.health` | `test_data_lineage.py::test_provenance_*` | F0 | done |
| F1 | Planner, street map and prototype pages | `/`, `/map`, `/prototype` | `web/` | `test_pages_share_navigation` | F1 | done |
| F2 | Trip planner: options across walk, cycle and public transport, ranked five ways, each labelled lower / similar / higher risk than the fastest, any departure time | `/` | `athena/journeys.py` | `test_journeys.py` (36) | F2 ×4 | done |
| F3 | Street map: every scored street coloured by risk for a time | `/map` | `server.street_safety` | `test_street_safety_overlay*` | F3 | done |
| F4 | Help near the route (police, hospital, station) and 000, with no model call | route detail, top bar | `web/plan.html`, `/api/safety-points` | `test_safety_points_absent_then_present` | F4 | done |
| F5 | "Explain this trade-off": a model drafts claims citing facts; rules and a model critic check them; up to 2 revisions, then fact-only text; cached; outage → fact-only, confidence low | route detail | `athena/agents.py` | `test_agents.py`, `test_cache.py`, `test_leakage.py` | F5 ×4 | done (live model needs the key) |
| F6 | Street report: injection blocked before any model, PII removed, classifier, verifier against street data, approved reports add a fading penalty | route detail → "Report a street condition" | `athena/agents.py`, `athena/guard.py`, `athena/classify/` | `test_report_*`, `test_instruction_in_a_report_*`, `test_privacy.py` | F6 ×4 | done; classifier choice **not evaluated** (no labelled reports yet) |
| F7 | Per-client rate limit, no IP stored | all POST endpoints | `server.rate_limit` | `test_rate_limit_is_per_client_not_shared` | F7 | done |
| F8 | Pre-hackathon prototype map, shown for comparison | `/prototype` | `web/index.html` (pre-existing, unchanged) | page loads (F1) | F1 | declared pre-existing work |
| F9 | Live OpenTripPlanner (plan any trip) | only when `OTP_URL` points at a running OTP | `athena/data/otp.py` | `test_live_otp_rows_are_scored_like_precomputed` (mock) | none | **never run against a live OTP**; off by default |
| F10 | Deployed URL | none yet | `Dockerfile`, `docs/deploy.md` | none | `--base-url` once deployed | **not deployed** |

## Not finished (and what finishing takes)

| Item | Why it is not done | What to run | Time |
| --- | --- | --- | --- |
| Real data in the submission | `data/processed/` was git-ignored; raw snapshots are on one laptop | on that laptop: `make data`, `python -m pipeline.fetch_raw --write`, upload the 2 needed raw files to a GitHub Release and paste the URLs into `data/raw_manifest.json`, `make data-verify`, `make eval-real LABEL=final`, commit `data/processed/` | 30 min |
| Labelled reports (classifier, agreement, test split) | needs people | `python evals/label.py add-reports ...` (150+, 30+ collected with consent), `python evals/label.py reports --as a` / `--as b`, `make labels`, `python evals/eval_classifier.py` (dev), choose, `--split test --touch-test` once, set `REPORT_CLASSIFIER` | 3–4 h |
| Judge audit | needs human PASS/FAIL labels | `python evals/label.py traces evals/traces/2026-11-14-live-scx-gemma-4-31B-it`, then `python evals/judge.py audit ...` | 1 h |
| Blind A/B, LLM vs fact-only | needs human choices | `python evals/ab.py prepare ...`, fill `choices.csv`, commit, `python evals/ab.py reveal --judge` | 45 min |
| Injection end to end with the real classifier | needs the key | `python evals/eval_injection_e2e.py --live` (about 50 calls) | 10 min |
| Red-team held-out set | needs people outside the team | `evals/guard/README.md` | 1 h |
| Task study (outcome metric) | needs participants | `docs/user_study.md`, then `python evals/eval_user_study.py` | 2 h |
| Deployment and load test | not deployed | `docs/deploy.md`, then `make load-test BASE_URL=...` and `python scripts/verify_demo.py --base-url ...` | 1 h |
| Critic and moderator model choice | only the explainer was compared | `python evals/compare_models.py` with labelled reports | 15 min |

Every row that is not finished shows as "not run yet" in [evaluation.md](evaluation.md) and the README, computed
from the absence of its result file, so nothing appears done before it is.

## Known limitations

- **Risk is a rule, not a measurement of safety.** Its weights are a team judgement (no risk labels exist), and the
  route evaluation scores Athena with that same rule ([evaluation.md](evaluation.md)).
- **The text guard alone misses most held-out injection attempts** (see the held-out row in evaluation.md). It is
  the first of three layers. The end-to-end number with the real classifier has not been measured yet.
- **Names and student ids are not removed** by PII redaction (regex cannot find names). The report form asks
  people to leave them out, and labellers read every report.
- **Lighting data is thin:** most real streets have no OSM lighting tag; unknown lighting is scored halfway and
  always shown as unknown.
- **Few real alternatives:** stock OpenTripPlanner gives one walking route per trip; most differences are between
  public transport options. Times are timetable times, not live.
- **The evaluation sets are small** (tens of cases; 95% intervals are shown), and several were written by the team
  or by an AI assistant; each set says who wrote it ([evaluation.md](evaluation.md)).
- **Route coverage:** 7 places, 42 pairs (real data) or 2 trips (sample). Other trips need `OTP_URL`.
- **Report storage** is the container's `runs/` folder: lost on redeploy, by design (privacy), so penalties reset.
- **Basemap and fonts load from the internet** (OpenFreeMap, Google Fonts). Offline, the map shows a plain
  background with a note, and the system fonts are used.

## Known bugs and issues

Fixed on 18 Nov 2026 (each has a test): `make check` failed outside a git checkout (secrets scan); the app's PII
redaction missed addresses, URLs, card numbers and bracketed landlines (one policy now, regex fixed); the "no
routes" message named a fixed corridor whatever the data; the sample was labelled "precomputed with
OpenTripPlanner"; file reads and writes used the platform encoding, so Windows could garble or crash on non-ASCII
data.

Open: `leg_streets` can hold both name-matched and location-matched steps for one leg, so its key may repeat. This
is reported in `data_quality.json` but not enforced. AGENTS.md asks for a 60-row test split, but 150 reports at 20%
gives about 30 rows.

## How the demo differs from this submission

| Aspect | Demo (Demo Day, video) | This submission | Why |
| --- | --- | --- | --- |
| Data | real OSM + OTP data from the team laptop (`data/processed`) | the same tables once committed (see "Not finished"); until then the hand-made sample, labelled amber in the UI | `data/processed/` was git-ignored |
| Models | live COMP3110 gateway (explainer `scx-gemma-4-31B-it`, critic and moderator `scx-gpt-oss-120b`) | identical with a key in `.env`; without one, `SAMPLE_MODE` uses an offline stand-in, labelled in the UI and in every response (`drafted_by`) | markers may not have a key |
| Chat / Ask page, Scout router, signed contracts | hackathon submission (tag `hackathon-final`) | removed on 14 Nov 2026; replaced by two typed endpoints | no free-text chat is needed; smaller attack surface |
| Zetaris, Meterless Nightshift, NVIDIA Nemotron | hackathon stack | removed on 14–15 Nov 2026 (tag `hackathon-final` keeps them) | course gateway and plain files |
| Prototype map with a "Live OTP" toggle | shown | unchanged, under `/prototype`, labelled pre-existing | declared prior work; live OTP needs a local OTP server |
| Data and model provenance line; PII policy; report form hint | added 18 Nov 2026 | present | transparency and privacy fixes from this audit |
| Deployment | (to be decided) | not deployed | see "Not finished" |

## External dependencies and what happens without them

| Dependency | Used for | Without it |
| --- | --- | --- |
| COMP3110 LiteLLM gateway | explainer, critic, report classifier (prompted) | explanations fall back to fact-only text (confidence low); reports go to human review; `SAMPLE_MODE` runs offline |
| OpenFreeMap tiles, Google Fonts | basemap, typefaces | plain map background with a note; system fonts |
| OpenTripPlanner (`OTP_URL`) | optional live routing | precomputed routes (default) |
| Overpass API, TfNSW Open Data Hub, GitHub Release mirror | rebuilding raw data only | not needed at runtime; `data/processed/` is committed |

## Rubric map: where each criterion is evidenced

| Criterion | Evidence | State |
| --- | --- | --- |
| Appropriate and relevant evaluation set | evaluation.md "What Athena must get right" and data table: one set per capability | built; reports, labels and study need people |
| Real cases where possible, or suitable synthetic cases | real: OSM/OTP trips, live traces, model drafts; to collect: reports, red-team, study. Synthetic sets say who wrote them and why | partly real; collection kits ready |
| Clearly defined metrics | `docs/metrics.md` (written before runs, dated additions) | done |
| Appropriate baselines | evaluation.md "Baselines"; every row has one | done |
| Documented methodology | `docs/evaluation.md`, `docs/metrics.md`, `evals/guard/README.md`, `docs/user_study.md` | done |
| Documented results | generated table in evaluation.md and README (`make check` keeps them in sync) | done for runs so far |
| Reproducible evaluation | `python scripts/reproduce.py`; locked dependencies; CI on Linux and Windows | done |
| Data sources identified and documented | `data_sources.md`, `data/raw_manifest.json`, `data/processed/manifest.json` | done |
| Repeatable cleaning and preparation | `make data`; `make data-verify` compares content hashes | built; needs the raw mirror |
| Tuning separate from held-back data | group split + frozen test; guard dev vs held-out; trace dev vs held-out assignment; `evals/checksums.txt` | done |
| Privacy | PII at ingest and before every model call, measured (`pii.json`); consent texts; retention; no accounts; hashed rate-limit ids | done; names are a stated limit |
| Licensing | `data_sources.md` licences; ODbL notice in `data/processed/LICENCE.md`; TfNSW CC BY attribution on the map; `NOTICE` | done |
| Data quality | `pipeline.check` (schema, keys, area, references) and `data_quality.json` (missing values, duplicates, snapshot age) | done |
| System implements the demonstrated features | table above, `verify.json` | done (F9 and F10 stated) |
| Can be run from the instructions | README quick start (Windows and macOS/Linux), lock file, CI | done |
| Can be independently verified | `scripts/verify_demo.py` locally or against a URL; `scripts/check.py`; CI artifacts | done |
| Incomplete features, limitations, demo differences documented | this page | done; keep it current |
