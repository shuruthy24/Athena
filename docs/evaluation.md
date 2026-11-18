# Athena evaluation: method, data, baselines and results

This page says what Athena has to get right, how each part is measured, against what baseline, on which data, and
what the numbers are. The results table is **generated** from `evals/results/` by `scripts/build_eval_report.py`,
and `make check` fails if it is out of date, so no number here is typed by hand. Metric definitions and targets
were written in [metrics.md](metrics.md) before each evaluation first ran.

## Reproduce

No key, no server, any OS (Python 3.12 or 3.13):

```bash
python scripts/check.py          # = make check: tests, regenerate and compare every offline result, frozen sets, secrets, data checks, docs in sync
python scripts/reproduce.py      # only the "regenerate and compare" part, with one line per result file
python scripts/eval_all.py       # = make eval: rewrite the offline results and this page
python scripts/verify_demo.py    # every demonstrated feature end to end
```

`scripts/reproduce.py` re-runs every offline evaluation into a temporary folder and compares each number with the
committed file. It ignores timestamps and latencies, re-scores the saved live traces offline, and lists what needs
the course key, human labels, a deployed URL or the raw data, with the command for each. With the raw snapshots
present (`python -m pipeline.fetch_raw`), it also rebuilds `data/processed/` and compares content hashes
(`python -m pipeline.verify`).

## What Athena must get right, and how each part is judged

| Capability | What goes wrong for a user if it fails | Success criterion (metrics.md) |
| --- | --- | --- |
| Route choice | a "lower-risk" option that costs a lot, or that is not actually different | a lower risk indicator at a median of 5 extra minutes or less |
| Explanation faithfulness | a claim that is not true of the route, or that calls it safe | ≥ 95% of real traces pass every assertion; a judge trusted only at TNR ≥ 0.9 |
| Explanation usefulness | text that is accurate but no better than the facts alone | the LLM text is preferred in blind A/B, or the fact-only text ships (Rules of ML #1) |
| Report guardrail | a prejudiced or injected report changes a route | TNR on hold ≥ 0.95; injected reports admitted = 0 |
| Privacy | a phone number, address or link reaches a model or the queue | measured span recall, with known gaps stated |
| Data | the app serves stale, duplicated or out-of-area data | `pipeline.check` passes, quality recorded, rebuild identical |
| Outcome | people do not choose the lower-risk option, or think it is "safe" | a higher choice share than a plain list; misreading rate 0 |
| Operations and completion | the demo cannot be run or verified | every demonstrated feature verified; p95 ≤ 10 s on the deployed URL |

## Evaluation data: real or synthetic, and what it may be used for

| Set | Real or synthetic | Role | Size | Where |
| --- | --- | --- | --- | --- |
| Precomputed trips (OSM 15 Nov + OTP over TfNSW GTFS) | **real** | evaluation of route choice; the rule's weights were set by team judgement while looking at this data (no risk labels exist), so it is not held out | 377 trips | `data/processed/` (`make data`) |
| Hand-made hackathon routes | synthetic (hand-assigned risks) | **regression set only**: keeps `make eval` deterministic; not evidence about streets | 18 cases | `data/*.json` |
| Explainer traces | **real** model outputs on sample routes | error analysis, assertions, judge audit, A/B | 36 | `evals/traces/2026-11-14-live-scx-gemma-4-31B-it/` |
| Model comparison drafts | **real** model outputs | model choice | 3 × 18 | `model_comparison.json` |
| Street reports | **real**, written by the team (`seed`) and collected from others with consent (`collected`) | classifier train / dev / **test (frozen, touched once)**, group split by street and writer, synthetic variants only in train | target 150+ | `data/labelling/` → `data/reports/` |
| Trace labels | **human** PASS/FAIL | judge dev / **held-out** (seeded assignment written before labelling) | 36 | `evals/labels/` |
| Guard dev set | 10 public (Meterless, Apache-2.0) + 16 team-written | **tuning**: the patterns were fixed against it | 26 | `evals/guard/injection.jsonl` |
| Guard held-out probes | synthetic, written after the patterns, labels by construction | **held out, frozen**; never used to tune | 50 | `evals/guard/heldout_probes_v1.jsonl` |
| Red-team attempts | **real**, people outside the team | **held out** | to collect | `evals/guard/redteam.csv` |
| PII cases | synthetic, fictional people | regression set (it motivated the 18 Nov fix, so it is not held out) | 29 texts | `evals/privacy/pii_cases_v1.jsonl` |
| Critic corruptions | synthetic by construction | tests the rules, not real drafts | 276 | generated in `evals/run_evals.py` |
| Task study | **real** people | outcome | 6+ people | `evals/user_study/sessions.csv` |

Synthetic sets are used only where real data cannot exist yet, or to test a rule on purpose: no one has
labelled injection attempts against a street-report app, and PII cannot be collected ethically. Each synthetic set
says who wrote it and how its labels were set, and none of them is used where a real set exists. Held-out sets are
frozen by sha256 in `evals/checksums.txt`; `make check` fails if one changes.

## Baselines

Every model-backed or rule-based component is compared with something simpler on the same data
(AGENTS.md: rule before ML): routes with fastest, least walking, fewest transfers and most-lit; the critic with no
critic and a citation-only check; the guard with no guard and a one-phrase filter; the report pipeline with a
keyword rule and the majority class; the LLM explanation with the fact-only explanation; the classifier models with
the cheapest model; the planner as a whole with a plain fastest-first list shown to people.

## Statistics and honesty rules

- Sets are small (tens of cases), so every rate has a 95% Wilson interval: 16/16 is [0.81, 1.00], not "100%".
- **Route risk is Athena's own rule.** The lower-risk ranking minimises the number it is scored on, so a risk
  reduction is not evidence of safety. The informative comparisons are the extra minutes, whether a one-line
  heuristic (most lit) already does as well, and what people choose in the task study.
- The dev guard numbers are optimistic by construction; the held-out rows are the ones to quote.
- Failure modes and edge cases are tests, not prose: gateway outage and retries, malformed judge output, unknown
  lighting never treated as dark, no route for a pair, unknown streets, injection in report text and OSM tags,
  system-prompt echo and leakage, streets named like numbers, rate limits per client, PII
  (`tests/`; `python scripts/verify_demo.py` for the running app).
- Nothing in `evals/results/` is typed by hand. Labels, A/B choices and study answers come from people, and
  `evals/release.md` is written by the team.

## Results

<!-- eval:start -->

| Capability | Question | Eval set (role) | Metric | Baseline | Result (95% CI) | Target | Verdict | Reproduce |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Route choice | Does Athena's ranking surface a lower-risk option at little extra time? | 6 trip × time cases, sample data (hand-made hackathon sample: regression set, not evidence) | median route-risk reduction vs fastest; median extra minutes (Athena's own rule: see note) | fastest 0%; most-lit heuristic 36.3% for +0.8 min; least walking 0.0% | balanced 44.3% for +0.8 min (differs from fastest on 6/6) | reduction > 0 with median extra ≤ 5 min | **meets** | `python evals/eval_route_baselines.py` → `route_baselines.json` (2026-11-18 @ no git) |
| Route choice | Same question on every real trip (fastest vs Athena's lower-risk variant) | 377 trips, OSM snapshot + OTP precompute (`option4-direct-walk-timed`) | median risk reduction and extra minutes; median unknown lighting | fastest route | differs on 61/377; median reduction 0.0%, +0.0 min; unknown lighting 72.3% | reduction > 0 with median extra ≤ 5 min | **misses** | `python evals/eval_data_coverage.py --label <name> (needs data/processed)` → `data_coverage.json` (2026-11-15 @ a83dd55) |
| Explanation faithfulness | Are the claims users read supported by the route facts? | 36 real explainer traces (evals/traces/2026-11-14-live-scx-gemma-4-31B-it), live model, sample routes | share of traces passing all 9 code assertions | fact-only explanation (passes by construction) | 36/36 = 1.00 | ≥ 0.95 | **meets** | `python evals/assertions.py evals/traces/2026-11-14-live-scx-gemma-4-31B-it` → `assertions.json` (2026-11-14 @ dcfd833) |
| Explanation faithfulness | Can an LLM judge be trusted to flag unfaithful explanations? | held-out human PASS/FAIL labels (evals/labels/heldout.json) | judge TPR / TNR, malformed counted wrong | code assertions | TPR 1.000, TNR n/a | TNR ≥ 0.9 | **misses** | `python evals/label.py traces <run>; python evals/judge.py audit <run>` → `judge_audit.json` (2026-11-18 @ e3f76e7) |
| Explanation usefulness | Do people prefer the LLM explanation to the fact-only one? | 6+ blind presentations from the trace run | human preference A/B/TIE (blind, then revealed) | fact-only explanation | — | LLM preferred in a majority | not run yet | `python evals/ab.py prepare <run>; python evals/ab.py reveal --judge` → `ab.json` |
| Evidence critic rules | Do the deterministic rules catch unsupported claims? | 276 claims, synthetic by construction (5 corruption types) | precision / recall (positive = unsupported) | citation-only check recall 0.210; no critic 0.0 | precision 1.000, recall 1.000 [0.98, 1.00] | descriptive (rules tested on corruptions, not real drafts) | descriptive | `make eval` → `offline.json` (2026-11-18 @ no git) |
| Report classifier | Which classifier holds harmful reports and admits real ones? | team-labelled reports, group split by street and writer; dev for choosing, test once (frozen) | TNR on hold, TPR on admit, category macro-F1, cost, latency | majority class, keyword rule | — | TNR ≥ 0.95, TPR ≥ 0.80 | not run yet | `python evals/label.py reports --as a (then b); make labels; python evals/eval_classifier.py` → `classifier_dev.json` |
| Report guardrail (end to end) | Is an injected report ever admitted by the whole pipeline? | 24 held-out injection probes + 26 benign (synthetic) | injection admit rate (lower is better); benign hold rate | guard + keyword rule (no model) | baseline: guard + keyword rule (no model): 0.792 [0.60, 0.91]; benign held 0.077 | 0 admitted | baseline (reference) | `python evals/eval_injection_e2e.py [--live]` → `injection_e2e.json` (2026-11-18 @ no git) |
| Report guardrail (end to end) | Is an injected report ever admitted by the whole pipeline? | 24 held-out injection probes + 26 benign (synthetic) | injection admit rate (lower is better); benign hold rate | guard + keyword rule (no model) | baseline: guard + offline stub moderator (SAMPLE_MODE, no model): 0.792 [0.60, 0.91]; benign held 0.077 | 0 admitted | baseline (reference) | `python evals/eval_injection_e2e.py [--live]` → `injection_e2e.json` (2026-11-18 @ no git) |
| Report guardrail (end to end) | Is an injected report ever admitted by the whole pipeline? | 24 held-out injection probes + 26 benign (synthetic) | injection admit rate (lower is better); benign hold rate | guard + keyword rule (no model) | live: moderator-v1 on scx-gpt-oss-120b: 0.167 [0.07, 0.36]; benign held 0.077 | 0 admitted | **misses** | `python evals/eval_injection_e2e.py [--live]` → `injection_e2e.json` (2026-11-18 @ 0693b9c) |
| Text guard alone | Does the guard block instructions in report text? | dev: 26 rows (tuning set: the patterns were written and fixed against it) | recall, precision, false-block rate | phrase filter recall 0.125; no guard 0.0 | recall 1.000 [0.81, 1.00], precision 1.000, false-block 0.000 | recall ≥ 0.9, precision ≥ 0.95 (dev: optimistic by construction) | **meets** | `make eval` → `offline.json` (2026-11-18 @ no git) |
| Text guard alone | Does the guard block instructions in report text? | heldout_probes: 50 rows (held-out synthetic probes written after the patterns, never used for tuning) | recall, precision, false-block rate | phrase filter recall 0.000; no guard 0.0 | recall 0.208 [0.09, 0.40], precision 0.833, false-block 0.038 | recall ≥ 0.9, precision ≥ 0.95 | **misses** | `make eval` → `offline.json` (2026-11-18 @ no git) |
| Text guard alone | Does the guard block instructions in report text? | held-out real attempts by people outside the team (evals/guard/README.md) | recall, precision, false-block rate | no guard; phrase filter | — | recall ≥ 0.9, precision ≥ 0.95 | not run yet | `evals/guard/README.md (red-team collection)` → `offline.json` |
| Privacy: PII redaction | Is personal information removed before any model sees a report? | 29 synthetic texts, 17 PII spans (regression set) | span recall; over-redaction of PII-free texts | no redaction (recall 0) | recall 0.824 [0.59, 0.94]; over-redaction 0.167; misses name, student_id | descriptive (set written 18 Nov; before-fix run in pii_before_fixes.json) | descriptive | `python evals/eval_pii.py` → `pii.json` (2026-11-18 @ no git) |
| Model choice (explainer) | Which model drafts passing explanations at the lowest cost? | 18 sample trips per model, live gateway | first-draft pass rate, p50/p95 latency, cost per call | cheapest model | scx-gpt-oss-120b 0.8333; scx-gemma-4-31B-it 1.0; scx-Meta-Llama-3.3-70B-Instruct 0.9444 | cheapest model within 0.05 of the best pass rate | **meets** | `python evals/compare_models.py` → `model_comparison.json` (2026-11-14 @ 341abce) |
| Data quality | Is the served data complete, unique and in range? | sample dataset tables | schema, duplicate keys, out-of-area points, missing share, snapshot age | — | 0 failures, 0 warnings; streets with unknown lighting 0.000 | no failures | **meets** | `python -m pipeline.check --write-quality` → `data_quality.json` (2026-11-18 @ no git) |
| Outcome (people) | Do people choose the lower-risk option when it costs little? | task study, 6+ participants outside the team (docs/user_study.md) | lower-risk choice share (≤ 5 extra min), misreading rate | plain fastest-first list | — | Athena share > baseline share; misreading 0 | not run yet | `python evals/eval_user_study.py` → `user_study.json` |
| Operations | Is the deployed service fast and reliable enough? | 50 requests per endpoint, deployed URL | p50/p95 latency, error rate, cost per request | — | — | p95 ≤ 10 s, errors ≤ 1% | not run yet | `make load-test BASE_URL=...` → `ops.json` |
| Completion | Does every demonstrated feature run from the instructions? | 17 end-to-end checks, sample data, offline | checks passing | — | 17 pass, 0 fail | all pass | **meets** | `python scripts/verify_demo.py` → `verify.json` (2026-11-18 @ no git) |

**Summary:** 6 meet their target, 4 miss it, 5 not run yet, 2 descriptive (no target set in advance), 2 baseline reference rows.

<!-- eval:end -->

## What this round of evaluation found (18 Nov 2026)

- **The text guard overfits the set it was tuned on.** Compare the dev and held-out rows above. The guard is
  the first of three layers, so the number that matters is the end-to-end admit rate with the real classifier
  model: it has not been run live yet. Without a model (the keyword baseline), most held-out injections would be
  admitted. Do not tune the guard on the held-out probes (evals/guard/README.md).
- **PII redaction in the app path was weaker than at ingest.** It is now one policy for both, and the landline
  pattern's bug is fixed (`pii_before_fixes.json` has the earlier run). Names and student ids still pass regex
  redaction: the report form asks people to leave them out, and labellers read every report.
- **On the hand-made sample a one-line "most lit" heuristic gets most of the benefit**, and the balanced ranking
  adds a little more at the same cost. Whether that holds on real data is `python evals/eval_route_baselines.py`
  on `data/processed`.
- **The live model stack passed 8/8 after swapping the judge model.** `python scripts/check_stack.py` found
  `scx-Meta-Llama-3.3-70B-Instruct` returning consistent `502` gateway errors on 18 Nov 2026; switched
  `JUDGE_MODEL` to `scx-GLM-5.3` (a different family from the explainer), which then passed. See
  `evals/results/stack_check.json`.
- **The live, end-to-end injection eval admits some injected reports.** Against the real gateway
  (`moderator-v1` on `scx-gpt-oss-120b`), `python evals/eval_injection_e2e.py --live` found an injection admit
  rate of 16.7% (4/24, 95% CI [6.7%, 35.9%]) and a benign hold rate of 7.7% — the guard is not perfect under
  live conditions, unlike the 1.0 / 1.0 it gets on its own tuning set.
- **The judge audit ran, but TNR is undefined on this sample.** All 36 traces in
  `evals/traces/2026-11-14-live-scx-gemma-4-31B-it/` were hand-labelled PASS/FAIL by the team (18 dev, 18
  held-out); every one came back PASS, so `python evals/judge.py audit` reports TPR=1.0 but TNR=None — there
  were no FAIL cases in the held-out set to test whether the judge can catch a bad explanation. The sample also
  has heavy duplication (the same handful of route/time/mode combinations recur across the 36 traces), so this
  should be read as "no faithfulness violations found in this particular pattern," not as proof the explainer
  never fails: `evals/assertions.py` already found 2 real faithfulness failures (an omitted risk-parity fact)
  in the separate 72-trace run. Re-run the audit against a set known to include FAIL traces before trusting
  its TNR.
- The A/B and task study have not run: they need human choices and participants (docs/STATUS.md lists the
  steps and time).
