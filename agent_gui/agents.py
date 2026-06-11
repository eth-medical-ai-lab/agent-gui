"""Hermes agent profiles — roster + discovery under ~/.hermes/profiles."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent_gui.gui_config import resolve_model_base_url

# Profiles hidden from the agent bench / pickers.
EXCLUDED_PROFILE_IDS: frozenset[str] = frozenset({"jedi", "padawan", "coder-agent", "coder_agent"})

# Built-in clone sources for ``hermes profile create --clone --clone-from …``.
PROFILE_PROTOTYPES: tuple[str, ...] = (
    "coder",
    "researcher",
)

# Canonical hosted-API profiles (shown under the API roster category).
API_PROFILE_IDS: tuple[str, ...] = (
    "cloud",
)

# Canonical local Ollama profiles (shown under the Local roster category).
LOCAL_PROFILE_IDS: tuple[str, ...] = (
    "local-ollama",
)

_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class AgentMeta:
    id: str
    name: str
    tagline: str
    color: str


ROSTER: tuple[AgentMeta, ...] = (
    AgentMeta("coder", "Coder", "Implements features and fixes bugs", "#4a8eff"),
    AgentMeta("researcher", "Researcher", "Reads docs and explores ideas", "#e67e22"),
    AgentMeta("cloud", "Google", "Gemini and other Google API models", "#a78bfa"),
    AgentMeta("local-ollama", "Ollama", "Local models via Ollama (:11434)", "#58a6ff"),
)
_ROSTER_BY_ID = {a.id: a for a in ROSTER}


def _read_model_info(profile_dir: Path) -> tuple[str, str]:
    cfg_path = profile_dir / "config.yaml"
    if not cfg_path.is_file():
        return "", ""
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        m = cfg.get("model") or {}
        return str(m.get("default") or ""), str(resolve_model_base_url(cfg) or "")
    except Exception:
        return "", ""


def _color_for_id(profile_id: str) -> str:
    digest = hashlib.sha256(profile_id.encode()).hexdigest()
    hue = int(digest[:6], 16) % 360
    return f"hsl({hue}, 62%, 58%)"


def _read_gui_meta(profile_dir: Path) -> dict:
    path = profile_dir / ".gui-meta.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_gui_meta(profile_dir: Path, meta: dict) -> None:
    path = profile_dir / ".gui-meta.yaml"
    path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")


def profile_dir(profiles_root: Path, profile_id: str) -> Path:
    if not profile_id or profile_id.startswith(".") or "/" in profile_id or "\\" in profile_id:
        raise ValueError(f"invalid agent profile id: {profile_id!r}")
    return profiles_root / profile_id


def _profile_id_aliases(profile_id: str) -> list[str]:
    """Disk names for a GUI profile id (Hermes may use underscores)."""
    ids = [profile_id]
    alt = profile_id.replace("-", "_")
    if alt not in ids:
        ids.append(alt)
    return ids


def resolve_agent_profile_dir(
    profiles_root: Path,
    hermes_home: Path,
    profile_id: str,
) -> Path:
    """Return the profile directory workers should use for config + secrets.

    Prefer the canonical Hermes profile home (``~/.hermes/profiles/<id>/``) so
    ``config.yaml``, ``.env``, and tool backends match ``hermes chat -p``.
    Fall back to the portable copy under ``profiles_root`` (repo ./profiles).
    """
    if not profile_id or profile_id.startswith(".") or "/" in profile_id or "\\" in profile_id:
        raise ValueError(f"invalid agent profile id: {profile_id!r}")

    candidates: list[Path] = []
    for pid in _profile_id_aliases(profile_id):
        candidates.append(hermes_home / "profiles" / pid)
    candidates.append(profiles_root / profile_id)

    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (path / "config.yaml").is_file():
            return path

    raise FileNotFoundError(
        f"agent profile {profile_id!r} not found under {hermes_home / 'profiles'} "
        f"or {profiles_root}"
    )


def validate_new_profile_id(profile_id: str) -> str:
    """Normalize and validate a new profile id (Hermes naming rules)."""
    pid = (profile_id or "").strip().lower()
    if not pid or pid == "default" or pid in EXCLUDED_PROFILE_IDS:
        raise ValueError(f"invalid profile id: {profile_id!r}")
    if not _PROFILE_ID_RE.match(pid):
        raise ValueError(
            "profile id must be lowercase alphanumeric with hyphens/underscores "
            f"(got {profile_id!r})"
        )
    return pid


def list_prototypes(profiles_root: Path, hermes_home: Path) -> list[dict]:
    """Return clone-source prototypes (coder, researcher) that are installed."""
    agents = {a["id"]: a for a in list_agents(profiles_root, hermes_home)}
    out: list[dict] = []
    for pid in PROFILE_PROTOTYPES:
        row = agents.get(pid)
        if row:
            out.append({**row, "is_prototype": True})
    return out


def _discover_profile_dirs(profiles_root: Path, hermes_home: Path) -> dict[str, Path]:
    """Map profile id → directory for every installed profile on disk."""
    found: dict[str, Path] = {}

    def _scan(root: Path) -> None:
        if not root.is_dir():
            return
        for p in root.iterdir():
            if not p.is_dir() or p.name.startswith("."):
                continue
            if p.name in EXCLUDED_PROFILE_IDS:
                continue
            if (p / "config.yaml").is_file() and p.name not in found:
                found[p.name] = p

    _scan(profiles_root)
    hermes_profiles = hermes_home / "profiles"
    if hermes_profiles.resolve() != profiles_root.resolve():
        _scan(hermes_profiles)
    return found


def list_agents(profiles_root: Path, hermes_home: Path | None = None) -> list[dict]:
    """Return agent profiles for the bench and desk pickers."""
    home = hermes_home or (Path.home() / ".hermes")
    on_disk = _discover_profile_dirs(profiles_root, home)

    # Prefer manifest order when present (portable profiles dir).
    ordered_ids: list[str] = []
    manifest_path = profiles_root / "manifest.yaml"
    if manifest_path.is_file():
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            for row in data.get("profiles") or []:
                if isinstance(row, dict) and row.get("id"):
                    pid = str(row["id"])
                    if pid not in EXCLUDED_PROFILE_IDS and pid in on_disk:
                        ordered_ids.append(pid)
        except Exception:
            pass

    # Prototypes first (stable order), then manifest extras, then the rest.
    ids: list[str] = []
    for pid in PROFILE_PROTOTYPES:
        if pid in on_disk and pid not in ids:
            ids.append(pid)
    for pid in API_PROFILE_IDS:
        if pid in on_disk and pid not in ids:
            ids.append(pid)
    for pid in LOCAL_PROFILE_IDS:
        if pid in on_disk and pid not in ids:
            ids.append(pid)
    for pid in ordered_ids:
        if pid not in ids:
            ids.append(pid)
    for pid in sorted(on_disk):
        if pid not in ids:
            ids.append(pid)

    out: list[dict] = []
    for pid in ids:
        pdir = on_disk[pid]
        meta = _ROSTER_BY_ID.get(pid)
        gui = _read_gui_meta(pdir)
        model, base_url = _read_model_info(pdir)
        name = str(gui.get("name") or (meta.name if meta else pid.replace("-", " ").replace("_", " ").title()))
        tagline = str(gui.get("tagline") or (meta.tagline if meta else ""))
        color = str(gui.get("color") or (meta.color if meta else _color_for_id(pid)))
        clone_from = gui.get("clone_from")
        if clone_from is None and pid in API_PROFILE_IDS:
            clone_from = "api"
        if clone_from is None and pid in LOCAL_PROFILE_IDS:
            clone_from = "local"
        out.append({
            "id": pid,
            "name": name,
            "tagline": tagline,
            "color": color,
            "available": True,
            "model": model,
            "base_url": base_url,
            "profile_path": str(pdir.resolve()),
            "is_prototype": pid in PROFILE_PROTOTYPES,
            "clone_from": clone_from,
        })
    return out


def read_persona(profile_dir: Path) -> dict[str, str]:
    soul_path = profile_dir / "SOUL.md"
    memory_path = profile_dir / "memories" / "MEMORY.md"
    soul = ""
    memory = ""
    try:
        if soul_path.is_file():
            soul = soul_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    try:
        if memory_path.is_file():
            memory = memory_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return {"soul": soul, "memory": memory}


def write_persona(profile_dir: Path, *, soul: str | None = None, memory: str | None = None) -> None:
    if soul is not None:
        (profile_dir / "SOUL.md").write_text(soul, encoding="utf-8")
    if memory is not None:
        mem_dir = profile_dir / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "MEMORY.md").write_text(memory, encoding="utf-8")


def create_profile_via_hermes(
    hermes_home: Path,
    profile_id: str,
    clone_from: str,
    *,
    hermes_bin: str | None = None,
) -> Path:
    """Run ``hermes profile create <id> --clone --clone-from <source>``.

    The source may be any installed profile (``~/.hermes/profiles/<source>``) or a
    built-in prototype — not just the prototypes.
    """
    pid = validate_new_profile_id(profile_id)
    if clone_from in EXCLUDED_PROFILE_IDS:
        raise ValueError(f"cannot clone from {clone_from!r}")
    if clone_from not in PROFILE_PROTOTYPES and not (hermes_home / "profiles" / clone_from).is_dir():
        raise ValueError(f"unknown clone source: {clone_from!r}")

    dest = hermes_home / "profiles" / pid
    if dest.exists():
        raise FileExistsError(f"profile {pid!r} already exists")

    bin_path = hermes_bin or "hermes"
    cmd = [bin_path, "profile", "create", pid, "--clone", "--clone-from", clone_from]
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}
    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or "hermes profile create failed"
        raise RuntimeError(err)
    if not dest.is_dir():
        raise RuntimeError(f"profile create succeeded but {dest} is missing")
    return dest


def write_model_config(
    profile_dir: Path,
    model: str,
    *,
    base_url: str | None = None,
    provider: str | None = None,
) -> None:
    """Update the ``model:`` block in a profile's config.yaml.

    Always sets ``model.default``. When ``base_url``/``provider`` are given (e.g.
    switching to another entry from the ``providers:`` block) they're written too,
    so the profile points at the chosen backend.
    """
    cfg_path = profile_dir / "config.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"missing config.yaml in {profile_dir}")
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise OSError(f"failed to read {cfg_path}: {exc}") from exc
    if not isinstance(cfg, dict):
        cfg = {}
    block = cfg.get("model")
    if not isinstance(block, dict):
        block = {}
        cfg["model"] = block
    block["default"] = (model or "").strip()
    if base_url is not None:
        block["base_url"] = base_url.strip()
    if provider is not None:
        block["provider"] = provider.strip()
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def write_model_default(profile_dir: Path, model: str) -> None:
    """Backwards-compatible shim — set only ``model.default``."""
    write_model_config(profile_dir, model)


def delete_profile(
    profiles_root: Path,
    hermes_home: Path,
    profile_id: str,
    *,
    hermes_bin: str | None = None,
) -> None:
    """Remove an agent profile via ``hermes profile delete -y``."""
    pid = (profile_id or "").strip().lower()
    bin_path = hermes_bin or "hermes"
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}
    proc = subprocess.run(
        [bin_path, "profile", "delete", pid, "-y"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode == 0:
        return
    err = (proc.stderr or proc.stdout or "").strip()
    if not err:
        err = f"hermes profile delete {pid!r} failed (exit {proc.returncode})"
    raise RuntimeError(err)
