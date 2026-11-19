# Evaluation

This page explains what Athena needs to get right, how we measured each part, what we compared it against, and
what we found. The results table is generated from `evals/results/` by `scripts/build_eval_report.py`, so no
number in it is typed by hand, and `make check` fails if it is out of date. We wrote each metric and target in
[metrics.md](metrics.md) before running that evaluation.

## Summary

- **Explanations stay faithful to the facts.** All 36 live explanation traces pass every code check, and the
  critic rules catch all 276 planted errors. The LLM judge can't be trusted yet, because our held-out labels have
  no failing examples to test it on.
- **The model's explanation isn't clearly better than the plain fact-only one.** In 6 blind comparisons people
  picked each 3 times.
- **The report guard alone isn't enough.** It catches every attempt in the set we tuned it on, but only 0.21 of
  held-out ones, and 4 of 24 injected reports still got through the full pipeline with the live classifier.
- **The risk score doesn't favour lit streets on real data.** The lower-risk pick barely changes the score and
  has a slightly lower lit share than the fastest route.
- **No report classifier is good enough yet.** The dev split has no reports that should be held, so we added 30
  synthetic probes that include them. On those, the no-model candidates hold at most 0.35 of the reports that
  should be held (the target is 0.95). The prompted classifiers still need to be scored on them with the key.
- **The data and the app pass their checks:** no data-quality failures, and 17 of 17 feature checks pass.

All sets are small (tens of cases), so every rate comes with a 95% confidence interval, and most sets were written
by us.

## How to reproduce

No key or server is needed:

```bash
python scripts/check.py        # everything below, plus tests, data checks and docs in sync (= make check)
python scripts/reproduce.py    # regenerate each offline result and compare it with the committed file
python scripts/eval_all.py     # rewrite the offline results and this page (= make eval)
```

`reproduce.py` reruns every offline evaluation into a temporary folder and compares the numbers with the
committed files (ignoring timestamps and latencies). It rescores the saved live traces offline, and lists the
results that need the gateway key, human labels or the raw data, with the command for each.

## What we measured

| Part | What goes wrong for a user if it fails | Target |
| --- | --- | --- |
| Route choice | the "lower-risk" option costs a lot of time, or isn't really different | lower risk score at a median of 5 extra minutes or less |
| Explanation faithfulness | a claim isn't true of the route, or calls it safe | 95% of real traces pass every check; trust the judge only at TNR ≥ 0.9 |
| Explanation usefulness | the text is accurate but no more useful than the facts alone | people prefer the model text in a blind A/B, otherwise ship the fact-only text |
| Report guardrail | a prejudiced or injected report changes a route | hold harmful reports (TNR ≥ 0.95); admit no injected reports |
| Privacy | personal details reach a model or the stored queue | measured recall, with known gaps stated |
| Data | the app serves stale, duplicated or out-of-area data | `pipeline.check` passes and the rebuild is identical |
| Completion | the demo can't be run or checked | every demonstrated feature passes its check |

We had also planned a user study (do people actually pick the lower-risk option?), but we didn't run it, so that
outcome is not measured.

## Evaluation data

| Set | Real or synthetic | Used for | Size |
| --- | --- | --- | --- |
| Precomputed trips (OSM + OTP over TfNSW GTFS) | real | route choice (not held out: we set the rule's weights while looking at it) | 377 trips |
| Hand-made sample routes | synthetic | fixed regression set for `make eval` only | 18 cases |
| Explainer traces (`evals/traces/2026-11-14-live-scx-gemma-4-31B-it/`) | real model output | checks, judge audit, A/B | 36 |
| Model comparison drafts | real model output | choosing the explainer model | 3 models × 18 |
| Street reports (`data/reports/`) | real: written by us and collected from others with consent | classifier train / dev / test | 49 |
| Trace labels (`evals/labels/`) | human PASS/FAIL | judge dev / held-out | 36 |
| Classifier hold probes (`evals/classifier/hold_probes_v1.jsonl`) | synthetic, AI-generated after the classifiers were written | held out | 30 |
| Guard dev set (`evals/guard/injection.jsonl`) | 10 public (Meterless, Apache-2.0) + 16 ours | tuning the guard | 26 |
| Guard held-out probes (`evals/guard/heldout_probes_v1.jsonl`) | synthetic, AI-generated after the guard was written | held out | 50 |
| PII cases (`evals/privacy/pii_cases_v1.jsonl`) | synthetic, fictional people | regression set | 29 |
| Critic errors | synthetic, generated in `evals/run_evals.py` | testing the critic rules | 276 |

We only used synthetic data where real data can't exist yet or would be unethical to collect (injection attempts,
personal details), and each set says who wrote it.

### Keeping tuning and held-back data separate

| Held-back set | Never used for | How we enforce it |
| --- | --- | --- |
| Guard held-out probes | writing or changing the guard | written after the guard; frozen by sha256 in `evals/checksums.txt`, so `make check` fails if it changes |
| Report test split (`data/reports/test.jsonl`) | training or choosing a classifier | split by street and by writer, so neither appears in two splits; frozen by sha256; `eval_classifier.py --split test` refuses to run without `--touch-test` (it has not been run) |
| Classifier hold probes (`evals/classifier/hold_probes_v1.jsonl`) | training or tuning any classifier or prompt | written after the classifiers; trained candidates only ever see `data/reports/train.jsonl`; edits go in a new `v2` file |
| Held-out trace labels (`evals/labels/heldout.json`) | tuning the judge prompt | trace ids assigned by a seeded shuffle before any labels existed; `judge.py` reads the labels only after all predictions are in |

The guard dev set, the report train and dev splits, and the dev trace labels are the tuning sets. The real trips,
the hand-made sample and the PII cases are not held out, and we say so wherever they are used.

## Baselines

Each part is compared with something simpler on the same data:

- **Routes:** fastest, least walking, fewest transfers and "most lit".
- **Critic:** no critic, and a check that only looks for citations.
- **Guard:** no guard, and a filter for the phrase "ignore previous instructions".
- **Report pipeline:** a keyword rule and the majority class.
- **Explanations:** the fact-only text.
- **Models:** the cheapest model.

## Reading the results

- **The risk score is our own rule.** The lower-risk ranking minimises the same number it's scored on, so a lower
  score isn't evidence of safety. The more useful comparisons are the extra minutes, whether "most lit" does as
  well, and the lit share, which the rule doesn't score.
- **The guard's dev numbers are optimistic** because we tuned on that set; the held-out row is the one to quote.
- **Edge cases are covered by tests** (`tests/`): gateway outages, bad judge output, unknown lighting, missing
  routes, injection in reports and street names, prompt leakage, rate limits and PII.

## Results

<!-- eval:start -->

| Capability | Question | Eval set (role) | Metric | Baseline | Result (95% CI) | Target | Verdict | Reproduce |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Route choice | Does Athena's ranking surface a lower-risk option at little extra time? | 126 trip × time cases, processed data (OSM + OTP, real) | median route-risk reduction vs fastest; median extra minutes (Athena's own rule: see note) | fastest 0%; most-lit heuristic 0.0% for +1.4 min; least walking 0.0% | balanced 0.65% for +0.2 min (differs from fastest on 68/126) | reduction > 0 with median extra ≤ 5 min | **meets** | `python evals/eval_route_baselines.py` → `route_baselines.json` (2026-11-18 @ a5c2601) |
| Route choice | Does the lower-risk pick put more of the trip on lit streets? (not scored by Athena's rule) | same 126 cases, processed data | median lit share and unknown-lighting share of each strategy's pick (inputs, not the risk score) | fastest lit 29.9% / unknown 70.1%; most-lit heuristic lit 51.4% / unknown 47.5% | lower_risk lit 27.0% / unknown 73.0%; balanced lit 25.0% / unknown 75.0% | descriptive (added after the first run, so no target) | descriptive | `python evals/eval_route_baselines.py` → `route_baselines.json` (2026-11-18 @ a5c2601) |
| Route choice | Does Athena's ranking surface a lower-risk option at little extra time? | 6 trip × time cases, sample data (hand-made sample: regression set, not evidence) | median route-risk reduction vs fastest; median extra minutes (Athena's own rule: see note) | fastest 0%; most-lit heuristic 36.3% for +0.8 min; least walking 0.0% | balanced 44.3% for +0.8 min (differs from fastest on 6/6) | reduction > 0 with median extra ≤ 5 min | **meets** | `python evals/eval_route_baselines.py` → `route_baselines.json` (2026-11-18 @ 6517634) |
| Route choice | Does the lower-risk pick put more of the trip on lit streets? (not scored by Athena's rule) | same 6 cases, sample data | median lit share and unknown-lighting share of each strategy's pick (inputs, not the risk score) | fastest lit 77.4% / unknown 0.0%; most-lit heuristic lit 100.0% / unknown 0.0% | lower_risk lit 100.0% / unknown 0.0%; balanced lit 100.0% / unknown 0.0% | descriptive (added after the first run, so no target) | descriptive | `python evals/eval_route_baselines.py` → `route_baselines.json` (2026-11-18 @ 6517634) |
| Route choice | Same question on every real trip (fastest vs Athena's lower-risk variant) | 377 trips, OSM snapshot + OTP precompute (`option4-direct-walk-timed`) | median risk reduction and extra minutes; median unknown lighting | fastest route | differs on 61/377; median reduction 0.0%, +0.0 min; unknown lighting 72.3% | reduction > 0 with median extra ≤ 5 min | **misses** | `python evals/eval_data_coverage.py --label <name> (needs data/processed)` → `data_coverage.json` (2026-11-15 @ a83dd55) |
| Explanation faithfulness | Are the claims users read supported by the route facts? | 36 real explainer traces (evals/traces/2026-11-14-live-scx-gemma-4-31B-it), live model, sample routes | share of traces passing all 9 code assertions | fact-only explanation (passes by construction) | 36/36 = 1.00 | ≥ 0.95 | **meets** | `python evals/assertions.py evals/traces/2026-11-14-live-scx-gemma-4-31B-it` → `assertions.json` (2026-11-14 @ dcfd833) |
| Explanation faithfulness | Can an LLM judge be trusted to flag unfaithful explanations? | held-out human PASS/FAIL labels (evals/labels/heldout.json) | judge TPR / TNR, malformed counted wrong | code assertions | TPR 1.000, TNR n/a | TNR ≥ 0.9 | **misses** | `python evals/label.py traces <run>; python evals/judge.py audit <run>` → `judge_audit.json` (2026-11-18 @ 4d61668) |
| Explanation usefulness | Do people prefer the LLM explanation to the fact-only one? | 6+ blind presentations from the trace run | human preference A/B/TIE (blind, then revealed) | fact-only explanation | LLM 3, fact-only 3, tie 0 | LLM preferred in a majority | **misses** | `python evals/ab.py prepare <run>; python evals/ab.py reveal --judge` → `ab.json` (2026-11-18 @ 7218628) |
| Evidence critic rules | Do the deterministic rules catch unsupported claims? | 276 claims, synthetic by construction (5 corruption types) | precision / recall (positive = unsupported) | citation-only check recall 0.210; no critic 0.0 | precision 1.000, recall 1.000 [0.98, 1.00] | descriptive (rules tested on corruptions, not real drafts) | descriptive | `make eval` → `offline.json` (2026-11-18 @ 6517634) |
| Report classifier | Which classifier holds harmful reports and admits real ones? | team-labelled reports, group split by street and writer; dev for choosing, test once (frozen) | TNR on hold, TPR on admit, category macro-F1, cost, latency | majority class, keyword rule | majority n/a/1.000; keyword n/a/1.000; tfidf-logreg n/a/1.000; tfidf-svm n/a/1.000; prompt-zero n/a/1.000; prompt-5shot n/a/1.000 | TNR ≥ 0.95, TPR ≥ 0.80 | **misses** | `python evals/label.py reports --as a (then b); make labels; python evals/eval_classifier.py` → `classifier_dev.json` (2026-11-18 @ 4d61668) |
| Report classifier | Same, on reports that should be held (dev has none) | 30 synthetic probes: 10 reject, 10 review, 10 approve (evals/classifier/, held out) | TNR on hold, TPR on admit | majority class, keyword rule | majority 0.050/1.000; keyword 0.350/1.000; tfidf-logreg 0.200/1.000; tfidf-svm 0.250/1.000 | TNR ≥ 0.95, TPR ≥ 0.80 | **misses** | `python evals/eval_classifier.py --split probes [--offline]` → `classifier_probes.json` (2026-11-19 @ 65be2d4) |
| Report guardrail (end to end) | Is an injected report ever admitted by the whole pipeline? | 24 held-out injection probes + 26 benign (synthetic) | injection admit rate (lower is better); benign hold rate | guard + keyword rule (no model) | baseline: guard + keyword rule (no model): 0.792 [0.60, 0.91]; benign held 0.077 | 0 admitted | baseline (reference) | `python evals/eval_injection_e2e.py [--live]` → `injection_e2e.json` (2026-11-18 @ 6517634) |
| Report guardrail (end to end) | Is an injected report ever admitted by the whole pipeline? | 24 held-out injection probes + 26 benign (synthetic) | injection admit rate (lower is better); benign hold rate | guard + keyword rule (no model) | baseline: guard + offline stub moderator (SAMPLE_MODE, no model): 0.792 [0.60, 0.91]; benign held 0.077 | 0 admitted | baseline (reference) | `python evals/eval_injection_e2e.py [--live]` → `injection_e2e.json` (2026-11-18 @ 6517634) |
| Report guardrail (end to end) | Is an injected report ever admitted by the whole pipeline? | 24 held-out injection probes + 26 benign (synthetic) | injection admit rate (lower is better); benign hold rate | guard + keyword rule (no model) | live: moderator-v1 on scx-gpt-oss-120b: 0.167 [0.07, 0.36]; benign held 0.077 | 0 admitted | **misses** | `python evals/eval_injection_e2e.py [--live]` → `injection_e2e.json` (2026-11-18 @ 0693b9c) |
| Text guard alone | Does the guard block instructions in report text? | dev: 26 rows (tuning set: the patterns were written and fixed against it) | recall, precision, false-block rate | phrase filter recall 0.125; no guard 0.0 | recall 1.000 [0.81, 1.00], precision 1.000, false-block 0.000 | recall ≥ 0.9, precision ≥ 0.95 (dev: optimistic by construction) | **meets** | `make eval` → `offline.json` (2026-11-18 @ 6517634) |
| Text guard alone | Does the guard block instructions in report text? | heldout_probes: 50 rows (held-out synthetic probes written after the patterns, never used for tuning) | recall, precision, false-block rate | phrase filter recall 0.000; no guard 0.0 | recall 0.208 [0.09, 0.40], precision 0.833, false-block 0.038 | recall ≥ 0.9, precision ≥ 0.95 | **misses** | `make eval` → `offline.json` (2026-11-18 @ 6517634) |
| Privacy: PII redaction | Is personal information removed before any model sees a report? | 29 synthetic texts, 17 PII spans (regression set) | span recall; over-redaction of PII-free texts | no redaction (recall 0) | recall 1.000 [0.82, 1.00]; over-redaction 0.167; misses none | descriptive (set written 18 Nov; before-fix run in pii_before_fixes.json) | descriptive | `python evals/eval_pii.py` → `pii.json` (2026-11-18 @ 6517634) |
| Model choice (explainer) | Which model drafts passing explanations at the lowest cost? | 18 sample trips per model, live gateway | first-draft pass rate, p50/p95 latency, cost per call | cheapest model | scx-gpt-oss-120b 0.8333; scx-gemma-4-31B-it 1.0; scx-Meta-Llama-3.3-70B-Instruct 0.9444 | cheapest model within 0.05 of the best pass rate | **meets** | `python evals/compare_models.py` → `model_comparison.json` (2026-11-14 @ 341abce) |
| Data quality | Is the served data complete, unique and in range? | processed dataset tables | schema, duplicate keys, out-of-area points, missing share, snapshot age | — | 0 failures, 0 warnings; streets with unknown lighting 0.836 | no failures | **meets** | `python -m pipeline.check --write-quality` → `data_quality.json` (2026-11-18 @ 62433e8) |
| Data quality | Is the served data complete, unique and in range? | sample dataset tables | schema, duplicate keys, out-of-area points, missing share, snapshot age | — | 0 failures, 0 warnings; streets with unknown lighting 0.000 | no failures | **meets** | `python -m pipeline.check --write-quality` → `data_quality.json` (2026-11-18 @ 6517634) |
| Operations | Is the deployed service fast and reliable enough? | 50 requests per endpoint, deployed URL | p50/p95 latency, error rate, cost per request | — | — | p95 ≤ 10 s, errors ≤ 1% | not run yet | `make load-test BASE_URL=...` → `ops.json` |
| Completion | Does every demonstrated feature run from the instructions? | 17 end-to-end checks, processed data, offline | checks passing | — | 17 pass, 0 fail | all pass | **meets** | `python scripts/verify_demo.py` → `verify.json` (2026-11-18 @ bbcd17b) |

**Summary:** 8 meet their target, 7 miss it, 1 not run yet, 4 descriptive (no target set in advance), 2 baseline reference rows.

<!-- eval:end -->

## Not run, and why

| Result | Why not | What would change that |
| --- | --- | --- |
| `classifier_test.json` (report test split) | A test split is used once, to confirm a decision. Dev and test only contain reports that should be approved, so the main metric (TNR on hold) can't be computed on either, and scoring test couldn't confirm or overturn anything. Hold cases are covered by the synthetic probes instead (`classifier_probes.json`). | more labelled reports that should be held, then choose on dev and score test once |
| `ops.json` (load test) | The system runs locally and isn't deployed. A load test on `localhost` would only measure the laptop. `verify_demo.py --base-url` checks the running app over HTTP instead. | deploy the container, then `make load-test BASE_URL=...` |
| `judge_audit_sample.json` | A second judge audit, on 12 traces that include FAIL labels (`evals/labels-sample/`), was run but its result file was overwritten and never saved, so we don't quote its numbers. | `python evals/judge.py audit evals/traces/2026-11-14-live-sample --labels evals/labels-sample` (needs the gateway key, 6 calls) |

## A warning about three scripts

These scripts overwrite committed results every time they run, and use whatever `.env` the shell has:

- `python evals/assertions.py <dir>` → `assertions.json`
- `python evals/judge.py audit <dir>` → `judge_audit.json` (`--labels` with another folder writes its own file)
- `python scripts/verify_demo.py` without `--base-url` → `verify.json`

To check results, use `reproduce.py` or `check.py`, which never write to `evals/results/`. Only run these scripts
to produce a new result on purpose, then run `make readme` and `python scripts/build_eval_report.py --write`.

## Findings

- **The guard overfits the set it was tuned on.** It catches everything in the dev set but 0.21 of held-out
  probes. It is only the first of three layers, so the number that matters is the end-to-end one: with the live
  classifier, 4 of 24 injected reports (17%, 95% CI 7–36%) were still admitted, compared with 19 of 24 for the
  keyword baseline. We haven't tuned on the held-out probes, so they stay a fair test.
- **The risk score doesn't steer people onto lit streets on real data.** Across 126 trip × time cases, the
  lower-risk pick lowers the score by a median 1.7% for 0.4 extra minutes, but its lit share (27.0%) is below the
  fastest route's (29.9%). A simple "most lit" rule reaches 51.4% for 1.4 extra minutes. Most lighting is unknown,
  so the other terms (path type, activity, time on board) win. On the hand-made sample the two agree, which made
  the rule look better than it is. The weights need revisiting before we can call the score lighting-aware.
- **PII redaction:** the app now uses the same rules as the data pipeline, and also removes student ids and names
  that follow an introduction ("my name is", "my friend", "I'm"). It removes all 17 spans in the regression set,
  but those rules were written against that set, so this is an upper bound; a name on its own still gets through.
- **The judge can't be trusted yet.** On 18 held-out labels it agrees on every PASS, but all 18 are PASS, so we
  can't tell whether it would catch a bad explanation (TNR undefined). Many of the 36 traces repeat the same few
  trips, so "no failures found" is a weak result.
- **The blind A/B was a tie,** 3 to 3 over 6 pairs. The pairwise judge returned unusable output on 3 of the 6 and
  agreed with people on only 1 of the other 3. With 6 pairs this is only indicative.
- **Classifier probes:** on 30 synthetic reports (10 reject, 10 review, 10 approve), every no-model candidate
  admits all the good reports but holds few of the bad ones: majority 0.05, keyword 0.35, TF-IDF 0.20–0.25. The
  keyword rule catches only 2 of the 10 prejudiced reports and TF-IDF catches none, and those are the cases that
  matter most.
  So a rule or small trained model isn't enough here, which is why the app uses a prompted moderator. Its score on
  these probes still needs the key: `python evals/eval_classifier.py --split probes`.
- **Model stack:** `scx-Meta-Llama-3.3-70B-Instruct` kept returning gateway errors, so we switched the judge to
  `scx-GLM-5.3` (a different model family from the explainer); all 8 stack checks then passed
  (`stack_check.json`).
