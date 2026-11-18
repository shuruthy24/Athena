"""Does "lamps mapped along a street" agree with OSM's own lit tag? (Option 1, 15 Nov 2026)

  python evals/eval_lit_inference.py          # reads data/processed/streets.parquet (run `make data` first)
                                              # writes evals/results/lit_inference.json

Labels: the streets that have an OSM `lit` tag (lit_osm). The prediction is pipeline.features.infer_lit, which
only ever says "yes" (lamps found) or nothing. The severe error is calling a street lit when OSM says it is not,
so the metric is precision of the inferred "yes".

Acceptance criteria (fixed before the first run; thresholds in pipeline/features.py are not tuned on this):
  precision of inferred "yes" against lit_osm >= 0.90, on at least 30 tagged streets predicted "yes".
Caveat: streets with a lit tag are not a random sample (mappers tag busy, well-surveyed streets more often), so
agreement here may be higher than on the untagged streets the inference is used for.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.data.source import read_table  # noqa: E402
from pipeline.features import LAMP_INFER_MIN, LAMP_INFER_PER_100M, infer_lit  # noqa: E402

MIN_PRECISION = 0.90
MIN_SUPPORT = 30


def agreement(rows: list[dict]) -> dict:
    tagged = [r for r in rows if r.get("lit_osm") in ("yes", "no")]
    untagged = [r for r in rows if r.get("lit_osm") is None]
    pred = [(r, infer_lit(int(r.get("lamps") or 0), r.get("lights_per_100m"))) for r in tagged]
    said_yes = [r for r, p in pred if p == "yes"]
    tp = sum(1 for r in said_yes if r["lit_osm"] == "yes")
    fp = len(said_yes) - tp
    osm_yes = sum(1 for r in tagged if r["lit_osm"] == "yes")
    precision = round(tp / len(said_yes), 3) if said_yes else None
    would_fill = sum(1 for r in untagged if infer_lit(int(r.get("lamps") or 0), r.get("lights_per_100m")) == "yes")
    passed = precision is not None and precision >= MIN_PRECISION and len(said_yes) >= MIN_SUPPORT
    return {
        "streets": len(rows), "tagged": len(tagged), "tagged_yes": osm_yes, "tagged_no": len(tagged) - osm_yes,
        "predicted_yes_on_tagged": len(said_yes), "true_positive": tp, "false_positive_lit_but_osm_no": fp,
        "precision": precision, "recall_on_osm_yes": round(tp / osm_yes, 3) if osm_yes else None,
        "untagged": len(untagged), "untagged_that_would_become_lit": would_fill,
        "false_positives": sorted(r["name"] for r, p in pred if p == "yes" and r["lit_osm"] == "no")[:20],
        "criteria": {"min_precision": MIN_PRECISION, "min_support": MIN_SUPPORT}, "passed": passed,
    }


def main() -> int:
    folder = ROOT / "data" / "processed"
    rows = read_table(folder, "streets")
    if not rows or "lit_osm" not in rows[0]:
        print("data/processed/streets.parquet has no lit_osm column: run `make data` first")
        return 2
    res = agreement(rows)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8")) if (folder / "manifest.json").exists() else {}
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None,
           "config": {"lamp_infer_min": LAMP_INFER_MIN, "lamp_infer_per_100m": LAMP_INFER_PER_100M,
                      "raw": manifest.get("raw")},
           "calls_used": 0, **res}
    path = ROOT / "evals/results/lit_inference.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"tagged streets {res['tagged']} (yes {res['tagged_yes']}, no {res['tagged_no']}); "
          f"lamps say yes on {res['predicted_yes_on_tagged']}: precision {res['precision']}, "
          f"recall {res['recall_on_osm_yes']}, false 'lit' {res['false_positive_lit_but_osm_no']}")
    print(f"untagged streets {res['untagged']}: {res['untagged_that_would_become_lit']} would become lit")
    print(f"criteria precision >= {MIN_PRECISION} on >= {MIN_SUPPORT}: {'PASS' if res['passed'] else 'FAIL'}")
    print(f"written to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
