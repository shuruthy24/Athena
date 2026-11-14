# Athena for COMP3110: one container, data baked in, the course gateway as the only external call.
#   make data && make train      (optional: processed Parquet and the TF-IDF classifier, if you use them)
#   docker build -t athena .
#   docker run -p 8000:8000 -e COMP3110_MODEL_BASE_URL=... -e COMP3110_API_KEY=... -e SCOUT_SECRET=... athena
# On a host, set COMP3110_API_KEY and SCOUT_SECRET in its secret store, never in this file or the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_BACKEND=files

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code, prompts, the data the app serves (sample JSON, plus data/processed if `make data` ran), web pages,
# and models/ if `make train` ran. .dockerignore keeps out raw snapshots, secrets, tests and git history.
COPY . .

RUN useradd --create-home --uid 10001 athena && mkdir -p /app/runs && chown -R athena /app/runs
USER athena

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health', timeout=4)"
CMD ["sh", "-c", "uvicorn athena.server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
