# Model card: Athena

Covers every model-backed component. Results live in `evals/results/`; entries that say "not run yet" are
waiting for the team's live runs.

## Components and models

| Role | What it does | Model | No-model baseline |
| --- | --- | --- | --- |
| Explainer | Drafts 2-4 claims, each citing fact keys, about the fastest vs lower-risk route | `scx-gemma-4-31B-it` (`EXPLAINER_MODEL`), chosen on first-draft pass rate in `model_comparison.json` | fact-only explanation built from the facts |
| Critic | Flags claims its cited facts do not support | `scx-gpt-oss-120b` (`CRITIC_MODEL`) | deterministic rules (numbers, wording, streets, direction, prompt echo) |
| Report classifier | approve / review / reject and a category for a street report | `moderator-v1` prompt by default; candidates in `athena/classify` (`REPORT_CLASSIFIER`) | majority class, keyword rule |
| Judge (offline only) | PASS/FAIL on explanation faithfulness; A/B/TIE preference | `scx-Meta-Llama-3.3-70B-Instruct` (`JUDGE_MODEL`) | assertions |
| TF-IDF classifier (optional) | the same report task, trained | scikit-learn logistic regression or LinearSVC on word and character n-grams, seed 3110 | majority, keyword |

All calls go through the COMP3110 LiteLLM gateway at temperature 0, non-streaming, with one retry. Prompts:
`prompts/`. Prices: Tech 6 README table; Nova and Bedrock routes are unpriced.

## Intended use

Night trips in the study area (UNSW, CBD, inner west). Athena compares the fastest route with a lower-risk one
and explains the trade-off with facts the user can check. It is advisory: the person decides.

## Out of scope

- Saying a route is safe. Athena shows risk indicators; it never uses "safe", "safer", "safest" or "guaranteed".
- Predicting crime or judging neighbourhoods. Crime counts are deliberately excluded (data_sources.md).
- Live conditions: routes are precomputed timetable plans, and street data is a dated snapshot.
- Emergencies: the emergency card sends people to 000 without any model call.

## Data

- Explanations use only the facts computed from street and route data for that trip (data_sources.md).
- The report classifier is trained and evaluated on team-labelled reports split by street and writer
  (`pipeline/split.py`); the test split is frozen by checksum. **Not labelled yet.**
- Synthetic report variants are used for training volume only, never for evaluation.

## Evaluation

See [metrics.md](metrics.md) for definitions and proposed targets and the README for current results.

| Evaluation | Status |
| --- | --- |
| Explainer first-draft pass rate per model | `model_comparison.json` (3 models, 18 sample trips; rules only) |
| Assertions on real traces | `assertions.json` (36 live traces on the sample data) |
| Judge TPR/TNR vs held-out human labels | not run yet (`evals/judge.py`) |
| Blind A/B, LLM vs fact-only | not run yet (`evals/ab.py`) |
| Report classifier TNR on hold / TPR on admit | not run yet (`evals/eval_classifier.py`) |
| Critic rules on generated corruptions | `evals/results/offline.json` (tests the rules, not model drafts) |
| Text guard on held-out probes (never tuned on) and with no-guard / phrase-filter baselines | `offline.json` → `guard.sets` |
| Injected reports admitted end to end (guard + classifier + verifier) | `injection_e2e.json` (offline baselines; live run not yet) |
| PII redaction before any model call | `pii.json` (before the 18 Nov fix: `pii_before_fixes.json`) |
| Route ranking vs fastest, least walking, fewest transfers, most lit | `route_baselines.json` |

All results, with baselines, 95% intervals and verdicts against the targets: [evaluation.md](evaluation.md).

## How a route is scored

A rule, not a model (there are no risk labels):

- **Street score** per hour (6 pm, 10 pm, 1 am): path type, tunnel, venues open nearby, time of night, and
  lighting: lit +0, **unknown +0.15**, unlit +0.30. Unknown lighting is scored halfway (team decision, 15 Nov
  2026) and still shown as unknown; it is never treated as dark. Lighting comes from OSM `lit` tags, or "lit"
  inferred from mapped street lamps (`evals/results/lit_inference.json`).
- **Route score** = 0.55 × mean + 0.45 × worst stretch. On the real data the mean is weighted by **time** over
  the whole trip: walking and cycling at their street's score, **waiting at a stop** at the score of the nearest
  street, and **time on board** a tram, bus or train at a fixed **0.10** (an assumption below every street score,
  not a measurement).
- **Labels** are relative to the fastest option: lower, similar (within 5%) or higher. The rule's absolute values
  have no ground truth, so the planner does not call a route "high" or "low" risk on its own.

## Ethical considerations

- **Who it is for:** people, often women, travelling home at night. The stakes of a wrong "lower risk" message
  are real, so every claim is checked against facts and the fallback is a fact-only explanation.
- **Reports can encode prejudice.** Reports that blame groups of people are rejected; this is the guardrail the
  classifier is measured on first (TNR on hold).
- **Risk indicators can stigmatise places.** The rule uses lighting, path type, venues open and lamp density,
  not crime counts or demographics. Unknown lighting stays unknown.
- **Privacy:** no accounts; PII (phones, emails, house addresses, links, card numbers, plates) is redacted before
  any model call with one policy for the app and ingest; names are not found by regex, so the form asks people to
  leave them out; rate-limit ids are keyed hashes; report text is stored only in the container and lost on redeploy.
- **Sovereignty:** check where each gateway model is hosted before sending user report text to it.

## Caveats

- The evaluation sets so far were written by the people who built the system, which flatters the numbers.
- The street risk rule's weights are a team judgement, not learned (there are no risk labels). Unknown lighting
  (+0.15) and time on board (0.10) are assumptions, recorded as team decisions.
- Lighting data is thin, so on most real trips the comparison rests on path type, activity and time of night.
- Stock OpenTripPlanner gives one walking route per trip; most real alternatives are public transport options.
- The mean ignores exposure length; waits use timetable times.
- Explanations and reports are only reachable from a route's detail page; there is no free-text chat.
