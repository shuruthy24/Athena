# Athena evaluation report

Generated 2026-10-27T12:54:50.705900+00:00 by `python3 evals/run_evals.py`.

All numbers below come from the run that wrote this file. Sets are small; read them as smoke tests, not benchmarks (Meterless says the same of its own 32-example v1 corpus).

## 1. Scout Intent routing

Held-out set: 28 prompts, written before the router was run on them and not used for tuning. Evaluated with the web surface's trip context (UNSW → Town Hall selected), as the app sends it. Injection set: 18 prompts (10 from Meterless v1 adversarial set, Apache-2.0, plus 8 Athena-specific).

| Metric | Value | Meterless target / floor | Status |
|---|---|---|---|
| intent_top1 | 0.821 | 0.92 / 0.85 | BELOW FLOOR |
| intent_top3_recall | 0.964 | 0.98 / 0.94 | above floor |
| injection_precision | 1.000 | 0.95 / 0.9 | meets target |
| injection_recall | 1.000 | 0.9 / 0.82 | meets target |
| tool_precision | 0.750 | 0.88 / 0.8 | BELOW FLOOR |
| clarification_rate | 0.286 | ≤ 0.15 / ≤ 0.25 | ABOVE MAX |

Clarification edge cases correct: 4/4. Stage-1 self-test worst case: 0.018 ms (budget 16 ms).

Misrouted held-out prompts:

- `h-plan-01` expected route.plan, got GENERAL (reject, 0.38)
- `h-rep-01` expected report.submit, got GENERAL (reject, 0.38)
- `h-rep-04` expected report.submit, got GENERAL (reject, 0.16)
- `h-rep-06` expected report.submit, got GENERAL (reject, 0.39)
- `h-emg-03` expected safety.emergency, got GENERAL (reject, 0.18)

Injection errors:

- none

## 2. Evidence Critic (deterministic layer)

276 claims built from the 18 trip/time/mode comparisons; each fact-only claim is tested clean and with five corruptions. Positive class = unsupported claim.

Precision 1.000 · Recall 0.789 · confusion {'tp': 180, 'fp': 0, 'fn': 48, 'tn': 48}

| Case | Accuracy |
|---|---|
| clean | 1.0 |
| number | 1.0 |
| safe | 1.0 |
| street | 1.0 |
| nofacts | 1.0 |
| direction | 0.0 |

## 3. Report moderation (synthetic reports)

14 synthetic reports with expected actions (approve, redact, review, reject). Model: `sample-offline-stub`. Accuracy 0.643.

| Report | Expected | Got | Reason | Scout intent |
|---|---|---|---|---|
| r01 | approve | approve | lighting | report.submit |
| r02 | approve | review | unknown_street | GENERAL |
| r03 | approve | approve | lighting | report.submit |
| r04 | approve | review | unknown_street | GENERAL |
| r05 | approve | approve | lighting | report.submit |
| r06 | approve | approve | harassment | report.submit |
| r07 | approve | review | unknown_street | GENERAL |
| r08 | approve | approve | lighting | report.submit |
| r09 | redact | review | unknown_street | GENERAL |
| r10 | redact | review | unknown_street | GENERAL |
| r11 | reject | reject | prejudiced | GENERAL |
| r12 | review | review | unknown_street | GENERAL |
| r13 | review | review | unknown_street | GENERAL |
| r14 | reject | reject | injection | GENERAL |

## 4. Athena route vs fastest route (baseline), sample data

Data: the pre-existing hand-made sample routes (`web/routes.json`, `source: sample`), not live OTP output. Risk is the app's rule-based street score, so this shows what the system does, not real-world safety.

Across 18 cases: median route-risk reduction 36.3% (range 20.2–46.2%), median extra time 0.8 min (max 2.9 min).

| Trip | Time | Mode | Extra min | Risk reduction % | Fastest lit % | Athena lit % |
|---|---|---|---|---|---|---|
| unsw|townhall | 18 | walk | 0.5 | 31.4 | 77.4 | 100.0 |
| unsw|townhall | 18 | bike | 0.8 | 31.4 | 77.4 | 100.0 |
| unsw|townhall | 18 | transit | 1.9 | 20.2 | 100.0 | 100.0 |
| unsw|townhall | 22 | walk | 0.5 | 46.2 | 77.4 | 100.0 |
| unsw|townhall | 22 | bike | 0.8 | 46.2 | 77.4 | 100.0 |
| unsw|townhall | 22 | transit | 2.9 | 39.7 | 100.0 | 100.0 |
| unsw|townhall | 01 | walk | 0.5 | 36.3 | 77.4 | 100.0 |
| unsw|townhall | 01 | bike | 0.8 | 36.3 | 77.4 | 100.0 |
| unsw|townhall | 01 | transit | 2.9 | 25.9 | 100.0 | 100.0 |
| townhall|unsw | 18 | walk | 0.5 | 31.4 | 77.4 | 100.0 |
| townhall|unsw | 18 | bike | 0.8 | 31.4 | 77.4 | 100.0 |
| townhall|unsw | 18 | transit | 1.9 | 20.2 | 100.0 | 100.0 |
| townhall|unsw | 22 | walk | 0.5 | 46.2 | 77.4 | 100.0 |
| townhall|unsw | 22 | bike | 0.8 | 46.2 | 77.4 | 100.0 |
| townhall|unsw | 22 | transit | 2.9 | 39.7 | 100.0 | 100.0 |
| townhall|unsw | 01 | walk | 0.5 | 36.3 | 77.4 | 100.0 |
| townhall|unsw | 01 | bike | 0.8 | 36.3 | 77.4 | 100.0 |
| townhall|unsw | 01 | transit | 2.9 | 25.9 | 100.0 | 100.0 |
