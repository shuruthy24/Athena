"""Live OpenTripPlanner source.

Implements the same three methods as `LocalSource` and `ZetarisSource` (`route_legs`, `leg_streets`,
`streets`), so `JourneyPlanner` scores live routes with exactly the same safety methodology. Street
attributes always come from the configured street source (Zetaris), never from OTP.

OTP 2.x serves a GraphQL endpoint at /otp/gtfs/v1. The query below is the same `planConnection` shape
our pipeline used to generate the precomputed alternatives, so the rows are interchangeable.

Enable by setting OTP_URL (for example http://localhost:8080). Safety-aware alternatives come from
OTP's walk `safetyFactor`, which our patched build reads from the `athena:risk` and `lit` street tags.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("athena.otp")

PLAN_QUERY = """
query plan($from: PlanLabeledLocationInput!, $to: PlanLabeledLocationInput!,
           $dateTime: PlanDateTimeInput, $modes: PlanModesInput, $first: Int) {
  planConnection(origin: $from, destination: $to, dateTime: $dateTime, modes: $modes, first: $first) {
    edges { node {
      start end duration numberOfTransfers walkDistance
      legs {
        mode distance duration transitLeg headsign
        from { name lat lon } to { name lat lon }
        legGeometry { points }
        route { shortName color }
        start { scheduledTime } end { scheduledTime }
        steps { streetName distance }
      }
    } }
  }
}
"""
MODES = {
    "walk": {"direct": ["WALK"]},
    "bike": {"direct": ["BICYCLE"]},
    "transit": {"transit": {"transit": [{"mode": "BUS"}, {"mode": "RAIL"}, {"mode": "TRAM"},
                                        {"mode": "FERRY"}, {"mode": "SUBWAY"}]}},
}


class OTPError(RuntimeError):
    pass


class OTPSource:
    name = "otp.live"
    freshness = {"source": "live-otp", "live": True,
                 "note": "Routes generated live by OpenTripPlanner. Times are scheduled unless the "
                         "feed supplies real-time updates."}

    def __init__(self, otp_url: str, street_source, places: dict[str, dict], schema: str = "athena",
                 timeout_s: float = 20.0, session: requests.Session | None = None):
        self.url = otp_url.rstrip("/") + "/otp/gtfs/v1"
        self.street_source = street_source
        self.places = places
        self.schema = schema
        self.timeout_s = timeout_s
        self.http = session or requests.Session()
        self._cache: dict[tuple, tuple[float, list[dict], list[dict]]] = {}

    def streets(self):
        return self.street_source.streets()

    def ping(self) -> bool:
        try:
            r = self.http.post(self.url, json={"query": "{__typename}"}, timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _plan(self, origin: str, destination: str, band: str, mode: str, depart: str | None = None):
        """Returns (leg rows, street-step rows) in the precomputed table shape. Cached for 60 s."""
        key = (origin, destination, band, mode, depart)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < 60:
            return hit[1], hit[2]
        o, d = self.places.get(origin), self.places.get(destination)
        if not o or not d:
            raise OTPError(f"unknown place: {origin if not o else destination}")
        hour = depart or {"18": "18:00", "22": "22:00", "01": "01:00"}[band]
        variables = {
            "from": {"location": {"coordinate": {"latitude": o["lat"], "longitude": o["lon"]}}, "label": o["name"]},
            "to": {"location": {"coordinate": {"latitude": d["lat"], "longitude": d["lon"]}}, "label": d["name"]},
            "dateTime": {"earliestDeparture": f"{_next_date(band, hour)}T{hour}:00+11:00"},
            "modes": MODES[mode], "first": 4,
        }
        t0 = time.perf_counter()
        try:
            r = self.http.post(self.url, json={"query": PLAN_QUERY, "variables": variables},
                               timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise OTPError(f"OpenTripPlanner unreachable: {exc.__class__.__name__}") from exc
        if r.status_code != 200:
            raise OTPError(f"OpenTripPlanner HTTP {r.status_code}: {r.text[:140]}")
        body = r.json()
        if body.get("errors"):
            raise OTPError("OpenTripPlanner: " + str(body["errors"][0].get("message"))[:140])
        edges = (((body.get("data") or {}).get("planConnection") or {}).get("edges")) or []
        ms = round((time.perf_counter() - t0) * 1000)
        legs, steps = [], []
        for idx, edge in enumerate(edges):
            node = edge.get("node") or {}
            journey = f"{origin}|{destination}|{band}|{mode}|otp|{idx}"
            head = {
                "journey": journey, "variant": "otp", "itinerary": idx,
                "total_min": round((node.get("duration") or 0) / 60, 1),
                "walk_m": round(node.get("walkDistance") or 0, 1),
                "transfers": node.get("numberOfTransfers") or 0,
                "start_time": node.get("start"), "end_time": node.get("end"),
            }
            for leg_no, leg in enumerate(node.get("legs") or []):
                route = leg.get("route") or {}
                legs.append({
                    **head, "leg": leg_no, "leg_mode": leg.get("mode"),
                    "line": route.get("shortName"), "line_color": route.get("color"),
                    "headsign": leg.get("headsign"),
                    "from_name": (leg.get("from") or {}).get("name"),
                    "from_lat": (leg.get("from") or {}).get("lat"),
                    "from_lon": (leg.get("from") or {}).get("lon"),
                    "to_name": (leg.get("to") or {}).get("name"),
                    "to_lat": (leg.get("to") or {}).get("lat"),
                    "to_lon": (leg.get("to") or {}).get("lon"),
                    "distance_m": round(leg.get("distance") or 0, 1),
                    "duration_s": round(leg.get("duration") or 0),
                    "leg_start": (leg.get("start") or {}).get("scheduledTime"),
                    "leg_end": (leg.get("end") or {}).get("scheduledTime"),
                    "polyline": (leg.get("legGeometry") or {}).get("points"),
                })
                for st_no, st in enumerate(leg.get("steps") or []):
                    steps.append({"journey": journey, "variant": "otp", "itinerary": idx, "leg": leg_no,
                                  "step": st_no, "street": st.get("streetName"),
                                  "distance_m": round(st.get("distance") or 0, 1)})
        log.info("otp %s->%s %s %s: %d itineraries in %d ms", origin, destination, band, mode, len(edges), ms)
        self._cache[key] = (time.time(), legs, steps)
        self._ms = ms
        return legs, steps

    def route_legs(self, origin, destination, band, mode, depart=None):
        legs, _ = self._plan(origin, destination, band, mode, depart)
        return legs, {"sql": f"OTP planConnection {origin}->{destination} {band} {mode}",
                      "executed_by": f"OpenTripPlanner live ({self.url})", "rows": len(legs),
                      "ms": getattr(self, "_ms", 0)}

    def leg_streets(self, origin, destination, band, mode, depart=None):
        _, steps = self._plan(origin, destination, band, mode, depart)
        return steps, {"sql": f"OTP planConnection steps {origin}->{destination} {band} {mode}",
                       "executed_by": f"OpenTripPlanner live ({self.url})", "rows": len(steps), "ms": 0}


def _next_date(band: str, hhmm: str | None = None) -> str:
    """The sample corridor was planned for a weekday evening; live queries use the next such date.
    Times before 05:00 are treated as the early hours of the following day."""
    from datetime import date, timedelta
    d = date.today()
    if (hhmm and hhmm < "05:00") or (not hhmm and band == "01"):
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()
