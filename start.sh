#!/bin/bash

# Start MongoDB with persistent data directory (survives VM restarts)
mkdir -p /home/runner/workspace/mongodb_data
mongod \
  --dbpath /home/runner/workspace/mongodb_data \
  --logpath /home/runner/workspace/mongodb.log \
  --fork --quiet 2>/dev/null || true
sleep 4

# Start FastAPI backend on port 8000 (in background)
echo "[START] Starting backend on port 8000..."
cd /home/runner/workspace/backend
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 &
cd /home/runner/workspace

# Start production proxy on port 5000 immediately (dist/ was built in build phase)
echo "[START] Starting production proxy on port 5000..."
cd /home/runner/workspace/mobile
PRODUCTION=true node proxy.js
