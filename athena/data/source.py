"""Data sources with one interface: ZetarisSource (live, the required data layer) and LocalSource
(SAMPLE_MODE: reads the same tables from data/*.json so judges can run the app without credentials).

Inputs are validated against fixed vocabularies before they reach SQL, so no user text is ever
interpolated into a query.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import ROOT
from .zetaris import ZetarisClient

PLACES = ("unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown")
BANDS = ("18", "22", "01")
MODES = ("walk", "bike", "transit")
STEP_COLS = "variant, duration_min, walk_m, transfers, leg, leg_mode, line, step, street, distance_m"
STREET_COLS = "name, highway, lit, tunnel, risk_18, risk_22, risk_01, venues_22"


def validate_trip(origin: str, destination: str, band: str, mode: str) -> None:
    if origin not in PLACES or destination not in PLACES:
        raise ValueError("unknown place")
    if origin == destination:
        raise ValueError("origin and destination are the same")
    if band not in BANDS or mode not in MODES:
        raise ValueError("unknown time band or mode")


def steps_sql(schema: str, origin: str, destination: str, band: str, mode: str) -> str:
    validate_trip(origin, destination, band, mode)
    return (f"SELECT {STEP_COLS} FROM {schema}.route_steps "
            f"WHERE trip = '{origin}|{destination}' AND band = '{band}' AND mode = '{mode}'")


def streets_sql(schema: str) -> str:
    return f"SELECT {STREET_COLS} FROM {schema}.streets"


class LocalSource:
    name = "sample.json"

    def __init__(self, data_dir: Path = ROOT / "data", schema: str = "athena"):
        self.data_dir = data_dir
        self.schema = schema

    def _rows(self, file: str) -> list[dict]:
        return json.loads((self.data_dir / file).read_text())["rows"]

    def route_steps(self, origin, destination, band, mode):
        t0 = time.perf_counter()
        sql = steps_sql(self.schema, origin, destination, band, mode)
        rows = [r for r in self._rows("route_steps.json")
                if r["trip"] == f"{origin}|{destination}" and r["band"] == band and r["mode"] == mode]
        return rows, {"sql": sql, "executed_by": "sample mode (local JSON, same table shape)",
                      "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 2)}

    def streets(self):
        t0 = time.perf_counter()
        rows = self._rows("streets.json")
        return rows, {"sql": streets_sql(self.schema), "executed_by": "sample mode (local JSON, same table shape)",
                      "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 2)}


class ZetarisSource:
    name = "zetaris.sql"

    def __init__(self, client: ZetarisClient, schema: str = "athena"):
        self.client = client
        self.schema = schema

    def _run(self, sql: str):
        t0 = time.perf_counter()
        rows = self.client.run_sql(sql)
        return rows, {"sql": sql, "executed_by": f"Zetaris REST (auth={self.client.auth_method})",
                      "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def route_steps(self, origin, destination, band, mode):
        rows, ev = self._run(steps_sql(self.schema, origin, destination, band, mode))
        for r in rows:  # Zetaris may return numerics as strings
            for k in ("duration_min", "walk_m", "distance_m"):
                if r.get(k) is not None:
                    r[k] = float(r[k])
            for k in ("transfers", "leg", "step"):
                if r.get(k) is not None:
                    r[k] = int(float(r[k]))
        return rows, ev

    def streets(self):
        rows, ev = self._run(streets_sql(self.schema))
        for r in rows:
            for k in ("risk_18", "risk_22", "risk_01"):
                if r.get(k) is not None:
                    r[k] = float(r[k])
            if isinstance(r.get("tunnel"), str):
                r["tunnel"] = r["tunnel"].lower() == "true"
            if r.get("lit") in ("", "null", "None"):
                r["lit"] = None
        return rows, ev
