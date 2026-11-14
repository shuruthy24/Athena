# Model comparison (2026-11-14, git 341abce)

54 calls. Cost from COMP3110 Tech 6 README pricing table (USD per 1M tokens); '-' means unpriced.

## Explainer (first-draft pass rate)

| Model | n | Pass rate | p50 ms | p95 ms | Tokens in/out | Cost per call USD |
|---|---|---|---|---|---|---|
| scx-gpt-oss-120b | 18 | 0.8333 | 1198 | 1532 | 12476/13140 | 0.00057629 |
| scx-gemma-4-31B-it | 18 | 1.0 | 5044 | 5960 | 12250/4326 | 0.00047386 |
| scx-Meta-Llama-3.3-70B-Instruct | 18 | 0.9444 | 1584 | 24827 | 11468/3772 | 0.00063279 |
