# Explanation traces

`python scripts/run_traces.py` writes one JSON per case to `evals/traces/<date>-live/` and adds a row per trace
to `evals/analysis.csv` for the team's error analysis. Commit the live run you analyse: the assertions, judge
audit and blind A/B all point at it. `--offline` runs use the labelled sample stand-in and are not evidence.

Each trace records `trip_used`; `trip_overridden` is always false now that the chat router, which used to read time
words in the question, is gone.

Order of work (Tech 6): vibe verdicts in analysis.csv before reading traces -> error analysis columns ->
`python evals/assertions.py evals/traces/<run>` -> labels in evals/labels/ -> judge audit ->
`python evals/ab.py prepare evals/traces/<run>` -> blind choices committed -> `python evals/ab.py reveal --judge`
-> evals/release.md.
