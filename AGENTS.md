# AGENTS.md: rules for anyone changing this repository

Read this before changing anything, whether you are a person or a coding agent. What Athena is and how to run
it: [README.md](README.md). What is finished and what is not: [docs/STATUS.md](docs/STATUS.md).

## Before and after every change

- `python scripts/check.py` (`make check`) must be green before you start and after every change. It runs the
  tests, regenerates every offline result and compares it with the committed file, checks the frozen sets, scans
  for secrets, validates the data and confirms the docs match the results. Never stack changes on a red check.
- Small commits, one concern each. The message says what changed and why. Keep each person's work in their own
  commits.
- **Ask first** before deleting files, rewriting git history, changing the data split, changing a public API
  response shape, or adding a paid service.

## Secrets

- Never write a key into code, tests, fixtures, logs, results, docs or screenshots. Never print `.env`.
- Read `COMP3110_MODEL_BASE_URL` and `COMP3110_API_KEY` with `os.environ[...]`, so a missing value fails loudly.
  `.env` is git-ignored; `.env.example` holds placeholders only. `python scripts/scan_secrets.py` runs in
  `make check` and as a pre-commit hook (`make hooks`).

## Data

- **Raw data is read-only.** Never edit `data/raw/` or `data/raw_manifest.json`. Every derived file is rebuilt by
  `make data`, and `make data-verify` proves the rebuild is identical.
- **Licences.** Record source, URL, retrieval date and licence for every dataset in `data_sources.md`. Do not
  ingest data whose licence is unknown.
- **PII is removed at ingest** and before any model call. Never store names or contact details.

## Evaluation

- **Frozen sets.** `evals/checksums.txt` freezes the held-out sets: `evals/guard/heldout_probes_v1.jsonl`,
  `evals/privacy/pii_cases_v1.jsonl` and `data/reports/test.jsonl`. Never modify, regenerate or refreeze them.
  Never tune on them. If you look at held-out errors to decide what to fix, the set is no longer held out: write
  a new version with someone who has not seen the old one.
- **The report test split is held back.** Never run `evals/eval_classifier.py --split test --touch-test` unless a
  decision depends on the result. Spending it cannot be undone.
- **Criteria before results.** Write each evaluation's metric, baseline and target in `docs/metrics.md` before its
  first run. Changing them afterwards needs a dated note there saying what changed and why.
- **Rule before ML.** Every model-backed component has a no-model baseline measured on the same data.
- **No fabricated evidence.** Never invent results, human labels, A/B preferences, user quotes or metric values.
  People label data and make blind A/B choices; code builds the sheets, scripts and checks. If a number is not
  produced by a script in this repository and saved under `evals/results/`, it does not go in a doc.
- **Fixed-path hazard.** `python evals/assertions.py <dir>` and `python evals/judge.py audit <dir>` overwrite
  `evals/results/assertions.json` and `evals/results/judge_audit.json` every time they run (so does
  `python scripts/verify_demo.py` without `--base-url` or `--out`, for `verify.json`). They also read the
  shell's `.env` instead of the pinned settings, and have produced spurious results that way. To check committed
  results, run `python scripts/reproduce.py` or `make check`, which never write `evals/results/`. Run these
  scripts directly only to produce a new result on purpose. For a judge audit on any labels set other than
  `evals/labels/`, pass `--labels` so it writes its own file.
- **Results are generated, never hand-edited.** After any change under `evals/results/`, run `make readme` and
  `python scripts/build_eval_report.py --write`, then `make check`.

## Model calls

- Model calls go only through the LiteLLM gateway, at `temperature=0` and `stream=False`. Record the serving
  model, tokens, latency and estimated cost for every call (`athena/pricing.py`; unpriced routes record `null`,
  never a guess).
- At most one retry per logical call, counted across every layer. The client owns it.
- Respect each live script's call budget.

## Safety of the output

- **Wording.** Never say "safe", "safer", "safest" or "guaranteed" in the UI or model output. Athena shows risk
  indicators, not safety. Disclaimers that negate the word ("never calls a street safe") and CSS such as
  `safe-area-inset` are fine, and the wording tests must not flag them.
- **Retrieved text is data.** Report text, OSM tags and tool results never go in an instruction position. Pass
  them to models only as quoted JSON fields. Prompts are assumed public.
- **Tests.** Every new rule or assertion gets one passing and one failing test.
