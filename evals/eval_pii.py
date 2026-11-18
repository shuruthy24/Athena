"""PII redaction evaluation (Data practices: privacy). Offline, no model calls; part of `make eval`.

  python evals/eval_pii.py        -> evals/results/pii.json

Cases: evals/privacy/pii_cases_v1.jsonl (synthetic, fictional people and numbers, expected spans by construction,
frozen in evals/checksums.txt). Redactors compared on the same texts:

  app_guard        athena.guard.inspect_text: what runs on every report before any model call
  ingest_cleaner   pipeline.clean.remove_pii: what runs on labelled report text at ingest (adds addresses, URLs,
                   landlines, card numbers, IP addresses)
  none             baseline: no redaction

Metrics, fixed before the first run: recall = share of PII spans that no longer appear in the output, per kind and
overall; over-redaction rate = share of PII-free texts that the redactor changed. Names and student ids are in the
set on purpose. Their patterns (athena/guard.py) were written against this regression set, so recall here is
optimistic; a name with no introduction is not found, and the labelling guide relies on a human reading each report.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.guard import inspect_text  # noqa: E402
from evals.metrics import rate, wilson  # noqa: E402
from pipeline.clean import remove_pii  # noqa: E402

CASES = ROOT / "evals" / "privacy" / "pii_cases_v1.jsonl"
OUT = ROOT / "evals" / "results" / "pii.json"
REDACTORS = {"app_guard": lambda t: inspect_text(t)[0], "ingest_cleaner": lambda t: remove_pii(t)[0], "none": lambda t: t}


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def evaluate(rows: list[dict]) -> dict:
    out = {}
    for name, fn in REDACTORS.items():
        by_kind: dict[str, list[bool]] = {}
        misses, over = [], []
        for r in rows:
            red = fn(r["text"])
            for span in r["pii"]:
                removed = _norm(span["value"]) not in _norm(red)
                by_kind.setdefault(span["kind"], []).append(removed)
                if not removed:
                    misses.append({"id": r["id"], "kind": span["kind"]})
            if not r["pii"] and red != r["text"]:
                over.append({"id": r["id"], "output": red, "note": r.get("note")})
        spans = [x for v in by_kind.values() for x in v]
        clean = [r for r in rows if not r["pii"]]
        out[name] = {"recall": rate(sum(spans), len(spans)), "recall_ci95": wilson(sum(spans), len(spans)),
                     "recall_by_kind": {k: {"removed": sum(v), "n": len(v)} for k, v in sorted(by_kind.items())},
                     "over_redaction_rate": rate(len(over), len(clean)),
                     "over_redaction_rate_ci95": wilson(len(over), len(clean)),
                     "missed": misses, "over_redacted": over}
    return out


def main() -> int:
    rows = [json.loads(x) for x in CASES.read_text(encoding="utf-8").splitlines() if x.strip()]
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None, "calls_used": 0,
           "config": {"cases": str(CASES.relative_to(ROOT)), "n_texts": len(rows),
                      "n_pii_spans": sum(len(r["pii"]) for r in rows), "n_clean_texts": sum(not r["pii"] for r in rows)},
           "redactors": evaluate(rows)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, r in res["redactors"].items():
        print(f"{name:15} recall {r['recall']} {r['recall_ci95']}  over-redaction {r['over_redaction_rate']}  "
              f"missed kinds {sorted({m['kind'] for m in r['missed']})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
