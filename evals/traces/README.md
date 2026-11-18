# Explanation traces

`python scripts/run_traces.py` (needs the gateway key) saves one JSON file per trip to
`evals/traces/<date>-live-<explainer model>/` and adds a row per trace to `evals/analysis.csv` for error analysis.
`--offline` runs use the sample stand-in and are not evidence.

How we used a run: error analysis in `analysis.csv`, then the code checks (`evals/assertions.py`), then human
labels in `evals/labels/` and the judge audit, then the blind A/B (`evals/ab.py`). To re-check the committed
results, use `python scripts/reproduce.py`; running `evals/assertions.py` directly overwrites
`evals/results/assertions.json`.
