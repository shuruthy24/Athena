"""Run the sample inputs through the agent (SAMPLE_MODE by default) and save the outputs.

  python3 scripts/make_samples.py            # sample mode -> samples/outputs_sample_mode/
  python3 scripts/make_samples.py --live     # live Zetaris + Nightshift -> samples/outputs_live/
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("--live", action="store_true")
args = ap.parse_args()
os.environ["SAMPLE_MODE"] = "false" if args.live else "true"

from athena.server import build_athena  # noqa: E402

inputs = json.loads((ROOT / "samples/inputs.json").read_text())
out_dir = ROOT / "samples" / ("outputs_live" if args.live else "outputs_sample_mode")
out_dir.mkdir(parents=True, exist_ok=True)
athena = build_athena()
for case in inputs:
    resp = athena.handle(case["message"], trip=case.get("trip"))
    (out_dir / f"{case['id']}.json").write_text(json.dumps({"input": case, "output": resp}, indent=1, ensure_ascii=False) + "\n")
    print(f"{case['id']:28} intent={resp['intent']:16} {resp['answer'][:70]}")
