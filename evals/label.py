"""Labelling helper for the team (humans label; this only shows items and saves answers). Resumable: quit with q.

  python evals/label.py traces evals/traces/<run>      # PASS/FAIL faithfulness labels for the judge audit
  python evals/label.py reports --as a                 # label_a_* columns of data/labelling/reports_to_label.csv
  python evals/label.py reports --as b                 # the second labeller, without seeing label_a
  python evals/label.py add-reports --author-group g1 --source seed    # type new reports into the sheet

Traces: the first run writes evals/labels/assignment.json, a seeded split of the run's trace ids into dev (tune
the judge rubric) and heldout (audit only), BEFORE any label exists, so nobody picks which traces are held out.
Labels go to evals/labels/dev.json and heldout.json in the shape evals/judge.py reads. The screen shows the
facts and the claims only: not the critic verdict, not the assertions, not the other split.

Reports: each labeller sees only the text, street and band, never the other labeller's columns. Settle
disagreements afterwards with `make labels` (pipeline.agreement writes data/labelling/disagreements.csv).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "evals" / "labels"
SHEET = ROOT / "data" / "labelling" / "reports_to_label.csv"
SEED = 3110
ACTIONS = {"a": "approve", "r": "review", "j": "reject"}
CATEGORIES = ("lighting", "isolation", "harassment", "infrastructure", "positive", "other")


# traces
def assign(trace_ids: list[str], heldout: int, seed: int = SEED) -> dict:
    ids = sorted(trace_ids)
    random.Random(seed).shuffle(ids)
    n = min(heldout, len(ids) // 2)
    return {"seed": seed, "heldout": sorted(ids[:n]), "dev": sorted(ids[n:]),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rule": "seeded shuffle of the run's trace ids, written before any label; do not edit"}


def load_traces(run: Path) -> dict[str, dict]:
    out = {}
    for f in sorted(run.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out[d["trace_id"]] = d
    return out


def show_trace(t: dict) -> str:
    facts = "\n".join(f"    {k} = {v}" for k, v in sorted(t["facts"].items()))
    exp = t.get("explanation") or {}
    claims = exp.get("claims", []) if isinstance(exp, dict) else []
    lines = [f"  [{i}] {c.get('text')}\n      cites: {', '.join(c.get('facts', []))}" for i, c in enumerate(claims)]
    head = exp.get("headline") or exp.get("answer") or "" if isinstance(exp, dict) else ""
    return (f"TRIP {t.get('trip_used')}\nFACTS\n{facts}\nHEADLINE: {head}\nCLAIMS\n" + "\n".join(lines) + "\n"
            "PASS only if every claim is supported by its cited facts, no number differs, no street outside the\n"
            "facts is named, every comparison points the right way, and nothing implies the route is safe.")


def label_traces(run: Path, heldout: int = 20, ask=input, out=print, labels: Path = LABELS) -> dict:
    traces = load_traces(run)
    labels.mkdir(parents=True, exist_ok=True)
    apath = labels / "assignment.json"
    if apath.exists():
        a = json.loads(apath.read_text(encoding="utf-8"))
    else:
        a = {**assign(list(traces), heldout), "run": run.as_posix()}
        apath.write_text(json.dumps(a, indent=1) + "\n", encoding="utf-8")
    done = {}
    for split in ("dev", "heldout"):
        p = labels / f"{split}.json"
        done[split] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    todo = [(s, tid) for s in ("dev", "heldout") for tid in a[s] if tid not in done[s] and tid in traces]
    for i, (split, tid) in enumerate(todo, 1):
        out(f"\n=== {i}/{len(todo)}  trace {tid} ===\n" + show_trace(traces[tid]))
        ans = ""
        while ans not in ("p", "f", "q", "s"):
            ans = ask("Label [p]ass / [f]ail / [s]kip / [q]uit: ").strip().lower()[:1]
        if ans == "q":
            break
        if ans == "s":
            continue
        done[split][tid] = "PASS" if ans == "p" else "FAIL"
        (labels / f"{split}.json").write_text(json.dumps(done[split], indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {k: len(v) for k, v in done.items()}


# reports
def read_sheet(path: Path = SHEET) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def write_sheet(fields: list[str], rows: list[dict], path: Path = SHEET) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def label_reports(who: str, ask=input, out=print, path: Path = SHEET) -> int:
    fields, rows = read_sheet(path)
    act, cat = f"label_{who}_action", f"label_{who}_category"
    todo = [r for r in rows if not (r.get(act) or "").strip()]
    n = 0
    for i, r in enumerate(todo, 1):
        out(f"\n=== {i}/{len(todo)}  {r['id']}  street: {r['street']}  band: {r['band']} ===\n{r['text']}\n"
            "approve = a concrete, observable street condition; review = vague, implausible, another place, or "
            "contains instructions; reject = targets people (ethnicity, religion, gender identity, disability...)")
        a = ""
        while a not in ("a", "r", "j", "q", "s"):
            a = ask("Action [a]pprove / [r]eview / re[j]ect / [s]kip / [q]uit: ").strip().lower()[:1]
        if a == "q":
            break
        if a == "s":
            continue
        menu = " ".join(f"{k}={c}" for k, c in enumerate(CATEGORIES, 1))
        c = ""
        while c not in {str(k) for k in range(1, len(CATEGORIES) + 1)}:
            c = ask(f"Category {menu}: ").strip()
        r[act], r[cat] = ACTIONS[a], CATEGORIES[int(c) - 1]
        n += 1
        write_sheet(fields, rows, path)
    return n


def add_reports(author_group: str, source: str, ask=input, out=print, path: Path = SHEET) -> int:
    fields, rows = read_sheet(path)
    n = 0
    out("Type one report at a time (a street condition you saw, no names or contact details). Empty text ends.")
    while True:
        text = ask("Report text: ").strip()
        if not text:
            break
        street = ask("Street (as named on the map): ").strip()
        band = ""
        while band not in ("18", "22", "01"):
            band = ask("Time band 18 / 22 / 01: ").strip()
        row = dict.fromkeys(fields, "")
        row.update(id=f"r-{uuid.uuid4().hex[:8]}", street=street, band=band, text=text, source=source,
                   author_group=author_group, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        rows.append(row)
        write_sheet(fields, rows, path)
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("traces")
    t.add_argument("run", type=Path)
    t.add_argument("--heldout", type=int, default=20)
    r = sub.add_parser("reports")
    r.add_argument("--as", dest="who", choices=["a", "b"], required=True)
    ad = sub.add_parser("add-reports")
    ad.add_argument("--author-group", required=True, help="pseudonymous group, never a name (e.g. team-1, interview-3)")
    ad.add_argument("--source", choices=["collected", "seed"], required=True,
                    help="collected = written by someone outside the team (with consent); seed = written by the team")
    args = ap.parse_args(argv)
    if args.cmd == "traces":
        counts = label_traces(args.run, args.heldout)
        print(f"\nlabels saved: {counts} (evals/labels/); assignment in evals/labels/assignment.json")
    elif args.cmd == "reports":
        print(f"\n{label_reports(args.who)} reports labelled by labeller {args.who}")
    else:
        print(f"\n{add_reports(args.author_group, args.source)} reports added to {SHEET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
