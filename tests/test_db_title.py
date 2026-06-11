"""Tests for HermesDB title inference (stripping the injected workspace prefix)."""
import sqlite3
from pathlib import Path

from agent_gui.db import HermesDB


def _make_db(tmp_path: Path, first_user_content: str, title: str = "") -> Path:
    """Create a minimal read-able state.db with one session + one user message."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT, started_at, ended_at, source, model, "
        "parent_session_id, title, message_count, input_tokens, output_tokens)"
    )
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_calls TEXT, tool_call_id TEXT, tool_name TEXT, timestamp TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("s1", "2026-05-29T00:00:00+00:00", None, "workbench", "m", None, title, 1, 0, 0),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?,?,?)",
        ("s1", "user", first_user_content),
    )
    conn.commit()
    conn.close()
    return tmp_path


_AUGMENTED = (
    "[Workspace paths:\n"
    "  - Terminal/bash (inside Docker): /workspace/foo\n"
    "  - vision_analyze and host file tools: /Users/x/foo\n"
    "Use the Docker path for terminal commands; use the host path for vision_analyze.\n"
    "Your task is provided below — start working on it immediately without reading any files first.]\n\n"
    "Write a joke about houses"
)

_COMPACT_WORKSPACE = (
    "[Workspace: all tools run inside Docker — use /workspace/ paths.\n"
    "  - Workspace root: /workspace/\n"
    "Your task is provided below — start working on it immediately "
    "without reading any files first.]\n\n"
    "Summarize this paper"
)


def test_infer_title_strips_workspace_prefix(tmp_path: Path):
    home = _make_db(tmp_path, _AUGMENTED)
    s = HermesDB(home).get_session("s1")
    assert s is not None
    assert s.title == "Write a joke about houses"


def test_infer_title_strips_compact_workspace_prefix(tmp_path: Path):
    home = _make_db(tmp_path, _COMPACT_WORKSPACE)
    s = HermesDB(home).get_session("s1")
    assert s is not None
    assert s.title == "Summarize this paper"


def test_infer_title_plain_query_unchanged(tmp_path: Path):
    home = _make_db(tmp_path, "Summarize this paper")
    s = HermesDB(home).get_session("s1")
    assert s is not None
    assert s.title == "Summarize this paper"


def test_explicit_title_is_preserved(tmp_path: Path):
    home = _make_db(tmp_path, _AUGMENTED, title="My Real Title")
    s = HermesDB(home).get_session("s1")
    assert s is not None
    assert s.title == "My Real Title"


def test_infer_title_strips_attached_image_marker(tmp_path: Path):
    home = _make_db(
        tmp_path,
        "[Attached image: Screenshot 2026-05-27 at 3.56.16 PM.png]  What is in this image",
    )
    s = HermesDB(home).get_session("s1")
    assert s is not None
    assert s.title == "What is in this image"
