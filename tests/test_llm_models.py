"""Tests for agent_gui.llm_models."""
import json
from pathlib import Path

import pytest
import yaml

from agent_gui.llm_models import (
    _ANTHROPIC_API_MODELS,
    fetch_llm_models,
    is_anthropic_backend,
    is_gemini_backend,
    models_from_hermes_cache,
    read_profile_provider,
    read_profile_providers,
)


def test_is_gemini_backend():
    assert is_gemini_backend("https://generativelanguage.googleapis.com/v1beta")
    assert is_gemini_backend("http://localhost:8010/v1", provider="gemini")
    assert not is_gemini_backend("http://127.0.0.1:11434/v1")


def test_is_anthropic_backend():
    assert is_anthropic_backend("https://api.anthropic.com")
    assert is_anthropic_backend(provider="anthropic")
    assert is_anthropic_backend(provider="claude")
    assert not is_anthropic_backend("http://127.0.0.1:11434/v1")
    assert not is_anthropic_backend(provider="gemini")


def test_models_from_hermes_cache(tmp_path: Path):
    pdir = tmp_path / "cloud"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(
        yaml.safe_dump({"model": {"provider": "gemini", "default": "gemini-2.5-flash"}})
    )
    cache = {
        "google": {
            "models": {
                "gemini-2.5-flash": {"id": "gemini-2.5-flash"},
                "gemini-2.5-pro": {"id": "gemini-2.5-pro"},
            },
        },
    }
    (pdir / "models_dev_cache.json").write_text(json.dumps(cache))
    assert models_from_hermes_cache(pdir, "gemini") == [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]


def test_read_profile_provider(tmp_path: Path):
    pdir = tmp_path / "p"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(yaml.safe_dump({"model": {"provider": "gemini"}}))
    assert read_profile_provider(pdir) == "gemini"


def test_read_profile_providers(tmp_path: Path):
    pdir = tmp_path / "p"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(yaml.safe_dump({
        "model": {"provider": "vllm-local", "default": "Qwen/Qwen3.6-27B",
                  "base_url": "http://127.0.0.1:8010/v1"},
        "providers": {
            "vllm-local": {"api": "http://127.0.0.1:8010/v1",
                           "default_model": "Qwen/Qwen3.6-27B",
                           "models": ["Qwen/Qwen3.6-27B"], "name": "vLLM"},
            "ollama-launch": {"api": "http://127.0.0.1:11434/v1",
                              "default_model": "qwen3.5:4b",
                              "models": ["qwen3.5:4b"], "name": "Ollama"},
            "no-api": {"default_model": "x"},
        },
    }))
    providers = read_profile_providers(pdir)
    # Providers without an `api` are skipped; the active provider sorts first.
    assert [p["id"] for p in providers] == ["vllm-local", "ollama-launch"]
    ollama = providers[1]
    assert ollama["base_url"] == "http://127.0.0.1:11434/v1"
    assert ollama["name"] == "Ollama"
    assert ollama["models"] == ["qwen3.5:4b"]
    assert ollama["default_model"] == "qwen3.5:4b"


def test_read_profile_providers_no_block(tmp_path: Path):
    pdir = tmp_path / "p"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "x"}}))
    assert read_profile_providers(pdir) == []


@pytest.mark.asyncio
async def test_fetch_llm_models_gemini_uses_cache(tmp_path: Path):
    pdir = tmp_path / "cloud"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(
        yaml.safe_dump({
            "model": {
                "provider": "gemini",
                "default": "gemini-3.1-flash-lite",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
            },
        })
    )
    (pdir / "models_dev_cache.json").write_text(json.dumps({
        "google": {"models": {"gemini-3.1-flash-lite": {}, "gemini-2.5-pro": {}}},
    }))
    models = await fetch_llm_models(
        "https://generativelanguage.googleapis.com/v1beta",
        profile_dir=pdir,
        provider="gemini",
    )
    assert models == ["gemini-2.5-pro", "gemini-3.1-flash-lite"]


@pytest.mark.asyncio
async def test_fetch_llm_models_anthropic_curated():
    # No /v1/models on api.anthropic.com → serve the curated Claude list (no probe).
    models = await fetch_llm_models(
        "https://api.anthropic.com", provider="anthropic",
    )
    assert models == list(_ANTHROPIC_API_MODELS)
    assert "claude-sonnet-4-6" in models
    assert "claude-haiku-4-5" in models


@pytest.mark.asyncio
async def test_fetch_llm_models_anthropic_uses_cache(tmp_path: Path):
    # If Hermes populated a models cache, prefer it over the curated fallback.
    pdir = tmp_path / "claude"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(
        yaml.safe_dump({
            "model": {"provider": "anthropic", "default": "claude-opus-4-8",
                      "base_url": "https://api.anthropic.com"},
        })
    )
    (pdir / "models_dev_cache.json").write_text(json.dumps({
        "anthropic": {"models": {"claude-opus-4-8": {}, "claude-sonnet-4-6": {}}},
    }))
    models = await fetch_llm_models(
        "https://api.anthropic.com", profile_dir=pdir, provider="anthropic",
    )
    assert models == ["claude-opus-4-8", "claude-sonnet-4-6"]
