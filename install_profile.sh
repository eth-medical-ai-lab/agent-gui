#!/usr/bin/env bash
# Interactively configure one (or more) Hermes agent profiles for the GUI.
#
# Builds a single profile from a model choice you make at the prompt, then loops
# so you can configure as many as you like. Every profile is cloned from the
# bundled base profile (agent_profiles/base — skills, toolsets, terminal, …);
# only the `model:` block and the optional `custom_providers:` block change based
# on your answers.
#
#   • API   → Gemini  (prompts for GEMINI_API_KEY, written to the profile .env)
#   • API   → Claude  (prompts for ANTHROPIC_API_KEY, written to the profile .env)
#   • Local → vLLM    (prompts for endpoint URL + model name)
#   • Local → Ollama  (prompts for serve URL + model name)
#
# Usage:  ./configure_profiles.sh
# Env:    HERMES_HOME (default: ~/.hermes)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILES_DIR="agent_profiles"
# Base profile whose config.yaml/skills/cron we clone for every new profile.
BASE_PROFILE="base"
BASE_DIR="$PROFILES_DIR/$BASE_PROFILE"

if ! command -v hermes >/dev/null 2>&1; then
  echo "✗ 'hermes' CLI not found on PATH — install Hermes first." >&2
  exit 1
fi
if [ ! -f "$BASE_DIR/config.yaml" ]; then
  echo "✗ base config '$BASE_DIR/config.yaml' not found next to this script." >&2
  exit 1
fi

# Replace (or append) KEY=value in an .env file, preserving other lines.
set_env_key() {
  local file="$1" key="$2" val="$3" tmp found=0 line
  tmp="$(mktemp)"
  if [ -f "$file" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        "$key="*) printf '%s=%s\n' "$key" "$val" >> "$tmp"; found=1 ;;
        *)        printf '%s\n' "$line" >> "$tmp" ;;
      esac
    done < "$file"
  fi
  [ "$found" -eq 1 ] || printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$file"
}

# Prompt with a default value: prompt_default "Question" "default" VARNAME
prompt_default() {
  local q="$1" def="$2" __var="$3" ans
  read -r -p "$q [$def]: " ans
  [ -n "$ans" ] || ans="$def"
  printf -v "$__var" '%s' "$ans"
}

# Rewrite the base config.yaml's `model:` block (and `custom_providers:` block)
# without needing PyYAML — pure-text surgery on the top-level keys.
#   $1 base config path   $2 new model block   $3 custom_providers block (maybe empty)
# Writes the result to stdout.
build_config() {
  MODEL_BLOCK="$2" CUSTOM_PROVIDERS_BLOCK="$3" python3 - "$1" <<'PY'
import os, re, sys

base = open(sys.argv[1], "r", encoding="utf-8").read()
lines = base.splitlines(keepends=True)

def is_top_key(line):
    # A non-indented `key:` line (not a list item, not blank/comment).
    return bool(re.match(r"^[A-Za-z0-9_]+:", line))

def strip_block(lines, key):
    """Remove a top-level `key:` block (the key line + everything indented
    under it, including `- ` list items) and return the trimmed list."""
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if re.match(rf"^{re.escape(key)}:", line):
            i += 1
            while i < n and not is_top_key(lines[i]):
                i += 1
            continue
        out.append(line)
        i += 1
    return out

lines = strip_block(lines, "model")
lines = strip_block(lines, "custom_providers")

body = "".join(lines)
model_block = os.environ["MODEL_BLOCK"].rstrip("\n") + "\n"
out = model_block + body
custom = os.environ.get("CUSTOM_PROVIDERS_BLOCK", "").strip("\n")
if custom:
    if not out.endswith("\n"):
        out += "\n"
    out += custom + "\n"
sys.stdout.write(out)
PY
}

# Build + install one profile from interactive answers.
configure_one() {
  local name model_block custom_block gemini_key="" anthropic_key=""
  prompt_default "Profile name" "GUIAgent" name

  echo
  echo "Model source for '$name':"
  echo "  1) API   (Gemini or Claude)"
  echo "  2) Local (vLLM or Ollama)"
  local src
  read -r -p "Choose 1 or 2 [1]: " src; [ -n "$src" ] || src=1

  if [ "$src" = "1" ]; then
    echo
    echo "  1) Gemini"
    echo "  2) Claude (Anthropic)"
    local api
    read -r -p "Choose 1 or 2 [1]: " api; [ -n "$api" ] || api=1

    if [ "$api" = "2" ]; then
      local cmodel
      prompt_default "Claude model" "claude-opus-4-8" cmodel
      read -rs -p "ANTHROPIC_API_KEY (input hidden, Enter to skip): " anthropic_key; echo
      # Hermes ships a native `anthropic` provider (api_mode: anthropic_messages,
      # x-api-key auth) — no custom_providers block needed.
      model_block=$(cat <<EOF
model:
  default: $cmodel
  provider: anthropic
  base_url: https://api.anthropic.com
EOF
)
      custom_block=""
    else
      local gmodel
      prompt_default "Gemini model" "gemini-3.1-flash-lite" gmodel
      read -rs -p "GEMINI_API_KEY (input hidden, Enter to skip): " gemini_key; echo
      model_block=$(cat <<EOF
model:
  default: $gmodel
  provider: gemini
  base_url: https://generativelanguage.googleapis.com/v1beta
EOF
)
      custom_block=""
    fi
  else
    echo
    echo "  1) vLLM"
    echo "  2) Ollama"
    local kind
    read -r -p "Choose 1 or 2 [1]: " kind; [ -n "$kind" ] || kind=1

    if [ "$kind" = "1" ]; then
      local url lmodel
      prompt_default "vLLM endpoint URL" "http://127.0.0.1:8010/v1" url
      prompt_default "vLLM model name" "Qwen/Qwen3.6-27B" lmodel
      model_block=$(cat <<EOF
model:
  default: $lmodel
  provider: custom
  base_url: $url
  api_mode: chat_completions
EOF
)
      custom_block=$(cat <<EOF
custom_providers:
- name: vllm
  base_url: $url
  model: $lmodel
EOF
)
    else
      local url lmodel
      prompt_default "Ollama serve URL" "http://127.0.0.1:11434/v1" url
      prompt_default "Ollama model name" "qwen3.5:4b" lmodel
      model_block=$(cat <<EOF
model:
  default: $lmodel
  provider: custom
  base_url: $url
  api_mode: chat_completions
EOF
)
      custom_block=$(cat <<EOF
custom_providers:
- name: local-ollama
  base_url: $url
  model: $lmodel
  api_mode: chat_completions
EOF
)
    fi
  fi

  # ── Assemble a profile source dir from the base, then patch it ──────────────
  local tmp; tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  cp -R "$BASE_DIR"/. "$tmp"/

  if ! build_config "$BASE_DIR/config.yaml" "$model_block" "$custom_block" > "$tmp/config.yaml"; then
    echo "✗ failed to generate config.yaml for '$name'." >&2
    return 1
  fi

  cat > "$tmp/distribution.yaml" <<EOF
name: $name
version: 0.0.1
description: "GUI-configured Hermes agent"
author: "Hermes GUI"
license: "MIT"

env_requires:
  - name: TERMINAL_ENV # docker
    description: "Terminal environment variables"
    required: true
  - name: BRAVE_SEARCH_API_KEY
    description: "Brave Search API key for web search"
    required: false
    default: ""
EOF

  printf 'TERMINAL_ENV=docker\nBRAVE_SEARCH_API_KEY=""\n' > "$tmp/.env.EXAMPLE"

  # ── Install (force-overwrite so reconfiguring an existing name works) ────────
  # Capture hermes' verbose manifest/preview/usage output and only surface it on
  # failure — the success preview (esp. the env-var "✓ set" lines) is misleading
  # here (an empty BRAVE_SEARCH_API_KEY="" still counts as "set").
  echo "→ installing profile '$name'…"
  local install_log
  if ! install_log="$(hermes profile install "$tmp" --name "$name" --force -y 2>&1)"; then
    echo "✗ failed to install '$name':" >&2
    echo "$install_log" >&2
    return 1
  fi

  # hermes may preserve an existing config.yaml on --force, so write ours in
  # directly to guarantee the chosen model lands. hermes normalizes the id
  # (e.g. lowercases it), so resolve the directory it actually created.
  local pid pdir
  pid="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
  if   [ -d "$HERMES_HOME/profiles/$pid" ];  then pdir="$HERMES_HOME/profiles/$pid"
  elif [ -d "$HERMES_HOME/profiles/$name" ]; then pdir="$HERMES_HOME/profiles/$name"
  else pdir="$HERMES_HOME/profiles/$pid"; fi
  if [ -d "$pdir" ]; then
    cp "$tmp/config.yaml" "$pdir/config.yaml"
    # Build a minimal .env with only keys we actually have values for. Do NOT
    # seed BRAVE_SEARCH_API_KEY="" — an empty placeholder means a later
    # `echo 'BRAVE_SEARCH_API_KEY=…' >> .env` just appends a second, shadowed
    # line. set_env_key creates the file if it doesn't exist yet.
    local env_file="$pdir/.env"
    set_env_key "$env_file" "TERMINAL_ENV" "docker"
    if [ -n "$gemini_key" ]; then
      set_env_key "$env_file" "GEMINI_API_KEY" "$gemini_key"
      echo "    ✓ wrote GEMINI_API_KEY to $name/.env"
    fi
    if [ -n "$anthropic_key" ]; then
      set_env_key "$env_file" "ANTHROPIC_API_KEY" "$anthropic_key"
      echo "    ✓ wrote ANTHROPIC_API_KEY to $name/.env"
    fi
  fi

  echo "✓ profile '$name' ready (HERMES_HOME=$HERMES_HOME)"
  return 0
}

echo "Configure Hermes agent profiles for the GUI."
echo "(HERMES_HOME=$HERMES_HOME)"
while :; do
  echo
  configure_one || echo "  (profile configuration failed — you can try again)"
  echo
  read -r -p "Configure another profile? [y/N]: " more
  case "$more" in
    y|Y|yes|YES) continue ;;
    *) break ;;
  esac
done

echo "Done."
