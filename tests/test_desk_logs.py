"""Persisted Agent Console + Debug terminal logs.

The console/terminal WS streams are otherwise ephemeral: token/thinking/stderr
output is never in Hermes' state.db, and an interrupted turn isn't committed at
all — so reopening a desk after a refresh used to show nothing (interrupted) or
only the post-refresh tail (in-flight). The server now tees every broadcast into a
per-turn buffer (replayed to a reconnecting WS) and flushes it to a per-desk
on-disk log at each turn boundary so the full history survives reloads. These tests
pin that behaviour, and the no-duplication invariant between the settled log and
the replayed in-flight turn."""
import types

import pytest

from agent_gui import server as srv


@pytest.fixture(autouse=True)
def _clean_state():
    stores = (srv._console_turn_buf, srv._terminal_turn_buf, srv._session_workspaces)
    for s in stores:
        s.clear()
    srv._log_seeded.clear()
    saved_db = srv._db_ref
    yield
    for s in stores:
        s.clear()
    srv._log_seeded.clear()
    srv._db_ref = saved_db


def _msg(role, content="", tool_calls=None, tool_call_id=None, tool_name=None):
    return types.SimpleNamespace(role=role, content=content, tool_calls=tool_calls or [],
                                 tool_call_id=tool_call_id, tool_name=tool_name)


# One committed prior turn: assistant runs a shell command and gets a result.
PRIOR_MSGS = [
    _msg("assistant", content="checking", tool_calls=[
        {"type": "function", "id": "c1",
         "function": {"name": "terminal", "arguments": '{"command": "ls /prior"}'}}]),
    _msg("tool", content='{"output": "prior_file.txt"}', tool_call_id="c1", tool_name="terminal"),
]


class _FakeDB:
    def _desk_db(self, sid):
        return True

    def get_desk_messages(self, sid, limit=5000):
        return list(PRIOR_MSGS)

    def get_messages(self, sid, limit=5000):
        return list(PRIOR_MSGS)


def _setup_desk(tmp_path, sid):
    """Mirror the real layout: gui_sandboxes/<sid>/docker/default/workspace."""
    base = tmp_path / "gui_sandboxes" / sid
    ws = base / "docker" / "default" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    srv._session_workspaces[sid] = str(ws)
    return base, ws


def test_log_dir_is_private_home_outside_workspace(tmp_path):
    base, ws = _setup_desk(tmp_path, "deskA")
    # The log dir is the desk's private HERMES_HOME, NOT the agent's /workspace —
    # so logs are invisible to the agent/Files tab and removed with the sandbox.
    assert srv._desk_state_dir("deskA") == base
    log = srv._desk_log_path("deskA", "terminal")
    assert log == base / srv._TERMINAL_LOG_NAME
    assert ws not in log.parents


def test_seed_then_inflight_replay_then_flush_no_dup(tmp_path):
    srv._db_ref = _FakeDB()
    sid = "deskB"
    _setup_desk(tmp_path, sid)

    # Seed at turn start from the DB (prior committed turn).
    srv._ensure_log_seeded(sid)
    assert "ls /prior" in srv._read_desk_log(sid, "terminal")
    assert "prior_file.txt" in srv._read_desk_log(sid, "console")  # shell I/O only

    # In-flight turn: buffered for WS replay, NOT yet on disk.
    srv._buffer_turn_log(sid, "terminal", "TOKEN-1 ")
    srv._buffer_turn_log(sid, "terminal", "TOKEN-2")
    assert srv._turn_log_text(sid, "terminal") == "TOKEN-1 TOKEN-2"
    assert "TOKEN-1" not in srv._read_desk_log(sid, "terminal")

    # Turn boundary flushes the buffer to disk and clears it — exactly once.
    srv._flush_turn_log(sid)
    log = srv._read_desk_log(sid, "terminal")
    assert "TOKEN-1 TOKEN-2" in log
    assert srv._turn_log_text(sid, "terminal") == ""        # buffer cleared
    assert log.count("ls /prior") == 1                      # seed not duplicated
    assert log.count("TOKEN-1") == 1                        # in-flight not duplicated


def test_interrupted_turn_is_persisted(tmp_path):
    srv._db_ref = _FakeDB()
    sid = "deskC"
    _setup_desk(tmp_path, sid)
    srv._ensure_log_seeded(sid)
    # A turn the user stops mid-stream never reaches the DB; the pump's finally
    # still flushes it, so reopening the desk shows it instead of an empty panel.
    srv._buffer_turn_log(sid, "terminal", "partial reply before Stop")
    srv._flush_turn_log(sid)
    assert "partial reply before Stop" in srv._read_desk_log(sid, "terminal")


def test_reseed_after_restart_does_not_duplicate(tmp_path):
    srv._db_ref = _FakeDB()
    sid = "deskD"
    _setup_desk(tmp_path, sid)
    srv._ensure_log_seeded(sid)
    srv._buffer_turn_log(sid, "terminal", "live output")
    srv._flush_turn_log(sid)

    srv._log_seeded.discard(sid)        # simulate a server restart (flag reset)
    srv._ensure_log_seeded(sid)         # log already exists → must not reseed
    log = srv._read_desk_log(sid, "terminal")
    assert log.count("ls /prior") == 1
    assert log.count("live output") == 1


def test_ondisk_log_capped_keeps_tail(tmp_path):
    sid = "deskE"
    _setup_desk(tmp_path, sid)
    srv._log_seeded.add(sid)            # skip seeding
    srv._append_desk_log(sid, "terminal", "X" * (srv._DESK_LOG_MAX_BYTES + 500_000))
    srv._append_desk_log(sid, "terminal", "TAIL_MARKER_END")
    size = srv._desk_log_path(sid, "terminal").stat().st_size
    assert size <= srv._DESK_LOG_MAX_BYTES
    assert srv._read_desk_log(sid, "terminal").endswith("TAIL_MARKER_END")


def test_inflight_buffer_capped(tmp_path):
    sid = "deskF"
    _setup_desk(tmp_path, sid)
    for _ in range((srv._TURN_LOG_MAX_CHARS // 1000) + 50):
        srv._buffer_turn_log(sid, "terminal", "y" * 1000)
    # Oldest chunks are dropped so the in-memory buffer can't grow without bound.
    assert len(srv._turn_log_text(sid, "terminal")) <= srv._TURN_LOG_MAX_CHARS + 1000


def test_no_log_falls_back_to_db_backfill(tmp_path):
    # A desk that never streamed under this server has no log file; the reader
    # returns "" so the REST endpoint falls back to the DB reconstruction.
    _setup_desk(tmp_path, "deskG")
    assert srv._read_desk_log("deskG", "terminal") == ""
    assert srv._read_desk_log("deskG", "console") == ""
