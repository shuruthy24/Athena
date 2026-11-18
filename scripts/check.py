"""`make check` without make (Windows, macOS, Linux). Changes no tracked file; exit 1 if anything fails.

  python scripts/check.py

  tests          pytest (offline, mock gateway)
  reproduce      regenerate every offline result into a temp folder and compare with evals/results/
  frozen sets    sha256 of every held-out set in evals/checksums.txt
  secrets        no credential in any tracked (or, outside git, any) file
  data           pipeline.check on the data the app serves (schema, keys, area, references)
  docs in sync   README results table and docs/evaluation.md match evals/results/
  demo           every demonstrated feature end to end (in-process, offline)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = {**os.environ, "SAMPLE_MODE": "true", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    tmp = tempfile.mkdtemp()
    steps = [
        ("tests", ["-m", "pytest", "-q"]),
        ("reproduce", ["scripts/reproduce.py"]),
        ("frozen sets", ["-m", "pipeline.lineage"]),
        ("secrets", ["scripts/scan_secrets.py"]),
        ("data", ["-m", "pipeline.check"]),
        ("docs in sync", ["scripts/results_table.py", "--check"]),
        ("docs in sync", ["scripts/build_eval_report.py", "--check"]),
        ("demo", ["scripts/verify_demo.py", "--out", str(Path(tmp) / "verify.json")]),
    ]
    results = []
    for name, cmd in steps:
        print(f"\n=== {name}: python {' '.join(cmd)}", flush=True)
        t0 = time.perf_counter()
        r = subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env)
        results.append((name, r.returncode == 0, time.perf_counter() - t0))
    print("\n" + "\n".join(f"{'PASS' if ok else 'FAIL'}  {name:13} {s:5.1f} s" for name, ok, s in results))
    failed = [n for n, ok, _ in results if not ok]
    print("make check: all green" if not failed else f"make check: FAILED ({', '.join(failed)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
