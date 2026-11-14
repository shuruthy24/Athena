"""Model prices for cost estimates (USD per one million tokens).

Source: COMP3110 Tech 6 README, "Model pricing reference" (the latest table the course published).
The course has published no prices for the AWS Nova or Bedrock routes, so those are left out and
their cost is reported as None ("unpriced") instead of being guessed.
"""
from __future__ import annotations

PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-oss-120b": {"input": 0.1872, "output": 0.6117},
    "gemma-4-31B-it": {"input": 0.3370, "output": 1.0174},
    "Meta-Llama-3.3-70B-Instruct": {"input": 0.5929, "output": 1.2171},
    "MiniMax-M2.7": {"input": 0.5368, "output": 1.9973},
}
PRICE_SOURCE = "COMP3110 Tech 6 README pricing table (USD per 1M tokens)"


def price_for(model: str | None) -> dict[str, float] | None:
    """Look up a gateway model ID such as 'scx-gpt-oss-120b'. Unknown or Bedrock routes return None."""
    if not model:
        return None
    name = model.split("/")[-1]
    if name.startswith("scx-"):
        name = name[4:]
    return PRICES_USD_PER_MILLION.get(name)


def estimate_cost_usd(model: str | None, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """input/1e6 x input price + output/1e6 x output price, or None when unpriced or tokens are missing."""
    price = price_for(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return input_tokens / 1_000_000 * price["input"] + output_tokens / 1_000_000 * price["output"]
