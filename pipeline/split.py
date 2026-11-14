"""Group split of the labelled reports (AGENTS.md Step 3, week 2 holdouts by group).

  python -m pipeline.split              # data/reports/labelled.jsonl -> train/dev/test.jsonl + evals/checksums.txt
  python -m pipeline.split --refreeze   # only with team agreement: replaces a frozen test split

Reports that share a street or an author_group are joined into one group (union-find), and whole groups go
to one split, so no street and no writer appears in two splits. Synthetic rows only ever go to train.
Groups with the most reports collected from people outside the team go to test first (the Step 3
caveat), then dev, then train, aiming at 60/20/20 by rows. Deterministic: the same input gives the same
split. The test split is frozen by its sha256 in evals/checksums.txt and is not rewritten without
--refreeze (changing the split is an ask-first action).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reports"
CHECKSUMS = ROOT / "evals" / "checksums.txt"
SEED = 3110
SHARES = {"test": 0.20, "dev": 0.20}


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def groups(rows: list[dict]) -> list[list[int]]:
    """Row indexes grouped so that any shared street or author_group lands in the same group."""
    uf = _UnionFind(len(rows))
    first: dict[tuple[str, str], int] = {}
    for i, r in enumerate(rows):
        for key in (("street", r["street"].strip().lower()), ("author", r["author_group"].strip().lower())):
            if key in first:
                uf.union(i, first[key])
            else:
                first[key] = i
    out: dict[int, list[int]] = {}
    for i in range(len(rows)):
        out.setdefault(uf.find(i), []).append(i)
    return sorted(out.values(), key=lambda g: min(g))


def split(rows: list[dict], seed: int = SEED) -> dict[str, list[dict]]:
    human = [r for r in rows if r.get("source") != "synthetic"]
    synthetic = [r for r in rows if r.get("source") == "synthetic"]
    gs = groups(human)
    rng = random.Random(seed)
    rng.shuffle(gs)
    # Groups richest in collected reports first, so test favours reports from people outside the team.
    gs.sort(key=lambda g: -sum(human[i].get("source") == "collected" for i in g) / len(g))
    target = {k: v * len(human) for k, v in SHARES.items()}
    out: dict[str, list[dict]] = {"train": [], "dev": [], "test": []}
    for g in gs:
        members = [human[i] for i in g]
        for name in ("test", "dev"):
            if len(out[name]) + len(members) <= target[name] or not out[name] and len(members) <= 2 * target[name]:
                out[name] += members
                break
        else:
            out["train"] += members
    out["train"] += synthetic  # synthetic variants are training volume only, never evaluation data
    for name in out:
        out[name].sort(key=lambda r: r["id"])
    return out


def leaks(parts: dict[str, list[dict]]) -> list[str]:
    """Streets or writers that appear in more than one split (must be empty)."""
    seen: dict[tuple[str, str], str] = {}
    problems = []
    for name, rows in parts.items():
        for r in rows:
            if r.get("source") == "synthetic":
                continue
            for key in (("street", r["street"].strip().lower()), ("author", r["author_group"].strip().lower())):
                if key in seen and seen[key] != name:
                    problems.append(f"{key[0]} {key[1]!r} in {seen[key]} and {name}")
                seen.setdefault(key, name)
    return sorted(set(problems))


def write_split(parts: dict[str, list[dict]], out_dir: Path = REPORTS, checksums: Path = CHECKSUMS,
                refreeze: bool = False) -> Path:
    test_path = out_dir / "test.jsonl"
    if test_path.exists() and checksums.exists() and not refreeze:
        raise FileExistsError("the test split is frozen (evals/checksums.txt); rerun with --refreeze only "
                              "after the team agrees to change it")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in parts.items():
        (out_dir / f"{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    digest = hashlib.sha256(test_path.read_bytes()).hexdigest()
    try:
        rel = test_path.relative_to(ROOT)
    except ValueError:
        rel = test_path
    checksums.parent.mkdir(parents=True, exist_ok=True)
    checksums.write_text(f"{digest}  {rel}\n")
    return test_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refreeze", action="store_true")
    args = ap.parse_args(argv)
    rows = [json.loads(line) for line in (REPORTS / "labelled.jsonl").read_text().splitlines() if line.strip()]
    parts = split(rows)
    problems = leaks(parts)
    if problems:
        print("split leaks:", problems)
        return 1
    write_split(parts, refreeze=args.refreeze)
    sizes = {k: len(v) for k, v in parts.items()}
    print(f"split sizes {sizes}; test frozen in {CHECKSUMS.relative_to(ROOT)}")
    if sizes["test"] < 60:
        print(f"note: test has {sizes['test']} rows; AGENTS.md Step 3 asks for 60. Add more human-written reports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
