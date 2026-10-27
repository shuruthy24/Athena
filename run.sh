#!/usr/bin/env bash
# Start the Athena server on http://localhost:8001  (SAMPLE_MODE=true ./run.sh to run without Zetaris/Nightshift)
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example: fill it in, or run with SAMPLE_MODE=true"; }
exec uvicorn athena.server:app --host 127.0.0.1 --port "${PORT:-8001}"
