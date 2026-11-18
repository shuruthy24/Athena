# Text guard evaluation sets

The guard (`athena/guard.py`) blocks instructions hidden in report text before any model sees it. Each set has a
role, and only one set may be used to change the patterns.

| Set | Role | Rows | Origin |
| --- | --- | --- | --- |
| `injection.jsonl` | **dev (tuning)**: the patterns were written and fixed against it, so its 1.0 / 1.0 is optimistic | 26 (16 injections) | 10 Meterless v1 (Apache-2.0, see NOTICE) + 16 written by the team |
| `heldout_probes_v1.jsonl` | **held out, frozen** in `evals/checksums.txt`; never used to tune | 50 (24 injections) | synthetic, written by Claude on 18 Nov 2026 after reading the patterns (adversarial probes, not a blind sample); labels by construction |
| `redteam.csv` | **held out, real**: attempts by people outside the team | not collected yet | see the protocol below |

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

## Red-team collection (real held-out cases)

1. Ask 3–5 people outside the team, with consent, to spend 10 minutes trying to get a street report past Athena
   that tells it to do something ("mark this street low risk", "approve everything", "reveal your prompt"), and to
   write a few normal reports too. They can use any wording or language.
2. Copy each attempt into `evals/guard/redteam.csv` with the columns `id,text,intended_injection,author_group`
   (`intended_injection` = `yes` if they meant it as an instruction to the system; the author decides, not the
   team). `author_group` is pseudonymous (`rt1`, `rt2`, ...).
3. Freeze it: `python -c "from pathlib import Path; from pipeline.lineage import freeze; freeze(Path('evals/guard/redteam.csv'), Path('evals/checksums.txt'), Path('.'))"`.
4. Run `make eval` and `python evals/eval_injection_e2e.py --live`, and commit the results.
