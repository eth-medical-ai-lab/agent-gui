"""Tests for agent_gui.read_guard — the host-side read/search confinement.

`path_within()` is the pure, security-critical predicate and is tested directly.
`install()` reaches into Hermes' `tools.file_tools`, which isn't importable in the
test env; we stand up a *stub* module in `sys.modules` that mimics the two call
sites (`get_read_block_error`, `search_tool`) and assert the wrapping behaves —
denylist preserved, escapes blocked, in-workspace access allowed, and Hermes-shape
errors returned. This covers the wiring contract without a live Hermes install.
"""
import json
import sys
import types

import pytest

from agent_gui import read_guard


# ── path_within (pure predicate) ─────────────────────────────────────────────


def test_path_within_self_and_descendant(tmp_path):
    assert read_guard.path_within(str(tmp_path), str(tmp_path)) is True
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert read_guard.path_within(str(sub), str(tmp_path)) is True


def test_path_within_rejects_sibling_and_parent(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    sibling = tmp_path / "other"
    sibling.mkdir()
    assert read_guard.path_within(str(sibling), str(root)) is False
    assert read_guard.path_within(str(tmp_path), str(root)) is False


def test_path_within_rejects_dotdot_traversal(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    assert read_guard.path_within(str(root / ".." / "secret.txt"), str(root)) is False


def test_path_within_follows_symlink_escape(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = root / "alias.txt"
    link.symlink_to(outside)
    # The link lives inside root, but resolves outside → not within.
    assert read_guard.path_within(str(link), str(root)) is False


def test_path_within_allows_symlink_staying_inside(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("ok")
    link = root / "alias.txt"
    link.symlink_to(real)
    assert read_guard.path_within(str(link), str(root)) is True


# ── install() against a stub Hermes file_tools module ────────────────────────


@pytest.fixture()
def stub_file_tools():
    """Inject a fake `tools.file_tools` exposing the two patched call sites."""
    tools_pkg = types.ModuleType("tools")
    ft = types.ModuleType("tools.file_tools")

    # Hermes' real denylist: returns an error string for blocked paths, else None.
    ft.get_read_block_error = lambda path: (
        "blocked: credential store" if str(path).endswith("auth.json") else None
    )
    # Hermes' real search: returns a JSON string; here it just echoes the path so
    # tests can tell a delegated call from a blocked one.
    ft.search_tool = lambda pattern, target="content", path=".", **kw: json.dumps(
        {"ran": True, "path": path}
    )

    tools_pkg.file_tools = ft
    sys.modules["tools"] = tools_pkg
    sys.modules["tools.file_tools"] = ft
    try:
        yield ft
    finally:
        sys.modules.pop("tools.file_tools", None)
        sys.modules.pop("tools", None)


def test_install_read_hook_preserves_denylist(tmp_path, stub_file_tools):
    read_guard.install(str(tmp_path))
    # Hermes' own denylist still wins.
    assert stub_file_tools.get_read_block_error(str(tmp_path / "auth.json")) == (
        "blocked: credential store"
    )


def test_install_read_hook_blocks_outside_workspace(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    ws.mkdir()
    read_guard.install(str(ws))
    # A path outside the workspace is now denied even though Hermes' denylist is None.
    msg = stub_file_tools.get_read_block_error(str(tmp_path / "elsewhere.txt"))
    assert msg is not None
    assert "outside the agent's workspace" in msg


def test_install_read_hook_allows_inside_workspace(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    ws.mkdir()
    inside = ws / "notes.txt"
    inside.write_text("hi")
    read_guard.install(str(ws))
    assert stub_file_tools.get_read_block_error(str(inside)) is None


def test_install_search_hook_blocks_absolute_escape(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    ws.mkdir()
    read_guard.install(str(ws))
    out = json.loads(stub_file_tools.search_tool("pat", path=str(tmp_path / "etc")))
    assert "error" in out
    assert "outside the agent's workspace" in out["error"]


def test_install_search_hook_blocks_dotdot_escape(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    ws.mkdir()
    read_guard.install(str(ws))
    out = json.loads(stub_file_tools.search_tool("pat", path="../.."))
    assert "error" in out


def test_install_search_hook_allows_workspace_relative(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    ws.mkdir()
    read_guard.install(str(ws))
    out = json.loads(stub_file_tools.search_tool("pat", path="."))
    assert out.get("ran") is True


def test_install_is_noop_when_hermes_absent(tmp_path):
    """No `tools.file_tools` in sys.modules → install must not raise."""
    sys.modules.pop("tools.file_tools", None)
    sys.modules.pop("tools", None)
    # Should swallow the ImportError and simply not patch anything.
    read_guard.install(str(tmp_path))


# ── extra_roots / team-repo sharing (Option A harmonization) ─────────────────


def test_path_within_any_matches_secondary_root(tmp_path):
    ws = tmp_path / "ws"
    repo = tmp_path / "repo"
    ws.mkdir()
    repo.mkdir()
    inside_repo = repo / "shared.txt"
    inside_repo.write_text("x")
    roots = [str(ws), str(repo)]
    assert read_guard.path_within_any(str(inside_repo), roots) is True
    assert read_guard.path_within_any(str(tmp_path / "elsewhere.txt"), roots) is False
    assert read_guard.path_within_any(str(inside_repo), []) is False  # fail-closed


def test_install_read_hook_allows_extra_root(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    repo = tmp_path / "gui_team_repos" / "team-a"
    ws.mkdir()
    repo.mkdir(parents=True)
    shared = repo / "data.csv"
    shared.write_text("a,b")
    read_guard.install(str(ws), extra_roots=[str(repo)])
    # Team-repo read is allowed…
    assert stub_file_tools.get_read_block_error(str(shared)) is None
    # …but an unrelated path is still denied.
    assert stub_file_tools.get_read_block_error(str(tmp_path / "secret.txt")) is not None


def test_install_read_hook_extra_root_message_mentions_team_repo(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    repo = tmp_path / "repo"
    ws.mkdir()
    repo.mkdir()
    read_guard.install(str(ws), extra_roots=[str(repo)])
    msg = stub_file_tools.get_read_block_error(str(tmp_path / "nope.txt"))
    assert "outside the agent's workspace" in msg
    assert "team repo" in msg


def test_install_search_hook_allows_extra_root_absolute(tmp_path, stub_file_tools):
    ws = tmp_path / "ws"
    repo = tmp_path / "repo"
    ws.mkdir()
    repo.mkdir()
    read_guard.install(str(ws), extra_roots=[str(repo)])
    out = json.loads(stub_file_tools.search_tool("pat", path=str(repo)))
    assert out.get("ran") is True
    # A sibling outside both roots is still blocked.
    out2 = json.loads(stub_file_tools.search_tool("pat", path=str(tmp_path / "other")))
    assert "error" in out2


def test_install_extra_root_does_not_widen_default(tmp_path, stub_file_tools):
    """With no extra_roots, behaviour is unchanged (single-root confinement)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    read_guard.install(str(ws))
    msg = stub_file_tools.get_read_block_error(str(tmp_path / "x.txt"))
    assert "team repo" not in msg  # message stays single-root
