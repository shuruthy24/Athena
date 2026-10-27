"""Run Athena's offline evaluations and write the evidence files.

  python3 evals/run_evals.py            # deterministic parts (no Zetaris/Nightshift needed)
  python3 evals/run_evals.py --live     # also runs report moderation through Nemotron via Nightshift

Outputs: docs/evidence/evaluation_report.md and evals/results.json

Evaluations:
 1. Scout Intent routing on a HELD-OUT set written before the router was run against it, plus the
    Meterless v1 adversarial injection set (Apache-2.0) extended with Athena-specific injections.
    Metrics and thresholds follow Meterless engines/scout-intent/evals/config/thresholds.yaml.
 2. Evidence Critic (deterministic layer): claims generated from every trip/time/mode in the data, half
    deliberately corrupted (wrong number, "safe" wording, street on neither route, reversed direction,
    no cited facts). Measures precision/recall of flagging unsupported claims.
 3. Report moderation on 14 SYNTHETIC reports with expected actions.
 4. Route outcome on the sample data: Athena route vs fastest route (baseline) for all 18 trip/time/mode cases.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.agents import Athena, verify_report  # noqa: E402
from athena.analysis import compare, fallback_explanation, flatten_facts  # noqa: E402
from athena.config import get_settings  # noqa: E402
from athena.data.source import LocalSource  # noqa: E402
from athena.llm import SampleLLM, parse_json  # noqa: E402
from athena.scout import Scout  # noqa: E402

THRESHOLDS = {  # Meterless scout-intent evals/config/thresholds.yaml (v1)
    "intent_top1": {"target": 0.92, "hard_floor": 0.85},
    "intent_top3_recall": {"target": 0.98, "hard_floor": 0.94},
    "injection_precision": {"target": 0.95, "hard_floor": 0.90},
    "injection_recall": {"target": 0.90, "hard_floor": 0.82},
    "tool_precision": {"target": 0.88, "hard_floor": 0.80},
    "clarification_rate": {"target_max": 0.15, "hard_max": 0.25},
}
UI_CONTEXT = {"trip": {"origin": "unsw", "destination": "townhall", "band": "22", "mode": "walk"}}


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def gate(metric: str, value: float) -> str:
    t = THRESHOLDS[metric]
    if "target" in t:
        return "meets target" if value >= t["target"] else "above floor" if value >= t["hard_floor"] else "BELOW FLOOR"
    return "meets target" if value <= t["target_max"] else "under hard max" if value <= t["hard_max"] else "ABOVE MAX"


def eval_scout(streets: list[str], heldout_file: str = "heldout_v2.jsonl") -> dict:
    scout = Scout(known_streets=streets, tool_availability={"sample.json": lambda: False, "sample.llm": lambda: False})
    held = load_jsonl(ROOT / "evals/scout" / heldout_file)
    rows, top1 = [], 0
    top3 = tools_ok = tools_n = clar = 0
    for ex in held:
        d = scout.decide(ex["prompt"], context=UI_CONTEXT)
        exp = ex["expected"]["intent"]
        cands = [c["intent"] for c in d.candidates]
        predicted = d.intent
        ok1 = predicted == exp
        ok3 = exp in cands[:3]
        top1 += ok1
        top3 += ok3
        clar += d.needs_clarification
        planned = [t["tool"] for t in d.tool_plan]
        if not d.needs_clarification and d.risk["level"] != "block":
            tools_n += 1
            tools_ok += all(t in planned for t in ex["expected"]["tools"]) and (bool(planned) == bool(ex["expected"]["tools"]))
        rows.append({"id": ex["id"], "expected": exp, "predicted": predicted, "band": d.band,
                     "score": d.confidence, "clarify": d.needs_clarification, "ok": ok1})
    inj = load_jsonl(ROOT / "evals/scout/injection.jsonl")
    tp = fp = fn = tn = 0
    inj_rows = []
    for ex in inj:
        d = scout.decide(ex["prompt"], context={})
        got = d.risk["injection"]["detected"]
        want = ex["expected"]["injection"]
        tp += got and want
        fp += got and not want
        fn += (not got) and want
        tn += (not got) and not want
        inj_rows.append({"id": ex["id"], "expected": want, "detected": got, "evidence": d.risk["injection"]["evidence"],
                         "source": ex["source"]})
    clar_cases = load_jsonl(ROOT / "evals/scout/clarification.jsonl")
    clar_ok = sum(scout.decide(c["prompt"], context=c["context"]).needs_clarification == c["expected"]["clarification"]
                  for c in clar_cases)
    n = len(held)
    m = {"intent_top1": top1 / n, "intent_top3_recall": top3 / n,
         "injection_precision": tp / (tp + fp) if tp + fp else 0.0, "injection_recall": tp / (tp + fn) if tp + fn else 0.0,
         "tool_precision": tools_ok / tools_n if tools_n else 0.0, "clarification_rate": clar / n}
    return {"metrics": m, "n_heldout": n, "n_injection": len(inj), "confusion_injection": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "clarification_cases": f"{clar_ok}/{len(clar_cases)}", "rows": rows, "injection_rows": inj_rows,
            "stage1_self_test_ms": scout.self_test_ms, "heldout_file": heldout_file,
            "registry_version": scout.registry_version}


def corrupt(claim: dict, kind: str, facts: dict, rng: random.Random) -> dict:
    text = claim["text"]
    if kind == "number":
        import re
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if nums:
            n = nums[0]
            text = text.replace(n, f"{float(n) * 1.5 + 7:.1f}", 1)
        return {"text": text, "facts": claim["facts"]}
    if kind == "safe":
        return {"text": text.rstrip(".") + ", so it is safe.", "facts": claim["facts"]}
    if kind == "street":
        return {"text": text.rstrip(".") + " and stays on Chalmers Street.", "facts": claim["facts"]}
    if kind == "nofacts":
        return {"text": text, "facts": []}
    if kind == "direction":
        return {"text": "The fastest route has lower risk than Athena's route.", "facts": ["risk_reduction_pct"]}
    raise ValueError(kind)


def eval_critic(athena: Athena) -> dict:
    src = LocalSource()
    streets, _ = src.streets()
    rng = random.Random(7)
    tp = fp = fn = tn = 0
    by_kind: dict[str, list[int]] = {}
    cases = 0
    for trip in ("unsw|townhall", "townhall|unsw"):
        o, dst = trip.split("|")
        for band in ("18", "22", "01"):
            for mode in ("walk", "bike", "transit"):
                steps, _ = src.route_steps(o, dst, band, mode)
                cmp = compare(steps, streets, band, use_reports=False)
                facts = flatten_facts(cmp)
                _, claims = fallback_explanation(cmp)
                for c in claims:
                    for kind in ("clean", "number", "safe", "street", "nofacts", "direction"):
                        if kind == "street" and "Chalmers Street" in facts["fastest.streets"] + facts["athena.streets"]:
                            continue
                        cl = c if kind == "clean" else corrupt(c, kind, facts, rng)
                        flagged = not athena.check_claims([cl], facts)[0]["supported"]
                        bad = kind != "clean"
                        tp += flagged and bad
                        fp += flagged and not bad
                        fn += (not flagged) and bad
                        tn += (not flagged) and not bad
                        by_kind.setdefault(kind, []).append(int(flagged == bad))
                        cases += 1
    return {"cases": cases, "precision": tp / (tp + fp) if tp + fp else 0.0, "recall": tp / (tp + fn) if tp + fn else 0.0,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "accuracy_by_kind": {k: round(sum(v) / len(v), 3) for k, v in by_kind.items()}}


def eval_reports(athena: Athena, live_llm=None) -> dict:
    reports = json.loads((ROOT / "data/reports.json").read_text())["rows"]
    streets = {s["name"]: s for s in json.loads((ROOT / "data/streets.json").read_text())["rows"]}
    rows, correct = [], 0
    from athena.agents import MODERATOR_SYSTEM
    llm = live_llm or SampleLLM()
    model = athena.s.fast_model if live_llm else SampleLLM.MODEL
    for r in reports:
        msg = f"Report: {r['text']} on {r['street']}"
        d = athena.scout.decide(msg, context={})
        if d.risk["level"] == "block":
            action, reason = "reject", "injection"
        else:
            reply = llm.chat(model, MODERATOR_SYSTEM, json.dumps({"text": d.redacted_prompt, "street": r["street"]}))
            mod = parse_json(reply.text)
            v = verify_report(d.redacted_prompt, d.parameters.get("street"), mod, streets)
            action, reason = v["action"], v["reason"]
        ok = action == r["expected_action"]
        correct += ok
        rows.append({"id": r["id"], "expected": r["expected_action"], "got": action, "reason": reason, "ok": ok,
                     "intent": d.intent})
    return {"n": len(reports), "accuracy": correct / len(reports), "model": model, "rows": rows}


def eval_routes() -> dict:
    src = LocalSource()
    streets, _ = src.streets()
    rows = []
    for trip in ("unsw|townhall", "townhall|unsw"):
        o, dst = trip.split("|")
        for band in ("18", "22", "01"):
            for mode in ("walk", "bike", "transit"):
                steps, _ = src.route_steps(o, dst, band, mode)
                c = compare(steps, streets, band, use_reports=False)
                rows.append({"trip": trip, "band": band, "mode": mode, "extra_min": c["extra_min"],
                             "risk_reduction_pct": c["risk_reduction_pct"], "fastest_risk": c["fastest"]["risk"],
                             "athena_risk": c["athena"]["risk"], "fastest_lit": c["fastest"]["lit_share_pct"],
                             "athena_lit": c["athena"]["lit_share_pct"]})
    red = [r["risk_reduction_pct"] for r in rows]
    ext = [r["extra_min"] for r in rows]
    return {"n": len(rows), "median_risk_reduction_pct": statistics.median(red), "min_risk_reduction_pct": min(red),
            "max_risk_reduction_pct": max(red), "median_extra_min": statistics.median(ext), "max_extra_min": max(ext),
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="moderate reports with Nemotron through Nightshift")
    args = ap.parse_args()
    s = get_settings()
    athena = Athena(s, SampleLLM(), LocalSource())
    street_names = [r["name"] for r in LocalSource().streets()[0]]
    live_llm = None
    if args.live:
        from athena.llm import NightshiftLLM
        live_llm = NightshiftLLM(s.meterless_base_url, s.meterless_token, timeout_s=s.llm_timeout_s)
    res = {"generated": datetime.now(timezone.utc).isoformat(), "scout": eval_scout(street_names),
           "scout_v1_set_after_changes": eval_scout(street_names, "heldout.jsonl"),
           "critic": eval_critic(athena), "reports": eval_reports(athena, live_llm), "routes": eval_routes()}
    (ROOT / "evals/results.json").write_text(json.dumps(res, indent=1) + "\n")
    write_report(res)
    sm = res["scout"]["metrics"]
    print("scout:", {k: round(v, 3) for k, v in sm.items()})
    print("critic: precision=%.3f recall=%.3f cases=%d" % (res["critic"]["precision"], res["critic"]["recall"], res["critic"]["cases"]))
    print("reports: accuracy=%.3f model=%s" % (res["reports"]["accuracy"], res["reports"]["model"]))
    print("routes: median risk reduction=%.1f%% median extra=%.1f min" % (res["routes"]["median_risk_reduction_pct"], res["routes"]["median_extra_min"]))
    return 0


def write_report(res: dict) -> None:
    sc, cr, rp, ro = res["scout"], res["critic"], res["reports"], res["routes"]
    L = [f"# Athena evaluation report", "", f"Generated {res['generated']} by `python3 evals/run_evals.py`"
         + (" --live" if rp["model"] != SampleLLM.MODEL else "") + ".", "",
         "All numbers below come from the run that wrote this file. Sets are small; read them as smoke tests, "
         "not benchmarks (Meterless says the same of its own 32-example v1 corpus).", "",
         "## 1. Scout Intent routing", "",
         f"Registry v{sc['registry_version']}. Held-out set `{sc['heldout_file']}`: {sc['n_heldout']} prompts, written and "
         "checksummed (evals/heldout_checksums.txt) before this router version was run on them. "
         f"Evaluated with the web surface's trip context (UNSW → Town Hall selected), as the app sends it. "
         f"Injection set: {sc['n_injection']} prompts (10 from Meterless v1 adversarial set, Apache-2.0, plus 8 Athena-specific).", "",
         "| Metric | Value | Meterless target / floor | Status |", "|---|---|---|---|"]
    for k, v in sc["metrics"].items():
        t = THRESHOLDS[k]
        tt = f"{t['target']} / {t['hard_floor']}" if "target" in t else f"≤ {t['target_max']} / ≤ {t['hard_max']}"
        L.append(f"| {k} | {v:.3f} | {tt} | {gate(k, v)} |")
    L += ["", f"Clarification edge cases correct: {sc['clarification_cases']}. Stage-1 self-test worst case: "
          f"{sc['stage1_self_test_ms']:.3f} ms (budget 16 ms).", "", "Misrouted held-out prompts:", ""]
    bad = [r for r in sc["rows"] if not r["ok"]]
    L += [f"- `{r['id']}` expected {r['expected']}, got {r['predicted']} ({r['band']}, {r['score']:.2f})" for r in bad] or ["- none"]
    missed = [r for r in sc["injection_rows"] if r["expected"] != r["detected"]]
    L += ["", "Injection errors:", ""] + ([f"- `{r['id']}` expected {r['expected']}, detected {r['detected']}" for r in missed] or ["- none"])
    v1 = res["scout_v1_set_after_changes"]["metrics"]
    L += ["", "History: registry v1 scored intent_top1 0.821, top3 0.964, tool_precision 0.750, clarification_rate 0.286 on "
          "`heldout.jsonl` (see evaluation_report_v1_baseline.md). Its failures were used to improve the router, so that set "
          f"is no longer held out: after the changes it scores top1 {v1['intent_top1']:.3f}, clarification_rate "
          f"{v1['clarification_rate']:.3f} (contaminated, shown for transparency only). Both held-out sets were written by "
          "the same authors as the registry, which is a known source of optimism."]
    L += ["", "## 2. Evidence Critic (deterministic layer)", "",
          f"{cr['cases']} claims built from the 18 trip/time/mode comparisons; each fact-only claim is tested clean and with "
          "five corruptions. Positive class = unsupported claim.", "",
          f"Precision {cr['precision']:.3f} · Recall {cr['recall']:.3f} · confusion {cr['confusion']}", "",
          "History: the first run caught 0% of reversed-direction claims (recall 0.789 overall); the direction rule was "
          "added after that run. This set is generated by construction, so it tests the rules, not real model drafts.", "",
          "| Case | Accuracy |", "|---|---|"] + [f"| {k} | {v} |" for k, v in cr["accuracy_by_kind"].items()]
    L += ["", "## 3. Report moderation (synthetic reports)", "",
          f"{rp['n']} synthetic reports with expected actions (approve, redact, review, reject). Model: `{rp['model']}`. "
          f"Accuracy {rp['accuracy']:.3f}.", "", "| Report | Expected | Got | Reason | Scout intent |", "|---|---|---|---|---|"]
    L += [f"| {r['id']} | {r['expected']} | {r['got']} | {r['reason']} | {r['intent']} |" for r in rp["rows"]]
    L += ["", "## 4. Athena route vs fastest route (baseline), sample data", "",
          "Data: the pre-existing hand-made sample routes (`web/routes.json`, `source: sample`), not live OTP output. "
          "Risk is the app's rule-based street score, so this shows what the system does, not real-world safety.", "",
          f"Across {ro['n']} cases: median route-risk reduction {ro['median_risk_reduction_pct']}% "
          f"(range {ro['min_risk_reduction_pct']}–{ro['max_risk_reduction_pct']}%), median extra time {ro['median_extra_min']} min "
          f"(max {ro['max_extra_min']} min).", "",
          "| Trip | Time | Mode | Extra min | Risk reduction % | Fastest lit % | Athena lit % |", "|---|---|---|---|---|---|---|"]
    L += [f"| {r['trip']} | {r['band']} | {r['mode']} | {r['extra_min']} | {r['risk_reduction_pct']} | {r['fastest_lit']} | {r['athena_lit']} |"
          for r in ro["rows"]]
    out = ROOT / "docs/evidence/evaluation_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
