#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating venv..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

PORT="${PRW_PORT:-4747}"
exec .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port "$PORT" --reload
