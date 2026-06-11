"""Shared LLM backend detection and api_mode normalization."""
from __future__ import annotations


def is_ollama_backend(base_url: str, provider: str = "") -> bool:
    """True when the configured backend is local Ollama.

    Uses ``provider: ollama`` from Hermes config when set; otherwise falls back
    to the conventional Ollama port (:11434) on ``base_url``.
    """
    if (provider or "").strip().lower() == "ollama":
        return True
    return ":11434" in (base_url or "")


def is_local_openai_compat(base_url: str, provider: str = "") -> bool:
    """True for localhost Ollama or vLLM OpenAI-compat endpoints."""
    if is_ollama_backend(base_url, provider):
        return True
    base = (base_url or "").strip().lower()
    return any(h in base for h in ("localhost", "127.0.0.1", ":8010"))


def normalize_api_mode(api_mode: str, base_url: str, provider: str = "") -> str:
    """Map GUI/Hermes api_mode values to what AIAgent expects locally.

    - ``openai`` → ``chat_completions`` (legacy GUI toggle)
    - ``codex_responses`` on a local Ollama/vLLM endpoint → ``chat_completions``
      (remote Codex keeps ``codex_responses``)
    """
    mode = (api_mode or "").strip().lower()
    if mode in ("openai",):
        return "chat_completions"
    if mode == "codex_responses" and is_local_openai_compat(base_url, provider):
        return "chat_completions"
    return mode


def supports_ollama_think_param(model: str) -> bool:
    """True for Ollama models that accept the top-level ``think`` field on /api/chat."""
    m = (model or "").lower()
    return any(k in m for k in ("qwen", "deepseek", "r1"))


def should_apply_reasoning_effort(model: str, base_url: str = "", provider: str = "") -> bool:
    """True when the GUI/worker should send reasoning/thinking params for this model."""
    if is_ollama_backend(base_url, provider):
        return supports_ollama_think_param(model)
    # Non-Ollama: Hermes handles provider-specific reasoning; don't block here.
    return True


def wants_native_ollama_proxy(cfg_api_mode: str, env_api_mode: str) -> bool:
    """Use Ollama's native /api/chat proxy only when explicitly requested.

    ``chat_completions`` / ``openai`` (and downgraded ``codex_responses``) use the
    OpenAI-compat ``/v1/chat/completions`` path — including for local Ollama, per
    the standard Hermes setup (see agent_create.md).
    """
    for raw in (cfg_api_mode, env_api_mode):
        m = (raw or "").strip().lower()
        if m == "ollama":
            return True
        if m in ("chat_completions", "openai", "codex_responses"):
            return False
    return False
