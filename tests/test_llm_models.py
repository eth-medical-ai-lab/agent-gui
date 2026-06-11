"""Tests for agent_gui.llm_models."""
import json
from pathlib import Path

import pytest
import yaml

from agent_gui.llm_models import (
    fetch_llm_models,
    is_gemini_backend,
    models_from_hermes_cache,
    read_profile_provider,
    read_profile_providers,
)


def test_is_gemini_backend():
    assert is_gemini_backend("https://generativelanguage.googleapis.com/v1beta")
    assert is_gemini_backend("http://localhost:8010/v1", provider="gemini")
    assert not is_gemini_backend("http://127.0.0.1:11434/v1")


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
