"""Check the live stack and save evidence: Zetaris SQL, Nightshift connection/auth/model discovery/text request,
and which model actually answered. Mirrors the checks in the Meterless POWER prompt and the Setup Guide smoke test.

  python3 scripts/check_stack.py          -> prints PASS/FAIL lines, writes docs/evidence/stack_checks.txt
No secrets are printed or written.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.config import get_settings  # noqa: E402
from athena.data.zetaris import ZetarisClient  # noqa: E402
from athena.llm import NightshiftLLM, parse_json  # noqa: E402


def main() -> int:
    s = get_settings()
    lines = [f"# Stack checks {datetime.now(timezone.utc).isoformat()}"]
    results = []

    def check(name, fn):
        try:
            detail = fn() or ""
            results.append(True)
            lines.append(f"PASS {name} {detail}")
        except Exception as exc:  # noqa: BLE001
            results.append(False)
            lines.append(f"FAIL {name} {exc.__class__.__name__}: {str(exc)[:160]}")
        print(lines[-1])

    z = ZetarisClient(s.zetaris_rest_url, s.zetaris_base_url, s.zetaris_org_id, s.zetaris_api_key,
                      s.zetaris_username, s.zetaris_password, s.zetaris_timeout_s)
    check("Zetaris web interface", lambda: requests.get(s.zetaris_base_url, timeout=10).raise_for_status())
    check("Zetaris SELECT 1 over REST", lambda: f"(auth={z.run_sql('SELECT 1 AS one') and z.auth_method})")
    check("Zetaris athena.streets", lambda: f"({len(z.run_sql(f'SELECT name FROM {s.zetaris_schema}.streets'))} rows)")
    check("Zetaris athena.route_steps", lambda: f"({len(z.run_sql(f'SELECT trip FROM {s.zetaris_schema}.route_steps'))} rows)")

    def health():
        r = requests.get(s.meterless_base_url.rsplit('/v1', 1)[0] + "/healthz", timeout=10)
        if "json" not in r.headers.get("content-type", ""):
            raise RuntimeError("non-JSON reply: wrong port or forwarding")
        return r.text[:60]
    check("Nightshift connection (/healthz)", health)
    llm = NightshiftLLM(s.meterless_base_url, s.meterless_token)
    models = []
    check("Nightshift auth + model discovery (/v1/models)", lambda: (models.extend(llm.models()) or f"({len(models)} models: {models[:5]})"))
    for label, model in (("fast", s.fast_model), ("critic", s.critic_model)):
        def text_req(model=model):
            r = llm.chat(model, "TASK: check\nReply with a JSON object {\"ok\": true}.", "{}")
            parse_json(r.text)
            if "nemotron" not in r.model.lower():
                return f"(requested {model}; served by {r.model}; NOT a Nemotron model, pin a Nemotron route for the NVIDIA award)"
            return f"(requested {model}; served by {r.model}; {r.ms:.0f} ms)"
        check(f"Nightshift text request, {label} model", text_req)
    out = ROOT / "docs/evidence/stack_checks.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"\n{sum(results)}/{len(results)} passed; written to {out.relative_to(ROOT)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
