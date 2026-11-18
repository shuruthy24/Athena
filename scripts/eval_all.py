"""`make eval` without make (Windows, macOS, Linux): regenerate every offline result and the generated docs.

  python scripts/eval_all.py

Pinned to the hand-made sample data and SAMPLE_MODE, so the regression results are identical on any machine and
no model is called. Results on the real data: `make eval-real` (or the commands it lists). Writes only files in
evals/results/, the README results table and the results block of docs/evaluation.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEPS = [
    ["evals/run_evals.py"],
    ["evals/eval_pii.py"],
    ["evals/eval_route_baselines.py"],
    ["evals/eval_injection_e2e.py"],
    ["-m", "pipeline.check", "--write-quality"],
    ["scripts/results_table.py", "--write"],
    ["scripts/build_eval_report.py", "--write"],
]


def main() -> int:
    env = {**os.environ, "SAMPLE_MODE": "true", "ATHENA_DATA_DIR": str(ROOT / "data"), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    for step in STEPS:
        print(f"\n$ python {' '.join(step)}", flush=True)
        r = subprocess.run([sys.executable, *step], cwd=ROOT, env=env,
                           stdout=None if "--write" not in step else subprocess.DEVNULL)
        if r.returncode != 0:
            print(f"eval: step failed: {' '.join(step)}")
            return r.returncode
    print("\neval: offline results, README table and docs/evaluation.md regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
