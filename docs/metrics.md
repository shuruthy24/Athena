# Metric tree

Written before the model work it measures (design and implement metrics first). Each
metric names its data, the script that computes it and the file it lands in. **Targets are proposals for the team
to confirm before running the evaluation**, then left alone: criteria are fixed before results are seen.

## Outcome

**People choose the lower-risk option when it costs them little.** Share of night trips where a person who sees
both options picks Athena's lower-risk route, and the extra minutes they accept for it.

- Would be measured with a task study (people choosing between Athena and a plain fastest-first list).
- **Not measured:** no user study was conducted, so this outcome metric has no result (evaluation.md).

**Guardrail: no report that targets people ever changes a route.** Week 5 baseline-review objective. Measured as
TNR on hold for the report pipeline, with every reject-labelled report held.

## Components

| Component | Metric (definition) | Baseline | Data | Script → file | Proposed target | Decision it informs |
| --- | --- | --- | --- | --- | --- | --- |
| Route choice | median risk-indicator reduction and median extra minutes, Athena vs fastest, over every precomputed trip | fastest route | hand-made sample (regression set); real OSM + OTP rebuild | `make eval` → `offline.json` (sample); `evals/eval_data_coverage.py` → `data_coverage.json` (real) | reduction > 0 on most trips with median extra ≤ 5 min | whether the lower-risk option is worth showing |
| Data coverage | share of trips with a risk comparison; share of walk/bike distance matched to a way; median unknown lighting on Athena's route | none | real rebuild | `evals/eval_data_coverage.py` → `data_coverage.json` | every trip compared; unknown lighting reported, not hidden | whether a data source (lamps, light poles) is worth adding |
| Report classifier | TNR on hold (review + reject held; wrongly admitting is the severe error) | majority, keyword rule | `data/reports/dev.jsonl`, then test once | `evals/eval_classifier.py` → `classifier_dev.json`, `classifier_test.json` | TNR ≥ 0.95 on hold, every reject held | which classifier the app uses (`REPORT_CLASSIFIER`) |
| | TPR on admit; category macro-F1; cost and latency per report | same | same | same | TPR ≥ 0.80; cheapest candidate within 0.02 TNR of the best | tie-breakers |
| Labels | Cohen's kappa for action and category on double-labelled reports | none | `data/labelling/reports_to_label.csv` | `make labels` → `label_agreement.json` | κ ≥ 0.6 for action | whether the guide needs rewriting before training |
| Explanations: faithfulness | share of real traces passing every assertion | fact-only fallback (passes by construction) | `evals/traces/<run>` | `evals/assertions.py` → `assertions.json` | ≥ 95% | whether the explainer ships |
| | judge TPR and TNR against held-out human PASS/FAIL, malformed counted wrong | rules only | `evals/labels/heldout.json` | `evals/judge.py` → `judge_audit.json` | TNR ≥ 0.9 before the judge may gate anything | whether the model critic or judge can be trusted |
| Explanations: usefulness | blind human preference, LLM explanation vs fact-only (A/B/TIE) | fact-only explanation | 6+ presentations from the trace run | `evals/ab.py` → `ab.json` | LLM preferred in a majority | No model where a rule does as well: if the LLM does not help, ship the fact-only text |
| Model choice | first-draft pass rate, p50/p95 latency, tokens, cost per call, per role | the cheapest model | 18+ trips; dev reports | `evals/compare_models.py` → `model_comparison.json` | cheapest model within 0.05 pass rate of the best | `EXPLAINER_MODEL`, `CRITIC_MODEL`, `MODERATOR_MODEL` |
| Security | injection precision and recall (direct and indirect); no prompt text in any output | none | `evals/guard/injection.jsonl`, `tests/test_leakage.py` | `make eval`, `make test` | recall ≥ 0.9, precision ≥ 0.95, zero leaks | release gate |
| Operations | p50/p95 latency, error rate, cost per request, cache hit share on the deployed URL | none | `scripts/load_test.py`, 50 requests per endpoint | `make load-test` → `ops.json` | p95 for /api/explain ≤ 10 s; errors ≤ 1% | model tier, cache, host |
| Data | `pipeline.check` passes; frozen test checksum unchanged | none | `data/processed` | `make check` | always | release gate |
| Report guardrail, end to end (added 18 Nov 2026, before its first live run) | injection admit rate: injected reports approved or redacted by guard + classifier + verifier | guard + keyword rule (no model) | held-out guard probes; red-team attempts when collected | `evals/eval_injection_e2e.py [--live]` → `injection_e2e.json` | 0 admitted | whether the classifier model is a real second layer |
| Security, held out (added 18 Nov 2026) | guard recall / precision / false-block rate on sets it was never tuned on | no guard; one-phrase filter | `evals/guard/heldout_probes_v1.jsonl` (frozen), `redteam.csv` | `make eval` → `offline.json` (`guard.sets`) | as Security above | whether the guard can be relied on alone |
| Route choice vs heuristics (added 18 Nov 2026) | median risk reduction and extra minutes of each ranking's first pick | fastest, least walking, fewest transfers, most lit | every trip in the served data | `evals/eval_route_baselines.py` → `route_baselines.json` | balanced beats most-lit at ≤ the same extra time | whether the risk rule adds anything over "stay on lit streets" |
| Privacy (descriptive) | PII span recall and over-redaction, app path and ingest | no redaction | `evals/privacy/pii_cases_v1.jsonl` (synthetic regression set) | `evals/eval_pii.py` → `pii.json` | none set in advance | which PII kinds need another safeguard |
| Data repeatability | rebuild of `data/processed` from `data/raw` has identical content hashes | none | `data/raw` snapshots (`pipeline.fetch_raw`) | `make data-verify` → `data_rebuild.json` | identical | release gate for the data |
| Completion | every demonstrated feature passes end to end | none | the served data, offline and on the deployed URL | `scripts/verify_demo.py` → `verify.json` | all pass | release gate for the demo |

## Rules

- A metric's definition, data and target are set before its first run; changing them afterwards needs a dated note
  next to the metric in this file saying what changed and why.
- The report test split is touched once per release and is checksummed.
- Every number quoted in the design doc or the README traces to a file in `evals/results/`.
- Every rate is reported with its 95% Wilson interval (`evals/metrics.py`); the sets are small.
- Results: [evaluation.md](evaluation.md) (generated; `make check` fails if it is out of date).
