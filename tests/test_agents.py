"""Tests for agent_gui.agents."""
from pathlib import Path
from unittest import mock

import pytest
import yaml

from agent_gui.agents import (
    EXCLUDED_PROFILE_IDS,
    create_profile_via_hermes,
    delete_profile,
    list_agents,
    list_prototypes,
    profile_dir,
    read_persona,
    resolve_agent_profile_dir,
    validate_new_profile_id,
    write_model_default,
    write_persona,
)


def test_list_agents_reads_manifest_order(tmp_path: Path):
    root = tmp_path / "profiles"
    hermes = tmp_path / "hermes"
    for name in ("coder", "jedi", "researcher"):
        (root / name).mkdir(parents=True)
        (root / name / "config.yaml").write_text(
            yaml.safe_dump({"model": {"default": f"model-{name}", "base_url": "http://localhost:8010/v1"}})
        )
    (root / "manifest.yaml").write_text(yaml.safe_dump({
        "profiles": [{"id": "researcher"}, {"id": "coder"}],
    }))
    agents = list_agents(root, hermes)
    ids = [a["id"] for a in agents]
    assert "jedi" not in ids
    assert ids.index("coder") < ids.index("researcher") or ids == ["coder", "researcher"]
    assert agents[0]["is_prototype"] is True


def test_list_agents_cloud_defaults_to_api_category(tmp_path: Path):
    root = tmp_path / "profiles"
    hermes = tmp_path / "hermes"
    cloud = hermes / "profiles" / "cloud"
    cloud.mkdir(parents=True)
    (cloud / "config.yaml").write_text(
        yaml.safe_dump({
            "model": {
                "default": "gemini-3.1-flash-lite",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
            },
        })
    )
    agents = list_agents(root, hermes)
    row = next(a for a in agents if a["id"] == "cloud")
    assert row["name"] == "Google"
    assert row["clone_from"] == "api"
    assert row["color"] == "#a78bfa"


def test_list_agents_local_ollama_defaults(tmp_path: Path):
    root = tmp_path / "profiles"
    hermes = tmp_path / "hermes"
    local = hermes / "profiles" / "local-ollama"
    local.mkdir(parents=True)
    (local / "config.yaml").write_text(
        yaml.safe_dump({
            "model": {"default": "qwen3.5:4b", "base_url": "http://127.0.0.1:11434/v1"},
        })
    )
    agents = list_agents(root, hermes)
    row = next(a for a in agents if a["id"] == "local-ollama")
    assert row["name"] == "Ollama"
    assert row["clone_from"] == "local"


def test_list_agents_merges_hermes_profiles(tmp_path: Path):
    root = tmp_path / "profiles"
    hermes = tmp_path / "hermes"
    (root / "coder").mkdir(parents=True)
    (root / "coder" / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "m"}})
    )
    custom = hermes / "profiles" / "my-agent"
    custom.mkdir(parents=True)
    (custom / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "custom"}}))
    (custom / ".gui-meta.yaml").write_text("name: My Agent\nclone_from: coder\n")
    agents = list_agents(root, hermes)
    assert "my-agent" in [a["id"] for a in agents]
    row = next(a for a in agents if a["id"] == "my-agent")
    assert row["name"] == "My Agent"
    assert row["clone_from"] == "coder"


def test_list_prototypes_returns_installed_prototypes(tmp_path: Path):
    root = tmp_path / "profiles"
    hermes = tmp_path / "hermes"
    for name in ("coder", "researcher", "other"):
        (root / name).mkdir(parents=True)
        (root / name / "config.yaml").write_text("model: {}\n")
    protos = list_prototypes(root, hermes)
    assert [p["id"] for p in protos] == ["coder", "researcher"]


def test_excluded_profiles_constant():
    assert "jedi" in EXCLUDED_PROFILE_IDS
    assert "padawan" in EXCLUDED_PROFILE_IDS


def test_profile_dir_rejects_invalid():
    with pytest.raises(ValueError):
        profile_dir(Path("/tmp"), "../escape")


def test_validate_new_profile_id():
    assert validate_new_profile_id("My-Coder") == "my-coder"
    with pytest.raises(ValueError):
        validate_new_profile_id("jedi")


def test_read_write_persona(tmp_path: Path):
    pdir = tmp_path / "p"
    pdir.mkdir()
    write_persona(pdir, soul="be kind", memory="note one")
    data = read_persona(pdir)
    assert data["soul"] == "be kind"
    assert data["memory"] == "note one"


def test_resolve_agent_profile_dir_prefers_hermes_home(tmp_path: Path):
    hermes = tmp_path / "hermes"
    portable = tmp_path / "portable"
    (hermes / "profiles" / "researcher").mkdir(parents=True)
    (portable / "researcher").mkdir(parents=True)
    (hermes / "profiles" / "researcher" / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "from-hermes"}})
    )
    (hermes / "profiles" / "researcher" / ".env").write_text("BRAVE_SEARCH_API_KEY=test\n")
    (portable / "researcher" / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "from-portable"}})
    )
    resolved = resolve_agent_profile_dir(portable, hermes, "researcher")
    assert resolved == hermes / "profiles" / "researcher"


def test_resolve_agent_profile_dir_alias_underscore(tmp_path: Path):
    hermes = tmp_path / "hermes"
    portable = tmp_path / "portable"
    (hermes / "profiles" / "coder_agent").mkdir(parents=True)
    (hermes / "profiles" / "coder_agent" / "config.yaml").write_text("model: {}\n")
    resolved = resolve_agent_profile_dir(portable, hermes, "coder-agent")
    assert resolved == hermes / "profiles" / "coder_agent"


def test_create_profile_via_hermes_subprocess(tmp_path: Path):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    dest = hermes / "profiles" / "newbie"
    with mock.patch("agent_gui.agents.subprocess.run") as run:
        def _side_effect(*args, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            return mock.Mock(returncode=0, stdout="", stderr="")
        run.side_effect = _side_effect
        got = create_profile_via_hermes(hermes, "newbie", "coder", hermes_bin="/bin/hermes")
    assert got == dest
    run.assert_called_once()
    args = run.call_args[0][0]
    assert args[:6] == ["/bin/hermes", "profile", "create", "newbie", "--clone", "--clone-from"]
    assert args[6] == "coder"


def test_delete_profile_removes_clone(tmp_path: Path):
    hermes = tmp_path / "hermes"
    portable = tmp_path / "profiles"
    with mock.patch("agent_gui.agents.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        delete_profile(portable, hermes, "my-clone", hermes_bin="/bin/hermes")
    run.assert_called_once()
    args = run.call_args[0][0]
    assert args == ["/bin/hermes", "profile", "delete", "my-clone", "-y"]


def test_delete_profile_raises_on_hermes_failure(tmp_path: Path):
    hermes = tmp_path / "hermes"
    portable = tmp_path / "profiles"
    with mock.patch("agent_gui.agents.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="profile not found")
        with pytest.raises(RuntimeError, match="profile not found"):
            delete_profile(portable, hermes, "missing", hermes_bin="/bin/hermes")


def test_write_model_default_updates_config(tmp_path: Path):
    pdir = tmp_path / "coder"
    pdir.mkdir()
    cfg_path = pdir / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "model": {"default": "old-model", "base_url": "http://localhost:8010/v1"},
    }))
    write_model_default(pdir, "new-model")
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["model"]["default"] == "new-model"
    assert cfg["model"]["base_url"] == "http://localhost:8010/v1"


def test_delete_profile_removes_prototype(tmp_path: Path):
    hermes = tmp_path / "hermes"
    portable = tmp_path / "profiles"
    with mock.patch("agent_gui.agents.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        delete_profile(portable, hermes, "coder", hermes_bin="/bin/hermes")
    args = run.call_args[0][0]
    assert args == ["/bin/hermes", "profile", "delete", "coder", "-y"]
