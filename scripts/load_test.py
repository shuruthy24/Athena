"""Load test a running Athena (AGENTS.md Step 9). Standard library only.

  python scripts/load_test.py --base-url https://<your-host>          # 50 requests per endpoint
  python scripts/load_test.py --base-url http://127.0.0.1:8001 -n 20 --concurrency 4

Measures p50/p95 latency and error rate per endpoint, and cost per /api/ask request from each response's
"usage" trace step (null when any model call was unpriced). /api/ask questions rotate across the trips
the app serves, so the run shows both cache misses and hits; the cache hit share is reported because it
changes cost a lot. Street reports are NOT sent: each approved report changes route risk for two weeks.
Each worker sends a different X-Forwarded-For so Scout's per-client rate limit treats them as separate
visitors, as real users would be.

Writes evals/results/ops.json (or --out). Run it against the deployed URL for the numbers in the docs.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# No time words: Scout reads "tonight" as the 10 pm band, which would override each trip's band.
QUESTION = "Why is the Athena route the better option?"
GETS = {
    "health": "/api/health",
    "journeys": "/api/journeys?origin=unsw&destination=townhall&band=01&mode=walk,transit&sort=balanced",
    "street_safety": "/api/street-safety?band=01",
    "safety_points": "/api/safety-points",
}


def _request(base: str, path: str, body: dict | None, client: int, timeout: float) -> tuple[float, int, dict | None]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method="POST" if body else "GET",
                                 headers={"Content-Type": "application/json",
                                          "X-Forwarded-For": f"198.51.100.{client % 250 + 1}"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read() or b"{}")
            return (time.perf_counter() - t0) * 1000, r.status, payload
    except urllib.error.HTTPError as e:
        return (time.perf_counter() - t0) * 1000, e.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return (time.perf_counter() - t0) * 1000, 0, None


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def summarise(results: list[tuple[float, int, dict | None]]) -> dict:
    ms = [r[0] for r in results]
    errors = sum(1 for r in results if not 200 <= r[1] < 300)
    return {"n": len(results), "error_rate": round(errors / len(results), 4) if results else None,
            "p50_ms": pct(ms, 0.50), "p95_ms": pct(ms, 0.95),
            "mean_ms": round(statistics.mean(ms), 1) if ms else None}


def trips(base: str, timeout: float) -> list[dict]:
    """Trips with routes, read from the server itself so the test follows whatever data it serves."""
    _, status, places = _request(base, "/api/places", None, 0, timeout)
    ids = [p["id"] for p in (places or {}).get("places", [])] or ["unsw", "townhall"]
    out = []
    for o in ids:
        for d in ids:
            if o != d:
                for band in ("18", "22", "01"):
                    for mode in ("walk", "bike", "transit"):
                        out.append({"origin": o, "destination": d, "band": band, "mode": mode})
    return out


def run(base: str, n: int, concurrency: int, timeout: float) -> dict:
    endpoints = {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for name, path in GETS.items():
            endpoints[name] = summarise(list(pool.map(lambda i, p=path: _request(base, p, None, i, timeout), range(n))))
        all_trips = trips(base, timeout)
        asks = [{"message": QUESTION, "trip": all_trips[i % len(all_trips)]} for i in range(n)]
        ask_results = list(pool.map(lambda a: _request(base, "/api/ask", a[1], a[0], timeout), enumerate(asks)))
    s = summarise(ask_results)
    usage = [next((t for t in (r[2] or {}).get("trace", []) if t.get("agent") == "usage"), None) for r in ask_results]
    usage = [u for u in usage if u]
    costs = [u.get("cost_usd") for u in usage]
    hits = sum(1 for r in ask_results if any(t.get("agent") == "cache" for t in (r[2] or {}).get("trace", [])))
    answered = [r for r in ask_results if r[2] and r[2].get("intent") == "route.explain"]
    s.update({"routes_explained": len(answered), "cache_hit_share": round(hits / len(ask_results), 3) if ask_results else None,
              "model_calls_per_request": round(sum(u.get("calls", 0) for u in usage) / len(usage), 2) if usage else None,
              "cost_usd_per_request": round(sum(costs) / len(costs), 8) if costs and all(c is not None for c in costs) else None,
              "fallbacks": sum(1 for r in answered if str(r[2].get("critic_verdict", "")).startswith("fallback"))})
    endpoints["ask_route"] = s
    return endpoints


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("-n", type=int, default=50)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--out", default=str(ROOT / "evals/results/ops.json"))
    args = ap.parse_args(argv)
    _, status, health = _request(args.base_url, "/api/health", None, 0, args.timeout)
    endpoints = run(args.base_url, args.n, args.concurrency, args.timeout)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_sha": sha or None,
           "config": {"base_url": args.base_url, "requests_per_endpoint": args.n, "concurrency": args.concurrency,
                      "sample_mode": (health or {}).get("sample_mode"), "gateway": (health or {}).get("gateway"),
                      "models": (health or {}).get("models")},
           "calls_used": None, "endpoints": endpoints}
    res["calls_used"] = round((endpoints["ask_route"].get("model_calls_per_request") or 0) * args.n)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
    for name, e in endpoints.items():
        extra = f" cost/req {e.get('cost_usd_per_request')} cache hits {e.get('cache_hit_share')}" if name == "ask_route" else ""
        print(f"{name:14} n={e['n']} p50={e['p50_ms']} ms p95={e['p95_ms']} ms errors={e['error_rate']}{extra}")
    if res["config"]["sample_mode"]:
        print("note: the server is in SAMPLE_MODE, so these numbers are not evidence about the deployed system")
    return 0


if __name__ == "__main__":
    sys.exit(main())
