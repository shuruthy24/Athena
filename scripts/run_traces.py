"""Generate real explanation traces for error analysis (AGENTS.md Step 6, Tech 6 workflow). Live: you run this.

  python scripts/run_traces.py                 # live explainer + critic through the gateway, budget 200 calls
  python scripts/run_traces.py --offline       # SampleLLM, to try the workflow; NOT evidence, labelled as such

Runs every trip/band/mode the app serves with two question phrasings (36 cases on the sample data) through
Athena.handle, exactly as /api/ask does, and saves one JSON per case under evals/traces/<run>/:
the case, the facts the explainer saw, the explanation shown to the user, the critic verdict, the full step
trace (served model, tokens, cost, prompt version) and the fact-only explanation for the blind A/B.
It also adds a row per trace to evals/analysis.csv with the human columns left empty.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.agents import Athena  # noqa: E402
from athena.analysis import compare, fallback_explanation, flatten_facts  # noqa: E402
from athena.config import get_settings  # noqa: E402
from athena.data.source import LocalSource  # noqa: E402
from athena.llm import SampleLLM  # noqa: E402
from evals.metrics import BudgetExceeded, CallBudget  # noqa: E402

QUESTIONS = ["Why is the Athena route better tonight?", "Is the Athena option worth the extra time?"]
ANALYSIS = ROOT / "evals" / "analysis.csv"
ANALYSIS_COLS = ["trace_id", "vibe_verdict", "failure_category", "first_bad_step", "consequence", "evaluator_type"]


def cases(src: LocalSource) -> list[dict]:
    rows = src._rows("route_steps.json")
    out = []
    for trip, band, mode in sorted({(r["trip"], r["band"], r["mode"]) for r in rows}):
        o, d = trip.split("|")
        for qi, q in enumerate(QUESTIONS):
            out.append({"case_id": f"{o}-{d}-{band}-{mode}-q{qi}", "question": q,
                        "trip": {"origin": o, "destination": d, "band": band, "mode": mode}})
    return out


def run(athena: Athena, src: LocalSource, out_dir: Path, limit: int | None = None) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    streets, _ = src.streets()
    saved = []
    for c in cases(src)[:limit]:
        # One eval user per case: Scout rate-limits a single user, as it should.
        r = athena.handle(c["question"], trip=c["trip"], user={"id": f"eval-{c['case_id']}", "role": "traveller"})
        # Facts for the trip Athena actually explained. Scout reads time words in the question ("tonight" is
        # the 10 pm band), which can differ from the trip passed in; both are saved so analysis can see it.
        used = {k: (r.get("trip") or {}).get(k) or c["trip"][k] for k in ("origin", "destination", "band", "mode")}
        steps, _ = src.route_steps(used["origin"], used["destination"], used["band"], used["mode"])
        cmp = compare(steps, streets, used["band"])  # same settings as the app, report penalties included
        head, claims = fallback_explanation(cmp)
        model_steps = [s for s in r["trace"] if s.get("model")]
        rec = {"trace_id": r["traceId"], "case": c, "trip_used": used,
               "trip_overridden": used != c["trip"], "facts": flatten_facts(cmp),
               "explanation": {"headline": r.get("answer"), "claims": r.get("claims", [])},
               "critic_verdict": r.get("critic_verdict"), "intent": r.get("intent"),
               "fact_only": {"headline": head, "claims": claims}, "trace": r["trace"],
               "models": sorted({s["model"] for s in model_steps}),
               "usage": {"calls": len(model_steps), "tokens_in": sum(s.get("tokens_in") or 0 for s in model_steps),
                         "tokens_out": sum(s.get("tokens_out") or 0 for s in model_steps),
                         "cost_usd": None if any(s.get("cost_usd") is None for s in model_steps)
                         else round(sum(s["cost_usd"] for s in model_steps), 6)}}
        (out_dir / f"{rec['trace_id']}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        saved.append(rec)
    return saved


def add_to_analysis(trace_ids: list[str], path: Path = ANALYSIS) -> None:
    existing = set()
    if path.exists():
        with path.open(newline="") as fh:
            existing = {r["trace_id"] for r in csv.DictReader(fh)}
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ANALYSIS_COLS)
        if new:
            w.writeheader()
        for tid in trace_ids:
            if tid not in existing:
                w.writerow({"trace_id": tid})  # human columns stay empty


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--budget", type=int, default=200)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    s = get_settings()
    src = LocalSource()
    if args.offline:
        llm, label = SampleLLM(), "offline-sample-not-evidence"
    else:
        from athena.llm import GatewayLLM
        llm = CallBudget(GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"],
                                    timeout_s=s.llm_timeout_s, fallback_model=s.fallback_model), args.budget)
        label = "live"
    run_name = f"{datetime.now(timezone.utc):%Y-%m-%d}-{label}"
    out_dir = ROOT / "evals" / "traces" / run_name
    try:
        saved = run(Athena(s, llm, src), src, out_dir, args.limit)
    except BudgetExceeded as exc:
        print(f"stopped: {exc}; traces saved so far are in {out_dir.relative_to(ROOT)}")
        saved = [json.loads(p.read_text()) for p in sorted(out_dir.glob("*.json"))]
    if not args.offline:
        add_to_analysis([r["trace_id"] for r in saved])
    print(f"{len(saved)} traces in {out_dir.relative_to(ROOT)}; "
          f"calls used {getattr(llm, 'used', 0)}; verdicts "
          f"{sorted({r['critic_verdict'] for r in saved if r['critic_verdict']})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
