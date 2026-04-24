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

# ── Build frontend if dist is missing or empty ──────────────────────────────
DIST_INDEX="/home/runner/workspace/mobile/dist/index.html"
if [ ! -f "$DIST_INDEX" ]; then
  echo "[START] dist/index.html not found — building Expo web export now..."
  cd /home/runner/workspace/mobile
  if [ ! -d node_modules ]; then
    echo "[START] Installing node_modules (--legacy-peer-deps)..."
    npm install --legacy-peer-deps --silent 2>&1 | tail -5
  fi
  npx expo export -p web --output-dir dist 2>&1 | tail -10
  if [ -f "$DIST_INDEX" ]; then
    echo "[START] Frontend build complete."
  else
    echo "[START] WARNING: build failed — site may not load correctly."
  fi
  cd /home/runner/workspace
else
  echo "[START] dist/index.html found — skipping build."
fi
# ───────────────────────────────────────────────────────────────────────────

# Start FastAPI backend on port 8000 (in background)
echo "[START] Starting backend on port 8000..."
cd /home/runner/workspace/backend
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 &
cd /home/runner/workspace

# Start production proxy on port 5000
echo "[START] Starting production proxy on port 5000..."
cd /home/runner/workspace/mobile
PRODUCTION=true node proxy.js
