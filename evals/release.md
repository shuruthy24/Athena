# Athena release memo

<!-- Filled in by the team (AGENTS.md Step 6, Tech 6 section 5). 250-400 words, tables excluded.
Every number must come from a file in evals/results/ (name the file). Rewrite this memo whenever a prompt,
model or tool changes. Do not let an agent fill in results, labels or preferences. -->

**Release:** git `<sha>` · prompts `<explainer.vN@sha, critic.vN@sha, moderator.vN@sha>` · models `<explainer / critic / moderator / judge>`

## Vibe check against error analysis

<!-- Vibe verdicts from evals/analysis.csv before reading traces, then what the traces showed. -->

| Failure category | Count | First bad step (typical) | Evaluator (code / human / llm) |
| --- | --- | --- | --- |
| | | | |

## Assertions (evals/results/assertions.json)

| Assertion | Passed | Failed |
| --- | --- | --- |
| | | |

## Judge audit (evals/results/judge_audit.json)

TPR: · TNR: · malformed: · held-out cases:

| Trace | Human label | Judge verdict | Decisive evidence | Why the judge may have failed |
| --- | --- | --- | --- | --- |
| | | | | |

## Blind A/B: LLM explanation vs fact-only (evals/results/ab.json)

| Presentation | Preference | Rationale | Revealed: preferred |
| --- | --- | --- | --- |
| | | | |

## Cost and latency (evals/results/model_comparison.json, ops.json)

## What code, humans and the LLM judge each measure and miss

## Recommendation: SHIP / DO NOT SHIP

<!-- One clear decision, the evidence behind it, and one next engineering action. Rules of ML #1: if the
blind A/B does not show the LLM explanation helping, say so and consider shipping the fact-only one. -->
