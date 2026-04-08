#!/bin/bash
set -e

# Start MongoDB
mkdir -p /tmp/mongodb/data
mongod --dbpath /tmp/mongodb/data --logpath /tmp/mongodb/mongod.log --fork --quiet 2>/dev/null || true

# Start backend on port 8000 (background)
cd /home/runner/workspace/backend
python -m uvicorn server:app --host localhost --port 8000 --reload &
BACKEND_PID=$!

# Give backend a moment to start
sleep 3

# Start frontend on port 5000
cd /home/runner/workspace/frontend
npm start
