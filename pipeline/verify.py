"""`python -m pipeline.verify` (make data-verify): prove the committed data/processed/ is reproducible.

Rebuilds the processed tables from exactly the raw snapshots named in data/processed/manifest.json (checking their
sha256 first) into a temporary folder, then compares row counts and content hashes table by table with the
committed manifest. Writes evals/results/data_rebuild.json. Exit 0 = identical, 1 = differs, 2 = cannot run
(raw snapshots missing: fetch them with `python -m pipeline.fetch_raw`, or `dvc pull` where a remote is set up).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .lineage import file_sha256
from .run import PROCESSED, RAW, ROOT, build

OUT = ROOT / "evals" / "results" / "data_rebuild.json"


def verify(processed: Path = PROCESSED, raw: Path = RAW, places_file: Path = ROOT / "data/places.json") -> dict:
    mpath = processed / "manifest.json"
    if not mpath.exists():
        return {"status": "cannot-run", "reason": "data/processed/manifest.json missing: run `make data` and commit data/processed/"}
    committed = json.loads(mpath.read_text(encoding="utf-8"))
    if not committed.get("content_sha256"):
        return {"status": "cannot-run", "reason": "the committed manifest has no content hashes: rebuild once with "
                                                  "this version (`make data`) and commit data/processed/"}
    checks, missing = [], []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_raw, tmp_out = Path(tmp) / "raw", Path(tmp) / "processed"
        tmp_raw.mkdir()
        for kind, src in committed.get("sources", {}).items():
            f = raw / src["file"]
            if not f.exists():
                missing.append(src["file"])
                continue
            ok = file_sha256(f) == src["sha256"]
            checks.append({"check": f"raw {kind} snapshot {src['file']} matches its recorded sha256", "ok": ok})
            shutil.copy2(f, tmp_raw / f.name)
        if missing:
            return {"status": "cannot-run", "reason": "raw snapshots not present: " + ", ".join(missing)
                    + " (python -m pipeline.fetch_raw, or dvc pull)", "missing": missing}
        rebuilt = build(raw=tmp_raw, out=tmp_out, places_file=places_file)
    for table, digest in committed["content_sha256"].items():
        got = rebuilt.get("content_sha256", {}).get(table)
        checks.append({"check": f"{table}: {committed['tables'].get(table)} rows, content hash identical",
                       "ok": got == digest and rebuilt["tables"].get(table) == committed["tables"].get(table),
                       "committed": digest[:16], "rebuilt": (got or "")[:16],
                       "rows_committed": committed["tables"].get(table), "rows_rebuilt": rebuilt["tables"].get(table)})
    return {"status": "identical" if all(c["ok"] for c in checks) else "differs", "checks": checks,
            "sources": committed.get("sources"), "committed_built_at": committed.get("built_at")}


def main() -> int:
    res = verify()
    for c in res.get("checks", []):
        print(f"{'PASS' if c['ok'] else 'FAIL'} {c['check']}")
    if res["status"] == "cannot-run":
        print(f"data-verify could not run: {res['reason']}")
        return 2
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None,
                               "calls_used": 0, "config": {"processed": "data/processed", "raw": "data/raw"},
                               **res}, indent=1) + "\n", encoding="utf-8")
    print(f"data-verify: rebuild is {res['status']} -> {OUT.relative_to(ROOT)}")
    return 0 if res["status"] == "identical" else 1


if __name__ == "__main__":
    sys.exit(main())
