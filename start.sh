#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONDA_ENV="agent-gui"

# Build frontend if not already built
if [ ! -d "frontend/dist" ]; then
  echo "Building frontend..."
  cd frontend
  npm install --silent
  npm run build
  cd ..
fi

# Create conda env if it doesn't exist (environment.yml pins python + sqlite)
if ! conda env list | grep -q "^${CONDA_ENV} "; then
  echo "Creating conda environment '${CONDA_ENV}'..."
  conda env create -f environment.yml
fi

# Resolve the env's own interpreter by absolute path and call it directly.
# `conda run -n <env> python` relies on PATH, where a pyenv (or other) shim can
# shadow the env's python and silently run the wrong interpreter. The prefix is
# discovered from conda itself, so this stays portable across machines/installs.
ENV_PREFIX="$(conda env list | awk -v e="$CONDA_ENV" '$1==e {print $NF}')"
if [ -z "$ENV_PREFIX" ] || [ ! -x "$ENV_PREFIX/bin/python" ]; then
  echo "Could not locate the '${CONDA_ENV}' conda environment." >&2
  exit 1
fi
ENV_PY="$ENV_PREFIX/bin/python"

# Install (or repair) the editable package in the env. Checked via pip rather
# than `import agent_gui`, which succeeds from the repo cwd even when the
# package and its dependencies were never installed.
if ! "$ENV_PY" -m pip show agent-gui >/dev/null 2>&1; then
  echo "Installing agent-gui into '${CONDA_ENV}'..."
  "$ENV_PY" -m pip install -e . -q
fi

# GPU selection for host-run agents (Claude Agent SDK desks run their CUDA work on
# the host) and Docker sandboxes — confined to CUDA_VISIBLE_DEVICES. Ask which
# GPU(s) to use at startup. The prompt is skipped when HERMES_GUI_CUDA_VISIBLE_DEVICES
# is already set (we honor your export), there's no `nvidia-smi` (no NVIDIA GPUs —
# e.g. macOS), or stdin isn't a TTY (non-interactive run).
if [ -z "${HERMES_GUI_CUDA_VISIBLE_DEVICES:-}" ] && [ -t 0 ] && command -v nvidia-smi >/dev/null 2>&1; then
  GPU_LIST="$(nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader 2>/dev/null || true)"
  if [ -n "$GPU_LIST" ]; then
    echo "Detected GPUs (index, name, mem used / total):"
    echo "$GPU_LIST" | sed 's/^/  /'
    read -r -p "Which GPU(s) should agents use? e.g. 0 or 0,1  [Enter = all]: " gpu_choice || true
    gpu_choice="${gpu_choice// /}"   # tolerate "0, 1"
    case "$gpu_choice" in
      ""|a|A|all|ALL) : ;;                                       # all GPUs → leave unset
      *[!0-9,]*) echo "  '$gpu_choice' isn't a GPU index list — using all GPUs." ;;
      *) export HERMES_GUI_CUDA_VISIBLE_DEVICES="$gpu_choice" ;;
    esac
  fi
fi

# To skip the prompt next time, export your choice first (or add it to ~/.zshrc):
#   export HERMES_GUI_CUDA_VISIBLE_DEVICES="2"     # only GPU 2
#   export HERMES_GUI_CUDA_VISIBLE_DEVICES="0,1"   # GPUs 0 and 1
# Consumed by agent_gui/server.py (_gpu_worker_env), injected into each agent worker.
if [ -n "${HERMES_GUI_CUDA_VISIBLE_DEVICES:-}" ]; then
  export HERMES_GUI_CUDA_VISIBLE_DEVICES
  GPU_NOTE="${HERMES_GUI_CUDA_VISIBLE_DEVICES}"
else
  GPU_NOTE="all"
fi

# Experimental/developmental features (currently the Claude Code SDK agent) are
# opt-in. Pass --experimental and it's forwarded to `python -m agent_gui` via
# "$@"; we just detect it here to surface the state in the startup banner.
EXPERIMENTAL="off"
for arg in "$@"; do
  [ "$arg" = "--experimental" ] && EXPERIMENTAL="ON (Claude Code SDK agent enabled)"
done

echo "Starting Agent GUI (conda env: ${CONDA_ENV}, GPU: ${GPU_NOTE}, experimental: ${EXPERIMENTAL})..."
exec "$ENV_PY" -m agent_gui "$@"
