"""No-ML baselines for the report classifier (Rules of ML #1: launch without ML first)."""
from __future__ import annotations

import re
from collections import Counter

from ..scout.engine import INJECTION_PATTERNS

# Reports that blame a group of people instead of describing the street. Group words only, no slurs.
PREJUDICE = re.compile(r"\b(ethnic|ethnicity|race|racial|foreigners?|immigrants?|refugees?|those people|"
                       r"that group|their kind|these people|religion|religious)\b", re.I)
VAGUE = re.compile(r"\b(this (whole )?area|bad area|avoid it|somewhere|not sure (which|where)|everywhere is)\b", re.I)
CATEGORY_WORDS = [
    ("harassment", r"\b(follow(ed|ing)?|shout(ed|ing)?|harass\w*|catcall\w*|cornered|grab\w*|approached|yell\w*)\b"),
    ("lighting", r"\b(lights?|lamps?|dark|dim|unlit|lit|flicker\w*|pitch black)\b"),
    ("infrastructure", r"\b(footpath|crossing|construction|works|blocked|closed|broken|stairs|flooded|fence|help (point|phone))\b"),
    ("isolation", r"\b(empty|deserted|nobody|no one|quiet|isolated|shut|closed by)\b"),
    ("positive", r"\b(busy|plenty of people|security|staff|well lit|felt fine|open 24|new lights)\b"),
]


class KeywordClassifier:
    name = "keyword"

    def predict(self, text: str, street: str | None = None) -> dict:
        t = text or ""
        if PREJUDICE.search(t):
            action, why = "reject", "targets a group of people"
        elif any(rx.search(t) for _, rx in INJECTION_PATTERNS) or VAGUE.search(t) or len(t.split()) < 3:
            action, why = "review", "vague, too short, or contains instructions"
        else:
            action, why = "approve", "describes a street condition"
        category = next((c for c, rx in CATEGORY_WORDS if re.search(rx, t, re.I)), "other")
        return {"action": action, "category": category, "reason": f"keyword rule: {why}"}


class MajorityClassifier:
    name = "majority"

    def __init__(self):
        self.action, self.category = "approve", "other"

    def fit(self, rows: list[dict]) -> "MajorityClassifier":
        if rows:
            self.action = Counter(r["action"] for r in rows).most_common(1)[0][0]
            self.category = Counter(r["category"] for r in rows).most_common(1)[0][0]
        return self

    def predict(self, text: str, street: str | None = None) -> dict:
        return {"action": self.action, "category": self.category, "reason": "majority class of the train split"}
