# Human labels for the judge audit

We labelled these by hand; none were generated. Each file maps a trace id from `evals/traces/<run>/` to PASS or FAIL:

- `dev.json` (18 traces): used only to read and adjust the judge prompt.
- `heldout.json` (18 different traces): kept away from anyone adjusting the prompt. `python evals/judge.py audit`
  collects every judge verdict first and only then reads these labels to compute TPR and TNR.
- `assignment.json`: which traces went to dev and which to held-out, decided by a seeded shuffle before any label
  was written.

PASS means every claim is supported by its cited facts, no number differs from a fact, no street outside the facts
is named, every comparison points the right way, and nothing implies the route is safe.
