"""LLM judge for explanation faithfulness, audited against human labels (AGENTS.md Step 6, Tech 6 section 4A).

  python evals/judge.py audit evals/traces/<run>     # budget 60 calls; writes evals/results/judge_audit.json
  python evals/judge.py audit evals/traces/<run> --labels evals/labels-<name>
                                                     # another labelled set; writes evals/results/judge_audit_<name>.json

WARNING: this writes a canonical, committed result file unconditionally and calls the live gateway with whatever
.env the shell has. Never run it to "check" a number: `python scripts/reproduce.py` (or `make reproduce` /
`make check`) re-checks committed results without writing them. Only run it on purpose, to produce a new audit
from new human labels, and commit the result with the labels. `--out` cannot point at judge_audit.json unless
the labels are the canonical evals/labels/ set, so a secondary audit can never overwrite the primary one
(docs/evaluation.md, "Results that are written to fixed paths").

Human labels (the team writes these; never an agent):
  evals/labels/dev.json       {"<trace_id>": "PASS" | "FAIL", ...}  used to tune the rubric only
  evals/labels/heldout.json   same shape; read only AFTER every judge prediction is collected

Contract, as in Tech 6: the reply is exactly one JSON object with exactly "verdict" and a non-empty "reason";
binary verdicts PASS|FAIL, pairwise A|B|TIE; Markdown fences, prose, duplicate or extra fields are rejected
and never repaired. Positive class = human PASS. TPR and TNR keep malformed replies in the denominator as
wrong. One deterministic request per case (temperature 0, no fallback model: the judge is measured as
itself). The judge model should be a different family from the explainer (JUDGE_MODEL).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.prompts import load  # noqa: E402
from evals.metrics import BudgetExceeded, CallBudget, tpr_tnr  # noqa: E402

LABELS = ROOT / "evals" / "labels"
CANONICAL_OUT = ROOT / "evals" / "results" / "judge_audit.json"


class JudgeOutputError(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise JudgeOutputError("reply repeats a field")
    return dict(pairs)


def check_output(raw: str, allowed: list[str]) -> dict[str, str]:
    try:
        data = json.loads(raw, object_pairs_hook=_no_duplicates)
    except (json.JSONDecodeError, TypeError):
        raise JudgeOutputError("reply is not exactly one JSON object") from None
    if not isinstance(data, dict) or set(data) != {"verdict", "reason"}:
        raise JudgeOutputError("reply must have exactly 'verdict' and 'reason'")
    if data["verdict"] not in allowed:
        raise JudgeOutputError(f"verdict must be one of {allowed}")
    if not isinstance(data["reason"], str) or not data["reason"].strip():
        raise JudgeOutputError("reason must be a non-empty string")
    return {"verdict": data["verdict"], "reason": data["reason"]}


def parse_binary(raw: str) -> dict[str, str]:
    return check_output(raw, ["PASS", "FAIL"])


def parse_pairwise(raw: str) -> dict[str, str]:
    return check_output(raw, ["A", "B", "TIE"])


def binary_user_message(case_id: str, explanation: dict, facts: dict) -> str:
    """Everything the judge sees for one case. No human label, no model name, no critic verdict."""
    return json.dumps({"case_id": case_id, "explanation": explanation, "facts": facts}, ensure_ascii=False)


def pairwise_user_message(presentation_id: str, a: dict, b: dict, facts: dict) -> str:
    return json.dumps({"presentation_id": presentation_id, "explanation_A": a, "explanation_B": b, "facts": facts},
                      ensure_ascii=False)


def binary_metrics(human: list[str], judge: list[str | None]) -> dict:
    m = tpr_tnr([h == "PASS" for h in human], [None if j is None else j == "PASS" for j in judge])
    return {"tpr": m["tpr"], "tnr": m["tnr"], "malformed": m["malformed"], "n": len(human),
            "false_passes": sum(1 for h, j in zip(human, judge) if h == "FAIL" and j == "PASS"),
            "false_fails": sum(1 for h, j in zip(human, judge) if h == "PASS" and j == "FAIL")}


def judge_cases(llm, model: str, recs: list[dict]) -> list[dict]:
    prompt = load("judge_binary")
    out = []
    for rec in recs:
        raw, verdict, reason, error = None, None, None, None
        try:
            reply = llm.chat(model, prompt.body, binary_user_message(rec["trace_id"], rec["explanation"], rec["facts"]),
                             role="judge_binary")
            raw = reply.text
            parsed = parse_binary(raw)
            verdict, reason = parsed["verdict"], parsed["reason"]
        except JudgeOutputError as exc:
            error = str(exc)
        except RuntimeError as exc:  # LLMError: the call itself failed; still counted as wrong
            error = f"call failed: {exc}"
        out.append({"trace_id": rec["trace_id"], "judge": verdict, "reason": reason, "error": error, "raw": raw})
    return out


def audit(llm, model: str, trace_dir: Path, labels_dir: Path = LABELS, budget: int = 60) -> dict:
    heldout_path = labels_dir / "heldout.json"
    if not heldout_path.exists():
        raise FileNotFoundError(f"{heldout_path} is missing: the team labels held-out traces first")
    heldout_ids = sorted(json.loads(heldout_path.read_text(encoding="utf-8")))  # ids only until predictions are in
    recs = {json.loads(p.read_text(encoding="utf-8"))["trace_id"]: json.loads(p.read_text(encoding="utf-8")) for p in trace_dir.glob("*.json")}
    missing = [t for t in heldout_ids if t not in recs]
    if missing:
        raise KeyError(f"held-out ids without a saved trace: {missing[:5]}")
    counted = CallBudget(llm, budget)
    try:
        outcomes = judge_cases(counted, model, [recs[t] for t in heldout_ids])
    except BudgetExceeded as exc:
        raise RuntimeError(f"{exc}: the audit needs one call per held-out case") from exc
    human = json.loads(heldout_path.read_text(encoding="utf-8"))  # labels revealed only now
    for o in outcomes:
        o["human"] = human[o["trace_id"]]
    metrics = binary_metrics([o["human"] for o in outcomes], [o["judge"] for o in outcomes])
    prompt = load("judge_binary")
    return {"config": {"model": model, "prompt": f"{prompt.id}@{prompt.sha}", "budget": budget,
                       "positive_class": "human PASS", "traces": str(trace_dir), "labels": _rel(labels_dir)},
            "calls_used": counted.used, "binary": {**metrics, "outcomes": outcomes}}


def _rel(p: Path) -> str:
    p = Path(p).resolve()
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)


def resolve_out(labels_dir: Path, out: Path | None = None) -> Path:
    """Where an audit is written. Only the canonical labels (evals/labels/) may write judge_audit.json; any other
    labels set defaults to judge_audit_<suffix>.json (evals/labels-sample -> judge_audit_sample.json)."""
    canonical_labels = Path(labels_dir).resolve() == LABELS.resolve()
    if out is None:
        if canonical_labels:
            return CANONICAL_OUT
        name = Path(labels_dir).resolve().name
        suffix = name[len("labels-"):] if name.startswith("labels-") else name
        return CANONICAL_OUT.with_name(f"judge_audit_{suffix}.json")
    out = Path(out)
    if out.resolve() == CANONICAL_OUT.resolve() and not canonical_labels:
        raise ValueError(f"refusing to write {_rel(CANONICAL_OUT)} from labels in {_rel(labels_dir)}: "
                         "it holds the primary audit on evals/labels/; choose another --out")
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python evals/judge.py", description="audit the LLM judge on held-out labels")
    ap.add_argument("command", choices=["audit"])
    ap.add_argument("traces", type=Path, help="evals/traces/<run>")
    ap.add_argument("--labels", type=Path, default=LABELS, help="folder with heldout.json (default evals/labels)")
    ap.add_argument("--out", type=Path, default=None,
                    help="result file (default judge_audit.json for evals/labels, else judge_audit_<suffix>.json)")
    args = ap.parse_args(argv)
    try:
        out = resolve_out(args.labels, args.out)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    from athena.config import get_settings
    from athena.llm import GatewayLLM
    s = get_settings()
    llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"], timeout_s=s.llm_timeout_s)
    res = audit(llm, s.judge_model, args.traces, labels_dir=args.labels)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None, **res}
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    b = res["binary"]
    print(f"judge {s.judge_model}: TPR {b['tpr']} TNR {b['tnr']} malformed {b['malformed']} on {b['n']} held-out cases"
          f" -> {_rel(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
