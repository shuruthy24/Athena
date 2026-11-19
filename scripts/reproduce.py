"""Reproduce every committed result that can be regenerated here, and compare it with the committed file.

  python scripts/reproduce.py            # make reproduce: exit 1 if any regenerated number differs
  python scripts/reproduce.py --record   # also write evals/results/reproduce.json (the evidence of the check)

What "reproduce" means for each result file in evals/results/:
  rerun     offline, deterministic: regenerated from code and committed data and compared number by number
            (offline.json, pii.json, route_baselines.json [sample], injection_e2e.json [offline baselines],
            data_quality.json [sample]).
  rescore   live model outputs are saved (traces), so their scoring is recomputed offline (assertions.json); for
            model_comparison.json the per-case pass flags are recounted to check the summary.
  rebuild   data/processed from data/raw (pipeline.verify), when the raw snapshots are present.
  input     needs the gateway key, human labels, a deployed URL or the raw data to regenerate; listed with the
            command that produces it.
Timestamps, git shas and latencies are never compared.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / "evals" / "results"
os.environ.setdefault("SAMPLE_MODE", "true")
os.environ["ATHENA_DATA_DIR"] = str(ROOT / "data")      # the committed regression sample, as in `make eval`

LIVE = {
    "model_comparison.json": "python evals/compare_models.py (key)",
    "classifier_dev.json": "python evals/eval_classifier.py (labelled reports; key for prompted candidates)",
    "classifier_test.json": "python evals/eval_classifier.py --split test --touch-test (once)",
    "label_agreement.json": "make labels (two human labellers)",
    "judge_audit.json": "python evals/judge.py audit evals/traces/<run> (human labels + key)",
    "judge_audit_sample.json": ("python evals/judge.py audit evals/traces/2026-11-14-live-sample "
                                "--labels evals/labels-sample (human labels + key, 6 calls)"),
    "ab.json": "python evals/ab.py reveal --judge (human choices + key)",
    "ops.json": "make load-test BASE_URL=... (deployed URL)",
    "data_coverage.json": "python evals/eval_data_coverage.py --label <name> (data/processed)",
    "lit_inference.json": "python evals/eval_lit_inference.py (data/raw OSM snapshot)",
    "stack_check.json": "python scripts/check_stack.py (key)",
}
IGNORE = {"generated_at", "generated", "git_sha", "ms", "mean_ms", "p50_ms", "p95_ms", "latency", "total_ms"}


def _load(name: str):
    p = RESULTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def diff(a, b, path="") -> list[str]:
    """Paths where two result structures differ, ignoring timestamps, shas and latencies; floats to 1e-6."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k in IGNORE:
                continue
            if k not in a or k not in b:
                out.append(f"{path}.{k} only in {'committed' if k in a else 'regenerated'}")
                continue
            out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path} length {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff(x, y, f"{path}[{i}]")]
    if isinstance(a, float) or isinstance(b, float):
        try:
            return [] if abs(float(a) - float(b)) <= 1e-6 else [f"{path} {a} != {b}"]
        except (TypeError, ValueError):
            return [f"{path} {a!r} != {b!r}"]
    return [] if a == b else [f"{path} {a!r} != {b!r}"]


def check_offline(tmp: Path) -> tuple[str, list[str]]:
    from evals import run_evals
    committed = _load("offline.json")
    if committed is None:
        return "missing", ["offline.json not committed: run make eval"]
    run_evals.main_args = None
    sys_argv = sys.argv
    try:
        sys.argv = ["run_evals.py", "--out-dir", str(tmp)]
        run_evals.main()
    finally:
        sys.argv = sys_argv
    fresh = json.loads((tmp / "offline.json").read_text(encoding="utf-8"))
    return "rerun", diff({k: committed[k] for k in ("guard", "critic", "reports", "routes")},
                         {k: fresh[k] for k in ("guard", "critic", "reports", "routes")})


def check_pii() -> tuple[str, list[str]]:
    from evals import eval_pii
    committed = _load("pii.json")
    if committed is None:
        return "missing", ["pii.json not committed: run make eval"]
    rows = [json.loads(x) for x in eval_pii.CASES.read_text(encoding="utf-8").splitlines() if x.strip()]
    return "rerun", diff(committed["redactors"], eval_pii.evaluate(rows))


def check_route_baselines() -> tuple[str, list[str]]:
    from athena.data.source import LocalSource
    from evals import eval_route_baselines as rb
    committed = (_load("route_baselines.json") or {}).get("datasets", {}).get("sample")
    if committed is None:
        return "missing", ["route_baselines.json has no sample entry: run make eval"]
    fresh = rb.evaluate(LocalSource(data_dir=ROOT / "data"))
    return "rerun", diff({"summary": committed["summary"], "rows": committed["rows"]},
                         {"summary": fresh["summary"], "rows": fresh["rows"]})


def check_injection_e2e() -> tuple[str, list[str]]:
    from athena.agents import Athena
    from athena.classify.baseline import KeywordClassifier
    from athena.config import get_settings
    from athena.data.source import LocalSource
    from athena.llm import SampleLLM
    from evals import eval_injection_e2e as e2e
    committed = (_load("injection_e2e.json") or {}).get("runs", {})
    base = {k: v for k, v in committed.items() if k.startswith("baseline:")}
    if not base:
        return "missing", ["injection_e2e.json has no offline baselines: run make eval"]
    src = LocalSource(data_dir=ROOT / "data")
    street = "George Street" if "George Street" in {s["name"] for s in src.streets()[0]} else src.streets()[0][0]["name"]
    problems = []
    for label, rec in base.items():
        a = Athena(get_settings(), SampleLLM(), src)
        if "keyword" in label:
            a.classifier = KeywordClassifier()
        fresh = e2e.run(a, street, label)
        keys = ("injection_admit_rate", "benign_hold_rate", "injection_admitted_ids", "actions")
        problems += diff({k: rec[k] for k in keys}, {k: fresh[k] for k in keys}, label)
    return "rerun", problems


def check_quality() -> tuple[str, list[str]]:
    from athena.data.source import read_table
    from pipeline.check import REQUIRED, quality
    committed = (_load("data_quality.json") or {}).get("datasets", {}).get("sample")
    if committed is None:
        return "missing", ["data_quality.json has no sample entry: run make check"]
    tables = {n: read_table(ROOT / "data", n) for n in REQUIRED}
    fresh = quality(tables, ROOT / "data")
    return "rerun", diff({"tables": committed["tables"], "failures": committed["failures"]},
                         {"tables": fresh["tables"], "failures": fresh["failures"]})


def check_assertions() -> tuple[str, list[str]]:
    from evals import assertions
    committed = _load("assertions.json")
    if committed is None:
        return "missing", ["assertions.json not committed"]
    folder = ROOT / committed["config"]["traces"]
    if not folder.is_dir():
        return "missing", [f"trace folder {committed['config']['traces']} not committed"]
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
    fresh = assertions.run_all(recs)
    return "rescore", diff({k: committed[k] for k in ("traces", "traces_passing_all", "assertions")}, fresh)


def check_model_comparison() -> tuple[str, list[str]]:
    committed = _load("model_comparison.json")
    if committed is None:
        return "missing", ["model_comparison.json not committed"]
    problems = []
    for model, r in committed["results"]["explainer"].items():
        rows = r["rows"]
        rate = round(sum(x["pass"] for x in rows) / len(rows), 4) if rows else None
        if rate != r["first_draft_pass_rate"] or len(rows) != r["n"]:
            problems.append(f"{model}: summary {r['first_draft_pass_rate']} vs recount {rate}")
    return "rescore", problems


def check_classifier_probes() -> tuple[str, list[str]]:
    from evals import eval_classifier as ec
    committed = _load("classifier_probes.json")
    if committed is None:
        return "missing", ["classifier_probes.json not committed: python evals/eval_classifier.py --split probes --offline"]
    offline = [r for r in committed["results"] if r["candidate"] in ec.OFFLINE]
    fresh = ec.run("probes", [r["candidate"] for r in offline])["results"]
    keys = ("candidate", "n", "tnr_hold", "tpr_admit", "category_macro_f1", "rows")
    return "rerun", diff([{k: r[k] for k in keys} for r in offline], [{k: r[k] for k in keys} for r in fresh])


def check_frozen() -> tuple[str, list[str]]:
    from pipeline.lineage import verify_frozen
    res = verify_frozen(ROOT / "evals" / "checksums.txt", ROOT)
    return "rerun", [f"{rel} changed" for rel, ok in res if not ok]


def check_rebuild() -> tuple[str, list[str]]:
    from pipeline.verify import verify
    res = verify()
    if res["status"] == "cannot-run":
        return "skipped", [res["reason"]]
    return "rebuild", [c["check"] for c in res["checks"] if not c["ok"]]


CHECKS = [("offline.json", check_offline), ("pii.json", check_pii), ("route_baselines.json", check_route_baselines),
          ("injection_e2e.json", check_injection_e2e), ("data_quality.json", check_quality),
          ("assertions.json", check_assertions), ("model_comparison.json", check_model_comparison),
          ("classifier_probes.json", check_classifier_probes),
          ("evals/checksums.txt", check_frozen), ("data/processed (rebuild)", check_rebuild)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="write evals/results/reproduce.json")
    args = ap.parse_args(argv)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        import athena.analysis as analysis
        saved = analysis.REPORT_QUEUE
        analysis.REPORT_QUEUE = Path(tmp) / "queue.jsonl"
        try:
            for name, fn in CHECKS:
                try:
                    kind, problems = fn(Path(tmp)) if fn is check_offline else fn()
                except Exception as exc:  # noqa: BLE001 - report, keep going
                    kind, problems = "error", [f"{exc.__class__.__name__}: {exc}"]
                status = ("SKIP" if kind in ("skipped", "missing") else "SAME" if not problems else "DIFFERS")
                rows.append({"result": name, "how": kind, "status": status, "details": problems[:10]})
        finally:
            analysis.REPORT_QUEUE = saved
    for name, cmd in LIVE.items():
        rows.append({"result": name, "how": "input", "status": "COMMITTED" if (RESULTS / name).exists() else "NOT RUN",
                     "details": [cmd]})
    for r in rows:
        print(f"{r['status']:9} {r['how']:8} {r['result']:28} {'; '.join(r['details'][:2])}")
    bad = [r for r in rows if r["status"] == "DIFFERS" or r["how"] == "error"]
    print(f"reproduce: {sum(r['status'] == 'SAME' for r in rows)} regenerated and identical, {len(bad)} differ")
    if args.record:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        (RESULTS / "reproduce.json").write_text(json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None, "calls_used": 0,
             "config": {"data": "data/ (sample)", "sample_mode": True}, "checks": rows}, indent=1) + "\n", encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
