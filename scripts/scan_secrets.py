"""Secrets scan for the repo. Standard library only, so it runs anywhere.

  python3 scripts/scan_secrets.py            # scan every tracked file (make check)
  python3 scripts/scan_secrets.py --staged   # scan staged changes only (pre-commit hook)

Exit code 1 and a file:line list when something looks like a live credential. Placeholders such as
YOUR_VIRTUAL_KEY and the test keys ending in -not-real are allowed. The matched value is never printed.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "aws access key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "private key block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # A secret-named setting given a literal value: quoted in code, or KEY=value in .env / shell files.
    # Assignments from variables or attributes (token = self._login_token) are not literals and pass.
    "assigned secret": re.compile(
        r"(?i)\b[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*['\"]?\s*[:=]\s*['\"]([^'\"\s]{16,})['\"]"),
    "env-file secret": re.compile(
        r"^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*=['\"]?([^'\"\s#]{16,})"),
}
# Values that are placeholders by construction.
ALLOWED_VALUE = re.compile(r"(?i)(your[-_]|change[-_]?me|not[-_]?real|placeholder|example|dummy|xxxx|<)")
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".parquet", ".joblib", ".woff", ".woff2"}
SKIP_FILES = {"web/vendor/maplibre-gl.js", "web/vendor/maplibre-gl.css"}


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line number, finding kind) for each suspicious line."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, rx in PATTERNS.items():
            for m in rx.finditer(line):
                value = m.group(1) if kind in ("assigned secret", "env-file secret") else m.group(0)
                if ALLOWED_VALUE.search(value) or ALLOWED_VALUE.search(line):
                    continue
                if kind != "openai-style key" and ("os.environ" in line or "getenv" in line or "e(\"" in line):
                    continue
                hits.append((n, kind))
    return hits


# Folders a source download (zip, no .git) may contain that are never part of the submission.
WALK_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "runs", "node_modules", ".dvc"}
WALK_SKIP_PREFIXES = ("data/raw/", "models/")


def walked_files(root: Path = ROOT) -> list[str]:
    """Every file under root except virtualenvs, caches and git-ignored data folders. Used when the code is
    not a git checkout (a zip download), so `make check` still scans what would be submitted."""
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if not p.is_file() or set(p.relative_to(root).parts[:-1]) & WALK_SKIP_DIRS:
            continue
        if rel.startswith(WALK_SKIP_PREFIXES):
            continue
        if p.name.startswith(".env") and p.name != ".env.example":
            continue  # local, git-ignored config; main() warns so it never ends up in a submission zip
        out.append(rel)
    return out


def tracked_files() -> list[str]:
    """Files git tracks; outside a git checkout (a zip download), every file that would be submitted."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return walked_files()
    return [f for f in out.splitlines() if f]


def staged_files() -> list[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    args = ap.parse_args()
    files = staged_files() if args.staged else tracked_files()
    findings = []
    for f in files:
        path = ROOT / f
        if f in SKIP_FILES or path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        if f.startswith(".env") and f != ".env.example":
            findings.append((f, 0, ".env file must not be committed"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings += [(f, n, kind) for n, kind in scan_text(text)]
    for f, n, kind in findings:
        print(f"SECRET? {f}:{n} {kind}")
    if not args.staged and (ROOT / ".env").exists() and not (ROOT / ".git").exists():
        print("note: .env exists in this folder (not a git checkout); leave it out of any zip you submit")
    print(f"secrets scan: {len(files)} files, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
