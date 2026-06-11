"""Per-profile tool presets + skills for agent bench preview."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_gui.agents import _read_gui_meta
from agent_gui.desk_toolsets import DESK_TOOLSETS, lean_enabled_names, toolset_sets


def _read_profile_disabled_toolsets(profile_dir: Path) -> list[str]:
    cfg_path = profile_dir / "config.yaml"
    if not cfg_path.is_file():
        return []
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        agent_cfg = cfg.get("agent") or {}
        raw = agent_cfg.get("disabled_toolsets") or []
        return [str(x) for x in raw if x]
    except Exception:
        return []


def _filter_ui_names(enabled: list[str], disabled_hermes: set[str]) -> list[str]:
    out: list[str] = []
    for entry in DESK_TOOLSETS:
        name = entry["name"]
        if name not in enabled:
            continue
        if set(toolset_sets(entry)) & disabled_hermes:
            continue
        out.append(name)
    return out


def global_tool_presets() -> dict[str, list[str]]:
    allt = [t["name"] for t in DESK_TOOLSETS]
    lean = lean_enabled_names()
    return {"chat": [], "lean": lean, "full": allt}


def profile_tool_presets(profile_dir: Path) -> dict[str, object]:
    """Chat / lean / full enabled UI toolsets for a profile."""
    gui = _read_gui_meta(profile_dir)
    global_presets = global_tool_presets()
    custom = gui.get("tool_presets")
    if isinstance(custom, dict):
        presets = {
            "chat": custom.get("chat", global_presets["chat"]),
            "lean": custom.get("lean", global_presets["lean"]),
            "full": custom.get("full", global_presets["full"]),
        }
        source = "profile"
    else:
        presets = dict(global_presets)
        source = "global"

    normalized: dict[str, list[str]] = {}
    for key in ("chat", "lean", "full"):
        val = presets.get(key, global_presets[key])
        if val is None:
            val = global_presets[key]
        normalized[key] = [str(x) for x in val] if isinstance(val, list) else []

    disabled_hermes = set(_read_profile_disabled_toolsets(profile_dir))
    if disabled_hermes:
        normalized = {
            k: _filter_ui_names(v, disabled_hermes) for k, v in normalized.items()
        }

    default_preset = str(gui.get("default_tool_preset") or "lean")
    if default_preset not in normalized:
        default_preset = "lean"

    return {
        "presets": normalized,
        "source": source,
        "default_preset": default_preset,
        "profile_disabled_toolsets": sorted(disabled_hermes),
    }


def list_profile_skills(profile_dir: Path) -> list[dict]:
    skills_root = profile_dir / "skills"
    if not skills_root.is_dir():
        return []
    bundles: list[dict] = []
    for bundle_path in sorted(skills_root.iterdir()):
        if not bundle_path.is_dir() or bundle_path.name.startswith("."):
            continue
        skills = sorted(
            d.name for d in bundle_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        bundles.append({
            "bundle": bundle_path.name,
            "count": len(skills),
            "skills": skills[:24],
        })
    return bundles


def agent_capabilities(profile_dir: Path) -> dict:
    tool_info = profile_tool_presets(profile_dir)
    bundles = list_profile_skills(profile_dir)
    total_skills = sum(b["count"] for b in bundles)
    return {
        **tool_info,
        "skill_bundles": bundles,
        "skill_count": total_skills,
    }
