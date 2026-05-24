#!/bin/sh
set -e

BRIDGE_PORT="${BRIDGE_PORT:-3080}"

if [ -f /app/services/browser_bridge/server.js ] && command -v node >/dev/null 2>&1; then
  echo "[entrypoint] Starting Browser Bridge on port ${BRIDGE_PORT}..."
  cd /app/services/browser_bridge
  node server.js &
  BRIDGE_PID=$!
  cd /app

  for i in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:${BRIDGE_PORT}/health" >/dev/null 2>&1; then
      echo "[entrypoint] Browser Bridge ready (pid ${BRIDGE_PID})"
      break
    fi
    sleep 0.5
  done
fi

echo "[entrypoint] Starting webchat2api..."
exec uv run python main.py
