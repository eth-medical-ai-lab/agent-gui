#!/usr/bin/env bash
# Dev launcher: ALWAYS rebuild the frontend before starting the server.
#
# start.sh only builds when frontend/dist is missing, so after a `git pull` a
# stale dist gets served against newer backend/frontend code — the UI silently
# runs old code. Use dev.sh while developing/after pulling to guarantee the
# served bundle matches the source. Then it hands off to start.sh for the
# environment setup and server launch.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Force a clean restart: kill any running server + desk workers first so we
# never end up with two servers fighting over the port / stale workers. Keep
# Ollama running so the model stays warm (no cold reload on restart).
echo "[dev.sh] Stopping any running Agent GUI..."
./stop.sh --keep-ollama || true

echo "[dev.sh] Rebuilding frontend (tsc && vite build)..."
(
  cd frontend
  # npm ci is reproducible from package-lock; fall back to install if no lockfile.
  if [ -f package-lock.json ]; then
    npm ci --silent
  else
    npm install --silent
  fi
  npm run build
)

echo "[dev.sh] Frontend rebuilt. Handing off to start.sh..."
exec ./start.sh "$@"
