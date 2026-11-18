"""Generate the results section of docs/evaluation.md from evals/results/ (every number traces to a file).

  python scripts/build_eval_report.py           # print
  python scripts/build_eval_report.py --write   # replace the block between the markers in docs/evaluation.md
  python scripts/build_eval_report.py --check   # exit 1 if docs/evaluation.md is out of date (make check)

Targets are the ones written in docs/metrics.md before each evaluation first ran (the injection admit rate was
added on 18 Nov 2026, before its first live run). A verdict is computed, never typed:
"meets", "misses", "not run yet", or "descriptive" where no target was set in advance.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
DOC = ROOT / "docs" / "evaluation.md"
START, END = "<!-- eval:start -->", "<!-- eval:end -->"


def _load(name):
    p = RESULTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _f(v, d=3):
    return "n/a" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))


def _ci(ci):
    return "" if not ci else f" [{ci[0]:.2f}, {ci[1]:.2f}]"


def _verdict(ok):
    return "**meets**" if ok else "**misses**"


def _when(r):
    if not r:
        return ""
    return f"{r.get('generated_at', '')[:10]} @ {r.get('git_sha') or 'no git'}"


def rows() -> list[dict]:
    R = []

    def add(**kw):
        R.append(kw)

    off = _load("offline.json")
    rb = _load("route_baselines.json") or {"datasets": {}}
    cov = _load("data_coverage.json")
    # 1 Route choice -----------------------------------------------------------------------------------------
    for ds, body in sorted(rb["datasets"].items()):
        allm = body["summary"].get("walk,bike,transit", {})
        if not allm:
            continue
        b = allm.get("balanced", {})
        heur = allm.get("most_lit", {})
        ok = (b.get("median_risk_reduction_pct") or 0) > 0 and (b.get("median_extra_min") or 0) <= 5
        add(cap="Route choice", q="Does Athena's ranking surface a lower-risk option at little extra time?",
            data=(f"{b.get('trips')} trip × time cases, {ds} data "
                  + ("(hand-made sample: regression set, not evidence)" if ds == "sample" else "(OSM + OTP, real)")),
            metric="median route-risk reduction vs fastest; median extra minutes (Athena's own rule: see note)",
            baseline=(f"fastest 0%; most-lit heuristic {heur.get('median_risk_reduction_pct')}% for "
                      f"+{heur.get('median_extra_min')} min; least walking {allm.get('least_walking', {}).get('median_risk_reduction_pct')}%"),
            result=f"balanced {b.get('median_risk_reduction_pct')}% for +{b.get('median_extra_min')} min "
                   f"(differs from fastest on {b.get('differs_from_fastest')}/{b.get('trips')})",
            target="reduction > 0 with median extra ≤ 5 min", verdict=_verdict(ok),
            cmd="python evals/eval_route_baselines.py", file="route_baselines.json", when=_when(body))
        lr, fa = allm.get("lower_risk", {}), allm.get("fastest", {})
        add(cap="Route choice", q="Does the lower-risk pick put more of the trip on lit streets? (not scored by Athena's rule)",
            data=f"same {b.get('trips')} cases, {ds} data",
            metric="median lit share and unknown-lighting share of each strategy's pick (inputs, not the risk score)",
            baseline=(f"fastest lit {fa.get('median_lit_share_pct')}% / unknown {fa.get('median_unknown_share_pct')}%; "
                      f"most-lit heuristic lit {heur.get('median_lit_share_pct')}% / unknown {heur.get('median_unknown_share_pct')}%"),
            result=(f"lower_risk lit {lr.get('median_lit_share_pct')}% / unknown {lr.get('median_unknown_share_pct')}%; "
                    f"balanced lit {b.get('median_lit_share_pct')}% / unknown {b.get('median_unknown_share_pct')}%"),
            target="descriptive (added after the first run, so no target)", verdict="descriptive",
            cmd="python evals/eval_route_baselines.py", file="route_baselines.json", when=_when(body))
    if cov and cov.get("runs"):
        label, c = list(cov["runs"].items())[-1]
        ok = (c["median_risk_reduction_pct"] or 0) > 0 and (c["median_extra_min"] or 0) <= 5
        add(cap="Route choice", q="Same question on every real trip (fastest vs Athena's lower-risk variant)",
            data=f"{c['trips']} trips, OSM snapshot + OTP precompute (`{label}`)",
            metric="median risk reduction and extra minutes; median unknown lighting",
            baseline="fastest route",
            result=(f"differs on {c['trips'] - c['trips_same_route']}/{c['trips']}; median reduction "
                    f"{c['median_risk_reduction_pct']}%, +{c['median_extra_min']} min; unknown lighting "
                    f"{c['median_athena_unknown_lighting_pct']}%"),
            target="reduction > 0 with median extra ≤ 5 min", verdict=_verdict(ok),
            cmd="python evals/eval_data_coverage.py --label <name> (needs data/processed)", file="data_coverage.json",
            when=_when(c))
    # 2 Explanations -----------------------------------------------------------------------------------------
    asr = _load("assertions.json")
    if asr:
        share = asr["traces_passing_all"] / asr["traces"]
        add(cap="Explanation faithfulness", q="Are the claims users read supported by the route facts?",
            data=f"{asr['traces']} real explainer traces ({asr['config']['traces']}), live model, sample routes",
            metric="share of traces passing all 9 code assertions", baseline="fact-only explanation (passes by construction)",
            result=f"{asr['traces_passing_all']}/{asr['traces']} = {share:.2f}", target="≥ 0.95",
            verdict=_verdict(share >= 0.95), cmd=f"python evals/assertions.py {asr['config']['traces']}",
            file="assertions.json", when=_when(asr))
    ja = _load("judge_audit.json")
    add(cap="Explanation faithfulness", q="Can an LLM judge be trusted to flag unfaithful explanations?",
        data="held-out human PASS/FAIL labels (evals/labels/heldout.json)", metric="judge TPR / TNR, malformed counted wrong",
        baseline="code assertions", result=(f"TPR {_f(ja['binary']['tpr'])}, TNR {_f(ja['binary']['tnr'])}" if ja else "—"),
        target="TNR ≥ 0.9", verdict=(_verdict((ja['binary']['tnr'] or 0) >= 0.9) if ja else "not run yet"),
        cmd="python evals/label.py traces <run>; python evals/judge.py audit <run>", file="judge_audit.json", when=_when(ja))
    ab = _load("ab.json")
    add(cap="Explanation usefulness", q="Do people prefer the LLM explanation to the fact-only one?",
        data="6+ blind presentations from the trace run", metric="human preference A/B/TIE (blind, then revealed)",
        baseline="fact-only explanation",
        result=(f"LLM {ab['human']['llm']}, fact-only {ab['human']['fact_only']}, tie {ab['human']['tie']}" if ab else "—"),
        target="LLM preferred in a majority",
        verdict=(_verdict(ab['human']['llm'] > (ab['human']['fact_only'] + ab['human']['tie'])) if ab else "not run yet"),
        cmd="python evals/ab.py prepare <run>; python evals/ab.py reveal --judge", file="ab.json", when=_when(ab))
    if off:
        cr = off["critic"]
        d = cr.get("detectors", {})
        add(cap="Evidence critic rules", q="Do the deterministic rules catch unsupported claims?",
            data=f"{cr['cases']} claims, synthetic by construction (5 corruption types)",
            metric="precision / recall (positive = unsupported)",
            baseline=(f"citation-only check recall {_f(d.get('citation_only', {}).get('recall'))}; no critic 0.0"),
            result=f"precision {_f(cr['precision'])}, recall {_f(cr['recall'])}{_ci(d.get('athena_rules', {}).get('recall_ci95'))}",
            target="descriptive (rules tested on corruptions, not real drafts)", verdict="descriptive",
            cmd="make eval", file="offline.json", when=_when(off))
    # 3 Reports ----------------------------------------------------------------------------------------------
    cl = _load("classifier_dev.json")
    if cl and cl.get("results"):
        best = max(cl["results"], key=lambda r: (r["tnr_hold"] or 0, r["tpr_admit"] or 0))
        res = "; ".join(f"{r['candidate']} {_f(r['tnr_hold'])}/{_f(r['tpr_admit'])}" for r in cl["results"])
        verdict = _verdict((best["tnr_hold"] or 0) >= 0.95 and (best["tpr_admit"] or 0) >= 0.8)
    else:
        res, verdict = "—", "not run yet"
    add(cap="Report classifier", q="Which classifier holds harmful reports and admits real ones?",
        data="team-labelled reports, group split by street and writer; dev for choosing, test once (frozen)",
        metric="TNR on hold, TPR on admit, category macro-F1, cost, latency", baseline="majority class, keyword rule",
        result=res, target="TNR ≥ 0.95, TPR ≥ 0.80", verdict=verdict,
        cmd="python evals/label.py reports --as a (then b); make labels; python evals/eval_classifier.py",
        file="classifier_dev.json", when=_when(cl))
    e2e = _load("injection_e2e.json")
    if e2e:
        for k, v in e2e["runs"].items():
            add(cap="Report guardrail (end to end)", q="Is an injected report ever admitted by the whole pipeline?",
                data=f"{v['n_injection']} held-out injection probes + {v['n_benign']} benign (synthetic)",
                metric="injection admit rate (lower is better); benign hold rate", baseline="guard + keyword rule (no model)",
                result=f"{k}: {_f(v['injection_admit_rate'])}{_ci(v['injection_admit_rate_ci95'])}; benign held {_f(v['benign_hold_rate'])}",
                target="0 admitted",
                verdict=("baseline (reference)" if k.startswith("baseline") else _verdict(v["injection_admit_rate"] == 0)),
                cmd="python evals/eval_injection_e2e.py [--live]", file="injection_e2e.json", when=_when(v))
        if not any(k.startswith("live") for k in e2e["runs"]):
            add(cap="Report guardrail (end to end)", q="Same, with the real classifier model", data="same probes",
                metric="injection admit rate", baseline="rows above", result="—", target="0 admitted",
                verdict="not run yet", cmd="python evals/eval_injection_e2e.py --live (about 50 calls)",
                file="injection_e2e.json", when="")
    if off:
        for name, st in off["guard"].get("sets", {}).items():
            if "detectors" not in st:      # a set that has not been collected (e.g. red-team) gets no row
                continue
            g, nv = st["detectors"]["athena_guard"], st["detectors"]["naive_phrase_filter"]
            ok = (g["recall"] or 0) >= 0.9 and (g["precision"] or 0) >= 0.95
            add(cap="Text guard alone", q="Does the guard block instructions in report text?",
                data=f"{name}: {st['n']} rows ({st['role']})", metric="recall, precision, false-block rate",
                baseline=f"phrase filter recall {_f(nv['recall'])}; no guard 0.0",
                result=(f"recall {_f(g['recall'])}{_ci(g['recall_ci95'])}, precision {_f(g['precision'])}, "
                        f"false-block {_f(g['false_positive_rate'])}"),
                target="recall ≥ 0.9, precision ≥ 0.95" + (" (dev: optimistic by construction)" if name == "dev" else ""),
                verdict=_verdict(ok), cmd="make eval", file="offline.json", when=_when(off))
    pii = _load("pii.json")
    if pii:
        r = pii["redactors"]["app_guard"]
        missed = sorted({m["kind"] for m in r["missed"]})
        add(cap="Privacy: PII redaction", q="Is personal information removed before any model sees a report?",
            data=f"{pii['config']['n_texts']} synthetic texts, {pii['config']['n_pii_spans']} PII spans (regression set)",
            metric="span recall; over-redaction of PII-free texts", baseline="no redaction (recall 0)",
            result=f"recall {_f(r['recall'])}{_ci(r['recall_ci95'])}; over-redaction {_f(r['over_redaction_rate'])}; misses {', '.join(missed) or 'none'}",
            target="descriptive (set written 18 Nov; before-fix run in pii_before_fixes.json)", verdict="descriptive",
            cmd="python evals/eval_pii.py", file="pii.json", when=_when(pii))
    # 4 Models, data, outcome, operations, completion ---------------------------------------------------------
    mc = _load("model_comparison.json")
    if mc and mc["results"].get("explainer"):
        ex = mc["results"]["explainer"]
        best = max(ex.values(), key=lambda v: v["first_draft_pass_rate"] or 0)["first_draft_pass_rate"]
        priced = {m: v for m, v in ex.items() if v.get("cost_usd_per_call") is not None}
        cheapest = min(priced, key=lambda m: priced[m]["cost_usd_per_call"]) if priced else None
        add(cap="Model choice (explainer)", q="Which model drafts passing explanations at the lowest cost?",
            data=f"{list(ex.values())[0]['n']} sample trips per model, live gateway",
            metric="first-draft pass rate, p50/p95 latency, cost per call", baseline="cheapest model",
            result="; ".join(f"{m} {v['first_draft_pass_rate']}" for m, v in ex.items()),
            target="cheapest model within 0.05 of the best pass rate",
            verdict=_verdict(cheapest is not None and best - ex[cheapest]["first_draft_pass_rate"] <= 0.05),
            cmd="python evals/compare_models.py", file="model_comparison.json", when=_when(mc))
    dq = _load("data_quality.json")
    for ds, q in sorted((dq or {}).get("datasets", {}).items()):
        miss = q["tables"].get("streets", {}).get("missing_share", {}).get("lit")
        add(cap="Data quality", q="Is the served data complete, unique and in range?", data=f"{ds} dataset tables",
            metric="schema, duplicate keys, out-of-area points, missing share, snapshot age",
            baseline="—", result=f"{len(q['failures'])} failures, {len(q['warnings'])} warnings; streets with unknown lighting {_f(miss)}",
            target="no failures", verdict=_verdict(not q["failures"]), cmd="python -m pipeline.check --write-quality",
            file="data_quality.json", when=_when(q))
    ops = _load("ops.json")
    a = (ops or {}).get("endpoints", {}).get("explain_route") if ops and not ops["config"].get("sample_mode") else None
    add(cap="Operations", q="Is the deployed service fast and reliable enough?", data="50 requests per endpoint, deployed URL",
        metric="p50/p95 latency, error rate, cost per request", baseline="—",
        result=(f"p95 {a['p95_ms']} ms, errors {a['error_rate']}" if a else "—"), target="p95 ≤ 10 s, errors ≤ 1%",
        verdict=(_verdict(a["p95_ms"] <= 10000 and a["error_rate"] <= 0.01) if a else "not run yet"),
        cmd="make load-test BASE_URL=...", file="ops.json", when=_when(ops))
    ver = _load("verify.json")
    if ver:
        add(cap="Completion", q="Does every demonstrated feature run from the instructions?",
            data=f"{len(ver['checks'])} end-to-end checks, {ver['config']['data_provenance'].get('mode')} data, offline",
            metric="checks passing", baseline="—", result=f"{ver['summary']['PASS']} pass, {ver['summary']['FAIL']} fail",
            target="all pass", verdict=_verdict(ver["summary"]["FAIL"] == 0), cmd="python scripts/verify_demo.py",
            file="verify.json", when=_when(ver))
    return R


def block() -> str:
    R = rows()
    L = [START, "",
         "| Capability | Question | Eval set (role) | Metric | Baseline | Result (95% CI) | Target | Verdict | Reproduce |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in R:
        L.append(f"| {r['cap']} | {r['q']} | {r['data']} | {r['metric']} | {r['baseline']} | {r['result']} | "
                 f"{r['target']} | {r['verdict']} | `{r['cmd']}` → `{r['file']}`{' (' + r['when'] + ')' if r['when'] else ''} |")
    counts = {v: sum(r["verdict"].strip("*") == v for r in R)
              for v in ("meets", "misses", "not run yet", "descriptive", "baseline (reference)")}
    L += ["", f"**Summary:** {counts['meets']} meet their target, {counts['misses']} miss it, "
              f"{counts['not run yet']} not run yet, {counts['descriptive']} descriptive (no target set in advance), "
              f"{counts['baseline (reference)']} baseline reference rows.", "", END]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    text = DOC.read_text(encoding="utf-8")
    current = text[text.index(START):text.index(END) + len(END)]
    if args.check:
        if current != block():
            print("docs/evaluation.md results are out of date: run `python scripts/build_eval_report.py --write`")
            return 1
        print("docs/evaluation.md: up to date")
        return 0
    if args.write:
        DOC.write_text(text.replace(current, block()), encoding="utf-8")
    print(block())
    return 0


if __name__ == "__main__":
    sys.exit(main())
