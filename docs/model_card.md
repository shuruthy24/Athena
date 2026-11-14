# Model card: Athena

Covers every model-backed component. Results live in `evals/results/`; entries that say "not run yet" are
waiting for the team's live runs.

## Components and models

| Role | What it does | Model (provisional until `model_comparison.json`) | No-model baseline |
| --- | --- | --- | --- |
| Explainer | Drafts 2-4 claims, each citing fact keys, about the fastest vs lower-risk route | `scx-gpt-oss-120b` (`EXPLAINER_MODEL`) | fact-only explanation built from the facts |
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
| Explainer first-draft pass rate per model | not run yet (`evals/compare_models.py`) |
| Assertions on real traces | not run yet (`evals/assertions.py`) |
| Judge TPR/TNR vs held-out human labels | not run yet (`evals/judge.py`) |
| Blind A/B, LLM vs fact-only | not run yet (`evals/ab.py`) |
| Report classifier TNR on hold / TPR on admit | not run yet (`evals/eval_classifier.py`) |
| Critic rules on generated corruptions | `evals/results/offline.json` (tests the rules, not model drafts) |

## Ethical considerations

- **Who it is for:** people, often women, travelling home at night. The stakes of a wrong "lower risk" message
  are real, so every claim is checked against facts and the fallback is a fact-only explanation.
- **Reports can encode prejudice.** Reports that blame groups of people are rejected; this is the guardrail the
  classifier is measured on first (TNR on hold).
- **Risk indicators can stigmatise places.** The rule uses lighting, path type, venues open and lamp density,
  not crime counts or demographics. Unknown lighting stays unknown.
- **Privacy:** no accounts; PII is redacted before any model call; rate-limit ids are keyed hashes; report text
  is stored only in the container and lost on redeploy.
- **Sovereignty:** check where each gateway model is hosted before sending user report text to it.

## Caveats

- The evaluation sets so far were written by the people who built the system, which flatters the numbers.
- The street risk rule's weights are a team judgement, not learned (there are no risk labels).
- Explanations and reports are only reachable from a route's detail page; there is no free-text chat.
