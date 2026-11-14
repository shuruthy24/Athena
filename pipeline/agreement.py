"""Labeller agreement for the street reports (AGENTS.md Step 3).

  python -m pipeline.agreement
    reads   data/labelling/reports_to_label.csv
    writes  evals/results/label_agreement.json    Cohen's kappa for action and category, counts
            data/labelling/disagreements.csv      rows where the labellers differ, to settle together
            data/reports/labelled.jsonl           rows with an agreed or settled label (input to the split)

A row's final label is the shared label when both labellers agree, otherwise the human-filled final_*
columns. Rows with a disagreement and no final label are left out of labelled.jsonl, never guessed.
Report text is cleaned here (PII removed at ingest), before anything downstream reads it.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from athena.schemas import Report

from .clean import drop_duplicates, drop_missing, find_conflicts, fix_encoding, normalise, remove_pii

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "data" / "labelling" / "reports_to_label.csv"
ACTIONS = ("approve", "review", "reject")
CATEGORIES = ("lighting", "isolation", "harassment", "infrastructure", "positive", "other")


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa for two labellers on the same items. None when undefined (no items, or chance
    agreement is 1 because both used a single label throughout)."""
    if not a or len(a) != len(b):
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def _norm(v: str | None) -> str:
    return (v or "").strip().lower()


def read_sheet(path: Path = SHEET) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def analyse(rows: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """(agreement summary, disagreement rows, labelled rows)."""
    both = [r for r in rows if _norm(r.get("label_a_action")) and _norm(r.get("label_b_action"))]
    summary = {"rows": len(rows), "double_labelled": len(both)}
    for field in ("action", "category"):
        a = [_norm(r[f"label_a_{field}"]) for r in both]
        b = [_norm(r[f"label_b_{field}"]) for r in both]
        k = cohen_kappa(a, b)
        summary[f"kappa_{field}"] = None if k is None else round(k, 3)
        summary[f"agreement_{field}"] = round(sum(x == y for x, y in zip(a, b)) / len(a), 3) if a else None
    disagreements, labelled = [], []
    for r in both:
        la = (_norm(r["label_a_action"]), _norm(r["label_a_category"]))
        lb = (_norm(r["label_b_action"]), _norm(r["label_b_category"]))
        final = (_norm(r.get("final_action")), _norm(r.get("final_category")))
        if la != lb:
            disagreements.append({"id": r["id"], "text": r["text"], "label_a": "/".join(la), "label_b": "/".join(lb),
                                  "final": "/".join(final) if all(final) else ""})
        chosen = la if la == lb else final if all(final) else None
        if chosen and chosen[0] in ACTIONS and chosen[1] in CATEGORIES:
            labelled.append({**r, "action": chosen[0], "category": chosen[1]})
    summary["disagreements"] = len(disagreements)
    summary["unsettled"] = sum(1 for d in disagreements if not d["final"])
    return summary, disagreements, labelled


def clean_reports(rows: list[dict]) -> tuple[list[dict], dict]:
    """Tech 4 order: drop missing -> (labels already canonical) -> conflicts -> duplicates -> encoding,
    with PII removed first. Returns Report-shaped dicts and the counts for the run report."""
    stats: dict = {}
    for r in rows:
        r["text"], kinds = remove_pii(r["text"])
        stats["pii_redacted_rows"] = stats.get("pii_redacted_rows", 0) + bool(kinds)
    rows, stats["missing_dropped"] = drop_missing(rows, ["id", "street", "band", "text", "author_group"])
    conflicts = find_conflicts(rows, ["text"], "action")
    stats["conflicts"] = [c["key"][0][:60] for c in conflicts]  # inspect; resolve in the sheet, not here
    conflict_keys = {c["key"] for c in conflicts}
    rows = [r for r in rows if (normalise(r["text"]),) not in conflict_keys]
    rows, stats["duplicates_dropped"] = drop_duplicates(rows, ["street", "text"])
    rows, stats["encoding_fixed"] = fix_encoding(rows, ["text", "street"])
    out, invalid = [], []
    for r in rows:
        rec = {"id": r["id"], "street": r["street"].strip(), "band": r["band"].strip(), "text": r["text"],
               "category": r["category"], "action": r["action"], "source": _norm(r.get("source")) or "seed",
               "author_group": r["author_group"].strip(), "created_at": r.get("created_at") or "1970-01-01"}
        try:
            out.append(Report.model_validate(rec).model_dump(mode="json"))
        except ValueError:
            invalid.append(r["id"])
    stats["invalid"] = invalid
    return out, stats


def git_sha() -> str | None:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() or None


def main() -> int:
    rows = read_sheet()
    summary, disagreements, labelled = analyse(rows)
    reports, clean_stats = clean_reports(labelled)
    out = ROOT / "data" / "labelling" / "disagreements.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "text", "label_a", "label_b", "final"])
        w.writeheader()
        w.writerows(disagreements)
    labelled_path = ROOT / "data" / "reports" / "labelled.jsonl"
    labelled_path.parent.mkdir(parents=True, exist_ok=True)
    labelled_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reports))
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": git_sha(),
              "config": {"sheet": str(SHEET.relative_to(ROOT))}, "calls_used": 0, **summary,
              "labelled_rows": len(reports), "label_counts": dict(Counter(r["action"] for r in reports)),
              "cleaning": clean_stats}
    res_path = ROOT / "evals" / "results" / "label_agreement.json"
    res_path.parent.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: result[k] for k in ("double_labelled", "kappa_action", "kappa_category", "disagreements",
                                             "unsettled", "labelled_rows")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
