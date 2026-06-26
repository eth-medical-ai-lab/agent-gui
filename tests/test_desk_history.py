"""Desk session-lineage history.

A GUI desk is one sandbox dir, named by its root/anchor session id. Each resume
or model-switch starts a NEW Hermes session row inside that desk's private
state.db, parented to the root — so a desk accumulates a lineage of session ids.
These tests pin that behavior at the db layer and through the /history + /export
endpoints.
"""
import io
import json
import os
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import agent_gui.server as server
from agent_gui.db import HermesDB
from agent_gui.server import _profile_at, create_app


@pytest.fixture(autouse=True)
def _clear_workspace_cache():
    """The server caches session_id → workspace path in a module global; clear it
    so tests that reuse the same desk id don't read a prior test's workspace."""
    server._session_workspaces.clear()
    yield
    server._session_workspaces.clear()


def _make_desk_db(home: Path, root_sid: str, rows: "list[tuple]") -> Path:
    """Create gui_sandboxes/<root_sid>/state.db with the given session rows.

    Each row: (id, started_at, model, parent_session_id, message_count).
    """
    sandbox = home / "gui_sandboxes" / root_sid
    sandbox.mkdir(parents=True, exist_ok=True)
    db_path = sandbox / "state.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, started_at REAL, ended_at REAL, source TEXT,
                model TEXT, parent_session_id TEXT, title TEXT, message_count INTEGER,
                input_tokens INTEGER, output_tokens INTEGER
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
                content TEXT, tool_calls TEXT, tool_call_id TEXT, tool_name TEXT,
                timestamp REAL
            );
            """
        )
        for (sid, started, model, parent, mc) in rows:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, NULL, 'workbench', ?, ?, 't', ?, 0, 0)",
                (sid, started, model, parent, mc),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _sandbox_ws(
    home: Path, root_sid: str, profile: str = "", model: str = "",
    profile_history: "list[dict] | None" = None,
    run_history: "list[dict] | None" = None,
) -> Path:
    ws = home / "gui_sandboxes" / root_sid / "docker" / "default" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "TASK.md").write_text("# Task\n\nWrite a haiku\n", encoding="utf-8")
    if profile:
        (ws / ".hermes_profile").write_text(profile, encoding="utf-8")
    if model:
        (ws / ".hermes_model").write_text(model, encoding="utf-8")
    if profile_history is not None:
        (ws / ".hermes_profile_history.json").write_text(json.dumps(profile_history), encoding="utf-8")
    if run_history is not None:
        (ws / ".hermes_run_history.json").write_text(json.dumps(run_history), encoding="utf-8")
    return ws


ROOT = "20260605_102905_701b67"
# Root session, then two resumes (the second switched models) — all parented to
# the root, all stored inside the one desk db.
ROWS = [
    (ROOT, 1000.0, "modelA", None, 4),
    ("20260605_122914_070e1e", 2000.0, "modelA", ROOT, 2),
    ("20260605_122916_fc99ce", 3000.0, "modelB", ROOT, 6),
]


def test_history_lists_every_session_oldest_first(tmp_path):
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)

    hist = HermesDB(home).get_desk_session_history(ROOT)

    assert [h.id for h in hist] == [r[0] for r in ROWS]
    # First row is the root; the rest are resumes parented to it.
    assert hist[0].is_root and hist[0].parent_session_id is None
    assert all(not h.is_root for h in hist[1:])
    assert all(h.parent_session_id == ROOT for h in hist[1:])
    # A model switch is visible as a different model on a later session.
    assert [h.model for h in hist] == ["modelA", "modelA", "modelB"]
    assert [h.message_count for h in hist] == [4, 2, 6]


def test_history_orders_by_started_at_not_insertion(tmp_path):
    home = tmp_path / "hermes"
    # Insert out of chronological order; history must come back oldest-first.
    rows = [
        ("child-late", 3000.0, "m", "root-x", 1),
        ("root-x", 1000.0, "m", None, 1),
        ("child-mid", 2000.0, "m", "root-x", 1),
    ]
    _make_desk_db(home, "root-x", rows)

    hist = HermesDB(home).get_desk_session_history("root-x")
    assert [h.id for h in hist] == ["root-x", "child-mid", "child-late"]
    assert hist[0].is_root


def test_history_empty_for_unknown_desk(tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    assert HermesDB(home).get_desk_session_history("nope") == []


def test_history_endpoint_returns_lineage_and_profile(tmp_path):
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)
    _sandbox_ws(home, ROOT, profile="coder", model="modelB")

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    resp = client.get(f"/api/sessions/{ROOT}/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["desk_id"] == ROOT
    assert data["profile"] == "coder"
    assert [s["id"] for s in data["sessions"]] == [r[0] for r in ROWS]
    assert data["sessions"][0]["is_root"] is True
    assert data["sessions"][-1]["model"] == "modelB"


def test_history_maps_per_session_profile_from_change_log(tmp_path):
    """A desk that switched profile mid-life shows the right profile per row.

    ROWS start at epochs 1000/2000/3000. The log switches profile at epoch 2500,
    so the first two rows are 'coderA' and the last is 'coderB'.
    """
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)
    _sandbox_ws(home, ROOT, profile="coderB", profile_history=[
        {"at": 500.0, "profile": "coderA"},
        {"at": 2500.0, "profile": "coderB"},
    ])

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    data = client.get(f"/api/sessions/{ROOT}/history").json()
    assert [s["profile"] for s in data["sessions"]] == ["coderA", "coderA", "coderB"]
    # Every resume is its own row — none are collapsed/skipped.
    assert len(data["sessions"]) == len(ROWS)


def test_history_uses_run_log_every_resume_is_its_own_entry(tmp_path):
    """When a run-log exists it is authoritative: every run shows as an entry,
    even consecutive resumes with the same profile + model (none collapsed). This
    matters because resumes reuse the desk's session id (one db row only)."""
    home = tmp_path / "hermes"
    # Only ONE db session row — the db can't tell us about the resumes.
    _make_desk_db(home, ROOT, [(ROOT, 1000.0, "modelA", None, 9)])
    run_log = [
        {"at": 1000.0, "kind": "start",  "profile": "coder",      "model": "modelA", "session_id": ROOT},
        {"at": 2000.0, "kind": "resume", "profile": "coder",      "model": "modelA", "session_id": ROOT},
        {"at": 3000.0, "kind": "resume", "profile": "coder",      "model": "modelA", "session_id": ROOT},
        {"at": 4000.0, "kind": "resume", "profile": "researcher", "model": "modelB", "session_id": ROOT},
    ]
    _sandbox_ws(home, ROOT, profile="researcher", run_history=run_log)

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    data = client.get(f"/api/sessions/{ROOT}/history").json()
    sessions = data["sessions"]
    assert len(sessions) == 4               # every run, none skipped
    assert sessions[0]["is_root"] is True
    assert all(not s["is_root"] for s in sessions[1:])
    assert [s["profile"] for s in sessions] == ["coder", "coder", "coder", "researcher"]
    assert [s["model"] for s in sessions] == ["modelA", "modelA", "modelA", "modelB"]


def test_profile_at_picks_most_recent_change_at_or_before_start():
    plog = [(500.0, "a"), (2500.0, "b")]
    # Row before any change → earliest known profile.
    assert _profile_at(plog, "1970-01-01T00:00:01+00:00", "fallback") == "a"
    # Between the two changes.
    assert _profile_at(plog, "1970-01-01T00:16:40+00:00", "fallback") == "a"   # epoch 1000
    # At/after the second change.
    assert _profile_at(plog, "1970-01-01T00:41:40+00:00", "fallback") == "b"   # epoch 2500
    # No log → fallback (current marker).
    assert _profile_at([], "1970-01-01T00:16:40+00:00", "fallback") == "fallback"


def test_export_endpoint_bundles_config_task_and_history(tmp_path):
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)
    _sandbox_ws(home, ROOT, profile="coder", model="modelB")

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    resp = client.get(f"/api/sessions/{ROOT}/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "agent-gui-desk/v1"
    assert data["desk_id"] == ROOT
    assert data["profile"] == "coder"
    assert data["model"] == "modelB"
    assert "Write a haiku" in data["task"]
    # The export carries the full session lineage, oldest-first.
    assert [s["id"] for s in data["sessions"]] == [r[0] for r in ROWS]


# ── Full-desk archive save / load ───────────────────────────────────────────────

def test_archive_and_import_roundtrip(tmp_path):
    """Save a whole desk to a .tar.gz, delete it, then load it back intact."""
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)
    ws = _sandbox_ws(home, ROOT, profile="coder", model="modelB", run_history=[
        {"at": 1000.0, "kind": "start", "profile": "coder", "model": "modelB", "session_id": ROOT},
    ])
    (ws / ".hermes_team_id").write_text("team-x", encoding="utf-8")

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    # Save → a gzip archive.
    r = client.get(f"/api/sessions/{ROOT}/archive")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/gzip")
    archive = r.content
    assert len(archive) > 0
    # Sanity: the archive carries a manifest + the sandbox tree.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = tar.getnames()
    assert "desk_manifest.json" in names
    assert any(n.startswith("sandbox/") and n.endswith("state.db") for n in names)

    # Delete the desk from disk.
    shutil.rmtree(home / "gui_sandboxes" / ROOT)
    assert not (home / "gui_sandboxes" / ROOT).exists()

    # Load it back.
    r2 = client.post(
        "/api/sessions/import",
        files={"file": ("desk.tar.gz", archive, "application/gzip")},
    )
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["session_id"] == ROOT
    assert data["team_id"] == "team-x"

    # Everything is restored: state.db, workspace, markers, and history works again.
    assert (home / "gui_sandboxes" / ROOT / "state.db").exists()
    assert (home / "gui_sandboxes" / ROOT / "docker" / "default" / "workspace" / "TASK.md").exists()
    hist = client.get(f"/api/sessions/{ROOT}/history").json()
    assert len(hist["sessions"]) >= 1
    assert hist["profile"] == "coder"

    # Loading again while it already exists is refused (no silent overwrite).
    r3 = client.post(
        "/api/sessions/import",
        files={"file": ("desk.tar.gz", archive, "application/gzip")},
    )
    assert r3.status_code == 409


def test_list_and_import_from_saved_dir(tmp_path, monkeypatch):
    """Load desk defaults to repo saved/ — list archives and import by filename."""
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)
    ws = _sandbox_ws(home, ROOT, profile="coder", model="modelB", run_history=[
        {"at": 1000.0, "kind": "start", "profile": "coder", "model": "modelB", "session_id": ROOT},
    ])
    (ws / ".hermes_team_id").write_text("team-x", encoding="utf-8")

    repo = tmp_path / "repo"
    saved = repo / "saved"
    saved.mkdir(parents=True)
    monkeypatch.setattr(server, "_REPO_ROOT", repo)

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    archive = client.get(f"/api/sessions/{ROOT}/archive").content
    (saved / f"desk-{ROOT}.tar.gz").write_bytes(archive)

    listed = client.get("/api/sessions/saved")
    assert listed.status_code == 200
    data = listed.json()
    assert data["archives"][0]["filename"] == f"desk-{ROOT}.tar.gz"

    shutil.rmtree(home / "gui_sandboxes" / ROOT)

    loaded = client.post("/api/sessions/import-saved", json={"filename": f"desk-{ROOT}.tar.gz"})
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["session_id"] == ROOT
    assert (home / "gui_sandboxes" / ROOT / "state.db").exists()


def test_archive_404_for_unknown_desk(tmp_path):
    home = tmp_path / "hermes"
    (home / "gui_sandboxes").mkdir(parents=True)
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))
    assert client.get("/api/sessions/nope/archive").status_code == 404


def test_import_rejects_archive_without_manifest(tmp_path):
    home = tmp_path / "hermes"
    (home / "gui_sandboxes").mkdir(parents=True)
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"hi"
        info = tarfile.TarInfo("sandbox/state.db")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    r = client.post(
        "/api/sessions/import",
        files={"file": ("bad.tar.gz", buf.read(), "application/gzip")},
    )
    assert r.status_code == 400


# ── Team files travel with the desk archive ──────────────────────────────────────

def _link_team_files(home: Path, ws: Path, team_id: str) -> Path:
    """Mirror _prepare_team_files_mount: workspace/team_files → relative symlink
    to gui_team_repos/<team_id>/. Returns the repo dir."""
    repo = home / "gui_team_repos" / team_id
    repo.mkdir(parents=True, exist_ok=True)
    dest = ws / "team_files"
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(os.path.relpath(repo.resolve(), ws.resolve()))
    return repo


def test_archive_bundles_team_files_and_import_keeps_them_in_workspace(tmp_path):
    """Saving a desk follows its team_files symlink and bundles the real files;
    loading leaves them as a plain workspace/team_files/ folder the desk owns
    (detached from the shared team repo) so a resumed desk sees them directly."""
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)
    ws = _sandbox_ws(home, ROOT, profile="coder", model="modelB")
    (ws / ".hermes_team_id").write_text("team-x", encoding="utf-8")
    repo = _link_team_files(home, ws, "team-x")
    (repo / "shared.csv").write_text("x=1\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.txt").write_text("hi\n", encoding="utf-8")

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))

    archive = client.get(f"/api/sessions/{ROOT}/archive").content
    tf = "sandbox/docker/default/workspace/team_files"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        # The team files ride along as REAL files (symlink dereferenced), and the
        # symlink itself is not stored — team_files is a directory in the archive.
        assert f"{tf}/shared.csv" in tar.getnames()
        assert f"{tf}/docs/readme.txt" in tar.getnames()
        assert tar.getmember(tf).isdir()
        assert tar.getmember(f"{tf}/shared.csv").isfile()

    # Wipe both the desk and the shared repo, then load the archive back.
    shutil.rmtree(home / "gui_sandboxes" / ROOT)
    shutil.rmtree(home / "gui_team_repos" / "team-x")

    r = client.post(
        "/api/sessions/import",
        files={"file": ("desk.tar.gz", archive, "application/gzip")},
    )
    assert r.status_code == 200, r.text
    # Loaded detached: no team association (so the empty-repo bind-mount can't
    # shadow the files), and no shared repo recreated.
    assert r.json()["team_id"] is None

    loaded_ws = home / "gui_sandboxes" / ROOT / "docker" / "default" / "workspace"
    mount = loaded_ws / "team_files"
    assert mount.is_dir() and not mount.is_symlink()       # plain folder, owned by the desk
    assert (mount / "shared.csv").read_text(encoding="utf-8") == "x=1\n"
    assert (mount / "docs" / "readme.txt").read_text(encoding="utf-8") == "hi\n"
    assert not (loaded_ws / ".hermes_team_id").exists()    # detached from team
    assert not (home / "gui_team_repos" / "team-x").exists()  # no shared repo touched


def test_archive_without_team_files_keeps_team_membership(tmp_path):
    """A desk whose team has no files (no team_files content) still archives and
    imports as a normal team desk — only bundled files trigger detachment."""
    home = tmp_path / "hermes"
    _make_desk_db(home, ROOT, ROWS)
    ws = _sandbox_ws(home, ROOT, profile="coder")
    (ws / ".hermes_team_id").write_text("team-x", encoding="utf-8")

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    client = TestClient(create_app(hermes_home=str(home), workspace_root=str(ws_root)))
    archive = client.get(f"/api/sessions/{ROOT}/archive").content
    shutil.rmtree(home / "gui_sandboxes" / ROOT)

    r = client.post(
        "/api/sessions/import",
        files={"file": ("desk.tar.gz", archive, "application/gzip")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["team_id"] == "team-x"
    loaded_ws = home / "gui_sandboxes" / ROOT / "docker" / "default" / "workspace"
    assert (loaded_ws / ".hermes_team_id").read_text(encoding="utf-8") == "team-x"
