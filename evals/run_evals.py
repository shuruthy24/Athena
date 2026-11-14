"""Run Athena's offline evaluations and write the evidence files.

  python3 evals/run_evals.py            # deterministic parts (no gateway calls)
  python3 evals/run_evals.py --live     # also moderates the 14 reports through the COMP3110 gateway

Outputs: evals/results/offline.json and evals/results/offline_report.md (or live.json / live_report.md
with --live). The hackathon evidence in evals/results.json and docs/evidence/ is frozen and never
overwritten (AGENTS.md section 2).

Evaluations:
 1. Prompt-injection guard (athena/guard.py) on the Meterless v1 adversarial set (Apache-2.0) extended with
    Athena-specific and indirect injections (evals/guard/injection.jsonl).
 2. Evidence Critic (deterministic layer): claims generated from every trip/time/mode in the data, half
    deliberately corrupted (wrong number, "safe" wording, street on neither route, reversed direction,
    no cited facts). Measures precision/recall of flagging unsupported claims.
 3. Report moderation on 14 SYNTHETIC reports with expected actions.
 4. Route outcome on the sample data: Athena route vs fastest route (baseline) for all 18 trip/time/mode cases.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
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
from athena.guard import inspect_text  # noqa: E402

THRESHOLDS = {  # Meterless scout-intent evals/config/thresholds.yaml (v1), injection rows only
    "injection_precision": {"target": 0.95, "hard_floor": 0.90},
    "injection_recall": {"target": 0.90, "hard_floor": 0.82},
}


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def gate(metric: str, value: float) -> str:
    t = THRESHOLDS[metric]
    return "meets target" if value >= t["target"] else "above floor" if value >= t["hard_floor"] else "BELOW FLOOR"


def eval_injection() -> dict:
    inj = load_jsonl(ROOT / "evals/guard/injection.jsonl")
    tp = fp = fn = tn = 0
    rows = []
    for ex in inj:
        _, evidence, _ = inspect_text(ex["prompt"])
        got, want = bool(evidence), ex["expected"]["injection"]
        tp += got and want
        fp += got and not want
        fn += (not got) and want
        tn += (not got) and not want
        rows.append({"id": ex["id"], "expected": want, "detected": got, "evidence": evidence, "source": ex["source"]})
    m = {"injection_precision": tp / (tp + fp) if tp + fp else 0.0, "injection_recall": tp / (tp + fn) if tp + fn else 0.0}
    return {"metrics": m, "n_injection": len(inj), "confusion_injection": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "injection_rows": rows}


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
    rows, correct, calls = [], 0, 0
    moderator_prompt = athena.prompts["moderator"]
    llm = live_llm or SampleLLM()
    model = athena.s.moderator_model if live_llm else SampleLLM.MODEL
    for r in reports:
        text, injection, _ = inspect_text(r["text"])
        if injection:
            action, reason = "reject", "injection"
        else:
            reply = llm.chat(model, moderator_prompt.body,
                             json.dumps({"text": text, "street": r["street"]}), role="moderator")
            calls += 1
            mod = parse_json(reply.text)
            v = verify_report(text, r["street"], mod, streets)
            action, reason = v["action"], v["reason"]
        ok = action == r["expected_action"]
        correct += ok
        rows.append({"id": r["id"], "expected": r["expected_action"], "got": action, "reason": reason, "ok": ok})
    return {"n": len(reports), "accuracy": correct / len(reports), "model": model, "calls": calls,
            "prompt": f"{moderator_prompt.id}@{moderator_prompt.sha}", "rows": rows}


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


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="moderate the reports through the COMP3110 gateway")
    ap.add_argument("--out-dir", default=str(ROOT / "evals/results"), help="where to write results")
    args = ap.parse_args()
    s = get_settings()
    athena = Athena(s, SampleLLM(), LocalSource())
    live_llm = None
    if args.live:
        from athena.llm import GatewayLLM
        live_llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"],
                              timeout_s=s.llm_timeout_s, fallback_model=s.fallback_model)
    reports = eval_reports(athena, live_llm)
    now = datetime.now(timezone.utc).isoformat()
    res = {"generated": now, "generated_at": now, "git_sha": git_sha(),
           "config": {"live": args.live, "moderator_model": reports["model"],
                      "data_source": "hand-made sample" if not LocalSource().processed else "processed (OSM + OTP)"},
           "calls_used": reports["calls"] if args.live else 0,
           "guard": eval_injection(),
           "critic": eval_critic(athena), "reports": reports, "routes": eval_routes()}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "live" if args.live else "offline"
    (out_dir / f"{name}.json").write_text(json.dumps(res, indent=1) + "\n")
    write_report(res, out_dir / f"{name}_report.md")
    print("guard:", {k: round(v, 3) for k, v in res["guard"]["metrics"].items()})
    print("critic: precision=%.3f recall=%.3f cases=%d" % (res["critic"]["precision"], res["critic"]["recall"], res["critic"]["cases"]))
    print("reports: accuracy=%.3f model=%s" % (res["reports"]["accuracy"], res["reports"]["model"]))
    print("routes: median risk reduction=%.1f%% median extra=%.1f min" % (res["routes"]["median_risk_reduction_pct"], res["routes"]["median_extra_min"]))
    return 0


def write_report(res: dict, out: Path) -> None:
    gd, cr, rp, ro = res["guard"], res["critic"], res["reports"], res["routes"]
    L = [f"# Athena evaluation report", "", f"Generated {res['generated']} by `python3 evals/run_evals.py`"
         + (" --live" if rp["model"] != SampleLLM.MODEL else "") + ".", "",
         "All numbers below come from the run that wrote this file. Sets are small; read them as smoke tests, "
         "not benchmarks (Meterless says the same of its own 32-example v1 corpus).", "",
         "## 1. Prompt-injection guard", "",
         f"Injection set `evals/guard/injection.jsonl`: {gd['n_injection']} prompts (10 from the Meterless v1 adversarial set, "
         "Apache-2.0, plus Athena-specific and indirect cases inside report text and OSM tags). The set and the patterns "
         "were written by the same authors, which is a known source of optimism.", "",
         "| Metric | Value | Meterless target / floor | Status |", "|---|---|---|---|"]
    for k, v in gd["metrics"].items():
        t = THRESHOLDS[k]
        L.append(f"| {k} | {v:.3f} | {t['target']} / {t['hard_floor']} | {gate(k, v)} |")
    missed = [r for r in gd["injection_rows"] if r["expected"] != r["detected"]]
    L += ["", "Injection errors:", ""] + ([f"- `{r['id']}` expected {r['expected']}, detected {r['detected']}" for r in missed] or ["- none"])
    L += ["", "## 2. Evidence Critic (deterministic layer)", "",
          f"{cr['cases']} claims built from the 18 trip/time/mode comparisons; each fact-only claim is tested clean and with "
          "five corruptions. Positive class = unsupported claim.", "",
          f"Precision {cr['precision']:.3f} · Recall {cr['recall']:.3f} · confusion {cr['confusion']}", "",
          "History: the first run caught 0% of reversed-direction claims (recall 0.789 overall); the direction rule was "
          "added after that run. This set is generated by construction, so it tests the rules, not real model drafts.", "",
          "| Case | Accuracy |", "|---|---|"] + [f"| {k} | {v} |" for k, v in cr["accuracy_by_kind"].items()]
    L += ["", "## 3. Report moderation (synthetic reports)", "",
          f"{rp['n']} synthetic reports with expected actions (approve, redact, review, reject). Model: `{rp['model']}`. "
          f"Accuracy {rp['accuracy']:.3f}.", "", "| Report | Expected | Got | Reason |", "|---|---|---|---|"]
    L += [f"| {r['id']} | {r['expected']} | {r['got']} | {r['reason']} |" for r in rp["rows"]]
    L += ["", "## 4. Athena route vs fastest route (baseline), sample data", "",
          "Data: the pre-existing hand-made sample routes (`web/routes.json`, `source: sample`), not live OTP output. "
          "Risk is the app's rule-based street score, so this shows what the system does, not real-world safety.", "",
          f"Across {ro['n']} cases: median route-risk reduction {ro['median_risk_reduction_pct']}% "
          f"(range {ro['min_risk_reduction_pct']}–{ro['max_risk_reduction_pct']}%), median extra time {ro['median_extra_min']} min "
          f"(max {ro['max_extra_min']} min).", "",
          "| Trip | Time | Mode | Extra min | Risk reduction % | Fastest lit % | Athena lit % |", "|---|---|---|---|---|---|---|"]
    L += [f"| {r['trip']} | {r['band']} | {r['mode']} | {r['extra_min']} | {r['risk_reduction_pct']} | {r['fastest_lit']} | {r['athena_lit']} |"
          for r in ro["rows"]]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
