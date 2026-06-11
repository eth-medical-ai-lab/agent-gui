"""Integration tests for agent_gui.server FastAPI endpoints.

Subprocess spawning is mocked so tests don't need hermes installed.
The server's HermesDB reads from a temp SQLite-less directory (returns [] gracefully).
"""
import asyncio
import json
import os
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from agent_gui import server as srv
from agent_gui.server import create_app


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_home(tmp_path: Path):
    """Minimal hermes_home with no state.db (HermesDB returns [] gracefully)."""
    return tmp_path


@pytest.fixture()
def app(tmp_home: Path, tmp_path: Path):
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    return create_app(hermes_home=str(tmp_home), workspace_root=str(ws_root))


@pytest_asyncio.fixture()
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _make_fake_proc(lines: list[str], returncode: int = 0):
    """Return a mock asyncio subprocess that streams the given newline-delimited JSON lines."""

    async def _readline_gen():
        for line in lines:
            yield (line + "\n").encode()

    proc = mock.AsyncMock()
    proc.stdout = _readline_gen()
    proc.returncode = returncode
    # wait() completes immediately
    proc.wait = mock.AsyncMock(return_value=returncode)
    proc.terminate = mock.MagicMock()
    proc.kill = mock.MagicMock()
    return proc


# ── /api/sessions ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions_empty(client: AsyncClient):
    r = await client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_hermes_status_keys(client: AsyncClient):
    r = await client.get("/api/hermes/status")
    assert r.status_code == 200
    body = r.json()
    assert "available" in body
    assert "hermes" in body
    assert "ollama" in body


# ── /api/sessions/new ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_session_returns_session_id(client: AsyncClient, tmp_home: Path):
    session_id = "20240101_120000_abc123"
    fake_proc = _make_fake_proc([
        json.dumps({"type": "session_id", "id": session_id}),
        json.dumps({"type": "log", "msg": "hello"}),
        json.dumps({"type": "done"}),
    ])

    with mock.patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        r = await client.post("/api/sessions/new", json={"content": "do something"})

    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    assert body["session"].get("is_running") is True
    assert body["session"].get("title") == "do something"
    # Clean up live queue so other tests don't see stale state
    srv._live_queues.pop(body["session_id"], None)
    srv._running_procs.pop(body["session_id"], None)


@pytest.mark.asyncio
async def test_new_session_missing_content(client: AsyncClient):
    r = await client.post("/api/sessions/new", json={})
    assert r.status_code == 400


def _make_persistent_proc():
    """Fake persistent worker: stdin capture + a stdout that blocks (stays warm)."""
    class _Blocking:
        def __aiter__(self):
            return self
        async def __anext__(self):
            await asyncio.Event().wait()  # never yields → pump stays alive
    proc = mock.AsyncMock()
    proc.returncode = None
    proc.stdout = _Blocking()
    proc.stderr = _Blocking()
    proc.stdin = mock.Mock()
    proc.stdin.write = mock.Mock()
    proc.stdin.drain = mock.AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_persistent_worker_reused_across_turns(client: AsyncClient, monkeypatch):
    """With HERMES_GUI_PERSISTENT_WORKER on, a desk spawns ONE worker and sends
    successive turns over stdin instead of respawning."""
    monkeypatch.setattr(srv, "_PERSISTENT_WORKERS", True)
    spawns = []
    proc = _make_persistent_proc()

    def _spawn(*args, **kwargs):
        spawns.append(args)
        return proc

    with mock.patch("asyncio.create_subprocess_exec", side_effect=_spawn):
        r = await client.post("/api/sessions/new", json={"content": "task one"})
        sid = r.json()["session_id"]
        # turn ended in the real flow via turn_done; simulate idle so resume is allowed
        srv._running_procs.pop(sid, None)
        await client.post(f"/api/sessions/{sid}/resume", json={"content": "task two"})

    try:
        # Exactly one process spawned, launched in --persistent mode.
        assert len(spawns) == 1
        assert "--persistent" in spawns[0]
        # Both turns were delivered as "run" commands over stdin.
        writes = b"".join(c.args[0] for c in proc.stdin.write.call_args_list)
        assert b'"cmd": "run"' in writes
        assert b"task one" in writes and b"task two" in writes
    finally:
        srv._persistent_procs.pop(sid, None)
        srv._running_procs.pop(sid, None)
        srv._live_queues.pop(sid, None)
        srv._turn_done_events.pop(sid, None)


def _make_inspect_echo_proc():
    """Fake persistent worker that answers `inspect` commands on stdin by emitting
    a matching `inspect_result` line on stdout (mirrors the real worker)."""
    out_q: asyncio.Queue = asyncio.Queue()

    class _Stdout:
        def __aiter__(self):
            return self
        async def __anext__(self):
            return await out_q.get()

    proc = mock.AsyncMock()
    proc.returncode = None
    proc.stdout = _Stdout()

    class _BlockingErr:
        def __aiter__(self):
            return self
        async def __anext__(self):
            await asyncio.Event().wait()

    proc.stderr = _BlockingErr()
    proc.stdin = mock.Mock()

    def _write(b: bytes):
        cmd = json.loads(b.decode().strip())
        if cmd.get("cmd") == "inspect":
            reply = {"type": "inspect_result", "id": cmd["id"], "ok": True,
                     "tool": cmd["tool"], "result": f"ran {cmd['tool']} {cmd['args']}"}
            out_q.put_nowait((json.dumps(reply) + "\n").encode())

    proc.stdin.write = mock.Mock(side_effect=_write)
    proc.stdin.drain = mock.AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_inspect_runs_tool_via_persistent_worker(client: AsyncClient, tmp_home: Path):
    """POST /inspect routes a whitelisted tool to the desk's worker and returns its
    result, correlated by request id (not leaked into the activity stream)."""
    sid = "20260607_100000_insp01"
    ws = tmp_home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    ws.mkdir(parents=True)
    srv._session_workspaces[sid] = str(ws)
    proc = _make_inspect_echo_proc()

    with mock.patch("asyncio.create_subprocess_exec", return_value=proc):
        r = await client.post(f"/api/sessions/{sid}/inspect",
                              json={"tool": "search_files",
                                    "args": {"pattern": "foo", "path": "/workspace"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["tool"] == "search_files"
    assert "ran search_files" in body["result"]
    assert not srv._inspect_waiters  # waiter cleaned up

    srv._persistent_procs.pop(sid, None)
    srv._live_queues.pop(sid, None)
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_inspect_requires_tool_and_sandbox(client: AsyncClient, tmp_home: Path):
    r = await client.post("/api/sessions/nope/inspect", json={"tool": "search_files"})
    assert r.status_code == 404  # no sandbox for this desk
    sid = "20260607_100000_insp02"
    ws = tmp_home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    ws.mkdir(parents=True)
    srv._session_workspaces[sid] = str(ws)
    r = await client.post(f"/api/sessions/{sid}/inspect", json={})
    assert r.status_code == 400  # tool required
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_new_session_gives_each_desk_a_private_sandbox(client: AsyncClient, tmp_home: Path):
    """Each new desk must get its own TERMINAL_SANDBOX_DIR so Hermes bind-mounts a
    private /workspace (no cross-desk file leakage)."""
    envs = []

    def _capture(*args, **kwargs):
        envs.append(kwargs.get("env", {}))
        return _make_fake_proc([json.dumps({"type": "done"})])

    with mock.patch("asyncio.create_subprocess_exec", side_effect=_capture):
        r1 = await client.post("/api/sessions/new", json={"content": "task one"})
        r2 = await client.post("/api/sessions/new", json={"content": "task two"})

    sid1, sid2 = r1.json()["session_id"], r2.json()["session_id"]
    sb1, sb2 = envs[0].get("TERMINAL_SANDBOX_DIR"), envs[1].get("TERMINAL_SANDBOX_DIR")
    assert sb1 and sb2 and sb1 != sb2                     # distinct private sandboxes
    assert sb1.endswith(f"gui_sandboxes/{sid1}")
    assert sb2.endswith(f"gui_sandboxes/{sid2}")
    # HERMES_WORKDIR is that sandbox's docker workspace (file tools + bash share it)
    assert envs[0]["HERMES_WORKDIR"] == f"{sb1}/docker/default/workspace"

    for sid in (sid1, sid2):
        srv._live_queues.pop(sid, None)
        srv._running_procs.pop(sid, None)
        srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_new_session_passes_task_inline_without_taskmd_read_prompt(client: AsyncClient):
    """The augmented message must contain the task inline and must NOT instruct
    the agent to read TASK.md — that wording caused a spurious read_file tool
    call adding ~4 min latency on every new session."""
    captured: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        return _make_fake_proc([json.dumps({"type": "done"})])

    with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        r = await client.post("/api/sessions/new", json={"content": "tell me a hiking joke"})

    assert r.status_code == 200
    assert captured, "subprocess was never called"

    # augmented_content is the 3rd positional arg: (venv_py, worker_script, content)
    augmented = captured[0][2]
    assert isinstance(augmented, str)

    # Task text must be in the message so the agent can act on it immediately
    assert "tell me a hiking joke" in augmented

    # Must NOT prompt the agent to read TASK.md
    assert "Task instructions are in TASK.md" not in augmented
    assert "read" not in augmented.lower().split("[")[0]  # not in the bracketed preamble

    # Must explicitly tell the agent to proceed without reading files
    assert "immediately" in augmented or "without reading" in augmented

    sid = r.json()["session_id"]
    srv._live_queues.pop(sid, None)
    srv._running_procs.pop(sid, None)


@pytest.mark.asyncio
async def test_new_session_creates_task_md(client: AsyncClient, tmp_home: Path):
    """TASK.md should exist in workspace immediately after the POST returns."""
    fake_proc = _make_fake_proc([json.dumps({"type": "done"})])

    with mock.patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        r = await client.post("/api/sessions/new", json={"content": "my task"})

    assert r.status_code == 200
    sid = r.json()["session_id"]
    ws_path = r.json().get("workspace_path", "")
    if ws_path:
        task_md = Path(ws_path) / "TASK.md"
        assert task_md.exists()
        assert "my task" in task_md.read_text()

    srv._live_queues.pop(sid, None)
    srv._running_procs.pop(sid, None)


# ── /api/sessions/{id}/interrupt ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interrupt_calls_terminate(client: AsyncClient):
    fake_proc = _make_fake_proc([json.dumps({"type": "done"})])
    sid = "test-interrupt-session"
    srv._running_procs[sid] = fake_proc
    queue: asyncio.Queue = asyncio.Queue()
    srv._live_queues[sid] = queue

    r = await client.post(f"/api/sessions/{sid}/interrupt")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    fake_proc.terminate.assert_called_once()
    # Proc should be removed from running map immediately
    assert sid not in srv._running_procs


@pytest.mark.asyncio
async def test_interrupt_unknown_session_is_ok(client: AsyncClient):
    """Interrupting a session that isn't running should not error."""
    r = await client.post("/api/sessions/nonexistent-xyz/interrupt")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── profile concurrency (every desk is isolated, so any profile may run on many) ──


@pytest.mark.asyncio
async def test_multiple_default_desks_can_run_together(client: AsyncClient, tmp_home: Path):
    """Default desks are fully isolated (private HERMES_HOME + per-desk container),
    so several may run at once — no profile is locked to one desk."""
    def _capture(*args, **kwargs):
        return _make_fake_proc([json.dumps({"type": "done"})])

    with mock.patch("asyncio.create_subprocess_exec", side_effect=_capture):
        r1 = await client.post("/api/sessions/new", json={"content": "one"})
        r2 = await client.post("/api/sessions/new", json={"content": "two"})
    try:
        assert r1.status_code == 200 and r2.status_code == 200
        sid1, sid2 = r1.json()["session_id"], r2.json()["session_id"]
        assert sid1 != sid2
    finally:
        for r in (r1, r2):
            sid = r.json().get("session_id")
            if sid:
                srv._live_queues.pop(sid, None)
                srv._running_procs.pop(sid, None)
                srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_resume_allowed_when_same_named_profile_runs_elsewhere(client: AsyncClient, tmp_home: Path, monkeypatch):
    """A profile is no longer locked to one desk: each desk is isolated (private
    HERMES_HOME + per-desk Docker container), so resuming a desk whose profile is
    already running on another desk is allowed."""
    busy_ws = tmp_home / "gui_sandboxes" / "desk-busy" / "ws"
    idle_ws = tmp_home / "gui_sandboxes" / "desk-idle" / "ws"
    busy_ws.mkdir(parents=True)
    idle_ws.mkdir(parents=True)
    (busy_ws / ".hermes_profile").write_text("alpha", encoding="utf-8")
    (idle_ws / ".hermes_profile").write_text("alpha", encoding="utf-8")
    srv._session_workspaces["desk-busy"] = str(busy_ws)
    srv._session_workspaces["desk-idle"] = str(idle_ws)
    srv._running_procs["desk-busy"] = _make_fake_proc([json.dumps({"type": "done"})])

    monkeypatch.setattr("agent_gui.server._PERSISTENT_WORKERS", False, raising=False)
    with mock.patch("asyncio.create_subprocess_exec",
                    return_value=_make_fake_proc([json.dumps({"type": "done"})])):
        try:
            r = await client.post("/api/sessions/desk-idle/resume", json={"content": "Continue."})
            # Same profile runs on desk-busy, but desk-idle still resumes (isolated).
            assert r.status_code != 409
        finally:
            srv._running_procs.pop("desk-busy", None)
            srv._running_procs.pop("desk-idle", None)
            srv._live_queues.pop("desk-idle", None)
            srv._session_workspaces.pop("desk-busy", None)
            srv._session_workspaces.pop("desk-idle", None)


# ── DELETE /api/sessions/{id} ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_session_removes_sandbox_and_db(client: AsyncClient, tmp_home: Path):
    """Deleting a desk session stops workers, purges its private state.db, and removes sandbox."""
    import sqlite3

    fake_proc = _make_fake_proc([json.dumps({"type": "done"})])
    with mock.patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        r = await client.post("/api/sessions/new", json={"content": "temporary task"})
    sid = r.json()["session_id"]
    sandbox = tmp_home / "gui_sandboxes" / sid
    assert sandbox.exists()
    # Worker is mocked, so seed the per-desk state.db the real worker would create.
    desk_db = sandbox / "state.db"
    conn = sqlite3.connect(desk_db)
    conn.execute(
        "CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL, ended_at REAL, "
        "source TEXT, model TEXT, parent_session_id TEXT, title TEXT, "
        "message_count INT, input_tokens INT, output_tokens INT)"
    )
    conn.execute(
        "CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_calls TEXT, tool_call_id TEXT, tool_name TEXT, timestamp REAL)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?)",
        (sid, 1000.0, None, "cli", "m", None, "", 1, 0, 0),
    )
    conn.commit()
    conn.close()

    r = await client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["deleted"] is True
    assert body["sandbox"] is True
    assert not sandbox.exists()
    assert sid not in srv._running_procs
    assert sid not in srv._session_workspaces


@pytest.mark.asyncio
async def test_delete_unknown_session_404(client: AsyncClient):
    r = await client.delete("/api/sessions/does-not-exist-xyz")
    assert r.status_code == 404


# ── /api/sessions/{id}/resume ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_returns_immediately(client: AsyncClient, tmp_home: Path, tmp_path: Path):
    """resume_session should NOT block on the session_id handshake (non-blocking path)."""
    sid = "20240101_120000_resume01"
    ws = tmp_path / "workspace" / "my-task"
    ws.mkdir(parents=True)
    (ws / ".hermes_session_id").write_text(sid)
    srv._session_workspaces[sid] = str(ws)

    fake_proc = _make_fake_proc([
        json.dumps({"type": "log", "msg": "[worker] starting..."}),
        json.dumps({"type": "session_id", "id": sid}),
        json.dumps({"type": "done"}),
    ])

    with mock.patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        r = await client.post(f"/api/sessions/{sid}/resume", json={"content": "continue"})

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["session_id"] == sid
    assert sid in srv._running_procs

    srv._live_queues.pop(sid, None)
    srv._running_procs.pop(sid, None)
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_resume_rejects_already_running(client: AsyncClient):
    sid = "already-running-sid"
    srv._running_procs[sid] = mock.AsyncMock()

    r = await client.post(f"/api/sessions/{sid}/resume", json={"content": "go"})
    assert r.status_code == 409

    srv._running_procs.pop(sid, None)


@pytest.mark.asyncio
async def test_resume_requires_content(client: AsyncClient):
    r = await client.post("/api/sessions/some-sid/resume", json={})
    assert r.status_code == 400


# ── /api/sessions/reassign ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reassign_interrupts_from_and_starts_to(client: AsyncClient, tmp_path: Path):
    """Core invariant: terminate() is called on from proc, new worker started for to."""
    from_sid = "from-session-111"
    to_sid = "to-session-222"

    ws = tmp_path / "workspace" / "to-task"
    ws.mkdir(parents=True)
    (ws / ".hermes_session_id").write_text(to_sid)
    srv._session_workspaces[to_sid] = str(ws)

    from_proc = _make_fake_proc([])
    srv._running_procs[from_sid] = from_proc
    from_queue: asyncio.Queue = asyncio.Queue()
    srv._live_queues[from_sid] = from_queue

    to_proc = _make_fake_proc([json.dumps({"type": "done"})])

    with mock.patch("asyncio.create_subprocess_exec", return_value=to_proc):
        r = await client.post("/api/sessions/reassign", json={
            "from_id": from_sid,
            "to_id": to_sid,
            "message": "Continue.",
        })

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["session_id"] == to_sid

    # from proc must have been terminated
    from_proc.terminate.assert_called_once()
    # from proc must have been awaited (proc.wait() called)
    from_proc.wait.assert_called_once()
    # from proc must be removed from running map
    assert from_sid not in srv._running_procs
    # to session must now be registered
    assert to_sid in srv._running_procs

    srv._live_queues.pop(to_sid, None)
    srv._running_procs.pop(to_sid, None)
    srv._session_workspaces.pop(to_sid, None)


@pytest.mark.asyncio
async def test_reassign_skips_interrupt_when_from_eq_to(client: AsyncClient, tmp_path: Path):
    """Self-reassign (from == to) must not interrupt the session."""
    sid = "self-reassign-sid"

    ws = tmp_path / "workspace" / "self-task"
    ws.mkdir(parents=True)
    (ws / ".hermes_session_id").write_text(sid)
    srv._session_workspaces[sid] = str(ws)

    proc = _make_fake_proc([])
    srv._running_procs[sid] = proc
    srv._live_queues[sid] = asyncio.Queue()

    # Because to_id is already running, reassign should return immediately
    r = await client.post("/api/sessions/reassign", json={
        "from_id": sid,
        "to_id": sid,
        "message": "Continue.",
    })

    assert r.status_code == 200
    # Terminate must NOT have been called (from_id == to_id guard)
    proc.terminate.assert_not_called()
    assert sid in srv._running_procs

    srv._live_queues.pop(sid, None)
    srv._running_procs.pop(sid, None)
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_reassign_nonexistent_from_still_starts_to(client: AsyncClient, tmp_path: Path):
    """A nonexistent from_id should be silently skipped; to_id should still start."""
    to_sid = "to-session-333"
    ws = tmp_path / "workspace" / "to-task2"
    ws.mkdir(parents=True)
    (ws / ".hermes_session_id").write_text(to_sid)
    srv._session_workspaces[to_sid] = str(ws)

    to_proc = _make_fake_proc([json.dumps({"type": "done"})])

    with mock.patch("asyncio.create_subprocess_exec", return_value=to_proc):
        r = await client.post("/api/sessions/reassign", json={
            "from_id": "nonexistent-abc",
            "to_id": to_sid,
            "message": "Continue.",
        })

    assert r.status_code == 200
    assert r.json()["session_id"] == to_sid
    assert to_sid in srv._running_procs

    srv._live_queues.pop(to_sid, None)
    srv._running_procs.pop(to_sid, None)
    srv._session_workspaces.pop(to_sid, None)


@pytest.mark.asyncio
async def test_reassign_to_already_running_is_noop(client: AsyncClient, tmp_path: Path):
    """If to_id is already running, reassign should return ok without double-spawning."""
    from_sid = "from-running"
    to_sid = "to-already-running"

    existing_proc = _make_fake_proc([])
    srv._running_procs[to_sid] = existing_proc
    srv._live_queues[to_sid] = asyncio.Queue()

    from_proc = _make_fake_proc([])
    srv._running_procs[from_sid] = from_proc
    srv._live_queues[from_sid] = asyncio.Queue()

    call_count = {"n": 0}
    original_cse = asyncio.create_subprocess_exec

    async def counting_cse(*args, **kwargs):
        call_count["n"] += 1
        return _make_fake_proc([])

    with mock.patch("asyncio.create_subprocess_exec", side_effect=counting_cse):
        r = await client.post("/api/sessions/reassign", json={
            "from_id": from_sid,
            "to_id": to_sid,
        })

    assert r.status_code == 200
    # No new subprocess should have been spawned for to_id
    assert call_count["n"] == 0

    srv._running_procs.pop(to_sid, None)
    srv._running_procs.pop(from_sid, None)
    srv._live_queues.pop(to_sid, None)
    srv._live_queues.pop(from_sid, None)


# ── /api/warmup ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warmup_ok(client: AsyncClient):
    """Warmup should return ok=True even if the venv python isn't found."""
    fake_proc = mock.AsyncMock()
    fake_proc.wait = mock.AsyncMock(return_value=0)

    with mock.patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        r = await client.post("/api/warmup")

    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── Periodic title refresh throttling ────────────────────────────────────────


@pytest.mark.asyncio
async def test_title_refresh_throttled_by_token_growth(monkeypatch):
    """First turn generates; then only regenerate after ~10k more tokens."""
    calls: list[str] = []

    async def fake_gen(sid: str) -> None:
        calls.append(sid)

    class _FakeSession:
        def __init__(self, t: int) -> None:
            self.token_estimate = t

    class _FakeDB:
        tokens = 0
        def get_session(self, sid: str):
            return _FakeSession(self.tokens)

    fdb = _FakeDB()
    srv._session_title_tokens.clear()
    monkeypatch.setattr(srv, "_generate_title", fake_gen)
    monkeypatch.setattr(srv, "_db_ref", fdb)

    sid = "sess-title"
    fdb.tokens = 3000
    await srv._maybe_refresh_title(sid)          # first time → generate
    assert len(calls) == 1
    fdb.tokens = 8000
    await srv._maybe_refresh_title(sid)          # +5k < 10k → skip
    assert len(calls) == 1
    fdb.tokens = 20000
    await srv._maybe_refresh_title(sid)          # +12k ≥ 10k → generate
    assert len(calls) == 2

    srv._session_title_tokens.pop(sid, None)


# ── Heartbeat auto-continue ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_autocontinue_endpoint_toggles(client: AsyncClient):
    sid = "ac-endpoint"
    try:
        r = await client.post(f"/api/sessions/{sid}/autocontinue", json={"enabled": True})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True and body["max"] == srv._AUTO_CONTINUE_MAX
        assert srv._session_autocontinue.get(sid) is True
        r2 = await client.post(f"/api/sessions/{sid}/autocontinue", json={"enabled": False})
        assert r2.json()["enabled"] is False
        assert srv._session_autocontinue.get(sid) is False
    finally:
        srv._session_autocontinue.pop(sid, None)
        srv._session_continue_count.pop(sid, None)


def _setup_heartbeat(tmp_path, monkeypatch, sid: str, verdict):
    ws = tmp_path / "hb"
    ws.mkdir()
    (ws / "TASK.md").write_text("# Task\n\nLoop 100 iterations of the action.")
    srv._session_workspaces[sid] = str(ws)
    srv._session_autocontinue[sid] = True
    srv._session_continue_count.pop(sid, None)
    srv._session_user_stopped.discard(sid)
    srv._running_procs.pop(sid, None)
    spawned: list[tuple[str, str]] = []

    async def fake_spawn(s, content, *a, **k):
        spawned.append((s, content))

    async def fake_judge(goal, recent):
        return verdict

    monkeypatch.setattr(srv, "_spawn_resume_ref", fake_spawn)
    monkeypatch.setattr(srv, "_judge_complete", fake_judge)
    return spawned


def _cleanup_heartbeat(sid: str):
    for d in (srv._session_workspaces, srv._session_autocontinue, srv._session_continue_count):
        d.pop(sid, None)
    srv._session_user_stopped.discard(sid)


@pytest.mark.asyncio
async def test_heartbeat_resumes_when_task_incomplete(tmp_path, monkeypatch):
    sid = "hb-incomplete"
    spawned = _setup_heartbeat(tmp_path, monkeypatch, sid, (False, "do iteration 2"))
    try:
        await srv._heartbeat_check(sid)
        assert len(spawned) == 1
        assert spawned[0][0] == sid
        assert "auto-continue" in spawned[0][1] and "iteration 2" in spawned[0][1]
        assert srv._session_continue_count[sid] == 1
    finally:
        _cleanup_heartbeat(sid)


@pytest.mark.asyncio
async def test_heartbeat_stops_when_task_done(tmp_path, monkeypatch):
    sid = "hb-done"
    spawned = _setup_heartbeat(tmp_path, monkeypatch, sid, (True, ""))
    try:
        await srv._heartbeat_check(sid)
        assert spawned == []
        assert srv._session_continue_count.get(sid, 0) == 0
    finally:
        _cleanup_heartbeat(sid)


@pytest.mark.asyncio
async def test_heartbeat_respects_cap(tmp_path, monkeypatch):
    sid = "hb-cap"
    spawned = _setup_heartbeat(tmp_path, monkeypatch, sid, (False, "more work"))
    srv._session_continue_count[sid] = srv._AUTO_CONTINUE_MAX
    try:
        await srv._heartbeat_check(sid)
        assert spawned == []  # over the cap → no resume
    finally:
        _cleanup_heartbeat(sid)


# ── /api/file/preview path containment ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_file_inside_workspace_ok(client: AsyncClient, tmp_path: Path):
    """A file inside the workspace root previews normally."""
    f = tmp_path / "workspace" / "hello.py"
    f.write_text("print('hi')\n")

    r = await client.get("/api/file/preview", params={"path": str(f)})

    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "code"
    assert "print('hi')" in body["content"]


@pytest.mark.asyncio
async def test_preview_file_outside_roots_is_forbidden(client: AsyncClient, tmp_path_factory):
    """A readable file outside the workspace/home roots must be rejected (403)."""
    outside = tmp_path_factory.mktemp("outside") / "secret.env"
    outside.write_text("API_KEY=supersecret\n")

    r = await client.get("/api/file/preview", params={"path": str(outside)})

    assert r.status_code == 403


# ── CORS scoping ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cors_rejects_arbitrary_origin(client: AsyncClient):
    """A random website's origin must NOT be reflected back — no wildcard CORS,
    so a drive-by site can't read API responses cross-origin."""
    r = await client.get("/api/sessions", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


@pytest.mark.asyncio
async def test_cors_allows_localhost_origin(client: AsyncClient):
    """The local dev/app origin is allowed (so the proxied/dev flows keep working)."""
    origin = "http://localhost:8765"
    r = await client.get("/api/sessions", headers={"Origin": origin})
    assert r.headers.get("access-control-allow-origin") == origin


# ── /api/docker/cleanup ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_docker_cleanup_removes_when_idle(client: AsyncClient):
    """With no workers running, all hermes-* containers are reaped."""
    srv._running_procs.clear()
    with mock.patch.object(srv, "_list_hermes_containers", return_value=["a", "b", "c"]), \
         mock.patch.object(srv, "_remove_containers", return_value=3) as rm:
        r = await client.post("/api/docker/cleanup")

    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] is False
    assert body["removed"] == 3
    rm.assert_called_once_with(["a", "b", "c"])


@pytest.mark.asyncio
async def test_docker_cleanup_skips_when_worker_running(client: AsyncClient):
    """A running worker means we can't tell which container is in use → skip, report kept."""
    srv._running_procs["sess-active"] = mock.MagicMock()
    try:
        with mock.patch.object(srv, "_list_hermes_containers", return_value=["a", "b"]), \
             mock.patch.object(srv, "_remove_containers") as rm:
            r = await client.post("/api/docker/cleanup")
    finally:
        srv._running_procs.pop("sess-active", None)

    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] is True
    assert body["kept"] == 2
    rm.assert_not_called()


# ── /api/sessions/{id}/workspace_tree ────────────────────────────────────────


@pytest.mark.asyncio
async def test_workspace_tree_lists_dirs_and_files(client: AsyncClient, tmp_path: Path):
    """Lists the real workspace dir: subdirs + all files, dotfiles skipped, with
    preview_type on files."""
    ws = tmp_path / "workspace" / "wt-task"
    (ws / "sub").mkdir(parents=True)
    (ws / "report.pdf").write_bytes(b"%PDF-1.4")
    (ws / "script.py").write_text("print(1)\n")
    (ws / "sub" / "notes.txt").write_text("hi\n")
    (ws / ".hermes_session_id").write_text("wt-sid")  # dotfile → skipped

    sid = "wt-sid"
    srv._session_workspaces[sid] = str(ws)
    try:
        r = await client.get(f"/api/sessions/{sid}/workspace_tree")
    finally:
        srv._session_workspaces.pop(sid, None)

    assert r.status_code == 200
    tree = r.json()
    names = {n["name"] for n in tree}
    assert "sub" in names and "report.pdf" in names and "script.py" in names
    assert ".hermes_session_id" not in names

    sub = next(n for n in tree if n["name"] == "sub")
    assert sub["is_dir"] is True
    assert {c["name"] for c in sub["children"]} == {"notes.txt"}

    pdf = next(n for n in tree if n["name"] == "report.pdf")
    assert pdf["is_dir"] is False
    assert pdf["preview_type"] == "pdf"


@pytest.mark.asyncio
async def test_workspace_tree_empty_when_no_workspace(client: AsyncClient):
    """Unresolvable workspace → [] so the client can fall back to the touched-files list."""
    r = await client.get("/api/sessions/no-such-session/workspace_tree")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_workspace_tree_lists_team_repo_under_team_files(client: AsyncClient, tmp_home: Path):
    """team_files/ on disk is an empty mount point; tree should show repo contents."""
    tid = "team-tree"
    repo = tmp_home / "gui_team_repos" / tid
    repo.mkdir(parents=True)
    (repo / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (repo / "nested").mkdir()
    (repo / "nested" / "notes.txt").write_text("hi\n", encoding="utf-8")

    ws = tmp_home / "gui_sandboxes" / "tree-sid" / "docker" / "default" / "workspace"
    ws.mkdir(parents=True)
    (ws / "TASK.md").write_text("task\n", encoding="utf-8")
    (ws / "team_files").mkdir()
    (ws / ".hermes_team_id").write_text(tid, encoding="utf-8")

    sid = "tree-sid"
    srv._session_workspaces[sid] = str(ws)
    try:
        r = await client.get(f"/api/sessions/{sid}/workspace_tree")
    finally:
        srv._session_workspaces.pop(sid, None)

    assert r.status_code == 200
    tree = r.json()
    tf = next(n for n in tree if n["name"] == "team_files")
    assert tf["is_dir"] is True
    top = {n["name"] for n in tf["children"]}
    assert "data.csv" in top and "nested" in top
    nested = next(n for n in tf["children"] if n["name"] == "nested")
    assert {c["name"] for c in nested["children"]} == {"notes.txt"}
    # Preview paths must point at the canonical repo (host mount point is empty).
    csv = next(n for n in tf["children"] if n["name"] == "data.csv")
    assert str(repo / "data.csv") == csv["path"]


def test_audit_files_includes_team_repo(tmp_path: Path, monkeypatch):
    """Manager audits must read team File Repo files under team_files/, not empty mount."""
    from agent_gui import server as srv

    home = tmp_path
    monkeypatch.setattr(srv, "_home_ref", home)
    ws = home / "ws"
    ws.mkdir()
    (ws / "TASK.md").write_text("Analyze the dataset\n", encoding="utf-8")
    (ws / "team_files").mkdir()
    tid = "team-audit"
    repo = home / "gui_team_repos" / tid
    repo.mkdir(parents=True)
    (repo / "data.csv").write_text("x=1\n", encoding="utf-8")
    (ws / ".hermes_team_id").write_text(tid, encoding="utf-8")

    files = srv._audit_files(ws)
    assert "team_files/data.csv" in files
    assert srv._read_evidence(ws, "team_files/data.csv") == "x=1\n"


@pytest.mark.asyncio
async def test_cached_audit_resumes_idle_desk_but_waits_on_running(tmp_path, monkeypatch):
    """A cached audit (unchanged state) with issues should still let the manager
    resume an IDLE desk (the agent stopped) — bounded by the cap — but not re-nudge
    a desk that's still actively running its turn."""
    sid = "audit-idle"
    ws = tmp_path / "ws"
    ws.mkdir()
    # Deterministic state hash so the cache always hits.
    monkeypatch.setattr(srv, "_audit_state", lambda s, w: ("h1", "do the task", [], 1))
    srv._session_audits[sid] = {
        "session_id": sid, "state_hash": "h1",
        "summary": {"passed": 1, "failed": 1, "unsure": 0, "total": 2},
        "results": [], "should_intervene": True,
    }
    srv._session_manager_resumes.pop(sid, None)
    srv._running_procs.pop(sid, None)
    try:
        # Idle → bounded resume.
        out = await srv._run_audit(sid, ws)
        assert out["cached"] is True
        assert out["should_intervene"] is True
        assert out["intervention_count"] == 1

        # Past the cap → escalate (no more resumes).
        srv._session_manager_resumes[sid] = srv._MANAGER_MAX_INTERVENTIONS
        out_capped = await srv._run_audit(sid, ws)
        assert out_capped["should_intervene"] is False

        # Running → never re-nudge identical work.
        srv._session_manager_resumes.pop(sid, None)
        srv._running_procs[sid] = object()
        out_running = await srv._run_audit(sid, ws)
        assert out_running["should_intervene"] is False
    finally:
        srv._session_audits.pop(sid, None)
        srv._session_manager_resumes.pop(sid, None)
        srv._running_procs.pop(sid, None)


@pytest.mark.asyncio
async def test_failed_adjudication_caches_nothing(tmp_path, monkeypatch):
    """If adjudication yields no verdicts (timeout/bad JSON), the audit must fail
    like a decompose failure — no cached 0/0 audit, no AUDIT.md. A cached empty
    audit keeps matching the idle desk's unchanged state hash, making the desk
    permanently un-auditable."""
    sid = "audit-empty-adjudication"
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(srv, "_audit_state", lambda s, w: ("h2", "tell a joke", [], 1))
    monkeypatch.setattr(srv, "_aux_model_config", lambda: ("http://test", "m"))
    monkeypatch.setattr(srv, "hermes_model_config", lambda home: ("http://test", "m"))

    calls = {"n": 0}

    async def fake_aux_json(base_url, model, prompt, max_tokens, retries=2):
        calls["n"] += 1
        if calls["n"] == 1:  # decompose succeeds
            return [{"id": 1, "task": "Task 1: joke", "criterion": "a joke exists",
                     "source": "conversation"}]
        return None  # adjudicate fails every retry

    monkeypatch.setattr(srv, "_aux_json", fake_aux_json)
    try:
        out = await srv._run_audit(sid, ws)
        assert out is None
        assert sid not in srv._session_audits
        assert not (ws / "AUDIT.md").exists()
    finally:
        srv._session_audits.pop(sid, None)
        srv._session_manager_resumes.pop(sid, None)
        srv._session_audit_best.pop(sid, None)


@pytest.mark.asyncio
async def test_fresh_audit_skipped_when_running_unless_forced(tmp_path, monkeypatch):
    """Auto patrol must not run a fresh LLM audit mid-turn; Ask manager (force) may."""
    sid = "audit-running-fresh"
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "TASK.md").write_text("# Task\n\ndo the thing\n", encoding="utf-8")
    srv._running_procs[sid] = object()
    srv._session_audits.pop(sid, None)
    monkeypatch.setattr(srv, "_aux_model_config", lambda: ("http://test", "m"))

    async def boom(*a, **k):
        raise AssertionError("fresh audit LLM should not run")

    monkeypatch.setattr(srv, "_aux_json", boom)
    try:
        out = await srv._run_audit(sid, ws, force=False)
        assert out["skipped_running"] is True
        assert out["should_intervene"] is False
    finally:
        srv._running_procs.pop(sid, None)


# ── Real per-event timestamps (overlay vs. coarse flush time) ────────────────


def _ev(event_type: str, ts: str = "2026-06-01T00:00:00+00:00", tool_name: str = ""):
    from agent_gui.activity_parser import ActivityEvent
    return ActivityEvent(timestamp=ts, event_type=event_type, icon="", title="",
                         detail="", tool_name=tool_name)


def test_apply_real_times_overlays_in_order_per_kind():
    sid = "ts-overlay"
    try:
        # Recorded stream (real emit times), interleaved across kinds.
        srv._record_event_time(sid, "user_message", 1000.0)
        srv._record_event_time(sid, "message", 1002.0)
        srv._record_event_time(sid, "tool_call", 1003.0, "bash")
        srv._record_event_time(sid, "tool_result", 1005.0, "bash")
        srv._record_event_time(sid, "tool_call", 1007.0, "read_file")
        srv._record_event_time(sid, "tool_result", 1009.0, "read_file")
        # DB-derived events (parse_activity emits tool_call(s) before message text).
        events = [
            _ev("user_message"), _ev("tool_call"), _ev("tool_result"),
            _ev("message"), _ev("tool_call"), _ev("tool_result"),
        ]
        srv._apply_real_times(events, sid)
        from datetime import datetime, timezone
        def sec(e): return datetime.fromisoformat(e.timestamp).timestamp()
        # Each kind consumed its recorded times FIFO, regardless of inter-kind order.
        assert [sec(e) for e in events] == [1000.0, 1003.0, 1005.0, 1002.0, 1007.0, 1009.0]
        assert all(e.time_exact for e in events)
    finally:
        srv._session_event_times.pop(sid, None)


def test_apply_real_times_no_data_keeps_flush_time_as_approximate():
    sid = "ts-none"
    events = [_ev("tool_call", ts="2026-06-01T12:00:00+00:00"),
              _ev("tool_result", ts="2026-06-01T12:00:00+00:00")]
    srv._apply_real_times(events, sid)  # nothing recorded
    # Timestamps untouched (no fabrication) and explicitly marked not-exact.
    assert all(e.timestamp == "2026-06-01T12:00:00+00:00" for e in events)
    assert all(e.time_exact is False for e in events)


def test_apply_real_times_partial_match_marks_rest_approximate():
    sid = "ts-partial"
    try:
        srv._record_event_time(sid, "tool_call", 2000.0, "bash")  # only one marker
        events = [_ev("tool_call"), _ev("tool_call")]
        srv._apply_real_times(events, sid)
        assert events[0].time_exact is True            # matched → exact
        assert events[1].time_exact is False           # unmatched → approximate
    finally:
        srv._session_event_times.pop(sid, None)


def test_record_worker_evt_time_token_runs_and_tools():
    sid = "ts-runs"
    try:
        state: dict = {}
        # token run → one 'message'; tool boundary; another token run → another 'message'.
        srv._record_worker_evt_time(sid, {"type": "token", "text": "he", "ts": 1.0}, state)
        srv._record_worker_evt_time(sid, {"type": "token", "text": "llo", "ts": 1.1}, state)
        srv._record_worker_evt_time(sid, {"type": "tool_start", "name": "bash", "ts": 2.0}, state)
        srv._record_worker_evt_time(sid, {"type": "tool_done", "name": "bash", "ts": 3.0}, state)
        srv._record_worker_evt_time(sid, {"type": "token", "text": "done", "ts": 4.0}, state)
        kinds = [k for k, _ts, _n in srv._session_event_times[sid]]
        assert kinds == ["message", "tool_call", "tool_result", "message"]
    finally:
        srv._session_event_times.pop(sid, None)


def test_record_worker_evt_time_records_thinking_runs():
    """A run of `thinking` events marks one 'thinking_start' time. Without this
    the reasoning event keeps Hermes's batch-flush time and the overview's
    timestamp sort shoves reasoning to the back of the turn (see #overview-bug)."""
    sid = "ts-think"
    try:
        state: dict = {}
        # think run → reasoning step; tokens (response); tool; think run again.
        srv._record_worker_evt_time(sid, {"type": "thinking", "text": "hm", "ts": 1.0}, state)
        srv._record_worker_evt_time(sid, {"type": "thinking", "text": "mm", "ts": 1.1}, state)
        srv._record_worker_evt_time(sid, {"type": "token", "text": "ok", "ts": 2.0}, state)
        srv._record_worker_evt_time(sid, {"type": "tool_start", "name": "bash", "ts": 3.0}, state)
        srv._record_worker_evt_time(sid, {"type": "tool_done", "name": "bash", "ts": 4.0}, state)
        srv._record_worker_evt_time(sid, {"type": "thinking", "text": "next", "ts": 5.0}, state)
        srv._record_worker_evt_time(sid, {"type": "token", "text": "done", "ts": 6.0}, state)
        recorded = srv._session_event_times[sid]
        kinds = [k for k, _ts, _n in recorded]
        # One marker per run; thinking_start recorded at the run's FIRST event.
        assert kinds == ["thinking_start", "message", "tool_call", "tool_result",
                         "thinking_start", "message"]
        assert recorded[0][1] == 1.0 and recorded[4][1] == 5.0
    finally:
        srv._session_event_times.pop(sid, None)


def test_apply_real_times_keeps_reasoning_interleaved_after_sort():
    """End-to-end: reasoning that got a real (early) time stays interleaved when
    the overview sorts by timestamp, instead of collapsing to the flush time at
    the end of the turn."""
    sid = "ts-think-sort"
    try:
        # Real per-kind emit times (reasoning precedes each tool step).
        srv._record_event_time(sid, "thinking_start", 1001.0)
        srv._record_event_time(sid, "tool_call", 1002.0, "bash")
        srv._record_event_time(sid, "tool_result", 1003.0, "bash")
        srv._record_event_time(sid, "thinking_start", 1004.0)
        srv._record_event_time(sid, "message", 1005.0)
        # DB-derived events all share one batch-flush time (the turn's end).
        flush = "2026-06-01T12:00:00+00:00"
        events = [
            _ev("thinking_start", ts=flush), _ev("tool_call", ts=flush),
            _ev("tool_result", ts=flush), _ev("thinking_start", ts=flush),
            _ev("message", ts=flush),
        ]
        srv._apply_real_times(events, sid)
        from datetime import datetime
        events.sort(key=lambda e: datetime.fromisoformat(e.timestamp).timestamp())
        # Reasoning is NOT shoved to the back — order matches real emit times.
        assert [e.event_type for e in events] == [
            "thinking_start", "tool_call", "tool_result", "thinking_start", "message"]
    finally:
        srv._session_event_times.pop(sid, None)


# ── Resume with a different agent (drag-from-bench) ───────────────────────────


@pytest.mark.asyncio
async def test_resume_can_switch_agent(tmp_path: Path):
    """Dropping a bench agent on a desk resumes the SAME conversation with a
    DIFFERENT profile: the .hermes_profile marker is rewritten and the resumed
    worker's env points at the new profile's config home."""
    from agent_gui.gui_config import GuiConfig

    profiles = tmp_path / "profiles"
    for name in ("coder", "researcher"):
        d = profiles / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text("model:\n  default: m\n  base_url: http://x/v1\n")
    home = tmp_path / "home"; home.mkdir()
    ws_root = tmp_path / "ws"; ws_root.mkdir()
    cfg = GuiConfig(hermes_home=home, agent_profiles_dir=profiles)
    app = create_app(gui_config=cfg, workspace_root=str(ws_root))

    envs: list[dict] = []

    def _cap(*args, **kwargs):
        envs.append(kwargs.get("env", {}))
        return _make_fake_proc([json.dumps({"type": "done"})])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with mock.patch("asyncio.create_subprocess_exec", side_effect=_cap):
            r = await client.post("/api/sessions/new", json={"content": "hi", "agent": "coder"})
            sid = r.json()["session_id"]
            srv._running_procs.pop(sid, None)
            srv._live_queues.pop(sid, None)
            r2 = await client.post(f"/api/sessions/{sid}/resume",
                                   json={"content": "keep going", "agent": "researcher"})
            assert r2.status_code == 200

    ws = home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    assert (ws / ".hermes_profile").read_text().strip() == "researcher"
    # new_session spawned with coder, resume spawned with researcher.
    assert envs[0].get("HERMES_GUI_CONFIG_HOME", "").endswith("coder")
    assert envs[-1].get("HERMES_GUI_CONFIG_HOME", "").endswith("researcher")

    srv._running_procs.pop(sid, None)
    srv._live_queues.pop(sid, None)
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_new_session_ollama_agent_model_override(tmp_path: Path):
    from agent_gui.gui_config import GuiConfig

    profiles = tmp_path / "profiles"
    ollama_dir = profiles / "coder"
    ollama_dir.mkdir(parents=True)
    (ollama_dir / "config.yaml").write_text(yaml.safe_dump({
        "model": {"default": "qwen3:4b", "base_url": "http://127.0.0.1:11434/v1"},
    }))
    home = tmp_path / "home"; home.mkdir()
    ws_root = tmp_path / "ws"; ws_root.mkdir()
    cfg = GuiConfig(hermes_home=home, agent_profiles_dir=profiles)
    app = create_app(gui_config=cfg, workspace_root=str(ws_root))

    envs: list[dict] = []

    def _cap(*args, **kwargs):
        envs.append(kwargs.get("env", {}))
        return _make_fake_proc([json.dumps({"type": "done"})])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with mock.patch("asyncio.create_subprocess_exec", side_effect=_cap):
            with mock.patch.object(srv, "_resolve_gui_model", side_effect=lambda m, *_a: m):
                r = await client.post("/api/sessions/new", json={
                    "content": "hi", "agent": "coder", "model": "llama3:8b",
                })
        assert r.status_code == 200
        sid = r.json()["session_id"]
        assert r.json()["session"]["agent_model"] == "llama3:8b"

    assert envs[0].get("HERMES_MODEL") == "llama3:8b"
    ws = home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    assert (ws / ".hermes_model").read_text().strip() == "llama3:8b"

    srv._running_procs.pop(sid, None)
    srv._live_queues.pop(sid, None)
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_new_session_does_not_pin_profile_default_model(tmp_path: Path):
    """A model equal to the profile default is NOT pinned, so the desk tracks the
    profile config live (model + base_url together) — prevents vLLM↔Ollama drift."""
    from agent_gui.gui_config import GuiConfig

    profiles = tmp_path / "profiles"
    coder = profiles / "coder"
    coder.mkdir(parents=True)
    (coder / "config.yaml").write_text(yaml.safe_dump({
        "model": {"default": "qwen3:4b", "base_url": "http://127.0.0.1:11434/v1"},
    }))
    home = tmp_path / "home"; home.mkdir()
    ws_root = tmp_path / "ws"; ws_root.mkdir()
    cfg = GuiConfig(hermes_home=home, agent_profiles_dir=profiles)
    app = create_app(gui_config=cfg, workspace_root=str(ws_root))

    def _cap(*args, **kwargs):
        return _make_fake_proc([json.dumps({"type": "done"})])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with mock.patch("asyncio.create_subprocess_exec", side_effect=_cap):
            with mock.patch.object(srv, "_resolve_gui_model", side_effect=lambda m, *_a: m):
                r = await client.post("/api/sessions/new", json={
                    "content": "hi", "agent": "coder", "model": "qwen3:4b",
                })
        assert r.status_code == 200
        sid = r.json()["session_id"]

    ws = home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    assert not (ws / ".hermes_model").exists()

    srv._running_procs.pop(sid, None)
    srv._live_queues.pop(sid, None)
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_new_session_agent_uses_profile_tool_preset(tmp_path: Path):
    """When tools are omitted, agent desks inherit the profile's default preset."""
    from agent_gui.gui_config import GuiConfig

    profiles = tmp_path / "profiles"
    pdir = profiles / "researcher"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text("model:\n  default: m\n  base_url: http://x/v1\n")
    (pdir / ".gui-meta.yaml").write_text(yaml.safe_dump({
        "default_tool_preset": "chat",
        "tool_presets": {
            "chat": [],
            "lean": ["file", "terminal", "search"],
            "full": ["file", "terminal", "search", "browser"],
        },
    }))
    home = tmp_path / "home"; home.mkdir()
    ws_root = tmp_path / "ws"; ws_root.mkdir()
    cfg = GuiConfig(hermes_home=home, agent_profiles_dir=profiles)
    app = create_app(gui_config=cfg, workspace_root=str(ws_root))

    envs: list[dict] = []

    def _cap(*args, **kwargs):
        envs.append(kwargs.get("env", {}))
        return _make_fake_proc([json.dumps({"type": "done"})])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with mock.patch("asyncio.create_subprocess_exec", side_effect=_cap):
            r = await client.post("/api/sessions/new", json={"content": "hi", "agent": "researcher"})
        assert r.status_code == 200
        sid = r.json()["session_id"]

    assert envs[0].get("HERMES_GUI_ENABLED_TOOLSETS") == ""
    ws = home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    assert (ws / ".hermes_tools").read_text() == ""

    srv._running_procs.pop(sid, None)
    srv._live_queues.pop(sid, None)
    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_patch_agent_syncs_profile_default_tools(tmp_path: Path):
    """Assigning an agent profile must rewrite .hermes_tools to that profile's default."""
    from dataclasses import dataclass
    from agent_gui.gui_config import GuiConfig
    from agent_gui.db import HermesDB

    profiles = tmp_path / "profiles"
    pdir = profiles / "researcher"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text("model:\n  default: m\n")
    (pdir / ".gui-meta.yaml").write_text(yaml.safe_dump({
        "default_tool_preset": "chat",
        "tool_presets": {
            "chat": [],
            "lean": ["file", "terminal", "search"],
            "full": ["file", "terminal", "search", "browser"],
        },
    }))
    home = tmp_path / "home"
    home.mkdir()
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    sid = "desk-chat-tools"
    ws = home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    ws.mkdir(parents=True)
    (ws / ".hermes_tools").write_text("file,terminal,search,browser")

    cfg = GuiConfig(hermes_home=home, agent_profiles_dir=profiles)
    app = create_app(gui_config=cfg, workspace_root=str(ws_root))
    srv._session_workspaces[sid] = str(ws)

    @dataclass
    class _FakeSession:
        id: str

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with mock.patch.object(HermesDB, "get_session", return_value=_FakeSession(sid)):
            r = await client.patch(f"/api/sessions/{sid}/desk-config", json={"agent": "researcher"})

    assert r.status_code == 200
    assert (ws / ".hermes_tools").read_text() == ""
    assert r.json().get("desk_tools") == []

    srv._session_workspaces.pop(sid, None)


@pytest.mark.asyncio
async def test_delete_agent_profile(tmp_path: Path):
    from agent_gui.gui_config import GuiConfig

    profiles = tmp_path / "profiles"
    home = tmp_path / "home"
    clone = home / "profiles" / "my-clone"
    clone.mkdir(parents=True)
    (clone / "config.yaml").write_text("model: {}\n")
    ws_root = tmp_path / "ws"; ws_root.mkdir()
    cfg = GuiConfig(hermes_home=home, agent_profiles_dir=profiles)
    app = create_app(gui_config=cfg, workspace_root=str(ws_root))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with mock.patch("agent_gui.server.delete_profile") as delete_mock:
            delete_mock.return_value = None
            r = await client.delete("/api/agents/coder")
        assert r.status_code == 200
        delete_mock.assert_called_once()
        with mock.patch("agent_gui.server.delete_profile") as delete_mock:
            delete_mock.return_value = None
            r2 = await client.delete("/api/agents/my-clone")
        assert r2.status_code == 200
        assert delete_mock.call_count == 1


@pytest.mark.asyncio
async def test_delete_agent_unbinds_deployed_desks(tmp_path: Path):
    from agent_gui.gui_config import GuiConfig

    profiles = tmp_path / "profiles"
    home = tmp_path / "home"
    agent_dir = home / "profiles" / "my-clone"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text("model: {}\n")
    ws_root = tmp_path / "ws"; ws_root.mkdir()
    sid = "desk-session-1"
    ws = home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    ws.mkdir(parents=True)
    (ws / ".hermes_profile").write_text("my-clone", encoding="utf-8")

    cfg = GuiConfig(hermes_home=home, agent_profiles_dir=profiles)
    app = create_app(gui_config=cfg, workspace_root=str(ws_root))
    srv = app.state.gui_server if hasattr(app.state, "gui_server") else None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with mock.patch("agent_gui.server.delete_profile") as delete_mock:
            delete_mock.return_value = None
            r = await client.delete("/api/agents/my-clone")
        assert r.status_code == 200
        assert r.json().get("unbound_desks") == [sid]
        assert not (ws / ".hermes_profile").exists()


# ── Team file repo ────────────────────────────────────────────────────────────


def _data_url(blob: bytes) -> str:
    import base64 as _b64
    return "data:application/octet-stream;base64," + _b64.b64encode(blob).decode()


@pytest.mark.asyncio
async def test_team_file_repo_upload_list_and_desk_sync(client: AsyncClient, tmp_home: Path):
    tid = "team-abc"
    r = await client.post(f"/api/teams/{tid}/files",
                          json={"path": "docs/readme.txt", "data": _data_url(b"hello world")})
    assert r.status_code == 200
    repo_file = tmp_home / "gui_team_repos" / tid / "docs" / "readme.txt"
    assert repo_file.read_bytes() == b"hello world"

    r = await client.get(f"/api/teams/{tid}/files")
    assert "docs" in [n["name"] for n in r.json()["files"]]

    # A new desk on that team gets a host-visible team_files symlink to the repo
    # (+ Docker volume env). The symlink makes the files visible from a host
    # terminal; the container still gets the repo via the team_files bind mount.
    with mock.patch("asyncio.create_subprocess_exec",
                    return_value=_make_fake_proc([json.dumps({"type": "done"})])):
        r2 = await client.post("/api/sessions/new", json={"content": "task", "team_id": tid})
    sid = r2.json()["session_id"]
    ws = tmp_home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    mount = ws / "team_files"
    assert mount.is_symlink()
    assert mount.resolve() == (tmp_home / "gui_team_repos" / tid).resolve()
    # Files in the repo are now visible through the desk's team_files symlink.
    assert (mount / "docs" / "readme.txt").read_bytes() == b"hello world"
    assert (tmp_home / "gui_team_repos" / tid / "docs" / "readme.txt").read_bytes() == b"hello world"

    r = await client.delete(f"/api/teams/{tid}/files", params={"path": "docs/readme.txt"})
    assert r.status_code == 200
    assert not repo_file.exists()

    srv._running_procs.pop(sid, None)
    srv._live_queues.pop(sid, None)
    srv._session_workspaces.pop(sid, None)
    srv._session_team.pop(sid, None)
    srv._team_sessions.pop(tid, None)


@pytest.mark.asyncio
async def test_team_file_repo_rejects_traversal(client: AsyncClient):
    bad = await client.post("/api/teams/team-x/files",
                            json={"path": "../escape.txt", "data": _data_url(b"x")})
    assert bad.status_code in (400, 403)
    # An id with illegal characters (space) is rejected before any filesystem touch.
    bad_id = await client.post("/api/teams/bad%20id%21/files",
                               json={"path": "ok.txt", "data": _data_url(b"x")})
    assert bad_id.status_code == 400


@pytest.mark.asyncio
async def test_team_register_syncs_existing_desk(client: AsyncClient, tmp_home: Path):
    """Registering desks after upload copies the repo even without new_session team_id."""
    tid = "team-reg"
    sid = "20260605_120000_reg001"
    ws = tmp_home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    ws.mkdir(parents=True)
    (ws / ".hermes_session_id").write_text(sid)
    srv._session_workspaces[sid] = str(ws)

    await client.post(f"/api/teams/{tid}/files",
                      json={"path": "shared.txt", "data": _data_url(b"team data")})

    r = await client.post(f"/api/teams/{tid}/register", json={"session_ids": [sid]})
    assert r.status_code == 200
    assert r.json()["registered"] == 1
    mount = ws / "team_files"
    assert mount.is_symlink()
    assert mount.resolve() == (tmp_home / "gui_team_repos" / tid).resolve()
    assert (mount / "shared.txt").read_bytes() == b"team data"
    assert (tmp_home / "gui_team_repos" / tid / "shared.txt").read_bytes() == b"team data"
    assert (ws / ".hermes_team_id").read_text().strip() == tid

    srv._session_workspaces.pop(sid, None)
    srv._session_team.pop(sid, None)
    srv._team_sessions.pop(tid, None)


@pytest.mark.asyncio
async def test_team_session_env_allows_repo_writes(client: AsyncClient, tmp_home: Path):
    """Worker env must expose team repo for host write_file + Docker bind-mount."""
    tid = "team-write-env"
    envs: list[dict] = []

    def _cap(*args, **kwargs):
        envs.append(kwargs.get("env", {}))
        return _make_fake_proc([json.dumps({"type": "done"})])

    with mock.patch("asyncio.create_subprocess_exec", side_effect=_cap):
        r = await client.post("/api/sessions/new", json={"content": "task", "team_id": tid})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    repo = (tmp_home / "gui_team_repos" / tid).resolve()
    env = envs[0]
    assert env.get("HERMES_GUI_TEAM_REPO") == str(repo)
    assert env.get("HERMES_GUI_DOCKER_WORKSPACE") == "/workspace"
    vols = json.loads(env.get("TERMINAL_DOCKER_VOLUMES", "[]"))
    ws = (tmp_home / "gui_sandboxes" / sid / "docker" / "default" / "workspace").resolve()
    assert f"{ws}:/workspace" in vols
    assert f"{repo}:/workspace/team_files" in vols

    srv._running_procs.pop(sid, None)
    srv._live_queues.pop(sid, None)
    srv._session_workspaces.pop(sid, None)
    srv._session_team.pop(sid, None)
    srv._team_sessions.pop(tid, None)


# ── Agent profiles (bench create / persona edit) ─────────────────────────────


def _seed_profile(root: Path, pid: str, *, soul: str = "", memory: str = "") -> Path:
    p = root / pid
    p.mkdir(parents=True, exist_ok=True)
    (p / "config.yaml").write_text("model:\n  default: test-model\n")
    if soul:
        (p / "SOUL.md").write_text(soul, encoding="utf-8")
    if memory:
        (p / "memories").mkdir(exist_ok=True)
        (p / "memories" / "MEMORY.md").write_text(memory, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_agents_list_excludes_jedi_padawan(client: AsyncClient, tmp_home: Path):
    profiles = tmp_home / "profiles"
    _seed_profile(profiles, "coder")
    _seed_profile(profiles, "jedi")
    _seed_profile(profiles, "researcher")
    r = await client.get("/api/agents")
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["agents"]]
    assert "coder" in ids and "researcher" in ids
    assert "jedi" not in ids


@pytest.mark.asyncio
async def test_agent_prototypes(client: AsyncClient, tmp_home: Path):
    profiles = tmp_home / "profiles"
    _seed_profile(profiles, "coder")
    _seed_profile(profiles, "researcher")
    r = await client.get("/api/agents/prototypes")
    assert [p["id"] for p in r.json()["prototypes"]] == ["coder", "researcher"]


@pytest.mark.asyncio
async def test_agent_persona_get_and_put(client: AsyncClient, tmp_home: Path):
    profiles = tmp_home / "profiles"
    _seed_profile(profiles, "coder", soul="hello soul", memory="hello mem")
    r = await client.get("/api/agents/coder/persona")
    assert r.status_code == 200
    body = r.json()
    assert body["soul"] == "hello soul"
    assert body["memory"] == "hello mem"
    assert body["is_prototype"] is True

    r2 = await client.put("/api/agents/coder/persona",
                          json={"soul": "updated soul", "memory": "updated mem"})
    assert r2.status_code == 200
    assert (profiles / "coder" / "SOUL.md").read_text() == "updated soul"
    assert (profiles / "coder" / "memories" / "MEMORY.md").read_text() == "updated mem"


@pytest.mark.asyncio
async def test_agent_persona_updates_model_default(client: AsyncClient, tmp_home: Path):
    profiles = tmp_home / "profiles"
    _seed_profile(profiles, "coder")
    (profiles / "coder" / "config.yaml").write_text(
        "model:\n  default: old\n  base_url: http://localhost:8010/v1\n"
    )
    r = await client.put("/api/agents/coder/persona", json={"model_default": "Qwen/New"})
    assert r.status_code == 200
    import yaml as _yaml
    cfg = _yaml.safe_load((profiles / "coder" / "config.yaml").read_text())
    assert cfg["model"]["default"] == "Qwen/New"


@pytest.mark.asyncio
async def test_list_llm_models(client: AsyncClient, monkeypatch):
    async def fake_fetch(base_url: str, **kwargs):
        assert "8010" in base_url
        return ["Qwen/A", "Qwen/B"]

    monkeypatch.setattr("agent_gui.server.fetch_llm_models", fake_fetch)
    r = await client.get("/api/llm/models?base_url=http://localhost:8010/v1&agent_id=coder")
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == ["Qwen/A", "Qwen/B"]
    assert body["base_url"] == "http://localhost:8010/v1"


@pytest.mark.asyncio
async def test_agent_capabilities(client: AsyncClient, tmp_home: Path):
    profiles = tmp_home / "profiles"
    _seed_profile(profiles, "coder")
    bundle = profiles / "coder" / "skills" / "dev"
    (bundle / "my-skill").mkdir(parents=True)
    r = await client.get("/api/agents/coder/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "coder"
    assert "chat" in body["presets"] and body["presets"]["chat"] == []
    assert "search" in body["presets"]["lean"]
    assert body["skill_count"] == 1
    assert body["skill_bundles"][0]["bundle"] == "dev"


@pytest.mark.asyncio
async def test_create_agent_profile(client: AsyncClient, tmp_home: Path):
    profiles = tmp_home / "profiles"
    _seed_profile(profiles, "coder")
    dest = profiles / "newone"

    with mock.patch("agent_gui.server.create_profile_via_hermes") as cp:
        def _fake_create(home, pid, clone_from):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "config.yaml").write_text("model: {}\n")
            return dest
        cp.side_effect = _fake_create
        r = await client.post("/api/agents", json={
            "id": "newone",
            "clone_from": "coder",
            "name": "New One",
            "soul": "custom soul",
            "memory": "custom mem",
        })
    assert r.status_code == 200
    assert r.json()["agent"]["id"] == "newone"
    assert (dest / "SOUL.md").read_text() == "custom soul"
    assert (dest / "memories" / "MEMORY.md").read_text() == "custom mem"
    meta = yaml.safe_load((dest / ".gui-meta.yaml").read_text())
    assert meta["clone_from"] == "coder"
    assert meta["name"] == "New One"


@pytest.mark.asyncio
async def test_create_agent_profile_with_provider(client: AsyncClient, tmp_home: Path):
    """Cloning with a chosen provider writes the model block into the clone."""
    profiles = tmp_home / "profiles"
    _seed_profile(profiles, "coder")
    dest = profiles / "ollama-clone"

    with mock.patch("agent_gui.server.create_profile_via_hermes") as cp:
        def _fake_create(home, pid, clone_from):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "config.yaml").write_text("model: {}\n")
            return dest
        cp.side_effect = _fake_create
        r = await client.post("/api/agents", json={
            "id": "ollama-clone",
            "clone_from": "coder",
            "model_default": "qwen3.5:4b",
            "base_url": "http://127.0.0.1:11434/v1",
            "provider": "ollama-launch",
        })
    assert r.status_code == 200
    cfg = yaml.safe_load((dest / "config.yaml").read_text())
    assert cfg["model"]["default"] == "qwen3.5:4b"
    assert cfg["model"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert cfg["model"]["provider"] == "ollama-launch"


@pytest.mark.asyncio
async def test_global_persona_get_put(tmp_home: Path, tmp_path: Path):
    home = tmp_home
    (home / "memories").mkdir(parents=True)
    (home / "SOUL.md").write_text("hello soul")
    (home / "memories" / "MEMORY.md").write_text("hello memory")
    (home / "config.yaml").write_text("model:\n  default: gpt-test\n  base_url: http://localhost/v1\n")
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    app = create_app(hermes_home=str(home), workspace_root=str(ws_root))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/global/persona")
        assert r.status_code == 200
        body = r.json()
        assert body["soul"] == "hello soul"
        assert body["memory"] == "hello memory"
        assert body["model"] == "gpt-test"

        r2 = await client.put("/api/global/persona", json={"soul": "new soul", "memory": "new mem"})
        assert r2.status_code == 200
        assert (home / "SOUL.md").read_text() == "new soul"
        assert (home / "memories" / "MEMORY.md").read_text() == "new mem"


@pytest.mark.asyncio
async def test_model_reasoning_qwen_off_on(tmp_home: Path, tmp_path: Path):
    home = tmp_home
    (home / "config.yaml").write_text(
        "model:\n  default: Qwen/Qwen3.6-27B\n  base_url: http://127.0.0.1:8010/v1\n"
    )
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    app = create_app(hermes_home=str(home), workspace_root=str(ws_root))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Global config is vLLM — without desk base_url, reasoning is unavailable.
        r0 = await client.get("/api/models/reasoning", params={"model": "qwen3.5:4b"})
        assert r0.json()["options"] == []

        # Desk Ollama profile backend → off/on for qwen.
        r = await client.get(
            "/api/models/reasoning",
            params={"model": "qwen3.5:4b", "base_url": "http://127.0.0.1:11434/v1"},
        )
    assert r.status_code == 200
    assert r.json()["options"] == [
        {"value": "none", "label": "off"},
        {"value": "medium", "label": "on"},
    ]


@pytest.mark.asyncio
async def test_model_reasoning_qwen_guictx_alias(tmp_home: Path, tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  default: qwen3.5:4b\n  base_url: http://127.0.0.1:11434/v1\n"
    )
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    app = create_app(hermes_home=str(home), workspace_root=str(ws_root))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(
            "/api/models/reasoning",
            params={"model": "qwen3.5:4b-guictx65536", "base_url": "http://127.0.0.1:11434/v1"},
        )
    assert r.status_code == 200
    assert r.json()["options"] == [
        {"value": "none", "label": "off"},
        {"value": "medium", "label": "on"},
    ]

