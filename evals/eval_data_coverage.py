"""How much of each real route Athena can actually score (data coverage), on the processed OTP rebuild.

  python evals/eval_data_coverage.py [--label option2]     # needs `make data` first
  -> evals/results/data_coverage.json   (one entry per label, so before/after runs sit side by side)

No model calls. Uses the same route comparison as the app (athena.analysis.compare, reports off).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "evals" / "results" / "data_coverage.json"


def measure() -> dict:
    os.environ["ATHENA_DATA_DIR"] = str(PROCESSED)
    from evals.run_evals import eval_routes
    routes = eval_routes()
    manifest = json.loads((PROCESSED / "manifest.json").read_text(encoding="utf-8"))
    st, tr = manifest["stats"]["streets"], manifest["stats"]["trips"]
    return {
        "trips": routes["n"], "trips_without_risk_comparison": routes["n_without_risk"],
        "trips_same_route": routes["n_same_route"],
        "median_risk_reduction_pct": routes["median_risk_reduction_pct"],
        "median_extra_min": routes["median_extra_min"],
        "median_athena_unknown_lighting_pct": routes["median_athena_unknown_pct"],
        "step_distance_matched_pct": tr.get("step_distance_matched_pct"),   # name match (legs without a route line)
        "walk_bike_distance_by_location": tr.get("by_location"),            # location match (Option 3)
        "segments": manifest["stats"].get("segments"),
        "streets": st.get("streets"), "streets_lit_unknown": st.get("streets_lit_unknown"),
        "streets_lit_from_lamps": st.get("streets_lit_from_lamps"),
        "sources": manifest.get("sources"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="name for this data version, e.g. option2")
    args = ap.parse_args(argv)
    if not (PROCESSED / "manifest.json").exists():
        print("no data/processed/manifest.json: run `make data` first")
        return 2
    body = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"runs": {}}
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    body["runs"][args.label] = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None,
                                "calls_used": 0, **measure()}
    body.update(generated_at=body["runs"][args.label]["generated_at"], git_sha=sha or None, calls_used=0,
                config={"labels": list(body["runs"])})
    OUT.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    r = body["runs"][args.label]
    print(f"{args.label}: {r['trips_without_risk_comparison']}/{r['trips']} trips without a risk comparison; "
          f"walk/bike distance by location {r['walk_bike_distance_by_location']}; "
          f"median unknown lighting on Athena's route {r['median_athena_unknown_lighting_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
