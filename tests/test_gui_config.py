"""Tests for agent_gui.gui_config."""
from pathlib import Path

from agent_gui.gui_config import hermes_model_config, load_gui_config, resolve_model_base_url


def test_defaults_without_overrides():
    cfg = load_gui_config()
    assert cfg.hermes_home == Path.home() / ".hermes"
    assert cfg.agent_profiles_dir == Path.home() / ".hermes" / "profiles"


def test_hermes_home_override(tmp_path: Path):
    cfg = load_gui_config(hermes_home=tmp_path / "h")
    assert cfg.hermes_home == (tmp_path / "h").resolve()
    assert cfg.agent_profiles_dir == (tmp_path / "h" / "profiles").resolve()


def test_profiles_dir_override(tmp_path: Path):
    profiles = tmp_path / "portable-profiles"
    cfg = load_gui_config(hermes_home=tmp_path / "h", agent_profiles_dir=profiles)
    assert cfg.agent_profiles_dir == profiles.resolve()


def test_resolve_model_base_url_legacy_layout():
    cfg = {"model": {"default": "m", "base_url": "http://127.0.0.1:8010/v1"}}
    assert resolve_model_base_url(cfg) == "http://127.0.0.1:8010/v1"


def test_resolve_model_base_url_provider_map():
    cfg = {
        "model": {"default": "Qwen/Qwen3.6-27B", "provider": "vllm-local"},
        "providers": {"vllm-local": {"api": "http://127.0.0.1:8010/v1"}},
    }
    assert resolve_model_base_url(cfg) == "http://127.0.0.1:8010/v1"


def test_resolve_model_base_url_explicit_wins_over_provider():
    cfg = {
        "model": {"base_url": "http://a/v1", "provider": "p"},
        "providers": {"p": {"api": "http://b/v1"}},
    }
    assert resolve_model_base_url(cfg) == "http://a/v1"


def test_resolve_model_base_url_missing():
    assert resolve_model_base_url({}) == ""
    assert resolve_model_base_url({"model": {"provider": "ghost"}}) == ""


def test_hermes_model_config_provider_map(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "model:\n"
        "  default: Qwen/Qwen3.6-27B\n"
        "  provider: vllm-local\n"
        "providers:\n"
        "  vllm-local:\n"
        "    api: http://127.0.0.1:8010/v1\n",
        encoding="utf-8",
    )
    base_url, model = hermes_model_config(tmp_path)
    assert base_url == "http://127.0.0.1:8010/v1"
    assert model == "Qwen/Qwen3.6-27B"
