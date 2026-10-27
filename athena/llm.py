"""Model access through Meterless Nightshift (the required primary AI provider).

Nightshift exposes an OpenAI-compatible Runtime: Authorization: Bearer <runtime token>, headers
meterless-app and meterless-scope, GET /v1/models and POST /v1/chat/completions. The NVIDIA key lives
only in Nightshift's Providers/Keys page; this app never sees it. Every call returns the model that
actually handled it (the response's "model" field) so the trace can prove which Nemotron model ran.

SampleLLM is an offline stand-in used only in SAMPLE_MODE. It is labelled "sample-offline-stub" in every
trace so it can never be mistaken for Nemotron output.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import requests

log = logging.getLogger("athena.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str
    model: str
    ms: float


def parse_json(text: str) -> dict:
    """Extract the first JSON object from a model reply (handles code fences and leading prose)."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise LLMError("model reply contained no JSON object")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise LLMError("unterminated JSON object in model reply")


class NightshiftLLM:
    provider = "meterless-nightshift"

    def __init__(self, base_url: str, token: str, app: str = "athena", timeout_s: float = 60.0,
                 session: requests.Session | None = None):
        if not token:
            raise LLMError("METERLESS_RUNTIME_TOKEN is not set")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "meterless-app": app, "meterless-scope": "user",
                        "Content-Type": "application/json", "Accept": "application/json"}
        self.timeout_s = timeout_s
        self.http = session or requests.Session()

    def models(self) -> list[str]:
        r = self.http.get(f"{self.base_url}/models", headers=self.headers, timeout=10)
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            raise LLMError(f"model discovery failed: HTTP {r.status_code} {r.headers.get('content-type')}")
        return [m["id"] for m in r.json().get("data", [])]

    def chat(self, model: str, system: str, user: str, temperature: float = 0.2, max_tokens: int = 700) -> LLMReply:
        t0 = time.perf_counter()
        try:
            r = self.http.post(f"{self.base_url}/chat/completions", headers=self.headers, timeout=self.timeout_s,
                               json={"model": model, "temperature": temperature, "max_tokens": max_tokens,
                                     "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]})
        except requests.RequestException as exc:
            raise LLMError(f"Nightshift unreachable: {exc.__class__.__name__}") from exc
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            raise LLMError(f"Nightshift HTTP {r.status_code}: {r.text[:160]}")
        if "json" not in r.headers.get("content-type", ""):
            raise LLMError("Nightshift returned a non-JSON page (is the Runtime port forwarded correctly?)")
        body = r.json()
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError("Nightshift reply had no choices") from exc
        served_by = body.get("model") or model
        log.info("nightshift model=%s ms=%d", served_by, ms)
        return LLMReply(text=text, model=served_by, ms=ms)


class SampleLLM:
    """Deterministic offline stand-in (SAMPLE_MODE only). Produces claims from the facts it is given."""
    provider = "local"
    MODEL = "sample-offline-stub"

    def models(self) -> list[str]:
        return [self.MODEL]

    def chat(self, model: str, system: str, user: str, temperature: float = 0.0, max_tokens: int = 700) -> LLMReply:
        t0 = time.perf_counter()
        task = re.search(r"TASK:\s*(\w+)", system)
        task = task.group(1) if task else "unknown"
        payload = parse_json(user)
        if task == "analyst":
            from .analysis import fallback_explanation
            headline, claims = fallback_explanation(payload["comparison"])
            out = {"headline": headline, "claims": claims}
        elif task == "critic":
            out = {"verdict": "approve", "unsupported": [], "notes": "offline stub: deterministic rules only"}
        elif task == "moderator":
            text = payload.get("text", "").lower()
            prejudiced = any(w in text for w in ("ethnic", "race", "racial", "foreigners", "those people"))
            cat = ("lighting" if any(w in text for w in ("light", "dark", "black", "lit")) else
                   "harassment" if any(w in text for w in ("shout", "harass", "follow", "catcall")) else
                   "infrastructure" if any(w in text for w in ("construction", "footpath", "blocked")) else
                   "positive" if any(w in text for w in ("busy", "plenty", "fine", "well lit")) else "other")
            out = {"prejudiced": prejudiced, "category": cat, "plausible": True,
                   "reason": "offline stub keyword rules"}
        else:
            out = {}
        return LLMReply(text=json.dumps(out), model=self.MODEL, ms=(time.perf_counter() - t0) * 1000)
