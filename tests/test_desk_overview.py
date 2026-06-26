"""Workspace-scoped overview merges related session ids."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from starlette.testclient import TestClient

from agent_gui.db import HermesDB, WORKSPACE_KEY_MARKER
from agent_gui.server import create_app, _ORPHAN_FEED_MARKER


def _make_desk_db(path: Path, sid: str, user_text: str, ts: float) -> None:
    conn = sqlite3.connect(path)
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
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, NULL, 'workbench', 'm', NULL, 't', 1, 0, 0)",
            (sid, ts),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
            (sid, user_text, ts + 10),
        )
        conn.commit()
    finally:
        conn.close()


def _sandbox_ws(home: Path, sid: str) -> Path:
    ws = home / "gui_sandboxes" / sid / "docker" / "default" / "workspace"
    ws.mkdir(parents=True)
    (ws / "TASK.md").write_text("# Task\n\nTrain GPT-2 on shakespeare\n", encoding="utf-8")
    (ws / ".hermes_team_id").write_text("team-a", encoding="utf-8")
    return ws


def test_get_desk_messages_includes_all_session_ids(tmp_path):
    home = tmp_path / "hermes"
    sandbox = home / "gui_sandboxes" / "anchor-desk"
    sandbox.mkdir(parents=True)
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
            INSERT INTO sessions VALUES
              ('old-run', 1000.0, 2000.0, 'workbench', 'm', NULL, 'first', 1, 0, 0),
              ('new-run', 3000.0, NULL, 'workbench', 'm', NULL, 'second', 1, 0, 0);
            INSERT INTO messages (session_id, role, content, timestamp) VALUES
              ('old-run', 'user', 'first task', 1100.0),
              ('new-run', 'user', 'second task', 3100.0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    db = HermesDB(home)
    msgs = db.get_desk_messages("anchor-desk", limit=100)
    assert len(msgs) == 2
    assert {m.session_id for m in msgs} == {"old-run", "new-run"}


def test_get_desk_messages_includes_reasoning_content(tmp_path):
    """Overview chart counts reasoning calls — desk reads must carry the trace."""
    home = tmp_path / "hermes"
    sandbox = home / "gui_sandboxes" / "desk-r"
    sandbox.mkdir(parents=True)
    conn = sqlite3.connect(sandbox / "state.db")
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
                timestamp REAL, reasoning_content TEXT
            );
            INSERT INTO sessions VALUES
              ('desk-r', 1000.0, NULL, 'workbench', 'm', NULL, 't', 2, 0, 0);
            INSERT INTO messages (session_id, role, content, timestamp, reasoning_content) VALUES
              ('desk-r', 'user', 'do it', 1100.0, NULL),
              ('desk-r', 'assistant', 'done', 1200.0, 'let me think about this');
            """
        )
        conn.commit()
    finally:
        conn.close()

    from agent_gui.activity_parser import parse_activity

    db = HermesDB(home)
    msgs = db.get_desk_messages("desk-r", limit=100)
    assert any((m.reasoning_content or "").strip() for m in msgs)
    events = parse_activity(msgs)
    assert any(e.event_type == "thinking_start" for e in events)


def test_desk_time_bounds_start_at_first_command(tmp_path):
    """The Overview span must start at the first command (earliest message), not
    the session row's started_at — workers write their session row at spawn,
    which precedes the first user command by the whole worker init time."""
    home = tmp_path / "hermes"
    sandbox = home / "gui_sandboxes" / "bounds-desk"
    sandbox.mkdir(parents=True)
    # _make_desk_db writes the session row at ts and the first message at ts+10.
    _make_desk_db(sandbox / "state.db", "bounds-desk", "do the thing", 1_000_000.0)

    db = HermesDB(home)
    t0, _t1 = db.get_desk_time_bounds("bounds-desk")
    assert db._timestamp_sort_key(t0) == 1_000_010.0


def test_desk_time_bounds_fall_back_to_session_start(tmp_path):
    """A desk whose worker has not received its first command yet (no messages)
    still gets a span anchored at the session row's started_at."""
    home = tmp_path / "hermes"
    sandbox = home / "gui_sandboxes" / "empty-desk"
    sandbox.mkdir(parents=True)
    conn = sqlite3.connect(sandbox / "state.db")
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
            INSERT INTO sessions VALUES
              ('empty-desk', 2000.0, NULL, 'workbench', 'm', NULL, 't', 0, 0, 0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    db = HermesDB(home)
    t0, _t1 = db.get_desk_time_bounds("empty-desk")
    assert db._timestamp_sort_key(t0) == 2000.0


def test_find_related_session_ids_by_workspace_key(tmp_path):
    home = tmp_path / "hermes"
    ws_a = _sandbox_ws(home, "session-a")
    ws_b = _sandbox_ws(home, "session-b")
    key = "shared-lineage-key"
    (ws_a / WORKSPACE_KEY_MARKER).write_text(key, encoding="utf-8")
    (ws_b / WORKSPACE_KEY_MARKER).write_text(key, encoding="utf-8")
    _make_desk_db(home / "gui_sandboxes" / "session-a" / "state.db", "session-a", "early", 1000.0)
    _make_desk_db(home / "gui_sandboxes" / "session-b" / "state.db", "session-b", "later", 2000.0)

    db = HermesDB(home)
    related = db.find_related_session_ids("session-b", ws_b)
    assert related == ["session-a", "session-b"]

    merged = db.get_workspace_messages(related, limit=100)
    assert len(merged) == 2
    assert {m.session_id for m in merged} == {"session-a", "session-b"}


def test_same_team_same_task_desks_stay_separate(tmp_path):
    """Regression: desks in one team created with the SAME prompt must NOT share
    an overview. Lineage is the workspace key alone — task-content fingerprints
    merged every same-team/same-prompt desk into one Activity overview."""
    home = tmp_path / "hermes"
    ws_a = _sandbox_ws(home, "desk-a")  # same team + identical TASK.md as desk-b
    ws_b = _sandbox_ws(home, "desk-b")
    (ws_a / WORKSPACE_KEY_MARKER).write_text("key-a", encoding="utf-8")
    (ws_b / WORKSPACE_KEY_MARKER).write_text("key-b", encoding="utf-8")
    _make_desk_db(home / "gui_sandboxes" / "desk-a" / "state.db", "desk-a", "a", 1000.0)
    _make_desk_db(home / "gui_sandboxes" / "desk-b" / "state.db", "desk-b", "b", 2000.0)

    db = HermesDB(home)
    assert db.find_related_session_ids("desk-b", ws_b) == ["desk-b"]


def test_keyless_desks_never_link_by_task_content(tmp_path):
    """Even with no workspace keys at all, identical team + TASK.md text must not
    link two desks — content-based linking is gone for good."""
    home = tmp_path / "hermes"
    ws_b = _sandbox_ws(home, "newer-run")
    _sandbox_ws(home, "older-run")
    _make_desk_db(home / "gui_sandboxes" / "older-run" / "state.db", "older-run", "a", 1000.0)
    _make_desk_db(home / "gui_sandboxes" / "newer-run" / "state.db", "newer-run", "b", 2000.0)

    db = HermesDB(home)
    assert db.find_related_session_ids("newer-run", ws_b) == ["newer-run"]


def test_overview_endpoint_merges_all_runs_in_one_desk_db(tmp_path):
    """Hermes may tag messages with different session_id values inside one desk db."""
    home = tmp_path / "hermes"
    sid = "desk-anchor"
    _sandbox_ws(home, sid)
    db_path = home / "gui_sandboxes" / sid / "state.db"
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
            INSERT INTO sessions VALUES
              ('old-run', 1000.0, 2000.0, 'workbench', 'm', NULL, 'first', 1, 0, 0),
              ('new-run', 3000.0, NULL, 'workbench', 'm', NULL, 'second', 1, 0, 0);
            INSERT INTO messages (session_id, role, content, timestamp) VALUES
              ('old-run', 'user', 'first task', 1100.0),
              ('new-run', 'user', 'second task', 3100.0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    app = create_app(hermes_home=str(home), workspace_root=str(ws_root))
    client = TestClient(app)
    resp = client.get(f"/api/sessions/{sid}/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["message_count"] == 2
    assert len(data["events"]) == 2
    details = {e["detail"] for e in data["events"]}
    assert details == {"first task", "second task"}


def _orphan(ts: float, event_type: str, title: str, detail: str,
            tool_name: str = "", is_error: bool = False) -> dict:
    return {
        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "event_type": event_type, "icon": "🔧", "title": title, "detail": detail,
        "tool_name": tool_name, "is_error": is_error, "files_touched": [],
        "time_exact": True,
    }


def test_overview_includes_interrupted_turn_orphans(tmp_path):
    """A turn stopped half-way never reaches Hermes' state.db — its partial work is
    kept in the per-desk orphan feed. The Overview must fold it in (like the Feed),
    so an interrupted run isn't a blank gap on the chart."""
    home = tmp_path / "hermes"
    sid = "interrupted-desk"
    ws = _sandbox_ws(home, sid)  # gui_sandboxes/<sid>/docker/default/workspace
    # The user row commits at turn-start; the agent half is interrupted (not flushed).
    _make_desk_db(home / "gui_sandboxes" / sid / "state.db", sid, "build it", 1000.0)
    orphans = [
        _orphan(1100.0, "tool_call", "calling bash", "make", tool_name="bash"),
        _orphan(1120.0, "message", "Agent", "partial reply before the stop"),
        _orphan(1130.0, "message", "Interrupted", "Turn stopped before completion."),
    ]
    (ws / _ORPHAN_FEED_MARKER).write_text(json.dumps(orphans), encoding="utf-8")

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    app = create_app(hermes_home=str(home), workspace_root=str(ws_root))
    client = TestClient(app)
    data = client.get(f"/api/sessions/{sid}/overview").json()

    details = {e["detail"] for e in data["events"]}
    # Committed user message AND the interrupted turn's agent activity are charted.
    assert "build it" in details
    assert "partial reply before the stop" in details
    assert {e["event_type"] for e in data["events"]} >= {"user_message", "tool_call", "message"}
    # Orphans keep their real exact times (so they land at their true spot).
    assert all(e["time_exact"] for e in data["events"] if e["event_type"] == "tool_call")
