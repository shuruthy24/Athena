"""Route choice against baselines: what each ranking would put first, on every trip in the served data.

  python evals/eval_route_baselines.py      # the active data (data/processed after `make data`, else the sample)
  -> evals/results/route_baselines.json     # one entry per dataset ("sample", "processed"), side by side

Strategies, each picking the first option for a trip (same candidates, same data, reports switched off):
  fastest            baseline: what a standard journey planner shows first
  least_walking      baseline: a common rule of thumb ("walk as little as possible at night")
  fewest_transfers   baseline: avoid waiting at stops
  most_lit           baseline: highest share of lit street distance (a single-indicator heuristic)
  lower_risk         Athena: lowest route risk indicator, then time
  balanced           Athena (planner default): time and risk normalised and weighted evenly

Metrics per strategy, per mode set (walk / bike / transit / all modes ranked together), fixed before the first run:
share of trips where the pick differs from the fastest; median extra minutes vs the fastest; median route-risk
change vs the fastest (% reduction, positive = lower risk indicator); median lit and unknown-lighting share of the
pick. Read with care: route risk is Athena's own rule, so "lower_risk" wins on it by construction. The useful
comparisons are (a) what the lower-risk pick costs in minutes, (b) whether simple heuristics (most_lit,
least_walking) already get the same reduction, and (c) lit / unknown shares, which are inputs, not the score.
Whether people find the trade-off worth it is not measured (no user study was conducted).
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evals" / "results" / "route_baselines.json"
BASELINES = ("fastest", "least_walking", "fewest_transfers", "most_lit")
ATHENA = ("lower_risk", "balanced")
MODE_SETS = ("walk", "bike", "transit", "walk,bike,transit")
CIRCULARITY = ("Route risk is Athena's own rule (docs/model_card.md), so the lower_risk strategy minimises the "
               "metric it is scored on. Compare minutes, lit and unknown shares, and the heuristics' reductions.")


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def picks_for(journeys: list[dict]) -> dict[str, dict]:
    from athena.journeys import rank
    out = {s: (rank([dict(j) for j in journeys], s) or [None])[0] for s in ("fastest", "least_walking",
                                                                             "fewest_transfers", "lower_risk",
                                                                             "balanced")}
    lit = [j for j in journeys if j["safety"].get("lit_share_pct") is not None]
    out["most_lit"] = max(lit, key=lambda j: (j["safety"]["lit_share_pct"], -j["total_min"])) if lit else None
    return out


def evaluate(source) -> dict:
    from athena.data.source import BANDS
    from athena.journeys import JourneyPlanner
    planner = JourneyPlanner(source)
    pairs = source.provenance()["routable_pairs"] if hasattr(source, "provenance") else []
    rows = []
    for pair in pairs:
        o, d = pair.split("|")
        for band in BANDS:
            for modes in MODE_SETS:
                try:
                    js = planner.plan(o, d, band, modes, sort="fastest", use_reports=False)["journeys"]
                except ValueError:
                    continue
                if not js:
                    continue
                p = picks_for(js)
                fast = p["fastest"]
                for strategy, j in p.items():
                    if j is None:
                        continue
                    fr, jr = fast["safety"]["risk"], j["safety"]["risk"]
                    rows.append({"trip": pair, "band": band, "modes": modes, "strategy": strategy,
                                 "options": len(js), "same_as_fastest": j is fast or j.get("journey") == fast.get("journey")
                                 and j.get("itinerary") == fast.get("itinerary") and j["total_min"] == fast["total_min"],
                                 "extra_min": round(j["total_min"] - fast["total_min"], 2),
                                 "risk": jr, "risk_reduction_pct": (round(100 * (fr - jr) / fr, 1)
                                                                    if fr and jr is not None else None),
                                 "lit_share_pct": j["safety"].get("lit_share_pct"),
                                 "unknown_share_pct": j["safety"].get("unknown_share_pct"),
                                 "walk_m": j.get("walk_m"), "transfers": j.get("transfers")})
    summary = {}
    for modes in MODE_SETS:
        summary[modes] = {}
        for strategy in BASELINES + ATHENA:
            rs = [r for r in rows if r["modes"] == modes and r["strategy"] == strategy]
            if not rs:
                continue
            summary[modes][strategy] = {
                "role": "athena" if strategy in ATHENA else "baseline", "trips": len(rs),
                "differs_from_fastest": sum(not r["same_as_fastest"] for r in rs),
                "median_extra_min": _med([r["extra_min"] for r in rs]),
                "median_risk_reduction_pct": _med([r["risk_reduction_pct"] for r in rs]),
                "mean_risk_reduction_pct": (round(statistics.mean([r["risk_reduction_pct"] for r in rs
                                                                   if r["risk_reduction_pct"] is not None]), 2)
                                            if any(r["risk_reduction_pct"] is not None for r in rs) else None),
                "median_lit_share_pct": _med([r["lit_share_pct"] for r in rs]),
                "median_unknown_share_pct": _med([r["unknown_share_pct"] for r in rs]),
                "trips_without_risk": sum(r["risk"] is None for r in rs)}
    return {"note": CIRCULARITY, "summary": summary, "rows": rows}


def main() -> int:
    from athena.data.source import LocalSource
    src = LocalSource()
    prov = src.provenance()
    res = evaluate(src)
    body = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"datasets": {}}
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat()
    body["datasets"][prov["mode"]] = {"generated_at": now, "git_sha": sha or None,
                                      "data": {k: v for k, v in prov.items() if k != "routable_pairs"}, **res}
    body.update(generated_at=now, git_sha=sha or None, calls_used=0,
                config={"strategies": {"baselines": BASELINES, "athena": ATHENA}, "mode_sets": MODE_SETS})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    allm = res["summary"].get("walk,bike,transit", {})
    print(f"{prov['mode']} data, all modes ranked together:")
    for k, v in allm.items():
        print(f"  {k:17} differs {v['differs_from_fastest']}/{v['trips']}  extra {v['median_extra_min']} min  "
              f"risk reduction {v['median_risk_reduction_pct']}%  lit {v['median_lit_share_pct']}%  "
              f"unknown {v['median_unknown_share_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
