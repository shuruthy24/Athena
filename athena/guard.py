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


def inspect_text(text: str) -> tuple[str, list[str], list[dict]]:
    """Return (redacted text, injection pattern names, redactions)."""
    inj = [name for name, rx in INJECTION_PATTERNS if rx.search(text)]
    if BIDI_CONTROL.search(text):
        inj.append("embedded-control-unicode")
    redacted, redactions = text, []
    for kind, rx in PII_PATTERNS:
        if rx.search(redacted):
            redacted = rx.sub(f"[{kind} removed]", redacted)
            redactions.append({"kind": kind})
    return redacted, inj, redactions
