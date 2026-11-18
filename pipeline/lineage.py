"""Data lineage helpers: content hashes for derived tables, so a rebuild can be compared with the committed copy.

A table's content hash is the sha256 of its rows as canonical JSON (keys sorted, rows sorted), so it does not
depend on Parquet writer metadata, row order or the build time. `pipeline.run` stores one per table in
data/processed/manifest.json next to the raw files' sha256; `pipeline.verify` rebuilds from data/raw/ into a
temporary folder and compares. Same raw snapshot + same code = same hashes, or the cleaning is not repeatable.
"""
from __future__ import annotations

import hashlib
import json
import math


def _canonical(v):
    if isinstance(v, float):
        if math.isnan(v):
            return None
        return round(v, 9)          # float noise below 1e-9 is not a data change
    if isinstance(v, dict):
        return {k: _canonical(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canonical(x) for x in v]
    return v


def rows_sha256(rows: list[dict]) -> str:
    lines = sorted(json.dumps(_canonical(r), sort_keys=True, ensure_ascii=False, default=str) for r in rows)
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


PROCESSED_LICENCE = """# Licence of the files in this folder

These tables are derived by `make data` (pipeline/run.py) from the raw snapshots listed in `manifest.json`.

- Streets, street lamps, venues and walking/cycling geometry: **© OpenStreetMap contributors**, available under
  the **Open Database Licence (ODbL) 1.0** (https://opendatacommons.org/licenses/odbl/1-0/). These tables are a
  derivative database of OpenStreetMap and are therefore also made available under the ODbL; any public use must
  keep this attribution and offer derived databases under the same licence.
- Planned public transport trips: computed with OpenTripPlanner from **Transport for NSW** "Timetables Complete
  GTFS", **CC BY 4.0** (https://opendata.transport.nsw.gov.au/). Attribution: "Contains data from Transport for NSW".
- The risk scores are Athena's rule (docs/model_card.md), not measurements of safety.

No personal data: OSM describes places, not people, and free-text OSM tags are not used.
"""


# ---------------------------------------------------------------- frozen evaluation sets
# evals/checksums.txt holds one "sha256  path" line per held-back set (the report test split, the guard's held-out
# probes, ...). A frozen set is never edited or used for tuning; `make check` fails if one changes.

def freeze(path, checksums, root) -> str:
    """Record (or replace) the sha256 line for one frozen file, keeping every other line."""
    from pathlib import Path
    path, checksums, root = Path(path), Path(checksums), Path(root)
    try:
        rel = path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        rel = str(path)
    digest = file_sha256(path)
    lines = [ln for ln in (checksums.read_text(encoding="utf-8").splitlines() if checksums.exists() else [])
             if ln.strip() and ln.split(maxsplit=1)[1].strip() != rel]
    checksums.parent.mkdir(parents=True, exist_ok=True)
    checksums.write_text("".join(f"{ln}\n" for ln in lines) + f"{digest}  {rel}\n", encoding="utf-8")
    return digest


def verify_frozen(checksums, root) -> list[tuple[str, bool]]:
    """(path, unchanged) for every frozen file listed."""
    from pathlib import Path
    checksums, root = Path(checksums), Path(root)
    out = []
    if not checksums.exists():
        return out
    for line in checksums.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, rel = line.split(maxsplit=1)
            p = root / rel.strip()
            out.append((rel.strip(), p.exists() and file_sha256(p) == digest))
    return out


if __name__ == "__main__":   # python -m pipeline.lineage  -> verify every frozen set (make checksums)
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parents[1]
    res = verify_frozen(_root / "evals" / "checksums.txt", _root)
    for rel, ok in res:
        print(f"{'ok' if ok else 'CHANGED'}  {rel}")
    print(f"checksums: {sum(ok for _, ok in res)}/{len(res)} frozen sets unchanged")
    sys.exit(0 if all(ok for _, ok in res) else 1)
