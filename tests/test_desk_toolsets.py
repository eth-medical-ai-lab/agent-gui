"""Tests for GUI desk toolset profiles."""

import os
import sys

from pathlib import Path

import pytest

from agent_gui.desk_toolsets import (
    DESK_TOOLSETS,
    disabled_from_enabled,
    hermes_sets_for_ui_names,
    lean_disabled_toolsets,
    lean_enabled_names,
    lean_hermes_toolsets,
    parse_tools_marker,
    profile_config_sources,
    ui_names_from_legacy_disabled,
)


def test_lean_includes_search_not_browser():
    lean = lean_enabled_names()
    assert "search" in lean
    assert "browser" not in lean


def test_lean_hermes_sets_include_search():
    assert "search" in lean_hermes_toolsets()


def test_lean_disabled_drops_browser_keeps_search():
    disabled = set(lean_disabled_toolsets())
    assert "browser" in disabled
    assert "search" not in disabled
    assert "tts" in disabled
    assert "cronjob" in disabled


def test_browser_toggle_no_longer_lists_web_search():
    browser = next(t for t in DESK_TOOLSETS if t["name"] == "browser")
    search = next(t for t in DESK_TOOLSETS if t["name"] == "search")
    assert "web_search" not in browser["tools"]
    assert "web_search" in search["tools"]


def test_parse_tools_marker_new_format():
    assert parse_tools_marker("file,search,browser") == ["file", "search", "browser"]


def test_parse_tools_marker_legacy_disabled():
    assert parse_tools_marker("browser,cronjob,tts") is None


def test_legacy_disabled_upgrades_to_enabled_ui_names():
    enabled = ui_names_from_legacy_disabled(["browser", "cronjob", "tts"])
    assert "search" in enabled
    assert "browser" not in enabled
    assert "tts" not in enabled


def test_profile_config_sources_prefer_profile_yaml(tmp_path: Path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("web: {backend: ''}\n")
    (hermes_home / ".env").write_text("GLOBAL=1\n")
    profile = tmp_path / "profiles" / "coder-agent"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("web: {backend: brave-free}\n")
    cfg, env = profile_config_sources(profile, hermes_home, agent_profile=True)
    assert cfg == profile / "config.yaml"
    assert env == profile / ".env"


def test_agent_profile_env_never_falls_back_to_global(tmp_path: Path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("GLOBAL=1\n")
    profile = tmp_path / "profiles" / "researcher"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("web: {backend: brave-free}\n")
    _cfg, env = profile_config_sources(profile, hermes_home, agent_profile=True)
    assert env == profile / ".env"
    assert not env.exists()


def test_allowlist_keeps_web_search_when_browser_off(tmp_path, monkeypatch):
    agent_root = os.path.expanduser("~/.hermes/hermes-agent")
    if not os.path.isdir(agent_root):
        pytest.skip("hermes-agent is not installed at ~/.hermes/hermes-agent")
    sys.path.insert(0, agent_root)
    # Isolated HERMES_HOME: the developer's real config may pin web.backend to a
    # provider whose API key isn't set, which requirement-gates the whole web
    # toolset off and would fail this test for config reasons, not code reasons.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    from model_tools import get_tool_definitions

    enabled = hermes_sets_for_ui_names(lean_enabled_names())
    tools = get_tool_definitions(
        enabled_toolsets=enabled,
        disabled_toolsets=["session_search"],
        quiet_mode=True,
    )
    names = {t["function"]["name"] for t in tools}
    assert "web_search" in names

    # Blocklist mode still strips web_search (legacy Hermes behaviour).
    blocked = get_tool_definitions(
        disabled_toolsets=lean_disabled_toolsets() + ["session_search"],
        quiet_mode=True,
    )
    blocked_names = {t["function"]["name"] for t in blocked}
    assert "web_search" not in blocked_names
