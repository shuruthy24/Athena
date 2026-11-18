"""Fetch the raw snapshots listed in data/raw_manifest.json and check their sha256 (data provenance, repeatability).

  python -m pipeline.fetch_raw            # download every missing file that has a URL, verify every hash
  python -m pipeline.fetch_raw --write    # (data owner) record name, size and sha256 of each file in data/raw/

Why: data/raw/ is versioned with DVC, but its remote is a folder on one laptop, so nobody else could rebuild
data/processed/. The manifest gives each raw file a public mirror URL (for example a GitHub Release asset of the
repository) and the hash it must have. A file whose hash differs is rejected, never used. The GTFS zip is only
needed to build the OpenTripPlanner graph (not by `make data`), so it has no mirror: download it from the TfNSW
Open Data Hub (free account) if you want to re-plan trips.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .lineage import file_sha256

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MANIFEST = ROOT / "data" / "raw_manifest.json"


def load(manifest: Path = MANIFEST) -> dict:
    return json.loads(manifest.read_text(encoding="utf-8"))


def write(raw: Path = RAW, manifest: Path = MANIFEST) -> dict:
    """Record every raw file's size and sha256, keeping the descriptive fields already in the manifest."""
    body = load(manifest) if manifest.exists() else {"files": {}}
    for f in sorted(p for p in raw.iterdir() if p.is_file() and not p.name.startswith(".")):
        entry = body["files"].setdefault(f.name, {})
        entry.update(bytes=f.stat().st_size, sha256=file_sha256(f))
    manifest.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return body


def fetch(raw: Path = RAW, manifest: Path = MANIFEST, download=None) -> list[tuple[str, str]]:
    """(file, status) per manifest entry: ok, downloaded, no-url, missing-hash or BAD-HASH."""
    body = load(manifest)
    raw.mkdir(parents=True, exist_ok=True)
    out = []
    for name, e in body["files"].items():
        path = raw / name
        if not path.exists():
            if not e.get("url"):
                out.append((name, "no-url" if e.get("needed_by_make_data") else "not needed by make data"))
                continue
            (download or _download)(e["url"], path)
            status = "downloaded"
        else:
            status = "ok"
        if not e.get("sha256"):
            out.append((name, "missing-hash"))
            continue
        if file_sha256(path) != e["sha256"]:
            bad = path.with_suffix(path.suffix + ".rejected")
            path.replace(bad)
            out.append((name, f"BAD-HASH (moved to {bad.name})"))
            continue
        out.append((name, status))
    return out


def _download(url: str, path: Path) -> None:
    import requests
    tmp = path.with_suffix(path.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="record sizes and hashes of the files in data/raw/")
    args = ap.parse_args(argv)
    if args.write:
        body = write()
        print(f"recorded {len(body['files'])} files in {MANIFEST.relative_to(ROOT)}; add each mirror URL, then commit")
        return 0
    results = fetch()
    for name, status in results:
        print(f"{status:>24}  {name}")
    needed = {n for n, e in load()["files"].items() if e.get("needed_by_make_data")}
    bad = [n for n, s in results if n in needed and s not in ("ok", "downloaded")]
    if bad:
        print("cannot rebuild data/processed until these are present with the recorded hash: " + ", ".join(bad))
        return 1
    print("raw snapshots needed by `make data` are present and match their hashes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
