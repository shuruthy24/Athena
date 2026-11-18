"""FastAPI server: the journey planner, the street map, and the explain/report API.

Run:  uvicorn athena.server:app --port 8001      (or ./run.sh)
Docs: http://localhost:8001/docs (OpenAPI generated from the models below)
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Literal

import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agents import Athena
from .config import ROOT, get_settings
from .data.source import LocalSource
from .llm import GatewayLLM, SampleLLM

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("athena.server")


def build_athena() -> Athena:
    s = get_settings()
    if s.sample_mode:
        log.info("SAMPLE_MODE=true: offline model stub; no gateway calls")
        return Athena(s, SampleLLM(), LocalSource())
    # os.environ[...] on purpose: a live run without the gateway settings must fail loudly.
    llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"],
                     timeout_s=s.llm_timeout_s, fallback_model=s.fallback_model)
    return Athena(s, llm, LocalSource())


@lru_cache(maxsize=1)
def athena() -> Athena:
    return build_athena()


app = FastAPI(title="Athena agent API", version=__version__,
              description="Evidence-checked night-route explanations and street report moderation.")


class Trip(BaseModel):
    origin: Literal["unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown"] | None = None
    destination: Literal["unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown"] | None = None
    band: Literal["18", "22", "01"] | None = None
    mode: Literal["walk", "bike", "transit"] | None = None


class ExplainRequest(BaseModel):
    origin: Literal["unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown"]
    destination: Literal["unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown"]
    band: Literal["18", "22", "01"]
    mode: Literal["walk", "bike", "transit"]


class ReportRequest(BaseModel):
    street: str = Field(min_length=1, max_length=100)
    band: Literal["18", "22", "01"]
    text: str = Field(min_length=1, max_length=500)


RATE_LIMIT_PER_MIN = 30
_hits: dict[str, deque] = defaultdict(deque)


def client_id(request: Request) -> str:
    """Pseudonymous per-client id for the rate limit: the first X-Forwarded-For hop (set by the hosting
    proxy) or the socket address, keyed-hashed with RATE_LIMIT_SECRET so no IP address is stored or logged.
    Without this every visitor shared one bucket and a busy room was rate-limited together."""
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    digest = hmac.new(get_settings().rate_limit_secret.encode(), ip.encode(), hashlib.sha256).hexdigest()
    return f"client-{digest[:12]}"


def rate_limit(request: Request) -> None:
    q, now = _hits[client_id(request)], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    q.append(now)
    if len(q) > RATE_LIMIT_PER_MIN:
        raise HTTPException(429, "Too many requests. Wait a minute and try again.")


@app.post("/api/explain")
def explain(req: ExplainRequest, request: Request) -> dict:
    """Evidence-checked explanation of the fastest route vs Athena's lower-risk route."""
    rate_limit(request)
    try:
        return athena().explain(req.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - return a clean error; details stay in the server log
        log.exception("explain failed")
        raise HTTPException(500, f"Athena could not complete this request ({exc.__class__.__name__}).") from exc


@app.post("/api/report")
def report(req: ReportRequest, request: Request) -> dict:
    """Moderate and queue one anonymous street report."""
    rate_limit(request)
    try:
        return athena().submit_report(req.street.strip(), req.band, req.text.strip())
    except Exception as exc:  # noqa: BLE001
        log.exception("report failed")
        raise HTTPException(500, f"Athena could not complete this request ({exc.__class__.__name__}).") from exc


@lru_cache(maxsize=1)
def places() -> dict[str, dict]:
    rows = json.loads((ROOT / "data" / "places.json").read_text(encoding="utf-8"))["rows"]
    return {p["id"]: p for p in rows}


@lru_cache(maxsize=1)
def planner_source():
    """Live OTP when OTP_URL is set and reachable, otherwise the precomputed alternatives."""
    s = get_settings()
    base = athena().source
    if s.otp_url:
        from .data.otp import OTPSource
        otp = OTPSource(s.otp_url, base, places(), timeout_s=s.otp_timeout_s)
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
        result = JourneyPlanner(src).plan(*args, depart=time)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - fall back to precomputed rather than failing the request
        base = athena().source
        log.warning("journey planning failed on %s (%s); falling back", getattr(src, "name", "?"), exc)
        if base is src:
            raise HTTPException(502, f"Journey planning is unavailable ({exc.__class__.__name__}).") from exc
        try:
            result = JourneyPlanner(base).plan(*args, depart=time)
            result["degraded"] = f"Live routing unavailable ({exc.__class__.__name__}); showing precomputed alternatives."
        except Exception as exc2:  # noqa: BLE001
            raise HTTPException(502, f"Journey planning is unavailable ({exc2.__class__.__name__}).") from exc2
    o, d = places().get(origin), places().get(destination)
    result["origin"] = o
    result["destination"] = d
    result["profile"] = {"band": band, "label": {"18": "6 pm", "22": "10 pm", "01": "1 am"}[band]}
    if not result.get("coverage"):
        prov = athena().source.provenance() if hasattr(athena().source, "provenance") else {}
        names = {k: v["name"] for k, v in places().items()}
        covered = [p.split("|") for p in prov.get("routable_pairs", [])]
        cover_text = ("Routes exist for: " + ", ".join(f"{names.get(a, a)} → {names.get(b, b)}" for a, b in covered[:8])
                      + (f" and {len(covered) - 8} more." if len(covered) > 8 else ".")) if covered else ""
        result["message"] = (
            f"No routes for {o['name'] if o else origin} to {d['name'] if d else destination} in the "
            f"{'hand-made sample' if prov.get('mode') == 'sample' else 'current'} data. {cover_text}"
            + ("" if get_settings().otp_url else " Set OTP_URL to plan any trip live."))
    result["data_provenance"] = {k: v for k, v in (athena().source.provenance().items()
                                                  if hasattr(athena().source, "provenance") else []) if k != "routable_pairs"}
    return result


@app.get("/api/street-safety")
def street_safety(band: str = "22", time: str | None = None) -> dict:
    """GeoJSON for the street-risk overlay. With the processed data (`make data`) every street carries its own
    OSM geometry; the hand-made sample has none, so its streets are drawn from web/streets.geojson."""
    band = resolve_band(band, time)
    try:
        rows, ev = planner_source().streets()
    except Exception:  # noqa: BLE001
        rows, ev = athena().source.streets()

    def props(name: str, a: dict) -> dict:
        risk = a.get(f"risk_{band}")
        return {"name": name, "risk": None if risk is None else float(risk), "lit": a.get("lit"),
                "lit_source": a.get("lit_source"), "highway": a.get("highway"), "tunnel": bool(a.get("tunnel")),
                "venues": a.get(f"venues_{band}")}

    features = []
    if rows and rows[0].get("geometry"):
        for a in rows:
            lines = [[[round(x, 5), round(y, 5)] for x, y in line] for line in json.loads(a["geometry"])]
            features.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines},
                             "properties": props(a["name"], a)})
    else:
        geo = json.loads((ROOT / "web" / "streets.geojson").read_text(encoding="utf-8"))
        attrs, seen = {r["name"]: r for r in rows}, set()
        for f in geo["features"]:
            name = f["properties"].get("name")
            a = attrs.get(name)
            if not name or not a or name in seen:
                continue
            seen.add(name)
            features.append({"type": "Feature", "geometry": f["geometry"], "properties": props(name, a)})
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
    body = json.loads(path.read_text(encoding="utf-8"))
    # Emergency phones are excluded: OpenStreetMap lists hundreds of them (lift and bus-stop intercoms
    # among them), which buried the police stations, hospitals and stations people actually look for.
    points = [p for p in body.get("rows", []) if p.get("category") in HELP_CATEGORIES]
    return {"points": points, "available": bool(points),
            "attribution": body.get("attribution"), "fetched": body.get("fetched")}


@app.get("/api/health")
def health() -> dict:
    a = athena()
    s = get_settings()
    out = {"version": __version__, "sample_mode": s.sample_mode, "report_classifier": s.report_classifier, "explanation_cache": a.cache.stats()}
    # Provenance for the interface badge and for anyone verifying a demo: which data and which models answer.
    prov = a.source.provenance() if hasattr(a.source, "provenance") else {"mode": "unknown"}
    out["data_provenance"] = prov
    out["llm"] = "offline stand-in (SAMPLE_MODE, no language model)" if s.sample_mode else "LiteLLM gateway"
    if s.sample_mode:
        out.update(gateway="skipped (sample mode)")
        return out
    out["data"] = getattr(a.source, "name", "files")
    try:
        out["gateway_models"] = a.llm.models()[:12]
        out["gateway"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["gateway"] = f"unreachable ({exc.__class__.__name__})"
    out["models"] = {"explainer": s.explainer_model, "critic": s.critic_model, "moderator": s.moderator_model,
                     "judge": s.judge_model, "fallback": s.fallback_model or None}
    return out


def _page(name: str):
    return FileResponse(ROOT / "web" / name)


@app.get("/")
def plan_page():
    """The journey planner."""
    return _page("plan.html")



@app.get("/map")
def map_page():
    """Street map: every scored street coloured by its risk indicators for a chosen time."""
    return _page("streets.html")


@app.get("/prototype")
def prototype_page():
    """The pre-existing route map (built before the hackathon), framed with the app's navigation."""
    return _page("prototype.html")


app.mount("/web", StaticFiles(directory=ROOT / "web"), name="web")
