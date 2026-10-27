"""HTTP API in sample mode, and the full live code path against mock Zetaris + Nightshift servers."""
import importlib

import pytest
from fastapi.testclient import TestClient

from conftest import API_KEY, MODEL, RUNTIME_TOKEN, MockHandler


def client_with_env(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import athena.server as server
    importlib.reload(server)
    server.athena.cache_clear()
    return TestClient(server.app)


def test_sample_mode_api(monkeypatch):
    c = client_with_env(monkeypatch, SAMPLE_MODE="true")
    h = c.get("/api/health").json()
    assert h["sample_mode"] is True and h["zetaris"].startswith("skipped")
    r = c.post("/api/ask", json={"message": "Why is the Athena route better tonight?",
                                 "trip": {"origin": "unsw", "destination": "townhall", "band": "22", "mode": "walk"}})
    assert r.status_code == 200 and r.json()["intent"] == "route.explain"
    assert c.get("/").status_code == 200 and "Ask Athena" in c.get("/").text


def test_api_validates_input(monkeypatch):
    c = client_with_env(monkeypatch, SAMPLE_MODE="true")
    assert c.post("/api/ask", json={"message": ""}).status_code == 422
    assert c.post("/api/ask", json={"message": "x" * 501}).status_code == 422
    assert c.post("/api/ask", json={"message": "hi", "trip": {"origin": "mars"}}).status_code == 422


def test_live_path_with_mock_services(monkeypatch, mock_server):
    MockHandler.calls.clear()
    c = client_with_env(
        monkeypatch, SAMPLE_MODE="false",
        ZETARIS_REST_URL=f"{mock_server}/api/v1.0", ZETARIS_BASE_URL=mock_server, ZETARIS_ORG_ID="1",
        ZETARIS_API_KEY=API_KEY, ZETARIS_USERNAME="tester@example.com", ZETARIS_PASSWORD="Correct1!",
        METERLESS_BASE_URL=f"{mock_server}/v1", METERLESS_RUNTIME_TOKEN=RUNTIME_TOKEN,
        FAST_MODEL=MODEL, CRITIC_MODEL=MODEL)
    r = c.post("/api/ask", json={"message": "Why is the Athena route better tonight?",
                                 "trip": {"origin": "unsw", "destination": "townhall", "band": "22", "mode": "walk"}}).json()
    assert r["data_mode"] == "zetaris"
    data_step = next(t for t in r["trace"] if t["agent"] == "data")
    assert "Zetaris REST (auth=login)" in data_step["detail"]  # API key refused -> login fallback
    models = {t["model"] for t in r["trace"] if t["agent"] in ("route-analyst", "evidence-critic")}
    assert models == {MODEL}
    verdicts = [t.get("verdict") for t in r["trace"] if t["agent"] == "evidence-critic"]
    assert verdicts == ["revise", "approve"]  # mock analyst says "safe" first; critic sends it back
    chat = [x for x in MockHandler.calls if x["path"] == "/v1/chat/completions"]
    assert all(x["headers"]["meterless-scope"] == "user" for x in chat)
    assert any(x["path"] == "/api/auth/login" for x in MockHandler.calls)
    h = c.get("/api/health").json()
    assert h["zetaris"] == "ok" and h["nightshift"] == "ok" and MODEL in h["nightshift_models"]


def test_live_path_zetaris_down_replans_to_sample(monkeypatch, mock_server):
    c = client_with_env(
        monkeypatch, SAMPLE_MODE="false",
        ZETARIS_REST_URL="http://127.0.0.1:9/api/v1.0", ZETARIS_BASE_URL="http://127.0.0.1:9", ZETARIS_TIMEOUT_S="1",
        ZETARIS_API_KEY=API_KEY, ZETARIS_USERNAME="u", ZETARIS_PASSWORD="p",
        METERLESS_BASE_URL=f"{mock_server}/v1", METERLESS_RUNTIME_TOKEN=RUNTIME_TOKEN, FAST_MODEL=MODEL, CRITIC_MODEL=MODEL)
    r = c.post("/api/ask", json={"message": "Why is the Athena route better tonight?",
                                 "trip": {"origin": "unsw", "destination": "townhall"}}).json()
    assert any("re-planning" in t["detail"] for t in r["trace"])
    assert any(e["event"] == "scout.replan" for e in r["events"])
    assert r["routes"]
