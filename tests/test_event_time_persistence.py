"""Per-event time markers must survive a server restart via the GUI-owned per-desk
store, so the Activity Overview/Feed keep real timing instead of collapsing to
Hermes's coarse batch-flush times.

The recording path stamps each streamed event with its real emit-time
(`_record_event_time`) and mirrors the markers to a private per-desk file at every
turn boundary (`_persist_event_times`). A fresh server folds them back into memory
once, before recording any new marker (`_load_event_times`), so timing is restored
without parsing agent.log or writing to Hermes's read-only state.db.
"""
import json
from datetime import datetime, timezone

import pytest

from agent_gui import server as srv
from agent_gui.activity_parser import parse_activity
from agent_gui.db import Message


@pytest.fixture
def desk(tmp_path):
    """A desk whose private store resolves to a temp dir, with clean marker state."""
    sid = "persist-desk"
    srv._session_event_times.clear()
    srv._event_times_loaded.clear()
    saved_ws = dict(srv._session_workspaces)
    saved_db = srv._db_ref
    srv._db_ref = None  # force the _desk_state_dir resolution path onto tmp
    srv._session_workspaces[sid] = str(tmp_path)
    yield sid, tmp_path
    srv._session_event_times.clear()
    srv._event_times_loaded.clear()
    srv._session_workspaces.clear()
    srv._session_workspaces.update(saved_ws)
    srv._db_ref = saved_db


def _simulate_restart() -> None:
    """Wipe the in-memory markers + load guard, as a fresh server process would
    (the on-disk store survives)."""
    srv._session_event_times.clear()
    srv._event_times_loaded.clear()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _msg(role, content=None, tool_calls=None, tool_name=None, tool_call_id=None, mid=1):
    return Message(id=mid, session_id="s", role=role, content=content,
                   timestamp="2026-06-24T20:36:55+00:00", tool_calls=tool_calls or [],
                   tool_name=tool_name, tool_call_id=tool_call_id)


def _tc(name, args, tc_id="tc1"):
    return {"type": "function", "id": tc_id,
            "function": {"name": name, "arguments": json.dumps(args)}}


# ── path + round-trip ─────────────────────────────────────────────────────────

def test_path_resolves_to_private_store(desk):
    sid, tmp = desk
    assert srv._event_times_path(sid) == tmp / srv._EVENT_TIMES_NAME


def test_persist_then_load_roundtrip(desk):
    sid, _ = desk
    srv._record_event_time(sid, "user_message", 100.0)
    srv._record_event_time(sid, "tool_call", 101.0, "bash")
    srv._record_event_time(sid, "tool_result", 102.0, "bash")
    srv._persist_event_times(sid)

    _simulate_restart()
    assert srv._session_event_times.get(sid) is None
    srv._load_event_times(sid)
    assert srv._session_event_times[sid] == [
        ("user_message", 100.0, ""),
        ("tool_call", 101.0, "bash"),
        ("tool_result", 102.0, "bash"),
    ]


def test_markers_survive_restart_end_to_end(desk):
    """The reported bug: after a restart, `_apply_real_times` must still overlay
    real per-event times instead of leaving the clustered flush time."""
    sid, _ = desk
    srv._record_event_time(sid, "user_message", 1000.0)
    srv._record_event_time(sid, "message", 1005.0)
    srv._record_event_time(sid, "tool_call", 1006.0, "write_file")
    srv._record_event_time(sid, "tool_result", 1060.0, "write_file")
    srv._persist_event_times(sid)

    _simulate_restart()  # in-memory markers gone, like a fresh server

    flush = _iso(1060.0)  # Hermes batch-flushes the whole turn at one instant
    rows = [
        _msg("user", content="Build it", mid=1),
        _msg("assistant", content="Writing the file now",
             tool_calls=[_tc("write_file", {"path": "/workspace/a.txt"}, "b")], mid=2),
        _msg("tool", content="ok", tool_name="write_file", tool_call_id="b", mid=3),
    ]
    rows[0].timestamp = _iso(1000.0)  # user row persisted at turn-start (real)
    rows[1].timestamp = flush
    rows[2].timestamp = flush
    events = parse_activity(rows)
    srv._apply_real_times(events, sid)  # loads persisted markers, overlays them

    by_type: dict = {}
    for e in events:
        by_type.setdefault(e.event_type, []).append(e)
    assert all(e.time_exact for e in events)  # every event recovered its real time
    assert _epoch(by_type["message"][0].timestamp) == pytest.approx(1005.0)
    assert _epoch(by_type["tool_result"][0].timestamp) == pytest.approx(1060.0)


# ── no duplication / ordering invariants ──────────────────────────────────────

def test_load_does_not_duplicate_live_markers(desk):
    """A desk that already streamed this run keeps its in-memory markers; the disk
    copy is a subset, so a stray load must NOT prepend/duplicate it."""
    sid, _ = desk
    srv._record_event_time(sid, "user_message", 100.0)
    srv._record_event_time(sid, "tool_call", 101.0, "bash")
    srv._persist_event_times(sid)
    before = list(srv._session_event_times[sid])

    srv._event_times_loaded.discard(sid)  # pretend the guard wasn't set yet
    srv._load_event_times(sid)
    assert srv._session_event_times[sid] == before


def test_user_message_record_folds_disk_in_first(desk):
    """After a restart, recording the resume turn's user_message must fold the prior
    run's persisted markers in BEFORE appending — otherwise the next persist would
    overwrite the file with only the resume marker, losing all prior timing."""
    sid, _ = desk
    srv._record_event_time(sid, "user_message", 50.0)
    srv._record_event_time(sid, "tool_call", 51.0, "bash")
    srv._persist_event_times(sid)

    _simulate_restart()
    srv._record_event_time(sid, "user_message", 500.0)  # resume turn — triggers fold
    kinds = [k for k, _t, _n in srv._session_event_times[sid]]
    assert kinds == ["user_message", "tool_call", "user_message"]

    srv._persist_event_times(sid)  # a fresh persist keeps BOTH runs' markers
    saved = json.loads(srv._event_times_path(sid).read_text())
    assert [row[0] for row in saved] == ["user_message", "tool_call", "user_message"]
    assert saved[-1][1] == pytest.approx(500.0)


# ── degenerate / safety cases ─────────────────────────────────────────────────

def test_persist_is_noop_without_markers(desk):
    sid, _ = desk
    srv._persist_event_times(sid)
    assert not srv._event_times_path(sid).exists()


def test_persist_does_not_wipe_good_file_on_empty_turn(desk):
    """An empty in-memory list must not delete a previously-written good file."""
    sid, _ = desk
    srv._record_event_time(sid, "user_message", 1.0)
    srv._persist_event_times(sid)
    path = srv._event_times_path(sid)
    assert path.exists()

    srv._session_event_times[sid] = []  # nothing recorded this turn
    srv._persist_event_times(sid)
    assert path.exists()
    assert json.loads(path.read_text()) == [["user_message", 1.0, ""]]


def test_atomic_write_leaves_no_tmp(desk):
    sid, tmp = desk
    srv._record_event_time(sid, "user_message", 1.0)
    srv._persist_event_times(sid)
    assert not (tmp / (srv._EVENT_TIMES_NAME + ".tmp")).exists()


def test_load_tolerates_corrupt_file(desk):
    """A torn/garbage file must degrade to 'no markers', not crash the read path."""
    sid, _ = desk
    srv._event_times_path(sid).write_text("{ not json", encoding="utf-8")
    _simulate_restart()
    srv._load_event_times(sid)  # must not raise
    assert srv._session_event_times.get(sid) is None
