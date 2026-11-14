"""Step 8: explanation cache and per-request usage in the trace."""
import json

from athena.agents import Athena
from athena.cache import ExplanationCache, cache_key
from athena.config import get_settings
from athena.data.source import LocalSource
from athena.llm import LLMError, LLMReply, SampleLLM

TRIP = {"origin": "unsw", "destination": "townhall", "band": "22", "mode": "walk"}
Q = "Why is the Athena route better tonight?"


class Counting(SampleLLM):
    def __init__(self, fail_first_draft=False):
        self.calls, self.fail_first_draft = 0, fail_first_draft

    def chat(self, model, system, user, temperature=0.0, max_tokens=700, role=None):
        self.calls += 1
        if self.fail_first_draft and role == "explainer":
            self.fail_first_draft = False
            raise LLMError("gateway unreachable")
        r = super().chat(model, system, user, temperature, max_tokens, role=role)
        return LLMReply(text=r.text, model="m", ms=1, input_tokens=100, output_tokens=20, cost_usd=0.0001)


def make(monkeypatch, llm):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    return Athena(get_settings(), llm, LocalSource())


def test_repeat_request_hits_the_cache(monkeypatch):
    llm = Counting()
    a = make(monkeypatch, llm)
    first = a.explain(TRIP)
    calls = llm.calls
    second = a.explain(TRIP)
    assert llm.calls == calls and any(t["agent"] == "cache" for t in second["trace"])
    assert second["claims"] == first["claims"] and second["critic_verdict"] == first["critic_verdict"]
    assert a.cache.stats()["hits"] == 1


def test_new_facts_miss_the_cache(monkeypatch):
    llm = Counting()
    a = make(monkeypatch, llm)
    a.explain(TRIP)
    a.submit_report("Anzac Parade", "22", "two street lights out")  # adds a penalty -> facts change
    calls = llm.calls
    r = a.explain(TRIP)
    assert llm.calls > calls and not any(t["agent"] == "cache" for t in r["trace"])


def test_outage_fallback_is_not_cached(monkeypatch):
    llm = Counting(fail_first_draft=True)
    a = make(monkeypatch, llm)
    assert a.explain(TRIP)["critic_verdict"] == "fallback-error"
    r = a.explain(TRIP)
    assert r["critic_verdict"] == "approve" and not any(t["agent"] == "cache" for t in r["trace"])


def test_key_depends_on_prompt_model_and_facts():
    base = cache_key(["explainer.v1@a", "critic.v1@b"], ["m1", "m2"], 2, {"extra_min": 1})
    assert base == cache_key(["explainer.v1@a", "critic.v1@b"], ["m1", "m2"], 2, {"extra_min": 1})
    assert base != cache_key(["explainer.v2@c", "critic.v1@b"], ["m1", "m2"], 2, {"extra_min": 1})
    assert base != cache_key(["explainer.v1@a", "critic.v1@b"], ["m3", "m2"], 2, {"extra_min": 1})
    assert base != cache_key(["explainer.v1@a", "critic.v1@b"], ["m1", "m2"], 2, {"extra_min": 2})


def test_cache_is_bounded():
    c = ExplanationCache(max_items=2)
    for i in range(3):
        c.put(str(i), {"verdict": "approve"})
    assert c.get("0") is None and c.get("2") is not None


def test_usage_step_totals_the_request(monkeypatch):
    r = make(monkeypatch, Counting()).explain(TRIP)
    usage = r["trace"][-1]
    model_steps = [t for t in r["trace"] if t.get("model")]
    assert usage["agent"] == "usage" and usage["calls"] == len(model_steps) == 2
    assert usage["tokens_in"] == 200 and abs(usage["cost_usd"] - 0.0002) < 1e-12
    json.dumps(r)  # the response stays JSON-serialisable
