"""Deterministic regression assertions over saved explanation traces (AGENTS.md Step 6, Tech 6 section 3).

  python evals/assertions.py evals/traces/<run>        # writes evals/results/assertions.json

Each assertion is a named, objective check that code can make from the saved trace. The first five are the
existing check_claims rules, now run on real model output; the rest add what the critic does not check. Add
one assertion per new code-checkable failure the team finds in evals/analysis.csv, with a passing and a
failing test in tests/test_explanation_evals.py.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.agents import BANNED, Athena  # noqa: E402
from athena.config import get_settings  # noqa: E402
from athena.data.source import LocalSource  # noqa: E402
from athena.llm import SampleLLM  # noqa: E402

UNKNOWN_DISCLOSED = re.compile(r"\b(unknown|no) lighting\b|\blighting (data|information) (is )?(missing|unknown)\b|"
                               r"\bno lighting data\b|\bunknown\b", re.I)


@dataclass
class AssertionResult:
    name: str
    passed: bool
    detail: str = ""


_CHECKER: Athena | None = None


def _rules(rec: dict) -> list[dict]:
    global _CHECKER
    if _CHECKER is None:
        _CHECKER = Athena(get_settings(), SampleLLM(), LocalSource())
    return _CHECKER.check_claims(rec["explanation"]["claims"], rec["facts"])


def _problems(rec: dict, *needles: str) -> list[str]:
    return [p for r in _rules(rec) for p in r["problems"] if any(n in p for n in needles)]


def claims_cite_known_facts(rec: dict) -> AssertionResult:
    bad = _problems(rec, "cites no facts", "unknown fact keys")
    return AssertionResult("claims_cite_known_facts", not bad, "; ".join(bad))


def numbers_match_facts(rec: dict) -> AssertionResult:
    bad = _problems(rec, "not in cited facts")
    return AssertionResult("numbers_match_facts", not bad, "; ".join(bad))


def no_banned_wording(rec: dict) -> AssertionResult:
    texts = [rec["explanation"].get("headline") or ""] + [c.get("text", "") for c in rec["explanation"]["claims"]]
    hits = sorted({m.group(0).lower() for t in texts for m in BANNED.finditer(t)})
    return AssertionResult("no_banned_wording", not hits, ", ".join(hits))


def streets_on_routes(rec: dict) -> AssertionResult:
    bad = _problems(rec, "on neither route")
    return AssertionResult("streets_on_routes", not bad, "; ".join(bad))


def direction_correct(rec: dict) -> AssertionResult:
    bad = _problems(rec, "lower risk")
    return AssertionResult("direction_correct", not bad, "; ".join(bad))


def claim_count(rec: dict) -> AssertionResult:
    n = len(rec["explanation"]["claims"])
    return AssertionResult("claim_count_1_to_4", 1 <= n <= 4, f"{n} claims")


def unknown_lighting_disclosed(rec: dict) -> AssertionResult:
    f = rec["facts"]
    unknown = max(f.get("fastest.unknown_share_pct") or 0, f.get("athena.unknown_share_pct") or 0)
    if unknown <= 15:
        return AssertionResult("unknown_lighting_disclosed", True, f"unknown {unknown}% (not required)")
    said = any(UNKNOWN_DISCLOSED.search(c.get("text", "")) for c in rec["explanation"]["claims"])
    return AssertionResult("unknown_lighting_disclosed", said, f"unknown {unknown}%")


def model_steps_observable(rec: dict) -> AssertionResult:
    """Every model step names its model, prompt version and token counts (week 1 gateways: log every call)."""
    steps = [s for s in rec["trace"] if s.get("model")]
    missing = [s["step"] for s in steps if not s.get("prompt") or s.get("tokens_in") is None]
    return AssertionResult("model_steps_observable", not missing, f"steps missing usage/prompt: {missing}")


ASSERTIONS = [claims_cite_known_facts, numbers_match_facts, no_banned_wording, streets_on_routes,
              direction_correct, claim_count, unknown_lighting_disclosed, model_steps_observable]


def run_all(recs: list[dict]) -> dict:
    per = {a.__name__: {"passed": 0, "failed": 0, "failures": []} for a in ASSERTIONS}
    for rec in recs:
        for a in ASSERTIONS:
            r = a(rec)
            per[a.__name__]["passed" if r.passed else "failed"] += 1
            if not r.passed:
                per[a.__name__]["failures"].append({"trace_id": rec["trace_id"], "detail": r.detail})
    traces_all_pass = sum(1 for rec in recs if all(a(rec).passed for a in ASSERTIONS))
    return {"traces": len(recs), "traces_passing_all": traces_all_pass, "assertions": per}


def main(argv=None) -> int:
    folder = Path((argv or sys.argv[1:] or [""])[0])
    if not folder.is_dir():
        print("usage: python evals/assertions.py evals/traces/<run>")
        return 2
    recs = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]
    res = run_all(recs)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None,
           "config": {"traces": str(folder)}, "calls_used": 0, **res}
    (ROOT / "evals/results/assertions.json").write_text(json.dumps(out, indent=1) + "\n")
    for name, r in res["assertions"].items():
        print(f"{name:28} {r['passed']}/{r['passed'] + r['failed']}")
    print(f"traces passing every assertion: {res['traces_passing_all']}/{res['traces']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

