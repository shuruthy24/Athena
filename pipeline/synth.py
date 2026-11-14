"""Synthetic training variants of train-split reports (AGENTS.md Step 3). Live: you run this.

  python -m pipeline.synth --max-rows 300            # uses MODERATOR_MODEL through the COMP3110 gateway
  python -m pipeline.synth --dry-run                 # shows the plan and the call budget, no calls

Reads only data/reports/train.jsonl (never dev or test), so no evaluation text leaks into training.
Writes data/reports/train_synthetic.jsonl with source="synthetic", the seed's labels and author_group
(variants of one writer's report stay with that writer), and an id "s-<seed id>-<k>". Each variant is
PII-scrubbed and dropped if it uses banned wording or duplicates its seed. Budget: at most
ceil(max_rows / n) calls, one retry each inside the client (AGENTS.md section 2).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from athena.agents import BANNED
from athena.prompts import load

from .clean import normalise, remove_pii

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reports"


def strict_variants(text: str, n: int) -> list[str]:
    """Parse {"variants": [...]} strictly: exactly that one key, a list of non-empty strings."""
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"variants"} or not isinstance(data["variants"], list):
        raise ValueError("reply must be exactly {\"variants\": [...]}")
    out = [v.strip() for v in data["variants"] if isinstance(v, str) and v.strip()]
    return out[:n]


def generate(seeds: list[dict], llm, model: str, n: int = 2, max_rows: int = 300) -> tuple[list[dict], dict]:
    prompt = load("synth")
    rows, calls, dropped, malformed = [], 0, 0, 0
    for seed in seeds:
        if len(rows) >= max_rows:
            break
        calls += 1
        try:
            reply = llm.chat(model, prompt.body, json.dumps({"text": seed["text"], "n": n}), role="synth")
            variants = strict_variants(reply.text, n)
        except (ValueError, RuntimeError):
            malformed += 1
            continue
        for k, v in enumerate(variants, 1):
            v, _ = remove_pii(v)
            if BANNED.search(v) or normalise(v) == normalise(seed["text"]):
                dropped += 1
                continue
            rows.append({**seed, "id": f"s-{seed['id']}-{k}", "text": v, "source": "synthetic"})
    stats = {"seeds_used": min(len(seeds), calls), "calls_used": calls, "rows": len(rows[:max_rows]),
             "dropped": dropped, "malformed": malformed, "prompt": f"{prompt.id}@{prompt.sha}", "model": model}
    return rows[:max_rows], stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rows", type=int, default=300)
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    seeds = [json.loads(x) for x in (REPORTS / "train.jsonl").read_text().splitlines() if x.strip()]
    seeds = [s for s in seeds if s.get("source") != "synthetic"]
    budget = min(len(seeds), math.ceil(args.max_rows / args.n))
    print(f"{len(seeds)} train seeds; budget {budget} calls for up to {args.max_rows} rows")
    if args.dry_run:
        return 0
    from athena.config import get_settings
    from athena.llm import GatewayLLM
    s = get_settings()
    llm = GatewayLLM(os.environ["COMP3110_MODEL_BASE_URL"], os.environ["COMP3110_API_KEY"],
                     timeout_s=s.llm_timeout_s, fallback_model=s.fallback_model)
    rows, stats = generate(seeds[:budget], llm, s.moderator_model, args.n, args.max_rows)
    (REPORTS / "train_synthetic.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    stats["generated_at"] = datetime.now(timezone.utc).isoformat()
    (ROOT / "evals/results/synth.json").write_text(json.dumps(stats, indent=1) + "\n")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
