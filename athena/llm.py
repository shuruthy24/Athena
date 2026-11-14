"""Model access through the COMP3110 LiteLLM gateway.

GatewayLLM uses the OpenAI Python SDK the way Tech 1 does: base_url from COMP3110_MODEL_BASE_URL and
api_key from COMP3110_API_KEY (read by the caller with os.environ[...], so a missing value fails
loudly). Every call is non-streaming at temperature 0 and returns the model that actually served it,
token usage, latency and an estimated cost from athena.pricing.

Retry rule (AGENTS.md section 2): at most one retry per logical call, counted across every layer.
The retry lives here, only for transient failures (connection, timeout, 429, 5xx, empty completion),
and goes to the fallback model when one is configured. Callers must not retry again.

SampleLLM is an offline stand-in used only in SAMPLE_MODE. It is labelled "sample-offline-stub" in
every trace so it can never be mistaken for model output.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from .pricing import estimate_cost_usd

log = logging.getLogger("athena.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str
    model: str
    ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    requested_model: str | None = None
    attempts: int = 1

    def usage(self) -> dict:
        """Fields added to a trace step for this call."""
        return {"tokens_in": self.input_tokens, "tokens_out": self.output_tokens,
                "cost_usd": None if self.cost_usd is None else round(self.cost_usd, 8)}


def parse_json(text: str) -> dict:
    """Lenient parser for the app path: extract the first JSON object from a model reply (handles code
    fences and leading prose). The judge uses the strict parser in evals/judge.py instead."""
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


class GatewayLLM:
    provider = "comp3110-gateway"

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 60.0, fallback_model: str | None = None,
                 client=None):
        if not base_url:
            raise LLMError("COMP3110_MODEL_BASE_URL is empty")
        if not api_key:
            raise LLMError("COMP3110_API_KEY is empty")
        self.fallback_model = fallback_model or None
        if client is None:
            from openai import OpenAI
            # max_retries=0: the SDK must not add hidden retries on top of the one counted below.
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0)
        self.client = client

    def models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list().data]
        except Exception as exc:  # noqa: BLE001 - report the class only; messages can echo request details
            raise LLMError(f"model discovery failed: {exc.__class__.__name__}") from exc

    def _once(self, model: str, system: str, user: str, temperature: float, max_tokens: int):
        return self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )

    @staticmethod
    def _transient(exc: Exception) -> bool:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            return True
        return isinstance(exc, APIStatusError) and exc.status_code >= 500

    def chat(self, model: str, system: str, user: str, temperature: float = 0.0, max_tokens: int = 1600,
             role: str | None = None) -> LLMReply:
        """One logical call: a first attempt, then at most one retry on a transient failure."""
        t0 = time.perf_counter()
        attempts, last = 0, ""
        for attempt_model in (model, self.fallback_model or model):
            attempts += 1
            try:
                response = self._once(attempt_model, system, user, temperature, max_tokens)
            except Exception as exc:  # noqa: BLE001
                from openai import OpenAIError
                if not isinstance(exc, OpenAIError):
                    raise
                last = exc.__class__.__name__ + (f" {exc.status_code}" if hasattr(exc, "status_code") else "")
                if attempts == 1 and self._transient(exc):
                    log.warning("gateway %s on %s (role=%s); one retry on %s", last, attempt_model, role,
                                self.fallback_model or model)
                    continue
                raise LLMError(f"gateway call failed: {last}") from exc
            try:
                msg = response.choices[0].message
            except (AttributeError, IndexError) as exc:
                raise LLMError("gateway reply had no choices") from exc
            text = msg.content or getattr(msg, "reasoning_content", None) or ""
            served = response.model or attempt_model
            if not text.strip():
                last = f"empty completion from {served}"
                if attempts == 1:
                    log.warning("gateway %s (role=%s); one retry", last, role)
                    continue
                raise LLMError(f"gateway returned an {last}")
            usage = response.usage
            tin = getattr(usage, "prompt_tokens", None) if usage else None
            tout = getattr(usage, "completion_tokens", None) if usage else None
            ms = (time.perf_counter() - t0) * 1000
            log.info("gateway role=%s requested=%s served=%s ms=%d tokens=%s/%s", role, attempt_model, served, ms,
                     tin, tout)
            # Price by the model that served the call; fall back to the requested route's price.
            cost = estimate_cost_usd(served, tin, tout)
            if cost is None:
                cost = estimate_cost_usd(attempt_model, tin, tout)
            return LLMReply(text=text, model=served, ms=ms, input_tokens=tin, output_tokens=tout, cost_usd=cost,
                            requested_model=attempt_model, attempts=attempts)
        raise LLMError(f"gateway call failed after one retry: {last}")


ROLE_TO_TASK = {"explainer": "analyst", "analyst": "analyst", "critic": "critic", "moderator": "moderator"}


class SampleLLM:
    """Deterministic offline stand-in (SAMPLE_MODE only). Produces claims from the facts it is given."""
    provider = "local"
    MODEL = "sample-offline-stub"

    def models(self) -> list[str]:
        return [self.MODEL]

    def chat(self, model: str, system: str, user: str, temperature: float = 0.0, max_tokens: int = 700,
             role: str | None = None) -> LLMReply:
        t0 = time.perf_counter()
        task = ROLE_TO_TASK.get(role or "")
        if task is None:  # legacy prompts carry a "TASK: <name>" line
            m = re.search(r"TASK:\s*(\w+)", system)
            task = m.group(1) if m else "unknown"
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
        return LLMReply(text=json.dumps(out), model=self.MODEL, ms=(time.perf_counter() - t0) * 1000,
                        input_tokens=0, output_tokens=0, cost_usd=0.0, requested_model=model)
