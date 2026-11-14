"""Render the README results table from evals/results/ (AGENTS.md Step 10: every number traces to a file).

  python scripts/results_table.py            # print the table
  python scripts/results_table.py --write    # replace the block between the README markers
  python scripts/results_table.py --check    # exit 1 if the README block is out of date (make check)

Rows for evaluations that have not been run say so and name the script that produces them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
README = ROOT / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"


def _load(name: str) -> dict | None:
    p = RESULTS / name
    return json.loads(p.read_text()) if p.exists() else None


def _f(v, digits=3):
    return "n/a" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def rows() -> list[tuple[str, str, str]]:
    out = []
    off = _load("offline.json")
    if off:
        m, sc = off["scout"]["metrics"], off["scout"]
        out.append(("Scout intent routing (held-out, 26 prompts)",
                    f"top-1 {_f(m['intent_top1'])}, tool precision {_f(m['tool_precision'])}, "
                    f"clarification rate {_f(m['clarification_rate'])}", "offline.json"))
        out.append((f"Prompt-injection guard ({sc['n_injection']} prompts, incl. indirect)",
                    f"precision {_f(m['injection_precision'])}, recall {_f(m['injection_recall'])}", "offline.json"))
        c = off["critic"]
        out.append((f"Evidence critic rules ({c['cases']} generated claims)",
                    f"precision {_f(c['precision'])}, recall {_f(c['recall'])}", "offline.json"))
        r = off["routes"]
        source = off.get("config", {}).get("data_source", "unknown data")
        out.append((f"Route outcome vs fastest ({r['n']} trips, {source})",
                    f"median risk-indicator reduction {r['median_risk_reduction_pct']}%, "
                    f"median extra {r['median_extra_min']} min", "offline.json"))
    else:
        out.append(("Offline evals", "not run yet (`make eval`)", "offline.json"))

    lab = _load("label_agreement.json")
    out.append(("Report labeller agreement",
                f"kappa action {_f(lab['kappa_action'])}, category {_f(lab['kappa_category'])} on "
                f"{lab['double_labelled']} reports" if lab else "not run yet (`make labels`)", "label_agreement.json"))

    mc = _load("model_comparison.json")
    if mc and mc["results"].get("explainer"):
        best = max(mc["results"]["explainer"].items(), key=lambda kv: kv[1]["first_draft_pass_rate"] or 0)
        out.append(("Explainer model comparison",
                    f"{len(mc['results']['explainer'])} models; best first-draft pass rate "
                    f"{_f(best[1]['first_draft_pass_rate'])} ({best[0]})", "model_comparison.json"))
    else:
        out.append(("Explainer model comparison", "not run yet (`python evals/compare_models.py`)",
                    "model_comparison.json"))

    for split in ("dev", "test"):
        cl = _load(f"classifier_{split}.json")
        if cl and cl["results"]:
            parts = [f"{r['candidate']} TNR {_f(r['tnr_hold'])}/TPR {_f(r['tpr_admit'])}" for r in cl["results"]]
            out.append((f"Report classifier, {split} split (TNR hold / TPR admit)", "; ".join(parts),
                        f"classifier_{split}.json"))
        else:
            out.append((f"Report classifier, {split} split", "not run yet (`python evals/eval_classifier.py`)",
                        f"classifier_{split}.json"))

    asr = _load("assertions.json")
    out.append(("Assertions on real explanation traces",
                f"{asr['traces_passing_all']}/{asr['traces']} traces pass every assertion" if asr
                else "not run yet (`python evals/assertions.py evals/traces/<run>`)", "assertions.json"))
    ja = _load("judge_audit.json")
    out.append(("LLM judge audit (held-out human labels)",
                f"TPR {_f(ja['binary']['tpr'])}, TNR {_f(ja['binary']['tnr'])}, malformed {ja['binary']['malformed']} "
                f"of {ja['binary']['n']}" if ja else "not run yet (`python evals/judge.py audit ...`)", "judge_audit.json"))
    ab = _load("ab.json")
    out.append(("Blind A/B: LLM vs fact-only explanation",
                f"humans preferred LLM {ab['human']['llm']}, fact-only {ab['human']['fact_only']}, tie {ab['human']['tie']}"
                if ab else "not run yet (`python evals/ab.py`)", "ab.json"))
    ops = _load("ops.json")
    if ops and not ops["config"].get("sample_mode"):
        a = ops["endpoints"]["ask_route"]
        out.append(("Deployed service (/api/ask)",
                    f"p50 {a['p50_ms']} ms, p95 {a['p95_ms']} ms, errors {a['error_rate']}, "
                    f"cost/request {_f(a['cost_usd_per_request'], 6)} USD", "ops.json"))
    else:
        out.append(("Deployed service", "not run yet (`make load-test BASE_URL=...` after deploying)", "ops.json"))
    return out


def table() -> str:
    lines = ["| Evaluation | Result | Source (evals/results/) |", "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | `{c}` |" for a, b, c in rows()]
    return "\n".join(lines)


def block() -> str:
    return f"{START}\n{table()}\n{END}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    text = README.read_text()
    if START not in text or END not in text:
        print("README has no results markers")
        return 1
    current = text[text.index(START):text.index(END) + len(END)]
    if args.check:
        if current != block():
            print("README results table is out of date: run `make readme`")
            return 1
        print("README results table: up to date")
        return 0
    if args.write:
        README.write_text(text.replace(current, block()))
    print(table())
    return 0


if __name__ == "__main__":
    sys.exit(main())
