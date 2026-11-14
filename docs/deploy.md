# Deploying Athena

One container: FastAPI, the web pages, the data files and (optionally) the trained classifier. The course
gateway is the only external call. These steps are for a team member to run; nothing here has been deployed.

## Before building

```bash
make check                  # tests, offline evals, pipeline.check, checksums, secrets scan
make data                   # optional: only after `make ingest` and the OTP precompute (see data_sources.md)
make train                  # only if REPORT_CLASSIFIER=tfidf-logreg or tfidf-svm
docker build -t athena .
docker run --rm -p 8000:8000 -e SAMPLE_MODE=true athena          # smoke test without a key
```

Open http://localhost:8000 and http://localhost:8000/api/health.

## Environment on the host

Set these in the host's **secret store**, never in the image, the repo or a screenshot:

| Variable | Value |
| --- | --- |
| `COMP3110_MODEL_BASE_URL` | `https://lllm.cs3110.cse.academy/v1` |
| `COMP3110_API_KEY` | your course key (secret) |
| `SCOUT_SECRET` | a long random string (secret); hashes client ids for the rate limit |
| `EXPLAINER_MODEL`, `CRITIC_MODEL`, `MODERATOR_MODEL`, `JUDGE_MODEL` | the models chosen from `evals/results/model_comparison.json` |
| `FALLBACK_MODEL` | optional single-retry route, e.g. the Bedrock one if SCX is rate-limiting |
| `REPORT_CLASSIFIER` | the classifier chosen from `evals/results/classifier_dev.json` (default `moderator-v1`) |

## Hosts that run one container

Check the free-tier limits (memory, sleep after inactivity, region) before choosing; a cold start on Demo Day
is worth avoiding.

- **Render** (Web Service from the repo's Dockerfile): add the variables above under Environment, set the
  health check path to `/api/health`.
- **Fly.io**: `fly launch --no-deploy` (it reads the Dockerfile), `fly secrets set COMP3110_API_KEY=... SCOUT_SECRET=...`,
  `fly deploy`. Set `min_machines_running = 1` so it does not sleep during the demo.
- **Hugging Face Docker Space**: create a Docker Space, push the repo, add the secrets under Settings, and
  set `PORT=7860` (Spaces expect that port).

The container trusts `X-Forwarded-For` from the host's proxy (`--proxy-headers`), so the rate limit treats each
visitor separately. The id is a keyed hash, so no IP address is stored.

## After deploying

```bash
curl https://<your-host>/api/health        # gateway "ok", models listed, sample_mode false
python scripts/load_test.py --base-url https://<your-host>     # writes evals/results/ops.json
```

`ops.json` holds p50/p95 latency, error rate, cache hit share and cost per request; those are the numbers for
the design doc and the talk. Then record the golden-path video against the deployed URL: UNSW to Central at
1 am, the explanation, a street report, an injection attempt blocked.

## Graceful degradation (already built in)

If the gateway fails, route explanations fall back to the fact-only explanation (`critic_verdict:
"fallback-error"`, confidence low) and reports go to human review. `SAMPLE_MODE=true` runs the whole app
offline for a backup demo.

## Privacy and sovereignty

Reports describe where people feel unsafe. The app keeps no accounts, redacts PII before any model call, and
stores report text only in the container's `runs/` folder, which is lost on redeploy. Check where each
gateway model is hosted before sending user text to it, and say so in the model card.
