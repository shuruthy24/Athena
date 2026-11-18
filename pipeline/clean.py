"""Cleaning helpers in plain Python over lists of dicts so the pipeline needs no pandas.

Order: drop_missing -> fix_labels -> find_conflicts (inspect, then decide
explicitly) -> drop_duplicates (exact, then normalised) -> fix_encoding. remove_pii runs at ingest, before
any other stage sees the text (never "we will strip it later": always anonymise at ingest).
Regex redaction cannot find names; a human reads the report text as well (data/labelling/guide.md).
"""
from __future__ import annotations

import re
import unicodedata

from athena.guard import ALL_PII, EXTRA_PII, PII_PATTERNS  # noqa: F401 (one policy, defined in the guard)

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


def normalise(text: str | None) -> str:
    """Lowercase and collapse whitespace, for comparing rows (a "normalised duplicate")."""
    return " ".join(str(text or "").lower().split())


def remove_pii(text: str) -> tuple[str, list[str]]:
    """Replace PII with '[<kind> removed]' markers. Returns the new text and the kinds found."""
    kinds = []
    for kind, rx in ALL_PII:
        if rx.search(text):
            kinds.append(kind)
            text = rx.sub(f"[{kind} removed]", text)
    return text, kinds


def drop_missing(rows: list[dict], cols: list[str]) -> tuple[list[dict], int]:
    keep = [r for r in rows if all(r.get(c) is not None and str(r.get(c)).strip() != "" for c in cols)]
    return keep, len(rows) - len(keep)


def fix_labels(rows: list[dict], col: str, mapping: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Map label variants to canonical values. Rows with an unknown label are returned separately, not
    guessed."""
    keep, unknown = [], []
    for r in rows:
        v = mapping.get(str(r.get(col, "")).strip().lower())
        if v is None:
            unknown.append(r)
        else:
            keep.append({**r, col: v})
    return keep, unknown


def find_conflicts(rows: list[dict], key_cols: list[str], label_col: str) -> list[dict]:
    """Groups with the same normalised key but different labels. Inspect them and decide explicitly;
    never keep whichever label happens to come first."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(normalise(r.get(c)) for c in key_cols), []).append(r)
    return [{"key": k, "labels": sorted({str(r.get(label_col)) for r in g}), "rows": g}
            for k, g in groups.items() if len({r.get(label_col) for r in g}) > 1]


def drop_duplicates(rows: list[dict], key_cols: list[str], normalised: bool = True) -> tuple[list[dict], int]:
    seen, keep = set(), []
    for r in rows:
        key = tuple(normalise(r.get(c)) if normalised else r.get(c) for c in key_cols)
        if key not in seen:
            seen.add(key)
            keep.append(r)
    return keep, len(rows) - len(keep)


def fix_encoding(rows: list[dict], cols: list[str]) -> tuple[list[dict], int]:
    """NFC-normalise, unify line endings and strip control and bidi characters."""
    fixed, out = 0, []
    for r in rows:
        new = dict(r)
        for c in cols:
            v = r.get(c)
            if isinstance(v, str):
                clean = CONTROL.sub("", unicodedata.normalize("NFC", v.replace("\r\n", "\n").replace("\r", "\n")))
                if clean != v:
                    fixed += 1
                    new[c] = clean
        out.append(new)
    return out, fixed
