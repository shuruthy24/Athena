# 07 · Status, verification, limitations and next steps

## Feature status
| Feature | Status | Verified how |
|---|---|---|
| Scout Intent engine (5 stages, bands, guard, capability graph, model profiles, signed contracts, re-plan, telemetry) | Implemented | 20 unit tests; held-out evaluation |
| Route Analyst ↔ Evidence Critic loop with fact-only fallback | Implemented | Unit tests for approve, revise, all-rejected and model-outage paths; mock Nemotron run shows revise → approve |
| Zetaris REST client (API key → login fallback, one statement per call, timeouts) | **Verified live** | 8/8 stack checks pass; 186 route rows and 12 street rows served over REST with `auth=login` |
| Zetaris table registration and row-count check | **Verified live** | All four tables load from the public GitHub raw URLs and match expected counts (12 / 186 / 7 / 14) |
| Nightshift client (Runtime token, meterless headers, discovery, per-call model) | **Verified live** | 108 models discovered; analyst and critic both served by `nvidia/nemotron-3.5-bolt-e4b`; 10 sample runs end to end |
| Report redaction → moderation → verification → fading penalty | **Verified live** | Unit tests; 14/14 with the offline stub, **12/14 (0.857) with Nemotron** — both misses send redacted reports to human review (see `04`) |
| Emergency path (000, staffed stations, no model) | Implemented | Unit tests |
| Ask Athena interface (loading, error, empty and success states; light and dark; phone width) | Implemented | Playwright screenshots in sample mode; no console errors except blocked web fonts in the sandbox |
| Sample mode | Implemented | All tests and samples run with no credentials |
| H-MEM memory, live OTP routing, City of Sydney poles | Not built | — |

## Evidence index
- `docs/evidence/test_results.txt`: pytest output (39 passed)
- `docs/evidence/evaluation_report.md`: all evaluation tables (generated)
- `docs/evidence/evaluation_report_v1_baseline.md`: first routing evaluation, before the v2 changes
- `docs/evidence/secrets_scan.txt`: scan for keys and tokens in tracked files
- `docs/evidence/stack_checks.txt`: 8/8 live checks, naming the serving Nemotron model
- `docs/evidence/zetaris_load.txt`: all four tables loaded and row counts matched
- `samples/outputs_live/`: 10 live runs with full traces (Zetaris rows and Nemotron model per step)
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
1. ~~Run live on the laptop~~ **Done 28 Oct**: tables loaded, 8/8 stack checks, 10 live sample runs, live evaluation. Evidence committed.
2. **Record the demo video and finish the slide deck** (required components).
3. **Paste the sponsor text, declare pre-existing parts, submit before 23:45 UTC.**
4. Confirm the VM eligibility question with the organisers (Help desk).
5. If time remains: add H-MEM to remember a user's home and preferred mode across sessions (a second engine).

## Model selection on the live stack (28 Oct, measured)
We sent the real analyst prompt (1,973 characters) to four Nemotron routes through our Nightshift Runtime:

| Route | Result |
|---|---|
| `nvidia/nemotron-3.5-lightning-30b-a3b` | **Fails**: `502 provider_error … _empty_response` every time, after ~57 s including one retry |
| `nvidia/nemotron-3.5-bolt-e4b` | **Works**: 7.4 s, returns the requested JSON directly |
| `nvidia/nemotron-3-ultra-550b-a55b` | Works but 37.8 s, and emits chain-of-thought prose before any JSON |
| `meterless/auto` | Routes to ultra (13.3 s), with the same prose-before-JSON behaviour |

We therefore pin `nvidia/nemotron-3.5-bolt-e4b` for both the analyst and critic lanes rather than leaving
`meterless/auto` to choose, which also makes the NVIDIA award evidence unambiguous: the trace names that model
on every drafting and critique step. The analyst prompt was also reduced to the flat facts (not the whole
comparison object) and the output cap raised to 1,600 tokens, since a reasoning model can spend a 700-token
budget before emitting any content.
