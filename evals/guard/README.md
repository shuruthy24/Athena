# Text guard evaluation sets

The guard (`athena/guard.py`) blocks instructions hidden in report text before any model sees it. Each set has a
role, and only one set may be used to change the patterns.

| Set | Role | Rows | Origin |
| --- | --- | --- | --- |
| `injection.jsonl` | **dev (tuning)**: the patterns were written and fixed against it, so its 1.0 / 1.0 is optimistic | 26 (16 injections) | 10 Meterless v1 (Apache-2.0, see NOTICE) + 16 written by the team |
| `heldout_probes_v1.jsonl` | **held out, frozen** in `evals/checksums.txt`; never used to tune | 50 (24 injections) | synthetic, AI-generated on 18 Nov 2026 after reading the patterns (adversarial probes, not a blind sample); labels by construction |

`make eval` scores the guard and two baselines (no guard; a filter for the phrase "ignore previous instructions")
on every set present, with 95% Wilson intervals (`evals/results/offline.json` → `guard.sets`).
`python evals/eval_injection_e2e.py [--live]` measures what users care about: whether an injected report is
**admitted** by the whole pipeline (guard, then classifier, then verifier).

## If you change the guard

Add the failure you are fixing to `injection.jsonl` (dev) in your own words, fix the pattern, and rerun `make eval`.
Do not copy rows from the held-out sets. After a change, the held-out numbers are still honest for that change,
but if you look at held-out errors to decide what to fix, the set stops being held out: write `heldout_probes_v2`
(someone who has not seen v1), freeze it with `python -c "from pipeline.lineage import freeze; ..."` (see
`pipeline/lineage.py`) and note the change in `docs/evaluation.md`.
