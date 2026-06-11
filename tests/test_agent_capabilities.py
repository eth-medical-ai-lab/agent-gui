"""Tests for per-profile tool + skill preview."""

from pathlib import Path

import yaml

from agent_gui.agent_capabilities import (
    agent_capabilities,
    global_tool_presets,
    profile_tool_presets,
)


def test_global_presets_match_desk_toolsets():
    presets = global_tool_presets()
    assert presets["chat"] == []
    assert "search" in presets["lean"]
    assert "browser" not in presets["lean"]
    assert "browser" in presets["full"]


def test_profile_tool_presets_from_gui_meta(tmp_path: Path):
    pdir = tmp_path / "coder"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(yaml.safe_dump({"agent": {"disabled_toolsets": []}}))
    (pdir / ".gui-meta.yaml").write_text(yaml.safe_dump({
        "default_tool_preset": "lean",
        "tool_presets": {
            "chat": [],
            "lean": ["file", "terminal", "search"],
            "full": ["file", "terminal", "search", "browser"],
        },
    }))
    info = profile_tool_presets(pdir)
    assert info["source"] == "profile"
    assert info["default_preset"] == "lean"
    assert info["presets"]["lean"] == ["file", "terminal", "search"]


def test_profile_disabled_toolsets_filter(tmp_path: Path):
    pdir = tmp_path / "researcher"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(yaml.safe_dump({
        "agent": {"disabled_toolsets": ["browser"]},
    }))
    info = profile_tool_presets(pdir)
    assert "browser" not in info["presets"]["full"]


def test_list_profile_skills(tmp_path: Path):
    pdir = tmp_path / "bio"
    bundle = pdir / "skills" / "data-science"
    (bundle / "jupyter-live-kernel").mkdir(parents=True)
    (bundle / "plan").mkdir(parents=True)
    caps = agent_capabilities(pdir)
    assert caps["skill_count"] == 2
    assert caps["skill_bundles"][0]["bundle"] == "data-science"
    assert "jupyter-live-kernel" in caps["skill_bundles"][0]["skills"]
