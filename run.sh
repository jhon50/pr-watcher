#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Seed your live (git-ignored) rules from the template on first run. Edit
# review_rules.md freely — it's never tracked, so `git pull` stays clean.
if [ ! -f review_rules.md ]; then
  cp review_rules.example.md review_rules.md
  echo "Created review_rules.md from the template — edit it to taste."
fi

if [ ! -d .venv ]; then
  echo "Creating venv..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

PORT="${PRW_PORT:-4747}"
exec .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port "$PORT" --reload
