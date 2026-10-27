"""Run trace: every agent step with the model that handled it, latency and a short detail.

The trace is returned to the UI ("How Athena decided") and appended to runs/YYYYMMDD.jsonl.
Secrets and raw report text containing personal information are never written here.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

log = logging.getLogger("athena")
RUNS = ROOT / "runs"


class Trace:
    def __init__(self, trace_id: str | None = None):
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.steps: list[dict] = []
        self.events: list[dict] = []
        self._t0 = time.perf_counter()

    def step(self, agent: str, detail: str, model: str | None = None, ms: float | None = None, **extra) -> dict:
        entry = {"step": len(self.steps) + 1, "agent": agent, "model": model,
                 "ms": None if ms is None else round(ms), "detail": detail, **extra}
        self.steps.append(entry)
        log.info("trace=%s step=%s agent=%s model=%s ms=%s %s", self.trace_id, entry["step"], agent, model,
                 entry["ms"], detail)
        return entry

    def event(self, name: str, **fields) -> None:
        """Scout telemetry events (scout.decision, contract.issued, contract.verified, ...)."""
        e = {"event": name, "traceId": self.trace_id, **fields}
        self.events.append(e)
        log.info("%s %s", name, " ".join(f"{k}={v}" for k, v in fields.items()))

    def total_ms(self) -> int:
        return round((time.perf_counter() - self._t0) * 1000)

    def persist(self, request: dict, response: dict) -> Path:
        RUNS.mkdir(exist_ok=True)
        path = RUNS / f"{datetime.now(timezone.utc):%Y%m%d}.jsonl"
        record = {"at": datetime.now(timezone.utc).isoformat(), "traceId": self.trace_id,
                  "request": request, "intent": response.get("intent"), "confidence": response.get("confidence"),
                  "trace": self.steps, "events": self.events, "total_ms": self.total_ms()}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
