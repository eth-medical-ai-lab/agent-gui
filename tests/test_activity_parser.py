"""Unit tests for agent_gui.activity_parser — pure parsing logic, no I/O."""
import json
import pytest
from agent_gui.db import Message
from agent_gui.activity_parser import parse_activity, ActivityEvent


def _msg(
    role: str,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    ts: str = "2024-01-01T00:00:00",
    mid: int = 1,
) -> Message:
    return Message(
        id=mid,
        session_id="s1",
        role=role,
        content=content,
        timestamp=ts,
        tool_calls=tool_calls or [],
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )


# ── parse_activity basics ──────────────────────────────────────────────────────


def test_empty_messages_returns_empty():
    assert parse_activity([]) == []


def test_user_message_produces_event():
    events = parse_activity([_msg("user", content="hello world")])
    assert any(e.event_type == "user_message" for e in events)


def test_assistant_text_message_produces_event():
    events = parse_activity([_msg("assistant", content="I will help you.")])
    types = {e.event_type for e in events}
    assert types & {"message", "user_message"}  # non-empty response produces at least one


def _tc(name: str, args: dict, tc_id: str = "tc1") -> dict:
    """Build an OpenAI-format tool call dict (the schema activity_parser expects)."""
    import json
    return {
        "type": "function",
        "id": tc_id,
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_tool_call_produces_tool_event():
    events = parse_activity([_msg("assistant", tool_calls=[_tc("bash", {"command": "ls"})])])
    tool_events = [e for e in events if e.event_type == "tool_call"]
    assert len(tool_events) >= 1
    assert tool_events[0].tool_name == "bash"


def test_tool_result_event():
    # tool role = result from a previous call
    events = parse_activity([
        _msg("tool", content="file.txt\ndir/", tool_name="bash", tool_call_id="tc1"),
    ])
    result_events = [e for e in events if e.event_type == "tool_result"]
    assert len(result_events) >= 1


def test_icons_assigned_for_known_tools():
    events = parse_activity([_msg("assistant", tool_calls=[
        _tc("write_file", {"path": "/workspace/x.py"})
    ])])
    assert any(e.icon == "📝" for e in events)


def test_unknown_tool_gets_default_icon():
    events = parse_activity([_msg("assistant", tool_calls=[
        _tc("some_unknown_tool_xyz", {})
    ])])
    tool_events = [e for e in events if e.event_type == "tool_call"]
    assert any(e.icon == "🔧" for e in tool_events)


def test_workspace_prefix_stripped_from_user_message():
    """The [Your workspace is ...] prefix injected by the server should be stripped."""
    content = "[Your workspace is /workspace/foo — task instructions are in TASK.md there.]\n\nActual task text"
    events = parse_activity([_msg("user", content=content)])
    user_events = [e for e in events if e.event_type == "user_message"]
    assert len(user_events) >= 1
    assert "workspace" not in user_events[0].title.lower() or "Actual task" in user_events[0].detail


def test_current_workspace_paths_prefix_stripped():
    """The current "[Workspace paths: … ]" wrapper must be stripped from the feed."""
    content = (
        "[Workspace paths:\n"
        "  - Terminal/bash (inside Docker): /workspace/foo\n"
        "  - vision_analyze and host file tools: /Users/x/foo\n"
        "Use the Docker path for terminal commands; use the host path for vision_analyze.\n"
        "Your task is provided below — start working on it immediately without reading any files first.]\n\n"
        "Write a joke about houses"
    )
    events = parse_activity([_msg("user", content=content)])
    user_events = [e for e in events if e.event_type == "user_message"]
    assert len(user_events) == 1
    assert user_events[0].detail == "Write a joke about houses"


def test_compact_workspace_prefix_stripped():
    """The current "[Workspace: …]" wrapper must be stripped from the feed."""
    content = (
        "[Workspace: all tools run inside Docker — use /workspace/ paths.\n"
        "  - Workspace root: /workspace/\n"
        "Your task is provided below — start working on it immediately "
        "without reading any files first.]\n\n"
        "Build a todo app"
    )
    events = parse_activity([_msg("user", content=content)])
    user_events = [e for e in events if e.event_type == "user_message"]
    assert len(user_events) == 1
    assert user_events[0].detail == "Build a todo app"


def test_attached_image_marker_stripped():
    """The frontend's "[Attached image: …]" marker must not show in the feed."""
    content = "[Attached image: foo.png]\nWhat is in this image"
    events = parse_activity([_msg("user", content=content)])
    user_events = [e for e in events if e.event_type == "user_message"]
    assert len(user_events) == 1
    assert user_events[0].detail == "What is in this image"


def test_workspace_and_attachment_both_stripped():
    content = (
        "[Workspace paths:\n  - x: /workspace/foo\nYour task is provided below.]\n\n"
        "[Attached image: a.png]\nDescribe it"
    )
    events = parse_activity([_msg("user", content=content)])
    user_events = [e for e in events if e.event_type == "user_message"]
    assert user_events[0].detail == "Describe it"


def test_tool_detail_bash_shows_command():
    events = parse_activity([_msg("assistant", tool_calls=[
        _tc("bash", {"command": "echo hello"})
    ])])
    tool_events = [e for e in events if e.event_type == "tool_call"]
    assert any("echo hello" in e.detail for e in tool_events)


def test_tool_detail_write_shows_path():
    events = parse_activity([_msg("assistant", tool_calls=[
        _tc("write_file", {"path": "/workspace/out.py"})
    ])])
    tool_events = [e for e in events if e.event_type == "tool_call"]
    assert any("/workspace/out.py" in e.detail for e in tool_events)


def test_timestamps_preserved():
    msg = _msg("user", content="hi", ts="2024-06-15T12:34:56")
    events = parse_activity([msg])
    assert any("2024-06-15" in e.timestamp for e in events)


def test_multiple_tool_calls_in_one_message():
    tcs = [
        _tc("bash",      {"command": "ls"},             "tc1"),
        _tc("read_file", {"path": "/workspace/x.py"},   "tc2"),
    ]
    events = parse_activity([_msg("assistant", tool_calls=tcs)])
    tool_events = [e for e in events if e.event_type == "tool_call"]
    assert len(tool_events) == 2


def test_json_encoded_content_blocks_parsed():
    """Hermes sometimes encodes assistant content as a JSON list of blocks."""
    blocks = json.dumps([{"type": "text", "text": "I'm working on it."}])
    events = parse_activity([_msg("assistant", content=blocks)])
    # Should surface the text somehow, not crash
    assert isinstance(events, list)
