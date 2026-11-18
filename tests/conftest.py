import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Pin the hand-made sample data for the whole session, before any test module is imported: some modules build
# a LocalSource at import time, and a local `make data` rebuild must not change test results.
os.environ["ATHENA_DATA_DIR"] = str(ROOT / "data")

GATEWAY_KEY = "test-gateway-key-not-real"   # gateway mock
MODEL = "scx-gpt-oss-120b"


class MockHandler(BaseHTTPRequestHandler):
    """Mock of the LiteLLM gateway (OpenAI-compatible /v1/models and /v1/chat/completions, with
    token usage)."""
    calls: list = []
    analyst_bad_first = True
    fail_next: list = []        # HTTP status codes to return for the next chat calls (retry tests)

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
            if self.headers.get("Authorization") != f"Bearer {GATEWAY_KEY}":
                return self._send(401, {"error": {"message": "unauthorized"}})
            return self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "litellm"}
                for m in (MODEL, "scx-Meta-Llama-3.3-70B-Instruct", "aws-nova-lite-v1")]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        MockHandler.calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path == "/v1/chat/completions":
            if self.headers.get("Authorization") != f"Bearer {GATEWAY_KEY}":
                return self._send(401, {"error": {"message": "unauthorized"}})
            if MockHandler.fail_next:
                code = MockHandler.fail_next.pop(0)
                return self._send(code, {"error": {"message": f"mock {code}"}})
            payload = json.loads(body["messages"][1]["content"])
            # Prompts carry no TASK marker any more; the user payload's shape says which role is calling.
            task = ("analyst" if "question" in payload else "critic" if "claims" in payload
                    else "moderator" if "text" in payload else "other")
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
            elif task == "moderator":
                t = payload["text"].lower()
                content = json.dumps({"prejudiced": "ethnic" in t, "category": "lighting", "plausible": True, "reason": "mock"})
            else:
                content = json.dumps({"ok": True})
            return self._send(200, {
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}", "object": "chat.completion", "created": 0,
                "model": body.get("model", MODEL),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}})
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
    """Keep test traces and report queues out of the real runs/ folder, and pin the hand-made sample data
    so a local `make data` rebuild cannot change test results."""
    monkeypatch.setenv("ATHENA_DATA_DIR", str(ROOT / "data"))
    import athena.analysis as analysis
    import athena.telemetry as telemetry
    q = tmp_path / "reports_queue.jsonl"
    monkeypatch.setattr(analysis, "REPORT_QUEUE", q)
    monkeypatch.setattr(telemetry, "RUNS", tmp_path)
    yield
