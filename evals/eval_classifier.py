"""Report classifier evaluation (AGENTS.md Step 5).

  python evals/eval_classifier.py --offline                     # dev split, no-model candidates only
  python evals/eval_classifier.py                               # dev split, adds prompt-zero and prompt-5shot (live)
  python evals/eval_classifier.py --split test --touch-test     # ONCE, after the team has chosen on dev

Metrics, fixed before any run (AGENTS.md Step 5):
  admit = approve (redact counts as admit: the rest of the report is used); hold = review or reject.
  TNR on hold    share of hold reports the pipeline holds (a wrongly admitted report is the severe error)
  TPR on admit   share of admit reports the pipeline admits
  macro-F1 on category, cost and latency per report. Malformed output or a failed call counts as wrong.
Every prediction goes through verify_report with the street data, exactly as in the app.

Choose on dev only. The test split is checksummed and touched once: --split test needs --touch-test, and
refuses to run again once classifier_test.json exists. Writes evals/results/classifier_<split>.json.
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

from athena.agents import verify_report  # noqa: E402
from athena.classify import CATEGORIES, ClassifierError  # noqa: E402
from athena.classify.baseline import KeywordClassifier, MajorityClassifier  # noqa: E402
from athena.classify.tfidf import TfidfClassifier, train_rows  # noqa: E402
from athena.data.source import LocalSource  # noqa: E402
from evals.metrics import ADMIT, BudgetExceeded, CallBudget, latency_summary, macro_f1, tpr_tnr  # noqa: E402

REPORTS = ROOT / "data" / "reports"
RESULTS = ROOT / "evals" / "results"
OFFLINE = ("majority", "keyword", "tfidf-logreg", "tfidf-svm")


def load_split(name: str, reports: Path = REPORTS) -> list[dict]:
    p = reports / f"{name}.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"{p.relative_to(ROOT)} is missing: label reports and run `make labels` first")
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def evaluate(clf, rows: list[dict], streets: dict[str, dict]) -> dict:
    truth_admit, pred_admit, truth_cat, pred_cat, ms, costs, tin, tout, out_rows = [], [], [], [], [], [], [], [], []
    for r in rows:
        if hasattr(clf, "last_reply"):
            clf.last_reply = None  # so a failed call never re-counts the previous reply's cost
        t0 = time.perf_counter()
        try:
            out = clf.predict(r["text"], r["street"])
            action = verify_report(r["text"], r["street"], out, streets)["action"]
            cat = out["category"]
        except (ClassifierError, RuntimeError, ValueError, KeyError):
            out, action, cat = None, None, None
        ms.append((time.perf_counter() - t0) * 1000)
        reply = getattr(clf, "last_reply", None)
        if reply is not None:
            costs.append(reply.cost_usd)
            tin.append(reply.input_tokens or 0)
            tout.append(reply.output_tokens or 0)
        truth_admit.append(r["action"] in ADMIT)
        pred_admit.append(None if action is None else action in ADMIT)
        truth_cat.append(r["category"])
        pred_cat.append(cat)
        out_rows.append({"id": r["id"], "expected": r["action"], "got": action, "category": r["category"],
                         "got_category": cat})
    admit = tpr_tnr(truth_admit, pred_admit)
    priced = bool(costs) and all(c is not None for c in costs)
    return {"candidate": clf.name, "n": len(rows), "tnr_hold": admit["tnr"], "tpr_admit": admit["tpr"],
            "malformed": admit["malformed"], "confusion": {k: admit[k] for k in ("tp", "fn", "tn", "fp")},
            "category_macro_f1": macro_f1(truth_cat, pred_cat, list(CATEGORIES)),
            "latency": latency_summary(ms), "model_calls": len(costs),
            "tokens_in": sum(tin), "tokens_out": sum(tout),
            "cost_usd_per_report": round(sum(costs) / len(rows), 8) if priced and rows else (0.0 if not costs else None),
            "rows": out_rows}


def candidates(train: list[dict], names: list[str], llm=None, model: str | None = None) -> list:
    out = []
    for name in names:
        if name == "majority":
            out.append(MajorityClassifier().fit(train))
        elif name == "keyword":
            out.append(KeywordClassifier())
        elif name in ("tfidf-logreg", "tfidf-svm"):
            out.append(TfidfClassifier(name.split("-")[1]).fit(train))
        elif name in ("prompt-zero", "prompt-5shot"):
            from athena.classify.prompted import PromptClassifier
            out.append(PromptClassifier(llm, model, shots=0 if name == "prompt-zero" else 5, train_rows=train))
    return out


def run(split: str, names: list[str], llm=None, model: str | None = None, budget: int = 150,
        reports: Path = REPORTS) -> dict:
    train, rows = train_rows(reports), load_split(split, reports)
    streets = {s["name"]: s for s in LocalSource().streets()[0]}
    counted = CallBudget(llm, budget) if llm is not None else None
    results, stopped = [], None
    for clf in candidates(train, names, counted, model):
        try:
            results.append(evaluate(clf, rows, streets))
        except BudgetExceeded as exc:
            stopped = str(exc)
            break
    return {"split": split, "config": {"candidates": names, "model": model, "train_rows": len(train),
                                       "budget": budget, "admit": sorted(ADMIT)},
            "calls_used": counted.used if counted else 0, "stopped": stopped, "results": results}


def table(res: dict) -> str:
    L = [f"| Candidate | n | TNR hold | TPR admit | Category macro-F1 | Malformed | p50 ms | Cost per report USD |",
         "|---|---|---|---|---|---|---|---|"]
    for r in res["results"]:
        L.append(f"| {r['candidate']} | {r['n']} | {r['tnr_hold']} | {r['tpr_admit']} | {r['category_macro_f1']} | "
                 f"{r['malformed']} | {r['latency']['p50_ms']} | {r['cost_usd_per_report'] if r['cost_usd_per_report'] is not None else 'unpriced'} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--offline", action="store_true", help="no-model candidates only")
    ap.add_argument("--candidates", default=None, help="comma list; default all")
    ap.add_argument("--budget", type=int, default=150)
    ap.add_argument("--touch-test", action="store_true")
    args = ap.parse_args(argv)
    out = RESULTS / f"classifier_{args.split}.json"
    if args.split == "test":
        if not args.touch_test:
            print("refusing: the test split is touched once, after choosing on dev. Add --touch-test.")
            return 2
        if out.exists():
            print(f"refusing: {out.name} exists; the test split was already used for this release.")
            return 2
    names = args.candidates.split(",") if args.candidates else list(OFFLINE) + ([] if args.offline else ["prompt-zero", "prompt-5shot"])
    llm, model = None, None
    if any(n.startswith("prompt") for n in names):
        from athena.config import get_settings
        from athena.llm import GatewayLLM
        s = get_settings()
        llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"],
                         timeout_s=s.llm_timeout_s)
        model = s.moderator_model
    res = run(args.split, names, llm, model, args.budget)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None, **res}
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1) + "\n")
    (RESULTS / f"classifier_{args.split}.md").write_text(table(res))
    print(table(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
