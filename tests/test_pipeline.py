"""Pipeline capability tests — the worker→server→stream chain, driven by mock data.

The real pipeline is: the Hermes worker prints newline-delimited JSON events on
stdout → `_pump_worker` parses each line, buffers it for replay, enqueues it on the
session's live queue (consumed by the activity WebSocket), and fans formatted text
out to the terminal/console WebSockets. These tests stand in a *fake worker proc*
(no Hermes, no model) and assert each stage of that transform in isolation, plus the
WebSocket endpoints' guard behaviour.
"""
import asyncio
import json
import unittest.mock as mock
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_gui import server as srv
from agent_gui.server import create_app


def _fake_proc(stdout_lines, stderr_lines=()):
    """A mock asyncio subprocess streaming the given stdout/stderr lines."""
    async def _agen(lines):
        for line in lines:
            yield (line + "\n").encode()

    proc = mock.AsyncMock()
    proc.stdout = _agen(stdout_lines)
    proc.stderr = _agen(stderr_lines)
    proc.returncode = 0
    proc.wait = mock.AsyncMock(return_value=0)
    proc.terminate = mock.MagicMock()
    proc.kill = mock.MagicMock()
    return proc


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test starts with empty per-session registries."""
    for d in (srv._live_queues, srv._terminal_queues, srv._console_queues,
              srv._running_procs, srv._session_workspaces, srv._live_event_buffer):
        d.clear()
    yield
    for d in (srv._live_queues, srv._terminal_queues, srv._console_queues,
              srv._running_procs, srv._session_workspaces, srv._live_event_buffer):
        d.clear()


async def _drain_queue(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── _pump_worker: stdout JSON → live queue + terminal fan-out ─────────────────


@pytest.mark.asyncio
async def test_pump_worker_routes_events_to_live_queue_in_order():
    sid = "s1"
    events = [
        {"type": "log", "msg": "[worker] starting..."},
        {"type": "token", "text": "Hello "},
        {"type": "token", "text": "world"},
        {"type": "tool_start", "name": "terminal"},
        {"type": "tool_done", "name": "terminal", "args": "", "result": "ok"},
        {"type": "done"},
    ]
    proc = _fake_proc([json.dumps(e) for e in events])
    queue: asyncio.Queue = asyncio.Queue()
    srv._live_queues[sid] = queue
    srv._running_procs[sid] = proc

    await srv._pump_worker(sid, proc, queue)

    got = await _drain_queue(queue)
    types = [e["type"] for e in got]
    # All worker events arrive in order; the finally-block appends a 'done' sentinel.
    assert types[:6] == ["log", "token", "token", "tool_start", "tool_done", "done"]
    assert types[-1] == "done"  # sentinel
    # Registries cleaned up after the worker finishes.
    assert sid not in srv._live_queues
    assert sid not in srv._running_procs


@pytest.mark.asyncio
async def test_pump_worker_broadcasts_formatted_terminal_text():
    sid = "s2"
    tq: asyncio.Queue = asyncio.Queue()
    srv._terminal_queues[sid] = [tq]
    events = [
        {"type": "token", "text": "Hi"},
        {"type": "tool_start", "name": "terminal"},
        {"type": "done"},
    ]
    proc = _fake_proc([json.dumps(e) for e in events])
    queue: asyncio.Queue = asyncio.Queue()
    srv._live_queues[sid] = queue
    srv._running_procs[sid] = proc

    await srv._pump_worker(sid, proc, queue)

    chunks = []
    while not tq.empty():
        chunks.append(tq.get_nowait())
    blob = "".join(c for c in chunks if c is not None)
    assert "Hi" in blob                 # token text passes through
    assert "── terminal ──" in blob     # tool_start header formatted
    # Terminal subscribers get a None sentinel so their WS loop can close.
    assert None in chunks


@pytest.mark.asyncio
async def test_pump_worker_skips_session_id_event():
    sid = "s3"
    events = [
        {"type": "session_id", "id": "abc"},  # must not reach the live queue
        {"type": "token", "text": "x"},
        {"type": "done"},
    ]
    proc = _fake_proc([json.dumps(e) for e in events])
    queue: asyncio.Queue = asyncio.Queue()
    srv._live_queues[sid] = queue
    srv._running_procs[sid] = proc

    await srv._pump_worker(sid, proc, queue)

    got = await _drain_queue(queue)
    assert all(e["type"] != "session_id" for e in got)


@pytest.mark.asyncio
async def test_pump_worker_tolerates_non_json_stdout():
    """A non-JSON stdout line is forwarded to the terminal, not crash the pump."""
    sid = "s4"
    tq: asyncio.Queue = asyncio.Queue()
    srv._terminal_queues[sid] = [tq]
    proc = _fake_proc(["this is not json", json.dumps({"type": "done"})])
    queue: asyncio.Queue = asyncio.Queue()
    srv._live_queues[sid] = queue
    srv._running_procs[sid] = proc

    await srv._pump_worker(sid, proc, queue)  # must not raise

    chunks = [c for c in _flush(tq) if c is not None]
    assert any("this is not json" in c for c in chunks)


@pytest.mark.asyncio
async def test_pump_worker_clears_replay_buffer_at_turn_end():
    """The live replay buffer is dropped once the turn ends (DB now covers it)."""
    sid = "s5"
    proc = _fake_proc([json.dumps({"type": "token", "text": "hi"}),
                       json.dumps({"type": "done"})])
    queue: asyncio.Queue = asyncio.Queue()
    srv._live_queues[sid] = queue
    srv._running_procs[sid] = proc

    await srv._pump_worker(sid, proc, queue)

    assert sid not in srv._live_event_buffer


def _flush(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── WebSocket endpoint guards ────────────────────────────────────────────────


@pytest.fixture()
def app(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "workspace"
    home.mkdir()
    ws.mkdir()
    return create_app(hermes_home=str(home), workspace_root=str(ws))


def test_terminal_ws_unknown_session_explains_and_closes(app):
    client = TestClient(app)
    with client.websocket_connect("/ws/terminal/nope") as ws:
        msg = ws.receive_text()
    assert "only available" in msg


def test_tail_ws_rejects_path_outside_workspace(app, tmp_path):
    sid = "tail1"
    ws_dir = tmp_path / "ws_tail"
    ws_dir.mkdir()
    srv._session_workspaces[sid] = str(ws_dir)
    client = TestClient(app)
    with client.websocket_connect(f"/ws/tail/{sid}?file=../../../etc/passwd") as ws:
        msg = ws.receive_text()
    assert "outside workspace" in msg


def test_tail_ws_missing_file_param_reports_not_found(app):
    sid = "tail2"
    client = TestClient(app)
    with client.websocket_connect(f"/ws/tail/{sid}") as ws:
        msg = ws.receive_text()
    assert "not found" in msg
