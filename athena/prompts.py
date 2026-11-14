"""Prompts as versioned files (AGENTS.md Step 4, week 3: "prompts are code, track in git").

Each prompt lives in prompts/<role>.v<N>.md with front matter:

    ---
    role: explainer
    model: ${EXPLAINER_MODEL}
    version: 1
    date: 2026-11-14
    eval: evals/results/model_comparison.json
    ---
    <prompt body>

load(role) returns the highest version unless a version is pinned. The body's sha256 goes in traces and
result files so every number can be tied to the exact prompt text that produced it.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"
_NAME = re.compile(r"^(?P<role>[a-z_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    role: str
    version: int
    body: str
    meta: dict = field(default_factory=dict)

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.body.encode()).hexdigest()[:12]

    @property
    def id(self) -> str:
        return f"{self.role}.v{self.version}"

    def model(self, default: str | None = None) -> str | None:
        """Resolve the front-matter model, e.g. ${EXPLAINER_MODEL}, from the environment."""
        m = self.meta.get("model", "")
        var = re.fullmatch(r"\$\{(\w+)\}", m)
        return os.environ.get(var.group(1), default) if var else (m or default)

    def render(self, **values: str) -> str:
        """Fill {{NAME}} placeholders. Only for trusted values (few-shot examples from the train split);
        user and report text never goes into the system prompt."""
        out = self.body
        for k, v in values.items():
            out = out.replace("{{" + k + "}}", v)
        return out


def parse(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("prompt file must start with front matter")
    head, body = text[4:].split("\n---\n", 1)
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body.strip() + "\n"


@lru_cache(maxsize=None)
def load(role: str, version: int | None = None, folder: Path = PROMPTS) -> Prompt:
    found = {}
    for p in folder.glob(f"{role}.v*.md"):
        m = _NAME.match(p.name)
        if m and m.group("role") == role:
            found[int(m.group("version"))] = p
    if not found:
        raise FileNotFoundError(f"no prompt file for role {role!r} in {folder}")
    v = version if version is not None else max(found)
    meta, body = parse(found[v].read_text())
    if meta.get("role") != role or int(meta.get("version", -1)) != v:
        raise ValueError(f"{found[v].name}: front matter role/version does not match the file name")
    return Prompt(role=role, version=v, body=body, meta=meta)
