# Metric tree

Written before the model work it measures (week 1, Rules of ML #2: design and implement metrics first). Each
metric names its data, the script that computes it and the file it lands in. **Targets are proposals for the team
to confirm before running the evaluation**, then left alone: criteria are fixed before results are seen.

## Outcome

**People choose the lower-risk option when it costs them little.** Share of night trips where a person who sees
both options picks Athena's lower-risk route, and the extra minutes they accept for it.

- Data: a task study with target users (accelerator weeks 4, 7, 8): the planner shown on a phone, trips and times
  they actually take, choice and comment recorded. A plain map is the baseline.
- Not automated, and not a generated result: record consent, sessions, choices and quotes (with permission) in
  `docs/user_study.md`. `evals/results/` holds only files written by scripts.

**Guardrail: no report that targets people ever changes a route.** Week 5 baseline-review objective. Measured as
TNR on hold for the report pipeline, with every reject-labelled report held.

## Components

| Component | Metric (definition) | Baseline | Data | Script → file | Proposed target | Decision it informs |
| --- | --- | --- | --- | --- | --- | --- |
| Route choice | median risk-indicator reduction and median extra minutes, Athena vs fastest, over every precomputed trip | fastest route | `data/processed` (sample until `make data`) | `make eval` → `offline.json` | reduction > 0 on most trips with median extra ≤ 5 min | whether the lower-risk option is worth showing |
| Report classifier | TNR on hold (review + reject held; wrongly admitting is the severe error) | majority, keyword rule | `data/reports/dev.jsonl`, then test once | `evals/eval_classifier.py` → `classifier_dev.json`, `classifier_test.json` | TNR ≥ 0.95 on hold, every reject held | which classifier the app uses (`REPORT_CLASSIFIER`) |
| | TPR on admit; category macro-F1; cost and latency per report | same | same | same | TPR ≥ 0.80; cheapest candidate within 0.02 TNR of the best | tie-breakers |
| Labels | Cohen's kappa for action and category on double-labelled reports | none | `data/labelling/reports_to_label.csv` | `make labels` → `label_agreement.json` | κ ≥ 0.6 for action | whether the guide needs rewriting before training |
| Explanations: faithfulness | share of real traces passing every assertion | fact-only fallback (passes by construction) | `evals/traces/<run>` | `evals/assertions.py` → `assertions.json` | ≥ 95% | whether the explainer ships |
| | judge TPR and TNR against held-out human PASS/FAIL, malformed counted wrong | rules only | `evals/labels/heldout.json` | `evals/judge.py` → `judge_audit.json` | TNR ≥ 0.9 before the judge may gate anything | whether the model critic or judge can be trusted |
| Explanations: usefulness | blind human preference, LLM explanation vs fact-only (A/B/TIE) | fact-only explanation | 6+ presentations from the trace run | `evals/ab.py` → `ab.json` | LLM preferred in a majority | Rules of ML #1: if the LLM does not help, ship the fact-only text |
| Model choice | first-draft pass rate, p50/p95 latency, tokens, cost per call, per role | the cheapest model | 18+ trips; dev reports | `evals/compare_models.py` → `model_comparison.json` | cheapest model within 0.05 pass rate of the best | `EXPLAINER_MODEL`, `CRITIC_MODEL`, `MODERATOR_MODEL` |
| Intent routing (Scout) | top-1, top-3 recall, tool precision, clarification rate on the checksummed held-out set | Meterless thresholds | `evals/scout/heldout_v2.jsonl` | `make eval` → `offline.json` | top-1 ≥ 0.92, clarification ≤ 0.15 | whether a model router is needed (so far: no) |
| Security | injection precision and recall (direct and indirect); no prompt text in any output | none | `evals/scout/injection.jsonl`, `tests/test_leakage.py` | `make eval`, `make test` | recall ≥ 0.9, precision ≥ 0.95, zero leaks | release gate |
| Operations | p50/p95 latency, error rate, cost per request, cache hit share on the deployed URL | none | `scripts/load_test.py`, 50 requests per endpoint | `make load-test` → `ops.json` | p95 for /api/ask ≤ 10 s; errors ≤ 1% | model tier, cache, host |
| Data | `pipeline.check` passes; frozen test checksum unchanged | none | `data/processed` | `make check` | always | release gate |

## Rules

- A metric's definition, data and target are set before its first run; changing them afterwards needs a note in
  `evals/release.md` saying what changed and why.
- The report test split and the Scout held-out set are touched once per release and are checksummed.
- Every number quoted in the talk, the design doc or the README traces to a file in `evals/results/`.
