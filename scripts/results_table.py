"""Render the README results table from evals/results/ (every number traces to a file).

  python scripts/results_table.py            # print the table
  python scripts/results_table.py --write    # replace the block between the README markers
  python scripts/results_table.py --check    # exit 1 if the README block is out of date (make check)

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
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _f(v, digits=3):
    return "n/a" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def rows() -> list[tuple[str, str, str]]:
    """One short line per evaluation. The full table, with baselines and intervals, is in docs/evaluation.md."""
    out = []
    rb = (_load("route_baselines.json") or {}).get("datasets", {}).get("processed")
    if rb:
        s = rb["summary"]["walk,bike,transit"]
        lr, fa = s["lower_risk"], s["fastest"]
        out.append((f"Route choice on real data ({lr['trips']} trips)",
                    f"lower-risk pick: risk score -{lr['median_risk_reduction_pct']}% for +{lr['median_extra_min']} min; "
                    f"lit share {lr['median_lit_share_pct']}% vs {fa['median_lit_share_pct']}% for the fastest",
                    "route_baselines.json"))
    asr = _load("assertions.json")
    if asr:
        out.append(("Explanations pass every fact check (live traces)",
                    f"{asr['traces_passing_all']}/{asr['traces']}", "assertions.json"))
    off = _load("offline.json")
    if off:
        c = off["critic"]
        out.append(("Critic rules catch planted errors", f"recall {_f(c['recall'], 2)} on {c['cases']} synthetic claims",
                    "offline.json"))
    ja = _load("judge_audit.json")
    if ja:
        b = ja["binary"]
        out.append(("LLM judge vs human labels", f"TPR {_f(b['tpr'], 2)}, TNR {_f(b['tnr'], 2)}" + (" (no FAIL labels held out)" if b["tnr"] is None else ""),
                    "judge_audit.json"))
    ab = _load("ab.json")
    if ab:
        h = ab["human"]
        out.append(("Blind A/B: LLM vs fact-only text", f"LLM {h['llm']}, fact-only {h['fact_only']}, tie {h['tie']}",
                    "ab.json"))
    if off:
        ho = off["guard"]["sets"].get("heldout_probes", {}).get("detectors")
        if ho:
            out.append(("Injection guard on held-out probes", f"recall {_f(ho['athena_guard']['recall'], 2)} "
                        f"({_f(off['guard']['metrics']['injection_recall'], 2)} on the set it was tuned on)", "offline.json"))
    e2e = (_load("injection_e2e.json") or {}).get("runs", {})
    live = next((v for k, v in e2e.items() if k.startswith("live")), None)
    base = next((v for k, v in e2e.items() if "keyword" in k), None)
    if live and base:
        out.append(("Injected reports admitted end to end (live)",
                    f"{len(live['injection_admitted_ids'])}/{live['n_injection']} "
                    f"(keyword baseline {len(base['injection_admitted_ids'])}/{base['n_injection']})", "injection_e2e.json"))
    pii = _load("pii.json")
    if pii:
        out.append(("PII removed before any model", f"recall {_f(pii['redactors']['app_guard']['recall'], 2)} "
                    f"on {pii['config']['n_pii_spans']} spans (regression set)", "pii.json"))
    cl = _load("classifier_dev.json")
    if cl:
        if all(r["tnr_hold"] is None for r in cl["results"]):
            res = "candidates can't be separated: dev has no reports that should be held"
        else:
            res = "; ".join(f"{r['candidate']} TNR {_f(r['tnr_hold'], 2)}" for r in cl["results"])
        out.append(("Report classifier, dev split", res, "classifier_dev.json"))
    cp = _load("classifier_probes.json")
    if cp and cp.get("results"):
        best = max(cp["results"], key=lambda r: (r["tnr_hold"] or 0, r["tpr_admit"] or 0))
        out.append((f"Report classifier on hold probes ({best['n']} synthetic)",
                    f"best: {best['candidate']} holds {_f(best['tnr_hold'], 2)} of reports that should be held, "
                    f"admits {_f(best['tpr_admit'], 2)} of good ones", "classifier_probes.json"))
    lab = _load("label_agreement.json")
    if lab:
        out.append(("Labeller agreement", f"kappa {_f(lab['kappa_action'], 2)} (action), {_f(lab['kappa_category'], 2)} "
                    f"(category), {lab['double_labelled']} reports", "label_agreement.json"))
    dq = (_load("data_quality.json") or {}).get("datasets", {}).get("processed")
    if dq:
        out.append(("Data quality checks", f"{len(dq['failures'])} failures", "data_quality.json"))
    ver = _load("verify.json")
    if ver:
        out.append(("Feature checks", f"{ver['summary']['PASS']}/{ver['summary']['PASS'] + ver['summary']['FAIL']} pass",
                    "verify.json"))
    if not _load("classifier_test.json"):
        out.append(("Report classifier, test split", "not run: held back on purpose", "classifier_test.json"))
    if not _load("ops.json"):
        out.append(("Load test", "not run: not deployed", "ops.json"))
    return out


def table() -> str:
    lines = ["| What we measured | Result | File (evals/results/) |", "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | `{c}` |" for a, b, c in rows()]
    return "\n".join(lines)


def block() -> str:
    return f"{START}\n{table()}\n{END}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    text = README.read_text(encoding="utf-8")
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
        README.write_text(text.replace(current, block()), encoding="utf-8")
    print(table())
    return 0


if __name__ == "__main__":
    sys.exit(main())
