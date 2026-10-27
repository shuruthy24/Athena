"""Zetaris REST client.

Sends one SQL statement per request to POST {ZETARIS_REST_URL}/sql-editor/sqls/run with a Bearer token,
X-Org-ID and a fresh UUID X-Request-ID (Participant Setup Guide, section 1.9).

Authentication: the Zetaris API key is tried first. On our local install API tokens are refused with
400 {"msg":"Not Allowed"} (reported to Help Zetaris), so on 400/401/403 the client signs in through
{ZETARIS_BASE_URL}/api/auth/login and retries with that session token, the same fallback used by
Zetaris's own getting-started helper scripts (scripts/zetaris_api.py).
"""
from __future__ import annotations

import logging
import re
import time
import uuid

import requests

log = logging.getLogger("athena.zetaris")
JWT = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


class ZetarisError(RuntimeError):
    pass


class ZetarisClient:
    def __init__(self, rest_url: str, base_url: str, org_id: str, api_key: str = "", username: str = "",
                 password: str = "", timeout_s: float = 30.0, session: requests.Session | None = None):
        self.rest_url = rest_url.rstrip("/")
        self.base_url = base_url.rstrip("/")
        self.org_id = str(org_id)
        self.api_key = api_key
        self.username = username
        self.password = password
        self.timeout_s = timeout_s
        self.http = session or requests.Session()
        self._login_token: str | None = None
        self.auth_method: str | None = None  # "api-key" or "login", for the trace and the README evidence

    def _login(self) -> str:
        if not (self.username and self.password):
            raise ZetarisError("API key refused and ZETARIS_USERNAME/ZETARIS_PASSWORD are not set")
        r = self.http.post(f"{self.base_url}/api/auth/login", timeout=self.timeout_s,
                           json={"username": self.username, "password": self.password},
                           headers={"X-Request-ID": str(uuid.uuid4())})
        if r.status_code != 200:
            raise ZetarisError(f"Zetaris login failed: HTTP {r.status_code}")
        m = JWT.search(r.text)
        if not m:
            raise ZetarisError("Zetaris login returned no token")
        self._login_token = m.group(0)
        return self._login_token

    def _post(self, token: str, sql: str, limit: int) -> requests.Response:
        return self.http.post(
            f"{self.rest_url}/sql-editor/sqls/run", timeout=self.timeout_s,
            headers={"Authorization": f"Bearer {token}", "X-Org-ID": self.org_id,
                     "X-Request-ID": str(uuid.uuid4()), "Content-Type": "application/json"},
            json={"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit})

    def run_sql(self, sql: str, limit: int = 500) -> list[dict]:
        """Run ONE statement. Returns rows as dicts keyed by column header."""
        if ";" in sql.strip().rstrip(";"):
            raise ZetarisError("one statement per call")
        t0 = time.perf_counter()
        r = None
        if self.api_key and self._login_token is None:
            r = self._post(self.api_key, sql, limit)
            if r.status_code == 200:
                self.auth_method = "api-key"
        if r is None or r.status_code in (400, 401, 403):
            token = self._login_token or self._login()
            r = self._post(token, sql, limit)
            if r.status_code == 401:  # session expired: sign in again once
                r = self._post(self._login(), sql, limit)
            self.auth_method = "login"
        if r.status_code != 200:
            raise ZetarisError(f"Zetaris HTTP {r.status_code}: {r.text[:160]}")
        body = r.json()
        headers = body.get("headers") or []
        data = body.get("data") or []
        rows = [dict(zip([str(h) for h in headers], row)) for row in data]
        log.info("zetaris rows=%d ms=%d auth=%s", len(rows), (time.perf_counter() - t0) * 1000, self.auth_method)
        return rows

    def ping(self) -> bool:
        try:
            rows = self.run_sql("SELECT 1 AS one", limit=1)
            return bool(rows)
        except Exception as exc:  # noqa: BLE001 - health check reports, never raises
            log.warning("zetaris ping failed: %s", exc)
            return False
