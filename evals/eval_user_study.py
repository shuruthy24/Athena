"""Task study analysis: do people choose the lower-risk option when it costs them little? (docs/user_study.md)

  python evals/eval_user_study.py      # reads evals/user_study/sessions.csv -> evals/results/user_study.json

The outcome metric in docs/metrics.md, measured with real people rather than by the system's own rule. Each
participant does the same trips in two conditions, in counterbalanced order: "baseline" (a plain list, fastest
first, no risk information: what a standard journey planner shows) and "athena" (the planner). Metrics, fixed
before the first session:

  lower-risk choice share   tasks where the lower-risk option was chosen / tasks, per condition (Wilson 95% CI)
  ... when cheap            the same, restricted to tasks where the lower-risk option costs <= 5 extra minutes
  extra minutes accepted    median extra minutes of the chosen option over the fastest, per condition
  misreading rate           participants who said the app called a route "safe" / participants (athena only;
                            target 0: Athena shows risk indicators, never safety)

Rows without consent=yes are dropped before anything is computed. Names never go in the sheet.
"""
from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.metrics import rate, wilson  # noqa: E402

SHEET = ROOT / "evals" / "user_study" / "sessions.csv"
OUT = ROOT / "evals" / "results" / "user_study.json"
CHEAP_MIN = 5.0


def analyse(rows: list[dict]) -> dict:
    rows = [r for r in rows if r.get("consent", "").strip().lower() == "yes"]
    out = {"participants": len({r["participant_group"] for r in rows}), "tasks": len(rows), "conditions": {}}
    for cond in ("baseline", "athena"):
        rs = [r for r in rows if r["condition"] == cond]
        if not rs:
            continue
        chose = [r["chosen"] == "lower_risk" for r in rs]
        cheap = [r["chosen"] == "lower_risk" for r in rs if float(r["lower_risk_extra_min"] or 0) <= CHEAP_MIN]
        extra = [float(r["lower_risk_extra_min"] or 0) if r["chosen"] == "lower_risk" else 0.0 for r in rs]
        c = {"tasks": len(rs), "lower_risk_choice_share": rate(sum(chose), len(chose)),
             "lower_risk_choice_share_ci95": wilson(sum(chose), len(chose)),
             "lower_risk_choice_share_when_cheap": rate(sum(cheap), len(cheap)),
             "lower_risk_choice_share_when_cheap_ci95": wilson(sum(cheap), len(cheap)),
             "median_extra_min_accepted": round(statistics.median(extra), 2) if extra else None}
        if cond == "athena":
            people = {}
            for r in rs:
                people.setdefault(r["participant_group"], []).append(r.get("understood_not_safe", "").strip().lower())
            mis = sum(any(v == "no" for v in vs) for vs in people.values())
            c.update(misreading_rate=rate(mis, len(people)), misreading_rate_ci95=wilson(mis, len(people)))
        out["conditions"][cond] = c
    return out


def main() -> int:
    if not SHEET.exists():
        print(f"{SHEET.relative_to(ROOT)} not found: run the sessions in docs/user_study.md first "
              "(copy sessions_template.csv to sessions.csv)")
        return 2
    with SHEET.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None, "calls_used": 0,
           "config": {"sheet": str(SHEET.relative_to(ROOT)), "cheap_threshold_min": CHEAP_MIN}, **analyse(rows)}
    OUT.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res["conditions"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
