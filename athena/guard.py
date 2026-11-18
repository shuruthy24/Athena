"""Text guard for anonymous report text: injection detection and PII redaction.

Moved out of the removed Scout chat router (Apache-2.0 Meterless Scout Intent spec, see NOTICE) so the report
path keeps the same checks. Evidence records the pattern name, never the payload. Report text is data: a
match blocks the report before any model sees it; PII is redacted before any model call.
"""
from __future__ import annotations

import re

# Guard: injection patterns (spec docs/risk-and-policy.md: pattern match, role drift, embedded control,
# out-of-band tool references). Evidence records the pattern name, never the payload.
INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("instruction-override", re.compile(r"\b(ignore|disregard|forget)\b.{0,20}\b(previous|prior|above|all|the)\b.{0,20}\b(instructions?|rules|prompts?|user)\b", re.I | re.S)),
    ("role-drift", re.compile(r"\byou are now\b|\bunrestricted mode\b|\bpretend to be\b|\bact as (the )?(system|admin)", re.I)),
    # Role markers inside user or report text ("SYSTEM: ...", "assistant: ...") are indirect injection.
    ("embedded-control", re.compile(r"</?\s*(system|user|assistant)\s*>|\[/?INST\]|\b(system|assistant|developer)\s*:", re.I)),
    ("secret-exfiltration", re.compile(r"\breveal\b.{0,40}\b(keys?|credentials?|passwords?|secrets?|tokens?)\b", re.I | re.S)),
    ("tool-ref", re.compile(r"\b(grant (me )?admin|admin panel|internal_\w+|drop (the )?\w+ table|delete (all|every))\b", re.I)),
    ("risk-tampering", re.compile(r"\bmark (every|all|each)\b.{0,20}\b(streets?|routes?)\b.{0,15}\b(as )?(safe|low risk)\b|\boverride\b.{0,15}\brisk\b"
                                  r"|\bmark\b.{0,30}\b(lowest|low|lower|zero|no)[- ]risk\b|\bset\b.{0,25}\brisk\b.{0,10}\bto\b", re.I | re.S)),
]
BIDI_CONTROL = re.compile(r"[‪-‮⁦-⁩​-‏]|\\u202[a-e]", re.I)
PII_PATTERNS = [
    ("phone", re.compile(r"(?:\+?61\s?|0)4\d{2}[\s-]?\d{3}[\s-]?\d{3}\b|\b\d{4}\s?\d{3}\s?\d{3}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("plate", re.compile(r"\b(rego|plate)\s*[:#]?\s*[A-Z0-9]{2,7}\b", re.I)),
]
# Patterns the ingest cleaner always had; since 18 Nov 2026 the app applies them too, so every report gets the same
# redaction before any model call (evals/results/pii_before_fixes.json showed the app path missing these kinds).
# Addresses with a house number are removed even when they are a business used as a location: the report's street
# is a separate field, and a home address is worse to leak than a shop's number is to lose.
EXTRA_PII = [
    ("address", re.compile(r"\b\d{1,4}[A-Za-z]?\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|"
                           r"Parade|Pde|Place|Pl|Drive|Dr|Crescent|Cres|Way|Terrace|Tce)\b")),
    ("url", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    # Landlines: +61 2 9123 4567, (02) 9123 4567, 02 9123 4567. (The old pattern expected a second area-code digit
    # after "(02)" and so never matched the bracketed form.)
    ("phone", re.compile(r"(?:\+?61\s?[2378]|\(0[2378]\)|\b0[2378])[\s-]?\d{4}[\s-]?\d{4}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]
# One redaction policy for the app and for ingest, in this order (URLs before emails, addresses first).
ALL_PII = EXTRA_PII[:2] + PII_PATTERNS + EXTRA_PII[2:]


def inspect_text(text: str) -> tuple[str, list[str], list[dict]]:
    """Return (redacted text, injection pattern names, redactions)."""
    inj = [name for name, rx in INJECTION_PATTERNS if rx.search(text)]
    if BIDI_CONTROL.search(text):
        inj.append("embedded-control-unicode")
    redacted, redactions = text, []
    for kind, rx in ALL_PII:
        if rx.search(redacted):
            redacted = rx.sub(f"[{kind} removed]", redacted)
            redactions.append({"kind": kind})
    return redacted, inj, redactions
