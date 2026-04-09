#!/bin/bash

# ── MongoDB persistent data directory ──────────────────────────────────────
# IMPORTANT: stored in /home/runner/.reversepicks_db — OUTSIDE the workspace.
# This means code updates / redeployments can NEVER wipe user passwords or data.
# The workspace (/home/runner/workspace/) is updated on every redeploy;
# the home directory (/home/runner/) is not touched by deployments.

DB_PATH="/home/runner/.reversepicks_db"
OLD_DB_PATH="/home/runner/workspace/mongodb_data"

mkdir -p "$DB_PATH"

# One-time migration: if new path is empty but old path has data, copy it over
if [ -z "$(ls -A $DB_PATH 2>/dev/null)" ] && [ -d "$OLD_DB_PATH" ] && [ -n "$(ls -A $OLD_DB_PATH 2>/dev/null)" ]; then
  echo "[START] Migrating MongoDB data from workspace to persistent home directory..."
  cp -r "$OLD_DB_PATH"/. "$DB_PATH"/
  echo "[START] Migration complete."
fi

mongod \
  --dbpath "$DB_PATH" \
  --logpath /home/runner/.reversepicks_mongo.log \
  --fork --quiet 2>/dev/null || true
sleep 4

# Start FastAPI backend on port 8000 (in background)
echo "[START] Starting backend on port 8000..."
cd /home/runner/workspace/backend
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 &
cd /home/runner/workspace

# Start production proxy on port 5000 immediately (dist/ is pre-built and committed)
echo "[START] Starting production proxy on port 5000..."
cd /home/runner/workspace/mobile
PRODUCTION=true node proxy.js
