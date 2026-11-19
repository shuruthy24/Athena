# Report classifier probes

The labelled report splits (`data/reports/`) have no dev or test reports that should be held, so the classifier's
main metric (TNR on hold) can't be computed on them. This probe set fills that gap until more real reports are
collected.

`hold_probes_v1.jsonl` has 30 short reports: 10 that should be **rejected** (they blame groups of people instead
of describing the street), 10 that should go to **review** (vague, about a different place, or asking the system to
do something), and 10 concrete street reports that should be **approved**.

- **Synthetic:** AI-generated on 19 Nov 2026, labels by construction, written after the classifiers already
  existed. They are not real reports, so they show whether a classifier separates these cases, not how it does on
  real people's writing.
- **Held out:** never used for training or for tuning any classifier or prompt. Don't edit rows to fix a failure;
  write a `v2` instead.
- Streets were chosen from those in both the sample and the real data, and the approve rows make no lighting
  claims, so the verifier's street-data check gives the same result on either dataset.

Run it with `python evals/eval_classifier.py --split probes --offline` (no key; majority, keyword and TF-IDF), or
without `--offline` to add the prompted classifiers (needs the gateway key). Results: `evals/results/classifier_probes.json`.
