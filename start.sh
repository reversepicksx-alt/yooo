#!/bin/bash

# ── MongoDB persistent data directory ──────────────────────────────────────
# IMPORTANT: stored in /home/runner/.reversepicks_db — OUTSIDE the workspace.
# This means code updates / redeployments can NEVER wipe user passwords or data.
# The workspace (/home/runner/workspace/) is updated on every redeploy;
# the home directory (/home/runner/) is not touched by deployments.

DB_PATH="/home/runner/.reversepicks_db"
OLD_DB_PATH="/home/runner/workspace/mongodb_data"

mkdir -p "$DB_PATH"

# Remove stale lock file — left behind when a previous deployment was
# hard-killed by the platform. Without this, mongod refuses to start on the
# next deploy, the backend crashes, and the health check times out.
if [ -f "$DB_PATH/mongod.lock" ]; then
  echo "[START] Removing stale mongod.lock..."
  rm -f "$DB_PATH/mongod.lock"
fi

# One-time migration: if new path is empty but old path has data, copy it over
if [ -z "$(ls -A $DB_PATH 2>/dev/null)" ] && [ -d "$OLD_DB_PATH" ] && [ -n "$(ls -A $OLD_DB_PATH 2>/dev/null)" ]; then
  echo "[START] Migrating MongoDB data from workspace to persistent home directory..."
  cp -r "$OLD_DB_PATH"/. "$DB_PATH"/
  echo "[START] Migration complete."
fi

# ── Ensure Node.js dependencies are installed ──────────────────────────────
# mobile/node_modules is excluded from the repl-layer push via .replitignore
# (it was causing deployment timeouts at ~372 MB). The build command also runs
# npm install, but Replit may re-apply the repl layer after the build, removing
# the freshly installed modules. Installing here guarantees the proxy always
# has express and http-proxy-middleware available before it starts.
echo "[START] Checking mobile/node_modules..."
cd /home/runner/workspace/mobile
if [ ! -d node_modules ] || [ ! -d node_modules/express ]; then
  echo "[START] Installing mobile dependencies (--legacy-peer-deps)..."
  npm install --legacy-peer-deps --silent 2>&1 | tail -5
  echo "[START] Dependencies installed."
else
  echo "[START] node_modules present — skipping install."
fi

# ── Start production proxy FIRST on port 5000 ──────────────────────────────
# The proxy serves the pre-built static dist/ immediately — it does NOT need
# MongoDB or the FastAPI backend to answer GET / with HTTP 200. Starting it
# first means the platform health check passes as soon as Node.js binds the
# port, while MongoDB and Python initialise in the background.
#
# The proxy is wrapped in a watchdog loop: if it ever exits (due to an
# uncaught EIO / socket error), it is immediately restarted. This prevents
# the container from going dark and showing users a blank white page.
echo "[START] Starting production proxy on port 5000..."
cd /home/runner/workspace/mobile
_proxy_watchdog() {
  while true; do
    PRODUCTION=true node proxy.js
    echo "[START] Proxy exited — restarting in 1s..."
    sleep 1
  done
}
_proxy_watchdog &
PROXY_PID=$!

# Give Node a moment to bind the port before we start the heavier processes
sleep 2

# ── Start MongoDB (background daemon) ──────────────────────────────────────
echo "[START] Starting MongoDB..."
mongod \
  --dbpath "$DB_PATH" \
  --logpath /home/runner/.reversepicks_mongo.log \
  --fork --quiet 2>/dev/null || true

# Wait for mongod to finish initialising its data files
sleep 6

# ── Build frontend if dist is missing (fallback only) ──────────────────────
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

# ── Start FastAPI backend on port 8000 ─────────────────────────────────────
echo "[START] Starting backend on port 8000..."
cd /home/runner/workspace/backend
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 &

# Keep the container alive by waiting on the proxy process
wait $PROXY_PID
