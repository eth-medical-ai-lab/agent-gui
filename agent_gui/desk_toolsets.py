"""Desk-toggleable Hermes toolsets for the GUI Tools menu and lean/default profiles."""

from __future__ import annotations

from pathlib import Path

# Hermes "hermes-cli" toolsets the GUI can toggle per desk.
# `lean` marks the default fast set; the rest are heavier and off by default.
# Each entry's `name` is the UI toggle; `sets` is the underlying Hermes toolset
# name(s) it controls (defaults to [name]). Vision groups image + video analysis.
DESK_TOOLSETS: list[dict] = [
    {
        "name": "file",
        "label": "Files",
        "lean": True,
        "tools": ["read_file", "write_file", "search_files", "patch"],
    },
    {
        "name": "terminal",
        "label": "Terminal",
        "lean": True,
        "tools": ["terminal", "process"],
    },
    {
        "name": "code_execution",
        "label": "Code execution",
        "lean": True,
        "tools": ["execute_code"],
    },
    {
        "name": "vision",
        "label": "Vision",
        "lean": True,
        "sets": ["vision", "video"],
        "tools": ["vision_analyze", "video_analyze"],
    },
    # Hermes renamed this toolset "search" → "web"; send both so either version works.
    {
        "name": "search",
        "label": "Search",
        "lean": True,
        "sets": ["web", "search"],
        "tools": ["web_search", "web_extract"],
    },
    {"name": "todo", "label": "Todo", "lean": True, "tools": ["todo"]},
    {
        "name": "skills",
        "label": "Skills",
        "lean": True,
        "tools": ["skills_list", "skill_view", "skill_manage"],
    },
    {"name": "memory", "label": "Memory", "lean": True, "tools": ["memory"]},
    {"name": "clarify", "label": "Clarify", "lean": True, "tools": ["clarify"]},
    {
        "name": "browser",
        "label": "Browser",
        "lean": False,
        "tools": [
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_scroll",
            "browser_snapshot",
            "browser_back",
            "browser_press",
            "browser_console",
            "browser_get_images",
            "browser_vision",
        ],
    },
    {
        "name": "delegation",
        "label": "Delegation",
        "lean": True,
        "tools": ["delegate_task"],
    },
    {
        "name": "tts",
        "label": "Text-to-speech",
        "lean": False,
        "tools": ["text_to_speech"],
    },
]

# Hermes toolsets kept off in lean but not exposed as GUI toggles.
_LEAN_EXTRA_DISABLED = ("cronjob",)


def toolset_sets(entry: dict) -> list[str]:
    return entry.get("sets", [entry["name"]])


_UI_NAMES = frozenset(t["name"] for t in DESK_TOOLSETS)


def lean_enabled_names() -> list[str]:
    return [t["name"] for t in DESK_TOOLSETS if t["lean"]]


def hermes_sets_for_ui_names(ui_names: list[str]) -> list[str]:
    """Map GUI toggle names to underlying Hermes toolset names."""
    out: set[str] = set()
    by_name = {t["name"]: t for t in DESK_TOOLSETS}
    for name in ui_names:
        entry = by_name.get(name)
        if entry:
            out.update(toolset_sets(entry))
    return sorted(out)


def lean_hermes_toolsets() -> list[str]:
    return hermes_sets_for_ui_names(lean_enabled_names())


def ui_names_from_legacy_disabled(disabled_hermes: list[str]) -> list[str]:
    """Best-effort upgrade for desks that saved a disabled-toolset marker."""
    disabled = set(disabled_hermes)
    return [t["name"] for t in DESK_TOOLSETS
            if not set(toolset_sets(t)) & disabled]


def parse_tools_marker(raw: str) -> list[str] | None:
    """Parse `.hermes_tools` — enabled UI names (new) or None for legacy disabled."""
    names = [t.strip() for t in raw.split(",") if t.strip()]
    if not names:
        return []
    if set(names) <= _UI_NAMES:
        return names
    return None


def disabled_from_enabled(enabled: list[str]) -> list[str]:
    """Turn an ENABLED toolset list (UI names) into Hermes toolset names to disable."""
    keep = set(enabled)
    disabled: set[str] = set()
    for entry in DESK_TOOLSETS:
        if entry["name"] not in keep:
            disabled.update(toolset_sets(entry))
    return sorted(disabled)


def lean_disabled_toolsets() -> list[str]:
    """Legacy disabled list for lean — prefer :func:`lean_hermes_toolsets` allowlist."""
    return sorted(set(disabled_from_enabled(lean_enabled_names())) | set(_LEAN_EXTRA_DISABLED))


def profile_config_sources(
    profile_dir: Path | None,
    hermes_home: Path,
    *,
    agent_profile: bool = False,
) -> tuple[Path, Path]:
    """Paths to symlink as a desk's ``config.yaml`` / ``.env``.

    Agent-profile desks use the resolved Hermes profile home (``~/.hermes/profiles/<id>/``)
    for both config and secrets. Portable repo copies under ``./profiles`` omit
    ``.env`` on purpose — never fall back to the global ``~/.hermes/.env`` for
    agent desks or one profile's keys would leak into another.
    """
    config_src = hermes_home / "config.yaml"
    env_src = hermes_home / ".env"
    if profile_dir and profile_dir.is_dir():
        pc = profile_dir / "config.yaml"
        if pc.is_file():
            config_src = pc
        # Agent desks: only this profile's .env (may be absent — do not use global).
        env_src = profile_dir / ".env" if agent_profile else env_src
        if not agent_profile:
            pe = profile_dir / ".env"
            if pe.is_file():
                env_src = pe
    return config_src, env_src
