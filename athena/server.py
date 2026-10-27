"""FastAPI server: the Ask Athena page, the pre-existing map, and the agent API.

Run:  uvicorn athena.server:app --port 8001      (or ./run.sh)
Docs: http://localhost:8001/docs (OpenAPI generated from the models below)
"""
from __future__ import annotations

import json
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


@lru_cache(maxsize=1)
def places() -> dict[str, dict]:
    rows = json.loads((ROOT / "data" / "places.json").read_text())["rows"]
    return {p["id"]: p for p in rows}


@lru_cache(maxsize=1)
def planner_source():
    """Live OTP when OTP_URL is set and reachable, otherwise the precomputed alternatives."""
    s = get_settings()
    base = athena().live_source or athena().local_source
    if s.otp_url:
        from .data.otp import OTPSource
        otp = OTPSource(s.otp_url, base, places(), s.zetaris_schema, s.otp_timeout_s)
        if otp.ping():
            log.info("journey planning: live OpenTripPlanner at %s", s.otp_url)
            return otp
        log.warning("OTP_URL set but %s is unreachable; using precomputed alternatives", s.otp_url)
    return base


@app.get("/api/places")
def list_places() -> dict:
    return {"places": list(places().values())}


def resolve_band(band: str | None, time: str | None) -> str:
    from .journeys import band_for_time
    if time:
        try:
            return band_for_time(time)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if band not in ("18", "22", "01"):
        raise HTTPException(422, "band must be 18, 22 or 01")
    return band


@app.get("/api/journeys")
def journeys(origin: str, destination: str, band: str = "22", mode: str = "transit",
             sort: str = "balanced", time: str | None = None) -> dict:
    """Journey options with safety indicators. `mode` may list several modes (walk,bike,transit), which are
    ranked together. `time` (HH:MM) is any departure time; it selects the closest street-risk profile."""
    from .journeys import SORTS, JourneyPlanner
    if sort not in SORTS:
        raise HTTPException(422, f"sort must be one of {', '.join(SORTS)}")
    band = resolve_band(band, time)
    src = planner_source()
    args = (origin, destination, band, mode, sort)
    try:
        result = JourneyPlanner(src, get_settings().zetaris_schema).plan(*args, depart=time)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - fall back to precomputed rather than failing the request
        log.warning("journey planning failed on %s (%s); falling back", getattr(src, "name", "?"), exc)
        fallback = athena().live_source or athena().local_source
        if fallback is src:
            raise HTTPException(502, f"Journey planning is unavailable ({exc.__class__.__name__}).") from exc
        try:
            result = JourneyPlanner(fallback, get_settings().zetaris_schema).plan(*args, depart=time)
            result["degraded"] = f"Live routing unavailable ({exc.__class__.__name__}); showing precomputed alternatives."
        except Exception as exc2:  # noqa: BLE001
            raise HTTPException(502, f"Journey planning is unavailable ({exc2.__class__.__name__}).") from exc2
    o, d = places().get(origin), places().get(destination)
    result["origin"] = o
    result["destination"] = d
    result["profile"] = {"band": band, "label": {"18": "6 pm", "22": "10 pm", "01": "1 am"}[band]}
    if not result.get("coverage"):
        result["message"] = (
            f"No routes for {o['name'] if o else origin} to {d['name'] if d else destination} yet. "
            "Athena's validated corridor covers UNSW Library and Town Hall station in both directions."
            + ("" if get_settings().otp_url else " Set OTP_URL to plan any trip live."))
    return result


@app.get("/api/street-safety")
def street_safety(band: str = "22", time: str | None = None) -> dict:
    """GeoJSON for the safety overlay: street geometry joined to the risk values Zetaris serves."""
    band = resolve_band(band, time)
    geo = json.loads((ROOT / "web" / "streets.geojson").read_text())
    try:
        rows, ev = planner_source().streets()
    except Exception:  # noqa: BLE001
        rows, ev = athena().local_source.streets()
    attrs = {r["name"]: r for r in rows}
    features, seen = [], set()
    for f in geo["features"]:
        name = f["properties"].get("name")
        a = attrs.get(name)
        if not name or not a or name in seen:
            continue
        seen.add(name)
        risk = a.get(f"risk_{band}")
        features.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "name": name, "risk": None if risk is None else float(risk), "lit": a.get("lit"),
            "highway": a.get("highway"), "tunnel": bool(a.get("tunnel")),
            "venues": a.get(f"venues_{band}")}})
    return {"type": "FeatureCollection", "features": features, "band": band,
            "source": ev.get("executed_by"),
            "note": "Risk is Athena's rule-based street score (lighting, path type, activity), not an incident count."}


HELP_CATEGORIES = ("police", "hospital", "station")


@app.get("/api/safety-points")
def safety_points() -> dict:
    """Police, hospitals, emergency phones and stations near the corridor (OpenStreetMap, ODbL).
    Populated by scripts/fetch_safety_points.py; absent until that has been run."""
    path = ROOT / "data" / "safety_points.json"
    if not path.exists():
        return {"points": [], "available": False,
                "note": "Run scripts/fetch_safety_points.py to load help locations from OpenStreetMap."}
    body = json.loads(path.read_text())
    # Emergency phones are excluded: OpenStreetMap lists hundreds of them (lift and bus-stop intercoms
    # among them), which buried the police stations, hospitals and stations people actually look for.
    points = [p for p in body.get("rows", []) if p.get("category") in HELP_CATEGORIES]
    return {"points": points, "available": bool(points),
            "attribution": body.get("attribution"), "fetched": body.get("fetched")}


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


def _page(name: str):
    return FileResponse(ROOT / "web" / name)


@app.get("/")
def plan_page():
    """The journey planner."""
    return _page("plan.html")


@app.get("/ask")
def ask_page():
    return _page("ask.html")


@app.get("/map")
def map_page():
    """Street map: every scored street coloured by its risk indicators for a chosen time."""
    return _page("streets.html")


@app.get("/prototype")
def prototype_page():
    """The pre-existing route map (built before the hackathon), framed with the app's navigation."""
    return _page("prototype.html")


app.mount("/web", StaticFiles(directory=ROOT / "web"), name="web")
