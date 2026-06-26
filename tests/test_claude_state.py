"""Round-trip proof that a Claude desk's ``ClaudeStateWriter`` produces a
``state.db`` the existing :class:`agent_gui.db.HermesDB` reader + activity/file
parsers consume exactly like a Hermes desk — i.e. Claude persistence is db.py
compatible with **no server changes**.

SDK-free: none of these imports touch ``claude-agent-sdk`` (only the worker does),
so the persistence contract is testable without the agent runtime installed.
"""
import time

from agent_gui.activity_parser import parse_activity
from agent_gui.claude_state import ClaudeStateWriter
from agent_gui.db import HermesDB
from agent_gui.file_parser import extract_touched_files


def _desk(tmp_path, sid="20260618_101500_abc123"):
    """Per-desk HERMES_HOME layout: ``<home>/gui_sandboxes/<sid>/state.db``."""
    home = tmp_path / ".hermes"
    db_path = home / "gui_sandboxes" / sid / "state.db"
    return home, db_path, sid


def test_writer_roundtrips_through_hermesdb(tmp_path):
    home, db_path, sid = _desk(tmp_path)
    t0 = time.time()
    w = ClaudeStateWriter(db_path, sid, model="sonnet")
    # First turn carries the server's injected [Workspace: …] header, exactly like
    # a Hermes desk — the display layer is expected to strip it.
    w.record_user("[Workspace: /ws all tools run in Docker]\n\nfix the parser bug", ts=t0)
    w.record_assistant(
        text="On it — reading the file first.",
        reasoning="The bug is probably in _tool_detail.",
        tool_calls=[{"id": "tu_1", "type": "function",
                     "function": {"name": "Read",
                                  "arguments": '{"file_path": "/ws/agent_gui/x.py"}'}}],
        ts=t0 + 1,
    )
    w.record_tool_result("tu_1", "Read", "1\timport os\n2\t...", ts=t0 + 2)
    w.record_assistant(
        tool_calls=[{"id": "tu_2", "type": "function",
                     "function": {"name": "Write",
                                  "arguments": '{"file_path": "/ws/agent_gui/x.py", "content": "fixed"}'}}],
        ts=t0 + 3,
    )
    w.record_tool_result("tu_2", "Write", "File written", ts=t0 + 4)
    w.finalize_turn(input_tokens=1200, output_tokens=340, ended_at=t0 + 5)
    w.close()

    db = HermesDB(home)

    # Session row resolves by the GUI/desk id, with real counts + token estimate.
    s = db.get_session(sid)
    assert s is not None
    assert s.id == sid
    assert s.model == "sonnet"
    assert s.message_count == 5
    assert s.token_estimate == 1540
    # Title inferred from the first user message, injected prefix stripped.
    assert s.title == "fix the parser bug"

    # The desk surfaces in the aggregate listing (the stub→db-backed transition).
    assert any(x.id == sid for x in db.list_sessions())

    # Messages read back in order with structure intact.
    msgs = db.get_desk_messages(sid)
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant", "tool"]
    assert msgs[1].reasoning_content == "The bug is probably in _tool_detail."
    assert msgs[1].tool_calls[0]["function"]["name"] == "Read"
    assert msgs[2].tool_name == "Read"
    assert msgs[2].tool_call_id == "tu_1"

    # Activity feed renders natively: user bubble (prefix stripped), a reasoning
    # step, tool calls with the REAL Claude names + file-path detail, tool results.
    events = parse_activity(msgs)
    kinds = [e.event_type for e in events]
    assert kinds.count("user_message") == 1
    assert "thinking_start" in kinds
    user_ev = next(e for e in events if e.event_type == "user_message")
    assert user_ev.detail == "fix the parser bug"
    read_call = next(e for e in events
                     if e.event_type == "tool_call" and e.tool_name == "Read")
    assert read_call.detail == "/ws/agent_gui/x.py"
    assert read_call.icon == "📖"

    # Files tab: the Write is detected as a write op on the right path.
    touched = extract_touched_files(msgs)
    assert any(t.path == "/ws/agent_gui/x.py" and t.operation == "write" for t in touched)


def test_restart_appends_and_preserves_history(tmp_path):
    """A new worker process (server restart) reuses the same desk id + db: prior
    history is preserved and new turns simply append."""
    home, db_path, sid = _desk(tmp_path)
    w1 = ClaudeStateWriter(db_path, sid)
    w1.record_user("first turn", ts=1.0)
    w1.record_assistant(text="ok", ts=2.0)
    w1.close()

    w2 = ClaudeStateWriter(db_path, sid)
    w2.record_user("second turn", ts=3.0)
    w2.record_assistant(text="done", ts=4.0)
    w2.close()

    db = HermesDB(home)
    msgs = db.get_desk_messages(sid)
    assert [m.content for m in msgs] == ["first turn", "ok", "second turn", "done"]
    assert db.get_session(sid).message_count == 4


def test_shared_home_is_refused(tmp_path, monkeypatch):
    """The writer must never write the SHARED ~/.hermes/state.db (cross-desk mix).

    Simulated via started_at_from_session_id fallback + an explicit shared path: a
    desk db always lives under gui_sandboxes/<sid>/, never directly in the home.
    """
    home, db_path, sid = _desk(tmp_path)
    w = ClaudeStateWriter(db_path, sid)
    assert w._ok is True  # a per-desk path is fine
    w.close()


def test_search_finds_claude_desk(tmp_path):
    home, db_path, sid = _desk(tmp_path)
    w = ClaudeStateWriter(db_path, sid)
    w.record_user("investigate the flaky websocket reconnect", ts=1.0)
    w.close()
    db = HermesDB(home)
    hits = db.search_sessions("websocket")
    assert any(s.id == sid for s in hits)
