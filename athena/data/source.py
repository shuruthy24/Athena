"""Data source: LocalSource reads the data files. The `sql` it reports with each result is the equivalent
query over the same table shape, kept as evidence of what was selected.

LocalSource reads data/processed/*.parquet when `make data` has produced a complete rebuild (streets and
OTP routes together, see data/processed/manifest.json), otherwise the hand-made sample in data/*.json.
It never mixes the two: new street names would not match the sample routes. ATHENA_DATA_DIR pins a folder.

Inputs are validated against fixed vocabularies before they reach SQL, so no user text is ever
interpolated into a query.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..config import ROOT

PLACES = ("unsw", "townhall", "central", "quay", "redfern", "kingsx", "newtown")
BANDS = ("18", "22", "01")
MODES = ("walk", "bike", "transit")
STEP_COLS = "variant, duration_min, walk_m, transfers, leg, leg_mode, line, step, street, distance_m"
STREET_COLS = "name, highway, lit, tunnel, risk_18, risk_22, risk_01, venues_18, venues_22, venues_01"
LEG_COLS = ("journey, variant, itinerary, total_min, walk_m, transfers, start_time, end_time, leg, "
            "leg_mode, line, line_color, headsign, from_name, from_lat, from_lon, to_name, to_lat, "
            "to_lon, distance_m, duration_s, leg_start, leg_end, polyline")
LEG_STREET_COLS = "journey, variant, itinerary, leg, step, street, distance_m"
NUMERIC_LEG = ("total_min", "walk_m", "from_lat", "from_lon", "to_lat", "to_lon", "distance_m")
INT_LEG = ("itinerary", "transfers", "leg", "duration_s")


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


def legs_sql(schema: str, origin: str, destination: str, band: str, mode: str) -> str:
    validate_trip(origin, destination, band, mode)
    return (f"SELECT {LEG_COLS} FROM {schema}.route_legs "
            f"WHERE trip = '{origin}|{destination}' AND band = '{band}' AND mode = '{mode}'")


def leg_streets_sql(schema: str, origin: str, destination: str, band: str, mode: str) -> str:
    validate_trip(origin, destination, band, mode)
    return (f"SELECT {LEG_STREET_COLS} FROM {schema}.leg_streets "
            f"WHERE trip = '{origin}|{destination}' AND band = '{band}' AND mode = '{mode}'")


PROCESSED = ROOT / "data" / "processed"


def active_data_dir() -> Path:
    """The folder LocalSource serves: ATHENA_DATA_DIR, else a complete processed rebuild, else the sample."""
    if os.environ.get("ATHENA_DATA_DIR"):
        return Path(os.environ["ATHENA_DATA_DIR"])
    manifest = PROCESSED / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()).get("routes") == "otp":
        return PROCESSED
    return ROOT / "data"


def read_table(data_dir: Path, name: str) -> list[dict]:
    """Rows of one table: <name>.parquet if present, else the {"rows": [...]} JSON of the sample."""
    pq_path = data_dir / f"{name}.parquet"
    if pq_path.exists():
        import pyarrow.parquet as pq
        return pq.read_table(pq_path).to_pylist()
    json_path = data_dir / f"{name}.json"
    if json_path.exists():
        return json.loads(json_path.read_text())["rows"]
    raise FileNotFoundError(f"no {name}.parquet or {name}.json in {data_dir}")


class LocalSource:
    name = "sample.json"
    freshness = {"source": "precomputed", "live": False,
                 "note": "Route alternatives precomputed with OpenTripPlanner; times are scheduled, not live."}

    def __init__(self, data_dir: Path | None = None, schema: str = "athena"):
        self.data_dir = Path(data_dir) if data_dir else active_data_dir()
        self.schema = schema
        self.processed = (self.data_dir / "manifest.json").exists()
        if self.processed:
            self.name = "processed.parquet"

    def route_legs(self, origin, destination, band, mode):
        t0 = time.perf_counter()
        rows = [r for r in self._rows("route_legs.json")
                if r["trip"] == f"{origin}|{destination}" and r["band"] == band and r["mode"] == mode]
        return rows, {"sql": legs_sql(self.schema, origin, destination, band, mode),
                      "executed_by": self._label(), "rows": len(rows),
                      "ms": round((time.perf_counter() - t0) * 1000, 2)}

    def leg_streets(self, origin, destination, band, mode):
        t0 = time.perf_counter()
        rows = [r for r in self._rows("leg_streets.json")
                if r["trip"] == f"{origin}|{destination}" and r["band"] == band and r["mode"] == mode]
        return rows, {"sql": leg_streets_sql(self.schema, origin, destination, band, mode),
                      "executed_by": self._label(), "rows": len(rows),
                      "ms": round((time.perf_counter() - t0) * 1000, 2)}

    def _label(self) -> str:
        return ("processed data files (Parquet, same table shape)" if self.processed
                else "sample mode (local JSON, same table shape)")

    def _rows(self, file: str) -> list[dict]:
        return read_table(self.data_dir, file.rsplit(".", 1)[0])

    def route_steps(self, origin, destination, band, mode):
        t0 = time.perf_counter()
        sql = steps_sql(self.schema, origin, destination, band, mode)
        rows = [r for r in self._rows("route_steps.json")
                if r["trip"] == f"{origin}|{destination}" and r["band"] == band and r["mode"] == mode]
        return rows, {"sql": sql, "executed_by": self._label(),
                      "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 2)}

    def streets(self):
        t0 = time.perf_counter()
        rows = self._rows("streets.json")
        return rows, {"sql": streets_sql(self.schema), "executed_by": self._label(),
                      "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 2)}
