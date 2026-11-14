"""Explanation cache (AGENTS.md Step 8; week 1 gateways: "caching is underrated").

Key = sha256 of the explainer and critic prompt versions, their models, MAX_REVISIONS and the exact facts.
Reuse is only safe when everything that shaped the answer matches (week 1: user, permissions, context and
model/prompt version). Here no user data reaches the explainer, so facts + prompts + models are the context:
a new report penalty changes the facts and therefore the key. The question wording is not part of the key;
the explanation describes the trip's facts. Only completed explanations are cached ("approve" or the
critic's "fallback"), never "fallback-error" from an outage, so a gateway blip is not remembered.
In-memory LRU, per process: the deployment is a single container.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import Lock

CACHEABLE = {"approve", "fallback"}


def cache_key(prompt_tags: list[str], models: list[str], max_revisions: int, facts: dict) -> str:
    blob = json.dumps({"prompts": prompt_tags, "models": models, "max_revisions": max_revisions, "facts": facts},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class ExplanationCache:
    def __init__(self, max_items: int = 512):
        self.max_items = max_items
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._lock = Lock()
        self.hits = self.misses = 0

    def get(self, key: str) -> dict | None:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                self.hits += 1
                return self._items[key]
            self.misses += 1
            return None

    def put(self, key: str, value: dict) -> None:
        if value.get("verdict") not in CACHEABLE:
            return
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def stats(self) -> dict:
        return {"items": len(self._items), "hits": self.hits, "misses": self.misses}
