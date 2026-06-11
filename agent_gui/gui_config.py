"""Resolve Agent GUI paths from CLI flags (defaults: ~/.hermes)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_HERMES = Path.home() / ".hermes"
_DEFAULT_OLLAMA_V1 = "http://127.0.0.1:11434/v1"
_DEFAULT_AUX_MODEL = "qwen3.5:4b"


@dataclass(frozen=True)
class GuiConfig:
    """GUI path settings — separate from Hermes's own ~/.hermes/config.yaml."""

    hermes_home: Path
    agent_profiles_dir: Path


def _expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(str(path))).resolve()


def load_gui_config(
    hermes_home: str | Path | None = None,
    agent_profiles_dir: str | Path | None = None,
) -> GuiConfig:
    """Build config from CLI flags; defaults match a standard Hermes install."""
    home = _expand(hermes_home) if hermes_home is not None else _DEFAULT_HERMES
    profiles_dir = _expand(agent_profiles_dir) if agent_profiles_dir is not None else home / "profiles"
    return GuiConfig(hermes_home=home, agent_profiles_dir=profiles_dir)


def resolve_model_base_url(cfg: dict) -> str:
    """Base URL for the configured model, supporting both Hermes config layouts.

    Legacy configs put the endpoint at ``model.base_url``; newer ones name a
    provider (``model.provider: vllm-local``) whose endpoint lives in the
    ``providers`` map (``providers.vllm-local.api``). Missing both → "".
    """
    m = cfg.get("model") or {}
    base_url = m.get("base_url") or ""
    if base_url:
        return base_url
    provider = str(m.get("provider") or "").strip()
    entry = (cfg.get("providers") or {}).get(provider)
    if isinstance(entry, dict):
        return entry.get("api") or entry.get("base_url") or ""
    return ""


def hermes_model_config(hermes_home: Path) -> tuple[str, str]:
    """Read the model endpoint and default model from Hermes config.yaml."""
    base_url = _DEFAULT_OLLAMA_V1
    model = _DEFAULT_AUX_MODEL
    cfg_path = hermes_home / "config.yaml"
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            m = cfg.get("model") or {}
            base_url = resolve_model_base_url(cfg) or base_url
            model = m.get("default") or model
        except Exception:
            pass
    return base_url, model
