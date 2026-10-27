"""FastAPI server: the Ask Athena page, the pre-existing map, and the agent API.

Run:  uvicorn athena.server:app --port 8001      (or ./run.sh)
Docs: http://localhost:8001/docs (OpenAPI generated from the models below)
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agents import Athena
from .config import ROOT, get_settings
from .data.source import LocalSource, ZetarisSource
from .data.zetaris import ZetarisClient
from .llm import NightshiftLLM, SampleLLM

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("athena.server")


def build_athena() -> Athena:
    s = get_settings()
    if s.sample_mode:
        log.info("SAMPLE_MODE=true: local JSON data and offline model stub; no Zetaris or Nightshift calls")
        return Athena(s, SampleLLM(), LocalSource(schema=s.zetaris_schema))
    client = ZetarisClient(s.zetaris_rest_url, s.zetaris_base_url, s.zetaris_org_id, s.zetaris_api_key,
                           s.zetaris_username, s.zetaris_password, s.zetaris_timeout_s)
    llm = NightshiftLLM(s.meterless_base_url, s.meterless_token, timeout_s=s.llm_timeout_s)
    return Athena(s, llm, ZetarisSource(client, s.zetaris_schema))


@lru_cache(maxsize=1)
def athena() -> Athena:
    return build_athena()


app = FastAPI(title="Athena agent API", version=__version__,
              description="Scout Intent routing, evidence-checked night-route explanations and report moderation.")


class Trip(BaseModel):
    origin: Literal["unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown"] | None = None
    destination: Literal["unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown"] | None = None
    band: Literal["18", "22", "01"] | None = None
    mode: Literal["walk", "bike", "transit"] | None = None


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    trip: Trip | None = None
    session: dict | None = Field(default=None, description="last_intent, recent_intents, home")


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    trip = {k: v for k, v in (req.trip.model_dump() if req.trip else {}).items() if v}
    session = {k: v for k, v in (req.session or {}).items() if k in ("last_intent", "recent_intents", "home")}
    try:
        return athena().handle(req.message, trip=trip, session=session)
    except Exception as exc:  # noqa: BLE001 - return a clean error; details stay in the server log
        log.exception("ask failed")
        raise HTTPException(status_code=500, detail=f"Athena could not complete this request ({exc.__class__.__name__}).") from exc


@app.get("/api/health")
def health() -> dict:
    a = athena()
    s = get_settings()
    out = {"version": __version__, "sample_mode": s.sample_mode, "scout_registry": a.scout.registry_version,
           "scout_self_test_ms": round(a.scout.self_test_ms, 3)}
    if s.sample_mode:
        out.update(zetaris="skipped (sample mode)", nightshift="skipped (sample mode)")
        return out
    out["zetaris"] = "ok" if a.live_source and a.live_source.client.ping() else "unreachable"
    out["zetaris_auth"] = a.live_source.client.auth_method if a.live_source else None
    try:
        out["nightshift_models"] = a.llm.models()[:8]
        out["nightshift"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["nightshift"] = f"unreachable ({exc.__class__.__name__})"
    out["fast_model"], out["critic_model"] = s.fast_model, s.critic_model
    return out


@app.get("/")
def ask_page():
    return FileResponse(ROOT / "web" / "ask.html")


@app.get("/map")
def map_page():
    return FileResponse(ROOT / "web" / "index.html")


app.mount("/web", StaticFiles(directory=ROOT / "web"), name="web")
