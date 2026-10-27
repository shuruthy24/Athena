import json
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JWT = "aaaaaaaa1.bbbbbbbb2.cccccccc3"
API_KEY = "test-api-key-not-real"
RUNTIME_TOKEN = "test-runtime-token"
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
STEP_COLS = ["variant", "duration_min", "walk_m", "transfers", "leg", "leg_mode", "line", "step", "street", "distance_m"]
STREET_COLS = ["name", "highway", "lit", "tunnel", "risk_18", "risk_22", "risk_01", "venues_18", "venues_22", "venues_01"]


class MockHandler(BaseHTTPRequestHandler):
    """Mock of the two live services: Zetaris REST (API key refused, login works) and the Nightshift Runtime."""
    calls: list = []
    analyst_bad_first = True

    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        body = json.dumps(obj).encode() if ctype == "application/json" else obj.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            if self.headers.get("Authorization") != f"Bearer {RUNTIME_TOKEN}":
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"object": "list", "data": [{"id": "meterless/auto"}, {"id": MODEL}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        MockHandler.calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path == "/api/auth/login":
            if body.get("password") != "Correct1!":
                return self._send(401, {"msg": "bad credentials"})
            return self._send(200, {"idToken": JWT})
        if self.path == "/api/v1.0/sql-editor/sqls/run":
            uuid.UUID(self.headers["X-Request-ID"])
            assert self.headers["X-Org-ID"] == "1"
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {API_KEY}":
                return self._send(400, {"msg": "Not Allowed"})
            if auth != f"Bearer {JWT}":
                return self._send(401, {"msg": "unauthorized"})
            sql = body["sql"]
            m = re.match(r"SELECT (.+?) FROM \w+\.(route_legs|leg_streets) WHERE trip = '([^']+)' AND band = '(\d+)' AND mode = '(\w+)'", sql)
            if m:  # journey-planner tables: return exactly the selected columns, as strings like Zetaris does
                cols, table, trip, band, mode = [x.strip() for x in m.group(1).split(",")], *m.groups()[1:]
                rows = [r for r in json.loads((ROOT / f"data/{table}.json").read_text())["rows"]
                        if r["trip"] == trip and r["band"] == band and r["mode"] == mode]
                return self._send(200, {"headers": cols, "data": [[None if r[c] is None else str(r[c]) for c in cols] for r in rows]})
            if "route_steps" in sql:
                trip, band, mode = re.search(r"trip = '([^']+)' AND band = '(\d+)' AND mode = '(\w+)'", sql).groups()
                rows = [r for r in json.loads((ROOT / "data/route_steps.json").read_text())["rows"]
                        if r["trip"] == trip and r["band"] == band and r["mode"] == mode]
                return self._send(200, {"headers": STEP_COLS, "data": [[None if r[c] is None else str(r[c]) for c in STEP_COLS] for r in rows]})
            if "streets" in sql:
                rows = json.loads((ROOT / "data/streets.json").read_text())["rows"]
                return self._send(200, {"headers": STREET_COLS, "data": [[None if r[c] is None else str(r[c]) for c in STREET_COLS] for r in rows]})
            return self._send(200, {"headers": ["1"], "data": [["1"]]})
        if self.path == "/v1/chat/completions":
            if self.headers.get("Authorization") != f"Bearer {RUNTIME_TOKEN}" or self.headers.get("meterless-app") != "athena":
                return self._send(401, {"error": "unauthorized"})
            system = body["messages"][0]["content"]
            payload = json.loads(body["messages"][1]["content"])
            task = re.search(r"TASK:\s*(\w+)", system).group(1)
            if task == "analyst":
                # The analyst receives the flat facts, not the comparison object (same as a live call).
                f = payload["facts"]
                head = "Athena's route is lower risk for this trip."
                claims = [{"text": f"Athena's route takes {f['extra_min']:+} min and has "
                                   f"{f['risk_reduction_pct']}% lower route risk than the fastest route.",
                           "facts": ["extra_min", "risk_reduction_pct"]}]
                if MockHandler.analyst_bad_first and not payload.get("previous_feedback"):
                    claims = claims + [{"text": "This route is completely safe.", "facts": ["risk_reduction_pct"]}]
                content = json.dumps({"headline": head, "claims": claims})
            elif task == "critic":
                content = "Here is my verdict:\n```json\n" + json.dumps({"verdict": "approve", "unsupported": [], "notes": "ok"}) + "\n```"
            else:
                t = payload["text"].lower()
                content = json.dumps({"prejudiced": "ethnic" in t, "category": "lighting", "plausible": True, "reason": "mock"})
            return self._send(200, {"model": MODEL, "choices": [{"message": {"role": "assistant", "content": content}}]})
        self._send(404, {})


@pytest.fixture(scope="session")
def mock_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def isolate_runs(tmp_path, monkeypatch):
    """Keep test traces and report queues out of the real runs/ folder."""
    import athena.analysis as analysis
    import athena.telemetry as telemetry
    q = tmp_path / "reports_queue.jsonl"
    monkeypatch.setattr(analysis, "REPORT_QUEUE", q)
    monkeypatch.setattr(telemetry, "RUNS", tmp_path)
    yield
