# Human labels for the judge audit

Written by the team, never by an agent (AGENTS.md section 2). Label saved traces from `evals/traces/<run>/`:

- `dev.json`: `{"<trace_id>": "PASS" | "FAIL", ...}` for about 20 traces, used only to read and tune the judge rubric.
- `heldout.json`: the same shape for 20 different traces. Do not show these to anyone tuning the rubric.
  `python evals/judge.py audit evals/traces/<run>` reads the ids first, collects every judge verdict, and only
  then reads the labels to compute TPR and TNR.

PASS means every claim is supported by its cited facts, no number differs from a fact, no street outside the
facts is named, every comparison points the right way, and nothing implies the route is safe.
