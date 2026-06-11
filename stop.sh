#!/usr/bin/env bash
# Stop the Agent GUI stack.
#   ./stop.sh               # desk workers, server, frontend dev server, and Ollama
#   ./stop.sh --keep-ollama # leave the local Ollama daemon running
set -u

KEEP_OLLAMA=0
case "${1:-}" in
  --keep-ollama) KEEP_OLLAMA=1 ;;
  -h|--help)     echo "usage: ./stop.sh [--keep-ollama]"; exit 0 ;;
  "")            ;;
  *)             echo "unknown arg: $1"; echo "usage: ./stop.sh [--keep-ollama]"; exit 1 ;;
esac

# kill_match <label> <grace_seconds> <pgrep/pkill args...> — graceful TERM,
# poll up to <grace_seconds>, then KILL if still alive.
kill_match() {
  local label="$1" grace="$2"; shift 2
  if ! pgrep "$@" >/dev/null 2>&1; then
    echo "  - ${label}: not running"
    return
  fi
  pkill "$@" 2>/dev/null || true
  local i=0
  while [ "$i" -lt "$grace" ] && pgrep "$@" >/dev/null 2>&1; do
    sleep 1
    i=$((i + 1))
  done
  if pgrep "$@" >/dev/null 2>&1; then
    pkill -9 "$@" 2>/dev/null || true
    echo "  - ${label}: force-killed"
  else
    echo "  - ${label}: stopped"
  fi
}

echo "Stopping Agent GUI stack..."
# Workers first so the server doesn't respawn them / log errors as they exit.
kill_match "desk workers"        5  -f hermes_worker
# Match the `python -m agent_gui` invocation specifically — a bare "agent_gui"
# would also match the project directory path in unrelated processes' argv.
# Long grace period: on SIGTERM the server waits for workers (≤4 s) and then
# reaps the hermes-* sandbox containers; killing it early leaks containers.
kill_match "GUI server"          20 -f "[-]m agent_gui"
kill_match "frontend dev server" 3  -f vite
if [ "$KEEP_OLLAMA" -eq 0 ]; then
  kill_match "Ollama daemon"     5  -x ollama
else
  echo "  - Ollama daemon: left running (--keep-ollama)"
fi

# With the default cleanup setting the server reaps all hermes-* containers as
# it shuts down. Any still alive here mean "keep sandbox containers" is enabled
# (⚙ → Docker) or the server was killed before cleanup finished — just note it.
if command -v docker >/dev/null 2>&1; then
  n=$(docker ps -q --filter name=hermes- 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n" != "0" ]; then
    echo "Note: ${n} hermes-* Docker container(s) still running (warm reuse on next start)."
    echo "      Clear with: docker rm -f \$(docker ps -aq --filter name=hermes-)"
  fi
fi

echo "Done."
