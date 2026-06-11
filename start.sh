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

echo "Starting Agent GUI (conda env: ${CONDA_ENV})..."
exec "$ENV_PY" -m agent_gui "$@"
