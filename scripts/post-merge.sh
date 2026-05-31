#!/bin/bash
set -e

echo "[post-merge] Installing backend Python dependencies..."
cd backend && pip install -r requirements.txt -q && cd ..

echo "[post-merge] Installing mobile npm dependencies..."
cd mobile && npm install --legacy-peer-deps --silent && cd ..

echo "[post-merge] Done."
