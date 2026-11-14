"""`python -m pipeline.check`: validate the dataset the app will serve (AGENTS.md Step 2, Tech 4 final cell).

Checks: every row matches its schema (athena/schemas.py); required tables are present and non-empty; no
normalised duplicate street names; every street on every route exists in the streets table; at least one
trip is routable; the report test split checksum is unchanged once it exists. Exit code 1 on any failure.

Which dataset: data/processed/ when its manifest says routes came from OTP (a complete rebuild), otherwise
the hand-made sample in data/*.json, which is what LocalSource serves in that case.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from athena.data.source import active_data_dir, read_table
from athena.schemas import TABLES

from .clean import normalise

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("streets", "route_steps", "route_legs", "leg_streets", "places")


def run_checks(data_dir: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    tables = {}
    for name in REQUIRED:
        try:
            tables[name] = read_table(data_dir, name)
        except FileNotFoundError:
            check(f"{name} present", False, "missing")
            continue
        check(f"{name} non-empty", len(tables[name]) > 0, f"{len(tables[name])} rows")
        bad = []
        for i, row in enumerate(tables[name]):
            try:
                TABLES[name].model_validate(row)
            except ValidationError as exc:
                bad.append(f"row {i}: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}")
        check(f"{name} schema", not bad, "; ".join(bad[:3]))
    if "streets" in tables:
        names = [normalise(r["name"]) for r in tables["streets"]]
        dupes = sorted({n for n in names if names.count(n) > 1})
        check("no duplicate street names", not dupes, ", ".join(dupes[:5]))
        known = {r["name"] for r in tables["streets"]}
        for t in ("route_steps", "leg_streets"):
            if t in tables:
                missing = sorted({r["street"] for r in tables[t] if r.get("street") and r["street"] not in known})
                check(f"every {t} street is in streets", not missing, ", ".join(missing[:5]))
    if "route_steps" in tables:
        trips = {(r["trip"], r["band"], r["mode"]) for r in tables["route_steps"]}
        variants = {(r["trip"], r["band"], r["mode"], r["variant"]) for r in tables["route_steps"]}
        incomplete = sorted(t for t in trips if (*t, "fastest") not in variants or (*t, "athena") not in variants)
        check("every trip has fastest and athena variants", not incomplete, str(incomplete[:3]))
        check("routable trips", len(trips) > 0, f"{len({t[0] for t in trips})} origin-destination pairs")
    sums = ROOT / "evals" / "checksums.txt"
    if sums.exists():
        bad = []
        for line in sums.read_text().splitlines():
            if line.strip():
                digest, rel = line.split(maxsplit=1)
                p = ROOT / rel.strip()
                if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                    bad.append(rel.strip())
        check("frozen test split unchanged", not bad, ", ".join(bad))
    return results


def main() -> int:
    data_dir = active_data_dir()
    results = run_checks(data_dir)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail else ""))
    failed = [r for r in results if not r[1]]
    print(f"pipeline.check on {data_dir.relative_to(ROOT)}: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
