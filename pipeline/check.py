"""`python -m pipeline.check`: validate the dataset the app will serve.

Checks: every row matches its schema (athena/schemas.py); required tables are present and non-empty; no
normalised duplicate street names; every street on every route exists in the streets table; at least one
trip is routable; the report test split checksum is unchanged once it exists. Exit code 1 on any failure.

Which dataset: data/processed/ when its manifest says routes came from OTP (a complete rebuild), otherwise
the hand-made sample in data/*.json, which is what LocalSource serves in that case.

Data quality (Data practices): besides the pass/fail checks, every run measures missing values per key column,
exact duplicate rows, duplicate keys, coordinates outside the study area and snapshot age, and writes them to
evals/results/data_quality.json under the dataset's name (sample or processed), so the quality of the data
behind every result is on record. Duplicate keys and out-of-area coordinates fail the check; missing values are
reported, not failed (unknown lighting is expected and is never filled in); a snapshot older than
STALE_DAYS is a warning.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from athena.data.source import active_data_dir, read_table
from athena.schemas import TABLES

from .clean import normalise

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("streets", "route_steps", "route_legs", "leg_streets", "places")
QUALITY_OUT = ROOT / "evals" / "results" / "data_quality.json"
STALE_DAYS = 180
STUDY_AREA = {"lat": (-34.2, -33.6), "lon": (150.9, 151.4)}   # generous box around UNSW, CBD and inner west
# Columns whose missing share is reported (None = unknown, never imputed).
KEY_COLUMNS = {"streets": ("lit", "highway", "risk_18", "risk_22", "risk_01", "venues_22"),
               "route_legs": ("polyline", "distance_m", "from_lat", "to_lat", "start_time", "total_min"),
               "route_steps": ("street", "distance_m", "duration_min"),
               "leg_streets": ("street", "distance_m"),
               "places": ("lat", "lon", "name")}
KEYS = {"streets": ("name",), "places": ("id",),
        "route_legs": ("journey", "leg"),
        "route_steps": ("trip", "band", "mode", "variant", "leg", "step"),
        "leg_streets": ("journey", "leg", "step")}
# Every table key must be unique. leg_streets could in principle hold both a name-matched and a location-matched
# step for one leg; that would now fail the check instead of passing as a warning.
FAIL_ON_DUPLICATE_KEYS = {"streets", "places", "route_steps", "route_legs", "leg_streets"}


def _missing(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and v != v)


def quality(tables: dict[str, list[dict]], data_dir: Path, now: datetime | None = None) -> dict:
    """Measured data quality for one dataset. Returns {"tables": {...}, "staleness": {...}, "failures": [...]}"""
    now = now or datetime.now(timezone.utc)
    out: dict = {"tables": {}, "failures": [], "warnings": []}
    for name, rows in tables.items():
        cols = KEY_COLUMNS.get(name, ())
        canon = [json.dumps(r, sort_keys=True, default=str) for r in rows]
        keys = [tuple(r.get(k) for k in KEYS[name]) for r in rows] if name in KEYS else []
        t = {"rows": len(rows),
             "missing_share": {c: round(sum(_missing(r.get(c)) for r in rows) / len(rows), 4) if rows else None
                               for c in cols},
             "exact_duplicate_rows": len(canon) - len(set(canon)),
             "duplicate_keys": len(keys) - len(set(keys)), "key": list(KEYS.get(name, ()))}
        coords = [(r.get(a), r.get(b)) for r in rows for a, b in (("lat", "lon"), ("from_lat", "from_lon"),
                                                                    ("to_lat", "to_lon")) if a in r]
        t["coordinates_outside_study_area"] = sum(
            1 for la, lo in coords if la is not None and lo is not None
            and not (STUDY_AREA["lat"][0] <= la <= STUDY_AREA["lat"][1] and STUDY_AREA["lon"][0] <= lo <= STUDY_AREA["lon"][1]))
        if t["duplicate_keys"] and name in FAIL_ON_DUPLICATE_KEYS:
            out["failures"].append(f"{name}: {t['duplicate_keys']} duplicate keys {KEYS[name]}")
        elif t["duplicate_keys"]:
            out["warnings"].append(f"{name}: {t['duplicate_keys']} rows share a key {KEYS[name]} (reported, not failed)")
        if t["coordinates_outside_study_area"]:
            out["failures"].append(f"{name}: {t['coordinates_outside_study_area']} coordinates outside the study area")
        out["tables"][name] = t
    manifest = data_dir / "manifest.json"
    stale = {}
    if manifest.exists():
        for kind, src in json.loads(manifest.read_text(encoding="utf-8")).get("sources", {}).items():
            when = src.get("fetched")
            if when:
                age = (now - datetime.fromisoformat(str(when).replace("Z", "+00:00"))).days
                stale[kind] = {"fetched": when, "age_days": age}
                if age > STALE_DAYS:
                    out["warnings"].append(f"{kind} snapshot is {age} days old (> {STALE_DAYS}): refresh with make ingest")
    else:
        stale["sample"] = {"note": "hand-made sample, no fetch date (not real measurements)"}
    out["staleness"] = stale
    return out


def write_quality(dataset: str, q: dict, out: Path = QUALITY_OUT) -> None:
    body = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"datasets": {}}
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat()
    body["datasets"][dataset] = {"generated_at": now, "git_sha": sha or None, **q}
    body.update(generated_at=now, git_sha=sha or None, calls_used=0, config={"stale_days": STALE_DAYS,
                                                                              "study_area": STUDY_AREA})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


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
        for line in sums.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, rel = line.split(maxsplit=1)
                p = ROOT / rel.strip()
                if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                    bad.append(rel.strip())
        check("frozen evaluation sets unchanged (evals/checksums.txt)", not bad, ", ".join(bad))
    if len(tables) == len(REQUIRED):
        q = quality(tables, data_dir)
        check("no duplicate keys and no coordinates outside the study area", not q["failures"],
              "; ".join(q["failures"][:3]))
        run_checks.last_quality = q
    return results


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-quality", action="store_true",
                    help="record the measured quality in evals/results/data_quality.json (make eval / make data)")
    args = ap.parse_args(argv)
    data_dir = active_data_dir()
    run_checks.last_quality = None
    results = run_checks(data_dir)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail else ""))
    q = run_checks.last_quality
    if q is not None:
        dataset = "processed" if (data_dir / "manifest.json").exists() else "sample"
        for w in q["warnings"]:
            print(f"WARN {w}")
        if args.write_quality:
            write_quality(dataset, q)
            print(f"data quality for the {dataset} dataset -> {QUALITY_OUT.relative_to(ROOT)}")
    failed = [r for r in results if not r[1]]
    print(f"pipeline.check on {data_dir.relative_to(ROOT)}: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
