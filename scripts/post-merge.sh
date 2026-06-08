#!/bin/bash

echo "[post-merge] Installing backend Python dependencies..."
pip install -r backend/requirements.txt -q || echo "[post-merge] WARNING: some pip packages failed (non-fatal)"

echo "[post-merge] Installing mobile npm dependencies..."
if [ -d "mobile" ]; then
  cd mobile && npm install --legacy-peer-deps --silent && cd ..
else
  echo "[post-merge] WARNING: mobile/ directory not found — skipping npm install"
fi

echo "[post-merge] Done."
