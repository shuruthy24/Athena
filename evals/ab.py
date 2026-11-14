"""Blind pairwise A/B: does the LLM explanation beat the fact-only one? (AGENTS.md Step 6, Tech 6 section 4B)

  python evals/ab.py prepare evals/traces/<run> --n 6   # writes evals/ab/review.md and an empty choices.csv
  (reviewers read ONLY review.md, fill choices.csv: A, B or TIE plus one evidence-based sentence, then commit it)
  python evals/ab.py reveal [--judge]                   # reads the committed choices, reveals the mapping,
                                                        # optionally asks the pairwise judge; writes evals/results/ab.json

Which candidate is A or B is decided per presentation by a seeded shuffle, so A does not consistently mean the
LLM. The mapping is written to evals/ab/mapping.json; do not open it before reveal. review.md records the
mapping's sha256, and reveal checks it, so the mapping cannot be changed after reviewers saw the sheet. reveal
refuses to run until every row has a choice and a rationale and choices.csv is committed, which locks the
human preferences before anyone sees the mapping.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AB = ROOT / "evals" / "ab"
SEED = 3110


def _render(expl: dict) -> str:
    lines = [f"**{expl.get('headline') or ''}**"] + [f"- {c.get('text', '')}" for c in expl.get("claims", [])]
    return "\n".join(lines)


def prepare(trace_dir: Path, n: int = 6, out: Path = AB, seed: int = SEED) -> dict:
    recs = [json.loads(p.read_text()) for p in sorted(trace_dir.glob("*.json"))]
    # Only cases where the user actually saw a model-written explanation that differs from the fact-only one.
    eligible = [r for r in recs if r.get("critic_verdict") == "approve"
                and r["explanation"]["claims"] != r["fact_only"]["claims"]]
    rng = random.Random(seed)
    chosen = sorted(rng.sample(eligible, min(n, len(eligible))), key=lambda r: r["trace_id"])
    presentations, mapping = [], {}
    for i, r in enumerate(chosen, 1):
        pid = f"AB-{i:02d}"
        llm_first = rng.random() < 0.5
        a, b = (r["explanation"], r["fact_only"]) if llm_first else (r["fact_only"], r["explanation"])
        mapping[pid] = {"trace_id": r["trace_id"], "A": "llm" if llm_first else "fact_only",
                        "B": "fact_only" if llm_first else "llm"}
        presentations.append({"presentation_id": pid, "trip": r["case"]["trip"], "question": r["case"]["question"],
                              "A": a, "B": b, "facts": r["facts"]})
    out.mkdir(parents=True, exist_ok=True)
    mapping_text = json.dumps(mapping, indent=1, sort_keys=True) + "\n"
    (out / "mapping.json").write_text(mapping_text)
    digest = hashlib.sha256(mapping_text.encode()).hexdigest()
    (out / "presentations.json").write_text(json.dumps({"mapping_sha256": digest, "presentations": presentations},
                                                       indent=1, ensure_ascii=False) + "\n")
    md = ["# Blind review", "",
          "For each trip, which explanation would help a person deciding how to get home act more confidently?",
          "Prefer the one that is specific, supported by the route facts, and honest about missing data.",
          "Write A, B or TIE and one sentence of evidence in choices.csv. Do not open mapping.json.", "",
          f"Mapping sha256: `{digest}`", ""]
    for p in presentations:
        t = p["trip"]
        md += [f"## {p['presentation_id']}: {t['origin']} to {t['destination']}, band {t['band']}, {t['mode']}", "",
               "### A", "", _render(p["A"]), "", "### B", "", _render(p["B"]), ""]
    (out / "review.md").write_text("\n".join(md))
    with (out / "choices.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["presentation_id", "preference", "rationale"])
        w.writeheader()
        for p in presentations:
            w.writerow({"presentation_id": p["presentation_id"]})
    return {"presentations": len(presentations), "eligible": len(eligible), "mapping_sha256": digest}


def read_choices(path: Path) -> dict[str, dict]:
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    bad = [r["presentation_id"] for r in rows
           if (r.get("preference") or "").strip().upper() not in ("A", "B", "TIE") or not (r.get("rationale") or "").strip()]
    if bad:
        raise ValueError(f"choices.csv incomplete for {bad}: every row needs A, B or TIE and a rationale")
    return {r["presentation_id"]: {"preference": r["preference"].strip().upper(), "rationale": r["rationale"].strip()}
            for r in rows}


def choices_committed(path: Path) -> bool:
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], cwd=ROOT, capture_output=True)
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(path)], cwd=ROOT)
    return tracked.returncode == 0 and clean.returncode == 0


def reveal(out: Path = AB, judge_llm=None, judge_model: str | None = None, require_commit: bool = True) -> dict:
    if require_commit and not choices_committed(out / "choices.csv"):
        raise PermissionError("commit evals/ab/choices.csv before reveal, so the blind choices are locked")
    choices = read_choices(out / "choices.csv")
    pres = json.loads((out / "presentations.json").read_text())
    mapping_text = (out / "mapping.json").read_text()
    if hashlib.sha256(mapping_text.encode()).hexdigest() != pres["mapping_sha256"]:
        raise ValueError("mapping.json changed after the review sheet was made")
    mapping = json.loads(mapping_text)
    human = {"llm": 0, "fact_only": 0, "tie": 0, "rows": []}
    for pid, c in sorted(choices.items()):
        winner = "tie" if c["preference"] == "TIE" else mapping[pid][c["preference"]]
        human[winner] += 1
        human["rows"].append({"presentation_id": pid, **c, "preferred": winner, "mapping": mapping[pid]})
    result = {"human": human, "judge": None, "calls_used": 0}
    if judge_llm is not None:
        from athena.prompts import load
        from evals.judge import JudgeOutputError, pairwise_user_message, parse_pairwise
        prompt = load("judge_pairwise")
        rows = []
        for p in pres["presentations"]:
            result["calls_used"] += 1
            raw, verdict, error = None, None, None
            try:
                raw = judge_llm.chat(judge_model, prompt.body,
                                     pairwise_user_message(p["presentation_id"], p["A"], p["B"], p["facts"]),
                                     role="judge_pairwise").text
                verdict = parse_pairwise(raw)["verdict"]
            except (JudgeOutputError, RuntimeError) as exc:
                error = str(exc)
            m = mapping[p["presentation_id"]]
            rows.append({"presentation_id": p["presentation_id"], "verdict": verdict, "error": error, "raw": raw,
                         "preferred": None if verdict is None else "tie" if verdict == "TIE" else m[verdict],
                         "llm_shown_as": "A" if m["A"] == "llm" else "B"})
        result["judge"] = {"model": judge_model, "prompt": f"{prompt.id}@{prompt.sha}", "rows": rows,
                           "agrees_with_humans": sum(1 for r in rows for h in human["rows"]
                                                     if h["presentation_id"] == r["presentation_id"]
                                                     and h["preferred"] == r["preferred"])}
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prepare")
    p1.add_argument("trace_dir")
    p1.add_argument("--n", type=int, default=6)
    p2 = sub.add_parser("reveal")
    p2.add_argument("--judge", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "prepare":
        print(json.dumps(prepare(Path(args.trace_dir), args.n), indent=1))
        return 0
    llm, model = None, None
    if args.judge:
        from athena.config import get_settings
        from athena.llm import GatewayLLM
        s = get_settings()
        llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"], timeout_s=s.llm_timeout_s)
        model = s.judge_model
    res = reveal(judge_llm=llm, judge_model=model)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None,
           "config": {"judge": bool(args.judge), "judge_model": model}, **res}
    (ROOT / "evals/results/ab.json").write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    h = res["human"]
    print(f"human preferences: LLM {h['llm']}, fact-only {h['fact_only']}, tie {h['tie']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
