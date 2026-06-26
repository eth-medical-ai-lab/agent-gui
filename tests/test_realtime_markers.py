"""Real per-event time markers must stay aligned 1:1 with the feed events they
overlay.

Hermes batch-flushes a whole turn to its DB with one clustered timestamp, so the
genuine per-event times come only from the worker's live stream, recorded as
ordered per-kind markers (`_record_worker_evt_time`) and overlaid onto the parsed
feed FIFO-per-kind (`_apply_real_times`). If a token run produces NO message event
(whitespace-only or ≤ MIN_MESSAGE_LEN chars — common right before a tool call) it
must NOT record a surplus 'message' marker: a surplus shifts every later message
onto an earlier run's time, which desynced agent messages from the tool calls
around them (an agent line showing an earlier time than the write_file it
preceded). Mirrors the already-fixed thinking-marker alignment.
"""
import json
from datetime import datetime, timezone

import pytest

from agent_gui import server as srv
from agent_gui.activity_parser import parse_activity
from agent_gui.db import Message


@pytest.fixture(autouse=True)
def _clean_state():
    srv._session_event_times.clear()
    yield
    srv._session_event_times.clear()


def _replay(sid: str, stream: list[dict]) -> None:
    """Feed a worker event stream through the per-event time-marker recorder."""
    state: dict = {}
    for evt in stream:
        srv._record_worker_evt_time(sid, evt, state)


def _kinds(sid: str) -> list[str]:
    return [k for k, _ts, _name in srv._session_event_times.get(sid, [])]


def _msg(role, content=None, tool_calls=None, tool_name=None, tool_call_id=None, mid=1):
    return Message(id=mid, session_id="s1", role=role, content=content,
                   timestamp="2026-06-24T20:36:55+00:00", tool_calls=tool_calls or [],
                   tool_name=tool_name, tool_call_id=tool_call_id)


def _tc(name, args, tc_id="tc1"):
    return {"type": "function", "id": tc_id,
            "function": {"name": name, "arguments": json.dumps(args)}}


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


# ── marker recording ────────────────────────────────────────────────────────────

def test_whitespace_token_run_records_no_message_marker():
    """A stray-space run before a tool call (no resulting message event) must not
    leave a surplus 'message' marker."""
    _replay("d", [
        {"type": "token", "text": " ", "ts": 101.0},      # whitespace only
        {"type": "tool_start", "name": "terminal", "ts": 102.0},
        {"type": "tool_done", "name": "terminal", "ts": 103.0},
    ])
    assert "message" not in _kinds("d")


def test_tiny_token_run_records_no_message_marker():
    """parse_activity drops content ≤ MIN_MESSAGE_LEN, so the marker must too."""
    _replay("d", [{"type": "token", "text": "ok", "ts": 5.0}])
    assert "message" not in _kinds("d")


def test_substantive_token_run_records_one_message_marker():
    _replay("d", [
        {"type": "token", "text": "Let me ", "ts": 10.0},
        {"type": "token", "text": "analyze the results", "ts": 10.2},
    ])
    assert _kinds("d") == ["message"]
    # Marker carries a real time, not the clustered flush time.
    assert srv._session_event_times["d"][0][1] == pytest.approx(10.0)


# ── parse ordering ──────────────────────────────────────────────────────────────

def test_parse_emits_message_before_tool_calls():
    """The assistant's visible text precedes its tool calls (matches the live
    stream), so a reload doesn't reorder them past their recorded times."""
    events = parse_activity([_msg("assistant",
                                  content="Let me write the v4 prompt now",
                                  tool_calls=[_tc("write_file", {"path": "/workspace/v4.txt"})])])
    types = [e.event_type for e in events]
    assert types.index("message") < types.index("tool_call")


# ── integration: the desync the user reported ────────────────────────────────────

def test_message_time_stays_synced_with_its_tool_call():
    """End-to-end: an earlier whitespace run + a real 'text then write_file' step.
    The message must take its OWN time (110) and render just before the write_file
    it preceded (111) — not an earlier run's time."""
    sid = "desk"
    _replay(sid, [
        {"type": "token", "text": " ", "ts": 101.0},                  # surplus source
        {"type": "tool_start", "name": "terminal", "ts": 102.0},
        {"type": "tool_done", "name": "terminal", "ts": 103.0},
        {"type": "token", "text": "Let me write the v4 prompt now", "ts": 110.0},
        {"type": "tool_start", "name": "write_file", "ts": 111.0},
        {"type": "tool_done", "name": "write_file", "ts": 112.0},
    ])
    # DB rows as Hermes would flush them (all share the clustered turn time).
    events = parse_activity([
        _msg("assistant", content="", tool_calls=[_tc("terminal", {"command": "ls"}, "a")], mid=1),
        _msg("tool", content="files…", tool_name="terminal", tool_call_id="a", mid=2),
        _msg("assistant", content="Let me write the v4 prompt now",
             tool_calls=[_tc("write_file", {"path": "/workspace/v4.txt"}, "b")], mid=3),
        _msg("tool", content="written", tool_name="write_file", tool_call_id="b", mid=4),
    ])
    srv._apply_real_times(events, sid)

    by_type = {}
    for e in events:
        by_type.setdefault(e.event_type, []).append(e)
    agent_msg = by_type["message"][0]
    write_call = next(e for e in events if e.tool_name == "write_file" and e.event_type == "tool_call")

    # The message got its OWN recorded time (110), not the earlier whitespace run.
    assert agent_msg.time_exact is True
    assert _epoch(agent_msg.timestamp) == pytest.approx(110.0)
    # …and it sits just before the write_file it preceded — in sync, not 9s stale.
    assert _epoch(agent_msg.timestamp) < _epoch(write_call.timestamp)
    assert events.index(agent_msg) < events.index(write_call)


# ── user-message markers must never push a prompt later than its DB time ──────────

def test_user_message_marker_not_applied_when_later_than_db_time():
    """After a mid-desk server restart only the resume turn's user_message marker
    survives in memory, but the original prompt's user row is still in the DB with
    its real (earlier) turn-start time. FIFO-matching must NOT hand that later
    marker to the earlier prompt — it belongs to the resume message, so the prompt
    keeps its own (earlier) DB time and the resume message claims the marker."""
    sid = "restart"
    srv._record_event_time(sid, "user_message", 500.0)   # resume turn's marker only
    prompt = _msg("user", content="the original task", mid=1)
    prompt.timestamp = _iso(100.0)                        # persisted at turn-start
    resume = _msg("user", content="Continue.", mid=2)
    resume.timestamp = _iso(501.0)
    events = parse_activity([prompt, resume])
    srv._apply_real_times(events, sid)

    by_detail = {e.detail: e for e in events}
    # Prompt keeps its earlier DB time (no later marker stolen) and stays approximate.
    assert _epoch(by_detail["the original task"].timestamp) == pytest.approx(100.0)
    assert by_detail["the original task"].time_exact is False
    # The resume message claims the marker (501 → 500, refined earlier).
    assert _epoch(by_detail["Continue."].timestamp) == pytest.approx(500.0)
    assert by_detail["Continue."].time_exact is True


def test_user_message_marker_refines_earlier_when_aligned():
    """The guard only rejects markers LATER than the DB time — an in-order marker
    (recorded at turn-start, so <= the DB persist time) must still apply and mark
    the user message exact."""
    sid = "aligned"
    srv._record_event_time(sid, "user_message", 99.0)    # turn-start, just before persist
    prompt = _msg("user", content="do the task", mid=1)
    prompt.timestamp = _iso(100.0)                        # DB persist a beat later
    events = parse_activity([prompt])
    srv._apply_real_times(events, sid)
    assert events[0].time_exact is True
    assert _epoch(events[0].timestamp) == pytest.approx(99.0)
