"""Team File Repo write path — HERMES_WRITE_SAFE_ROOT vs symlink target."""

import json
import os
from pathlib import Path

import pytest


def test_team_repo_write_patch_allows_symlink_target(tmp_path: Path, monkeypatch):
    """Writes through workspace/team_files/ must not be blocked by safe-root realpath."""
    workspace = tmp_path / "workspace"
    repo = tmp_path / "gui_team_repos" / "team-a"
    workspace.mkdir()
    repo.mkdir(parents=True)
    (repo / "seed.txt").write_text("seed", encoding="utf-8")
    link = workspace / "team_files"
    link.symlink_to(repo, target_is_directory=True)

    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(workspace))
    monkeypatch.setenv("HERMES_GUI_TEAM_REPO", str(repo))

    from agent_gui.hermes_worker import _patch_team_repo_writes

    _patch_team_repo_writes()

    from tools.file_operations import _is_write_denied

    target = link / "output.txt"
    assert _is_write_denied(str(target)) is False

    out = repo / "output.txt"
    out.write_text("ok", encoding="utf-8")
    assert out.read_text(encoding="utf-8") == "ok"


def test_team_repo_write_patch_still_blocks_outside(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    repo = tmp_path / "gui_team_repos" / "team-a"
    outside = tmp_path / "escape.txt"
    workspace.mkdir()
    repo.mkdir(parents=True)

    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(workspace))
    monkeypatch.setenv("HERMES_GUI_TEAM_REPO", str(repo))

    from agent_gui.hermes_worker import _patch_team_repo_writes

    _patch_team_repo_writes()

    from tools.file_operations import _is_write_denied

    assert _is_write_denied(str(outside)) is True


def test_host_path_to_docker_maps_workspace_and_team_repo(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    repo = tmp_path / "gui_team_repos" / "team-a"
    workspace.mkdir()
    repo.mkdir(parents=True)
    (repo / "data.npz").write_bytes(b"x")

    monkeypatch.setenv("HERMES_WORKDIR", str(workspace))
    monkeypatch.setenv("HERMES_GUI_TEAM_REPO", str(repo))
    monkeypatch.setenv("HERMES_GUI_DOCKER_WORKSPACE", "/workspace")

    from agent_gui.hermes_worker import _host_path_to_docker

    assert _host_path_to_docker(str(workspace / "TASK.md")) == "/workspace/TASK.md"
    assert _host_path_to_docker(str(repo / "data.npz")) == "/workspace/team_files/data.npz"
    assert _host_path_to_docker("/workspace/team_files/data.npz") == "/workspace/team_files/data.npz"


def test_patch_gui_docker_config_survives_profile_clobber(tmp_path, monkeypatch):
    """GUI volume mounts must win over profile config.yaml docker_volumes: []."""
    # Real dirs: the cwd→/workspace mapping only applies when the host cwd exists.
    ws = str(tmp_path / "desk-ws")
    repo = str(tmp_path / "team-repo")
    os.makedirs(ws)
    os.makedirs(repo)
    vols = json.dumps([f"{ws}:/workspace", f"{repo}:/workspace/team_files"])
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", vols)
    monkeypatch.setenv("TERMINAL_CWD", ws)

    from agent_gui import hermes_worker as hw

    hw._capture_gui_docker_env()
    # Simulate cli.load_cli_config() overwriting GUI env from profile config.
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", "[]")
    monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "false")

    hw._restore_gui_docker_env()
    assert os.environ["TERMINAL_DOCKER_VOLUMES"] == vols
    assert os.environ["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"] == "true"

    try:
        import tools.terminal_tool as tt  # noqa: PLC0415
    except ImportError:
        pytest.skip("hermes terminal_tool not available")

    hw._patch_gui_docker_config()
    cfg = tt._get_env_config()
    assert cfg["docker_volumes"] == json.loads(vols)
    assert cfg["docker_persist_across_processes"] is True
    assert cfg["env_type"] == "docker"
    assert cfg["cwd"] == "/workspace"


def test_reset_docker_skipped_without_force_flag(monkeypatch):
    """Container must survive worker restart unless mounts changed.

    The reset now force-removes only THIS desk's labeled container via
    ``docker rm -f`` (per-desk ``hermes-task-id`` label), so it is safe across
    concurrent desks and works on a cold worker start.
    """
    import shutil
    import subprocess

    from agent_gui import hermes_worker as hw

    monkeypatch.setenv("HERMES_GUI_TEAM_REPO", "/tmp/team-repo")
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", '["/tmp/ws:/workspace"]')
    monkeypatch.setenv("TERMINAL_SANDBOX_DIR", "/tmp/gui_sandboxes/20260101_000000_abcdef")
    monkeypatch.delenv("HERMES_GUI_FORCE_DOCKER_RESET", raising=False)

    hw._capture_gui_docker_env()
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")

    runs: list[list[str]] = []

    class _Result:
        stdout = "container123\n"
        returncode = 0

    def _fake_run(cmd, *_a, **_k):
        runs.append(cmd)
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    # No force flag → no docker calls at all.
    hw._reset_docker_for_team_repo()
    assert runs == []

    # Force flag → list this desk's container, then rm -f it.
    monkeypatch.setenv("HERMES_GUI_FORCE_DOCKER_RESET", "1")
    hw._reset_docker_for_team_repo()
    assert any("ps" in c for c in runs)
    rm = next(c for c in runs if "rm" in c)
    assert "-f" in rm and "container123" in rm
    # Targeted at THIS desk's label, not the shared "default" key.
    ps = next(c for c in runs if "ps" in c)
    assert "label=hermes-task-id=20260101_000000_abcdef" in ps
