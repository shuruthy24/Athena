"""Prompted report classifier (moderator.v2): zero-shot or n-shot with examples from the train split only.

The report reaches the model only as a quoted JSON field in the user message. Few-shot examples are
train-split reports (already PII-scrubbed by pipeline.agreement), rendered as JSON objects into the
{{SHOTS}} slot. The reply must be exactly {"action", "category", "reason"}: no fences, prose, extra or
missing fields, and no repair. Anything else raises ClassifierError, which sends the report to review.
"""
from __future__ import annotations

import json
import random

from ..prompts import load
from . import ACTIONS, CATEGORIES, ClassifierError

SEED = 3110


def strict_parse(text: str) -> dict:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClassifierError("reply is not a JSON object") from exc
    if not isinstance(data, dict) or set(data) != {"action", "category", "reason"}:
        raise ClassifierError("reply must have exactly action, category and reason")
    if data["action"] not in ACTIONS or data["category"] not in CATEGORIES:
        raise ClassifierError("action or category outside the allowed labels")
    if not isinstance(data["reason"], str) or not data["reason"].strip():
        raise ClassifierError("reason must be a non-empty string")
    return data


def pick_shots(train_rows: list[dict], n: int = 5, seed: int = SEED) -> list[dict]:
    """Deterministic shots from human-written train rows: one per action first, then varied categories."""
    pool = sorted((r for r in train_rows if r.get("source") != "synthetic"), key=lambda r: r["id"])
    random.Random(seed).shuffle(pool)
    chosen: list[dict] = []
    for action in ACTIONS:
        hit = next((r for r in pool if r["action"] == action and r not in chosen), None)
        if hit:
            chosen.append(hit)
    for r in pool:
        if len(chosen) >= n:
            break
        if r not in chosen and r["category"] not in {c["category"] for c in chosen}:
            chosen.append(r)
    for r in pool:
        if len(chosen) >= n:
            break
        if r not in chosen:
            chosen.append(r)
    return chosen[:n]


def render_shots(shots: list[dict]) -> str:
    return "\n".join(json.dumps({"text": s["text"], "street": s["street"],
                                 "answer": {"action": s["action"], "category": s["category"]}}, ensure_ascii=False)
                     for s in shots) or "(none)"


class PromptClassifier:
    def __init__(self, llm, model: str | None, shots: int = 0, train_rows: list[dict] | None = None):
        if llm is None or not model:
            raise ValueError("a prompted classifier needs the gateway client and a model")
        self.llm, self.model = llm, model
        self.prompt = load("moderator", 2)
        self.shots = pick_shots(train_rows or [], shots) if shots else []
        if shots and len(self.shots) < 5:
            raise ValueError(f"{shots}-shot needs at least 5 train examples, found {len(self.shots)}")
        self.name = f"prompt-{len(self.shots)}shot" if self.shots else "prompt-zero"
        self.system = self.prompt.render(SHOTS=render_shots(self.shots))
        self.last_reply = None

    def predict(self, text: str, street: str | None = None) -> dict:
        try:
            reply = self.llm.chat(self.model, self.system, json.dumps({"text": text, "street": street}),
                                  role="moderator")
        except RuntimeError as exc:  # LLMError
            raise ClassifierError(f"model call failed: {exc}") from exc
        self.last_reply = reply
        return strict_parse(reply.text)
