"""Model comparison in the Tech 2/3 shape (AGENTS.md Step 4). Live: the team runs this.

  python evals/compare_models.py --dry-run                     # plan and budget, no calls
  python evals/compare_models.py                               # default candidates, 200-call budget
  python evals/compare_models.py --models scx-gpt-oss-120b,scx-gemma-4-31B-it --budget 120

Same prompt and settings for every model (temperature 0, stream=False, one request at a time), latency
from time.perf_counter() around the complete response, token usage from the response, and cost from the
course price table (athena/pricing.py; unpriced routes report null, never a guess).

Criteria, fixed before any run (week 1 "metrics first"; Tech 2 "criteria before results"):

  explainer   cases: every trip/band/mode the app can serve (the route data has no train/test split)
              PASS when the first draft (a) parses to {"headline", "claims"} with 2-4 claims, (b) every claim
              passes check_claims (numbers equal cited facts, no banned wording, streets on the routes,
              correct direction, cites facts), and (c) the headline has no banned wording.
              metric: first-draft pass rate. Malformed output is a fail.
  moderator   cases: data/reports/dev.jsonl (human labels; never train or test)
              the model output goes through verify_report exactly as in the app, then
              metrics: TNR on hold (review/reject; a wrongly admitted report is the severe error),
              TPR on admit (approve), macro-F1 on category. Malformed output counts as wrong.

Writes evals/results/model_comparison.json and model_comparison.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.agents import Athena, BANNED, verify_report  # noqa: E402
from athena.analysis import compare, flatten_facts  # noqa: E402
from athena.config import get_settings  # noqa: E402
from athena.data.source import LocalSource  # noqa: E402
from athena.llm import LLMError, SampleLLM, parse_json  # noqa: E402
from athena.pricing import PRICE_SOURCE  # noqa: E402
from athena.prompts import load  # noqa: E402
from evals.metrics import ADMIT, HOLD, BudgetExceeded, CallBudget, latency_summary, macro_f1, tpr_tnr  # noqa: E402

DEFAULT_MODELS = ["scx-gpt-oss-120b", "scx-gemma-4-31B-it", "scx-Meta-Llama-3.3-70B-Instruct", "aws-nova-lite-v1"]
QUESTION = "Why is the Athena route better tonight?"
CATEGORIES = ["lighting", "isolation", "harassment", "infrastructure", "positive", "other"]
DEV = ROOT / "data" / "reports" / "dev.jsonl"


def explainer_cases(src: LocalSource) -> list[dict]:
    steps_all = src._rows("route_steps.json")
    streets, _ = src.streets()
    cases = []
    for trip, band, mode in sorted({(r["trip"], r["band"], r["mode"]) for r in steps_all}):
        steps = [r for r in steps_all if (r["trip"], r["band"], r["mode"]) == (trip, band, mode)]
        cmp = compare(steps, streets, band, use_reports=False)
        if cmp.get("available"):
            cases.append({"id": f"{trip}|{band}|{mode}", "facts": flatten_facts(cmp)})
    return cases


def moderator_cases() -> list[dict]:
    if not DEV.exists():
        return []
    return [json.loads(x) for x in DEV.read_text().splitlines() if x.strip()]


def _usage(rows: list[dict]) -> dict:
    tin = [r["tokens_in"] for r in rows if r.get("tokens_in") is not None]
    tout = [r["tokens_out"] for r in rows if r.get("tokens_out") is not None]
    costs = [r["cost_usd"] for r in rows if r.get("ok_call")]
    priced = costs and all(c is not None for c in costs)
    return {"calls": len(rows), "tokens_in": sum(tin), "tokens_out": sum(tout),
            "cost_usd_total": round(sum(costs), 6) if priced else None,
            "cost_usd_per_call": round(sum(costs) / len(costs), 8) if priced else None,
            "latency": latency_summary([r["ms"] for r in rows if r.get("ms") is not None]),
            "served_by": sorted({r["served_by"] for r in rows if r.get("served_by")}),
            "retried": sum(1 for r in rows if (r.get("attempts") or 1) > 1)}


def _call(llm, model, prompt, payload, role) -> tuple[dict | None, dict]:
    t0 = time.perf_counter()
    try:
        r = llm.chat(model, prompt.body, json.dumps(payload), role=role)
    except LLMError as exc:
        return None, {"ok_call": False, "error": str(exc)[:120], "ms": (time.perf_counter() - t0) * 1000}
    meta = {"ok_call": True, "ms": (time.perf_counter() - t0) * 1000, "served_by": r.model,
            "tokens_in": r.input_tokens, "tokens_out": r.output_tokens, "cost_usd": r.cost_usd, "attempts": r.attempts}
    try:
        return parse_json(r.text), meta
    except (LLMError, ValueError):
        return None, {**meta, "malformed": True}


def run_explainer(llm, model: str, cases: list[dict], checker: Athena) -> dict:
    prompt, rows, passed = load("explainer"), [], 0
    for c in cases:
        out, meta = _call(llm, model, prompt, {"question": QUESTION, "facts": c["facts"], "previous_feedback": None},
                          "explainer")
        ok, problems = False, []
        if out is not None:
            claims = [x for x in out.get("claims", []) if isinstance(x, dict)]
            headline = str(out.get("headline", ""))
            if not 2 <= len(claims) <= 4:
                problems.append(f"{len(claims)} claims")
            problems += [p for r in checker.check_claims(claims, c["facts"]) for p in r["problems"]]
            if BANNED.search(headline):
                problems.append("headline banned wording")
            ok = not problems
        else:
            problems.append("malformed or failed call")
        passed += ok
        rows.append({"case": c["id"], "pass": ok, "problems": problems[:4], **meta})
    return {"prompt": f"{prompt.id}@{prompt.sha}", "n": len(cases),
            "first_draft_pass_rate": round(passed / len(cases), 4) if cases else None,
            **_usage(rows), "rows": rows}


def run_moderator(llm, model: str, cases: list[dict], streets: dict) -> dict:
    prompt, rows = load("moderator"), []
    truth_admit, pred_admit, truth_cat, pred_cat = [], [], [], []
    for c in cases:
        out, meta = _call(llm, model, prompt, {"text": c["text"], "street": c["street"]}, "moderator")
        action = None if out is None else verify_report(c["text"], c["street"], out, streets)["action"]
        truth_admit.append(c["action"] in ADMIT)
        pred_admit.append(None if action is None else action in ADMIT)
        truth_cat.append(c["category"])
        pred_cat.append(None if out is None else out.get("category"))
        rows.append({"case": c["id"], "expected": c["action"], "got": action, **meta})
    admit = tpr_tnr(truth_admit, pred_admit)
    return {"prompt": f"{prompt.id}@{prompt.sha}", "n": len(cases), "tpr_admit": admit["tpr"],
            "tnr_hold": admit["tnr"], "malformed": admit["malformed"],
            "category_macro_f1": macro_f1(truth_cat, pred_cat, CATEGORIES), **_usage(rows), "rows": rows}


def compare_models(llm, models: list[str], budget: int, max_per_task: int | None = None) -> dict:
    s = get_settings()
    src = LocalSource()
    checker = Athena(s, SampleLLM(), src)
    streets = {r["name"]: r for r in src.streets()[0]}
    ex_cases, mod_cases = explainer_cases(src), moderator_cases()
    tasks = [t for t, cases in (("explainer", ex_cases), ("moderator", mod_cases)) if cases]
    per_task = max(1, budget // max(1, len(models) * len(tasks)))
    if max_per_task:
        per_task = min(per_task, max_per_task)
    counted = CallBudget(llm, budget)
    results: dict = {t: {} for t in tasks}
    for model in models:
        try:
            if "explainer" in tasks:
                results["explainer"][model] = run_explainer(counted, model, ex_cases[:per_task], checker)
            if "moderator" in tasks:
                results["moderator"][model] = run_moderator(counted, model, mod_cases[:per_task], streets)
        except BudgetExceeded as exc:
            results.setdefault("stopped", str(exc))
            break
    return {"config": {"models": models, "budget": budget, "cases_per_task_per_model": per_task,
                       "temperature": 0, "stream": False, "price_source": PRICE_SOURCE,
                       "explainer_cases_available": len(ex_cases), "moderator_dev_cases_available": len(mod_cases),
                       "skipped": [t for t in ("explainer", "moderator") if t not in tasks]},
            "calls_used": counted.used, "results": results}


def to_markdown(res: dict) -> str:
    L = [f"# Model comparison ({res['generated_at'][:10]}, git {res['git_sha']})", "",
         f"{res['calls_used']} calls. Cost from {res['config']['price_source']}; '-' means unpriced.", ""]
    if "explainer" in res["results"]:
        L += ["## Explainer (first-draft pass rate)", "", "| Model | n | Pass rate | p50 ms | p95 ms | Tokens in/out | Cost per call USD |",
              "|---|---|---|---|---|---|---|"]
        for m, r in res["results"]["explainer"].items():
            L.append(f"| {m} | {r['n']} | {r['first_draft_pass_rate']} | {r['latency']['p50_ms']} | "
                     f"{r['latency']['p95_ms']} | {r['tokens_in']}/{r['tokens_out']} | {r['cost_usd_per_call'] or '-'} |")
    if "moderator" in res["results"]:
        L += ["", "## Moderator (dev split)", "", "| Model | n | TNR hold | TPR admit | Category macro-F1 | Malformed | p50 ms | Cost per call USD |",
              "|---|---|---|---|---|---|---|---|"]
        for m, r in res["results"]["moderator"].items():
            L.append(f"| {m} | {r['n']} | {r['tnr_hold']} | {r['tpr_admit']} | {r['category_macro_f1']} | "
                     f"{r['malformed']} | {r['latency']['p50_ms']} | {r['cost_usd_per_call'] or '-'} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--budget", type=int, default=200)
    ap.add_argument("--max-per-task", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.dry_run:
        src = LocalSource()
        print(f"models {models}; explainer cases {len(explainer_cases(src))}; moderator dev cases "
              f"{len(moderator_cases())}; budget {args.budget} calls")
        return 0
    from athena.llm import GatewayLLM
    llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"],
                     timeout_s=get_settings().llm_timeout_s)  # no fallback: each model is measured as itself
    res = compare_models(llm, models, args.budget, args.max_per_task)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None, **res}
    out = ROOT / "evals/results/model_comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1) + "\n")
    (ROOT / "evals/results/model_comparison.md").write_text(to_markdown(res))
    print(to_markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
