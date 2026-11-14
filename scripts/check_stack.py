"""Check the live stack: COMP3110 gateway model discovery, one tiny request per role model, and Zetaris only
when DATA_BACKEND=zetaris. No secrets are printed or written.

  python3 scripts/check_stack.py     -> PASS/FAIL lines, evals/results/stack_check.json

Budget: one chat call per distinct role model (at most 4, plus at most one retry each).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.config import get_settings  # noqa: E402
from athena.llm import GatewayLLM, parse_json  # noqa: E402
from athena.prompts import load  # noqa: E402


def main() -> int:
    s = get_settings()
    rows, calls = [], 0

    def check(name, fn):
        try:
            detail = fn() or {}
            rows.append({"check": name, "ok": True, **detail})
            print(f"PASS {name} {json.dumps(detail)}")
        except Exception as exc:  # noqa: BLE001 - class name only; messages can echo request details
            rows.append({"check": name, "ok": False, "error": exc.__class__.__name__})
            print(f"FAIL {name} {exc.__class__.__name__}")

    llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"],
                     timeout_s=s.llm_timeout_s, fallback_model=s.fallback_model)
    models: list[str] = []

    def discover():
        models.extend(llm.models())
        return {"models": sorted(models)}
    check("gateway model discovery (/models)", discover)

    roles = {"explainer": s.explainer_model, "critic": s.critic_model, "moderator": s.moderator_model,
             "judge": s.judge_model}
    for role, model in roles.items():
        def listed(model=model):
            if models and model not in models:
                raise LookupError(f"{model} is not in /models")
            return {"model": model}
        check(f"{role} model listed", listed)
    for model in sorted(set(roles.values())):
        def text_req(model=model):
            nonlocal calls
            calls += 1
            r = llm.chat(model, load("stack_check").body, "{}", max_tokens=200, role="stack_check")
            parse_json(r.text)
            return {"requested": model, "served_by": r.model, "ms": round(r.ms), "tokens_in": r.input_tokens,
                    "tokens_out": r.output_tokens, "cost_usd": r.cost_usd, "attempts": r.attempts}
        check(f"text request {model}", text_req)

    if s.data_backend == "zetaris":
        from athena.data.zetaris import ZetarisClient
        z = ZetarisClient(s.zetaris_rest_url, s.zetaris_base_url, s.zetaris_org_id, s.zetaris_api_key,
                          s.zetaris_username, s.zetaris_password, s.zetaris_timeout_s)
        check("Zetaris SELECT 1 over REST", lambda: {"auth": z.run_sql("SELECT 1 AS one") and z.auth_method})

    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = ROOT / "evals/results/stack_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None,
                               "config": {"data_backend": s.data_backend, "models": roles,
                                          "fallback_model": s.fallback_model or None},
                               "calls_used": calls, "checks": rows}, indent=1) + "\n")
    passed = sum(r["ok"] for r in rows)
    print(f"\n{passed}/{len(rows)} passed; written to {out.relative_to(ROOT)}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
