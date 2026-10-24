#!/usr/bin/env bash
# Dev launcher.
#
# PRIMARY (default port 8765): ALWAYS rebuild the frontend, after a clean stop.
#   start.sh only builds when frontend/dist is missing, so after a `git pull` a
#   stale dist gets served against newer code. Use dev.sh while developing / after
#   pulling to guarantee the served bundle matches the source.
#     bash dev.sh
#
# SECONDARY (any other --port): run a second app ALONGSIDE the primary — e.g. a
#   white/red screenshot instance — WITHOUT stopping or rebuilding the primary.
#     bash dev.sh --port 8766 --front-end light
#   It reuses the already-built dist (the theme is injected server-side, no rebuild)
#   and adds --no-docker-reap so its Ctrl+C won't reap the primary app's sandboxes.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DEFAULT_PORT=8765

# Find an explicit --port / --port=N / -p N in the args (default 8765).
PORT="$DEFAULT_PORT"
prev=""
for a in "$@"; do
  case "$prev" in --port|-p) PORT="$a" ;; esac
  case "$a" in --port=*) PORT="${a#*=}" ;; esac
  prev="$a"
done

if [ "$PORT" != "$DEFAULT_PORT" ]; then
  # ── Secondary instance ──────────────────────────────────────────────────────
  echo "[dev.sh] Secondary app on :$PORT — primary app + build left untouched."
  # Don't reap the primary's Docker sandboxes on this app's shutdown (unless the
  # caller already asked). start.sh builds the dist only if it's missing.
  case " $* " in
    *" --no-docker-reap "*) exec ./start.sh "$@" ;;
    *)                      exec ./start.sh "$@" --no-docker-reap ;;
  esac
fi

# ── Primary instance: full clean restart + rebuild ────────────────────────────
# Kill any running server + desk workers first so we never end up with two
# servers fighting over the port / stale workers. Keep Ollama warm.
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
