"""Orphaned-feed preservation: turns that error or get killed mid-flight never
reach Hermes' DB, so the server converts their live replay buffer into persistent
feed events and merges them into every DB-derived activity snapshot. Without this
the Feed visibly "sweeps away" everything the user just watched stream."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_gui import server as srv
from agent_gui.activity_parser import ActivityEvent, parse_activity
from agent_gui.db import Message


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset the module-level session state these tests touch."""
    stores = (srv._live_event_buffer, srv._orphan_feed_events,
              srv._session_workspaces, srv._session_event_times)
    for s in stores:
        s.clear()
    srv._session_turn_interrupted.clear()
    yield
    for s in stores:
        s.clear()
    srv._session_turn_interrupted.clear()


def _feed_buffer(sid: str, events: list[dict]) -> None:
    for evt in events:
        srv._buffer_live_event(sid, evt)


def test_preserve_errored_turn_keeps_stream_and_appends_error():
    sid = "desk1"
    _feed_buffer(sid, [
        {"type": "thinking", "text": "let me think", "ts": 100.0},
        {"type": "token", "text": "Partial ", "ts": 101.0},
        {"type": "token", "text": "answer"},
        {"type": "tool_start", "name": "bash", "ts": 102.0},
        {"type": "tool_done", "name": "bash", "ts": 103.0,
         "args": json.dumps({"command": "ls -la"}), "result": "ok"},
    ])
    srv._preserve_orphan_turn(sid, error_msg="API connection refused")

    orphans = srv._load_orphan_feed(sid)
    types = [o["event_type"] for o in orphans]
    assert types == ["thinking_start", "message", "tool_call", "tool_result", "error"]
    msg = orphans[1]
    assert msg["detail"] == "Partial answer"          # token run coalesced
    assert msg["time_exact"] is True
    call = orphans[2]
    assert call["detail"] == "ls -la"                 # backfilled from tool_done args
    err = orphans[-1]
    assert err["is_error"] is True
    assert "API connection refused" in err["detail"]
    # Buffer is consumed so a reconnect won't replay it on top of the orphans.
    assert sid not in srv._live_event_buffer


def test_preserve_interrupted_turn_adds_marker():
    sid = "desk2"
    _feed_buffer(sid, [{"type": "token", "text": "halfway th", "ts": 50.0}])
    srv._preserve_orphan_turn(sid, interrupted=True)
    orphans = srv._load_orphan_feed(sid)
    assert [o["event_type"] for o in orphans] == ["message", "message"]
    assert orphans[0]["detail"] == "halfway th"
    assert orphans[1]["title"] == "Interrupted"


def test_preserve_noop_when_nothing_happened():
    sid = "desk3"
    srv._preserve_orphan_turn(sid)          # empty buffer, no error, no interrupt
    assert srv._load_orphan_feed(sid) == []


def test_orphans_survive_restart_via_sidecar(tmp_path: Path):
    sid = "desk4"
    ws = tmp_path / "ws"
    ws.mkdir()
    srv._session_workspaces[sid] = str(ws)
    _feed_buffer(sid, [{"type": "token", "text": "saved text", "ts": 10.0}])
    srv._preserve_orphan_turn(sid, error_msg="boom")
    sidecar = ws / srv._ORPHAN_FEED_MARKER
    assert sidecar.exists()
    # Simulate a server restart: memory gone, sidecar reloads on demand.
    srv._orphan_feed_events.clear()
    srv._session_workspaces.clear()
    orphans = srv._load_orphan_feed(sid, ws)
    assert [o["detail"] for o in orphans][:1] == ["saved text"]


def _ev(ts: str, event_type: str, detail: str) -> ActivityEvent:
    return ActivityEvent(timestamp=ts, event_type=event_type, icon="x",
                         title=event_type, detail=detail)


def test_merge_inserts_orphans_in_time_order():
    sid = "desk5"
    srv._orphan_feed_events[sid] = [
        srv._orphan_event(200.0, "message", "🤖", "Agent", "partial reply"),
        srv._orphan_event(201.0, "error", "❌", "Error", "rate limited"),
    ]
    t1 = srv._orphan_event(100.0, "x", "x", "x", "")["timestamp"]
    t3 = srv._orphan_event(300.0, "x", "x", "x", "")["timestamp"]
    db_events = [_ev(t1, "user_message", "do the thing"),
                 _ev(t3, "user_message", "try again")]
    merged = srv._merge_orphan_feed(db_events, sid)
    assert [e.detail for e in merged] == [
        "do the thing", "partial reply", "rate limited", "try again"]
    # Orphans came back as real ActivityEvents with exact times.
    assert all(isinstance(e, ActivityEvent) for e in merged)
    assert merged[1].time_exact is True


def test_merge_drops_orphans_already_committed_to_db():
    # Race cover: a turn flagged interrupted that actually committed would
    # otherwise show its reply twice.
    sid = "desk6"
    srv._orphan_feed_events[sid] = [
        srv._orphan_event(200.0, "message", "🤖", "Agent", "same text"),
    ]
    t1 = srv._orphan_event(150.0, "x", "x", "x", "")["timestamp"]
    db_events = [_ev(t1, "message", "same text")]
    merged = srv._merge_orphan_feed(db_events, sid)
    assert [e.detail for e in merged] == ["same text"]


def test_merge_without_orphans_returns_input():
    db_events = [_ev("2026-01-01T00:00:00+00:00", "message", "hi")]
    assert srv._merge_orphan_feed(db_events, "no-such-desk") is db_events


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def test_prompt_stays_before_orphans_after_restart():
    """End-to-end repro of the reported desk bug. Turn 1 was interrupted (its
    streamed work saved as orphans, its user row committed to the DB). The server
    restarted — wiping in-memory markers — then a 'Continue.' resume ran. With only
    the resume turn's user_message marker left in memory, _apply_real_times must
    not hand it to the earlier prompt; the merged feed must start with the prompt,
    then the interrupted turn's orphans, then the resume + its work."""
    sid = "deskR"
    # Interrupted turn 1's preserved events (sit between the prompt and the resume).
    srv._orphan_feed_events[sid] = [
        srv._orphan_event(160.0, "message", "🤖", "Agent", "exploring the repo"),
        srv._orphan_event(300.0, "message", "⏸", "Interrupted",
                          "Turn stopped before completion."),
    ]
    # Post-restart markers: only the resume turn's (its user_message marker is
    # LATER than the original prompt's DB time — the trap).
    srv._record_event_time(sid, "user_message", 360.0)
    srv._record_event_time(sid, "message", 362.0)

    db = [
        Message(id=1, session_id=sid, role="user", content="# the original task",
                timestamp=_iso(100.0)),                       # prompt, turn-start time
        Message(id=2, session_id=sid, role="user", content="Continue.",
                timestamp=_iso(361.0)),                       # resume prompt
        Message(id=3, session_id=sid, role="assistant",
                content="On it — starting the task now", timestamp=_iso(999.0)),  # batch time
    ]
    events = parse_activity(db)
    srv._apply_real_times(events, sid)
    merged = srv._merge_orphan_feed(events, sid)

    details = [e.detail for e in merged]
    assert details == [
        "# the original task",      # prompt FIRST …
        "exploring the repo",       # … then the interrupted turn's orphans …
        "Turn stopped before completion.",
        "Continue.",                # … then the resume …
        "On it — starting the task now",  # … and the resumed work.
    ]
    # The prompt kept its real DB turn-start time, not the resume marker.
    prompt = merged[0]
    assert datetime.fromisoformat(prompt.timestamp).timestamp() == pytest.approx(100.0)


def test_worker_surfaces_swallowed_api_failure(monkeypatch):
    """Hermes returns {'failed': True, 'error': ...} instead of raising when API
    retries are exhausted (HTTP 502 etc.) — the worker must turn that into an
    explicit "error" event or the turn ends looking clean and the feed shows
    nothing but the user message."""
    import sys
    real = sys.stdout
    from agent_gui import hermes_worker as hw
    sys.stdout = real
    captured: list[dict] = []
    monkeypatch.setattr(hw, "emit", captured.append)

    hw._emit_turn_failure({"failed": True, "error": "HTTP 502: Error code: 502",
                           "final_response": None, "completed": False})
    assert captured == [{"type": "error", "msg": "HTTP 502: Error code: 502"}]

    captured.clear()
    hw._emit_turn_failure({"failed": False, "final_response": "all good"})
    hw._emit_turn_failure(None)        # defensive: non-dict results
    hw._emit_turn_failure("response")
    assert captured == []


def test_preserve_drops_stale_realtime_markers_for_failed_turn():
    """The failed turn's live-recorded emit-times must not be handed to the next
    turn's DB events by _apply_real_times (it matches markers in order)."""
    sid = "desk7"
    srv._record_event_time(sid, "user_message", 99.0)
    srv._record_event_time(sid, "message", 101.5)       # belongs to the dying turn
    _feed_buffer(sid, [{"type": "token", "text": "dying turn", "ts": 101.0}])
    srv._preserve_orphan_turn(sid, interrupted=True)
    kinds = [k for k, _, _ in srv._session_event_times[sid]]
    assert kinds == ["user_message"]
