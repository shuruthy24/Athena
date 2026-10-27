# 07 · Status, verification, limitations and next steps

## Feature status
| Feature | Status | Verified how |
|---|---|---|
| Scout Intent engine (5 stages, bands, guard, capability graph, model profiles, signed contracts, re-plan, telemetry) | Implemented | 20 unit tests; held-out evaluation |
| Route Analyst ↔ Evidence Critic loop with fact-only fallback | Implemented | Unit tests for approve, revise, all-rejected and model-outage paths; mock Nemotron run shows revise → approve |
| Zetaris REST client (API key → login fallback, one statement per call, timeouts) | Implemented | Mock Zetaris tests (login fallback, headers, UUIDs, org ID). **Live run on the team laptop: pending** |
| Zetaris table registration and row-count check | Implemented | SQL generated and reviewed. **Not yet run against Zetaris; the REST-table-from-URL step is unverified** |
| Nightshift client (Runtime token, meterless headers, discovery, per-call model) | Implemented | Mock Runtime tests. The team's live Nightshift answered `/v1/models` and `/v1/chat/completions` with `nvidia/nemotron-3.5-lightning-30b-a3b` on 26 Oct (setup session). **The agent itself hasn't run live yet** |
| Report redaction → moderation → verification → fading penalty | Implemented | Unit tests; 14/14 synthetic reports with the offline stub. **Nemotron moderation accuracy not measured** |
| Emergency path (000, staffed stations, no model) | Implemented | Unit tests |
| Ask Athena interface (loading, error, empty and success states; light and dark; phone width) | Implemented | Playwright screenshots in sample mode; no console errors except blocked web fonts in the sandbox |
| Sample mode | Implemented | All tests and samples run with no credentials |
| H-MEM memory, live OTP routing, City of Sydney poles | Not built | — |

## Evidence index
- `docs/evidence/test_results.txt`: pytest output (39 passed)
- `docs/evidence/evaluation_report.md`: all evaluation tables (generated)
- `docs/evidence/evaluation_report_v1_baseline.md`: first routing evaluation, before the v2 changes
- `docs/evidence/secrets_scan.txt`: scan for keys and tokens in tracked files
- `docs/evidence/stack_checks.txt`, `docs/evidence/zetaris_load.txt`: **created when the team runs** `scripts/check_stack.py` and `scripts/load_zetaris.py`
- `samples/outputs_sample_mode/`: 10 sample runs with full traces

## Known limitations
1. **Coverage:** one corridor (UNSW Library ↔ Town Hall), 3 time bands, 3 modes. Other trips get an honest "no routes yet".
2. **Risk is a rule, not a measurement.** The 36% median reduction is in the app's own score on hand-made routes.
3. **Evaluation sets are small and written by the team.** They're smoke tests, not benchmarks.
4. **The emergency classifier is lexical.** Unusual phrasings may be missed; 000 stays visible in the header on every screen.
5. **Nightshift runs in a VM.** It needs about 4 GB of RAM and must be opened after each VM start. Eligibility confirmation pending.
6. **Zetaris authentication uses login** because API tokens are refused locally. The credentials sit in `.env`.
7. **No user study yet.** Impact on real decisions is unmeasured.

## Unverified dependencies (must be checked on the laptop)
- Zetaris accepts `CREATE LIGHTNING REST TABLE` from a raw GitHub URL (fallbacks: local HTTP via the Docker gateway, or `--inline`).
- DDL statements run over the REST `/sql-editor/sqls/run` endpoint as they do in the SQL Editor.
- Zetaris returns the view's column names as the `headers` the client maps on.
- The Nemotron route names in the team's Nightshift catalog (set `FAST_MODEL`, `CRITIC_MODEL`).

## Prioritised remaining work (highest value first)
1. **Run live on the laptop:** `load_zetaris.py`, then `check_stack.py`, then `make_samples.py --live`, then `run_evals.py --live`. Commit the evidence files. *Blocks the claim that the live integration works.*
2. **Record the demo video and build the slide deck** (required components).
3. **Push to a public GitHub repo, paste the sponsor text, declare pre-existing parts, submit.**
4. Confirm the VM eligibility question with the organisers (Help desk).
5. If time remains: add H-MEM to remember a user's home and preferred mode across sessions (a second engine).
