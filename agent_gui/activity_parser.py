"""Parse Hermes messages into a human-readable activity feed."""
import json
import re
from dataclasses import dataclass, field

from agent_gui.message_sanitize import strip_injected_prefix

from agent_gui.db import Message

TOOL_ICONS = {
    "bash": "⚡", "execute_command": "⚡", "terminal": "⚡", "run_command": "⚡",
    "write_file": "📝", "create_file": "📝", "file_write": "📝",
    "str_replace_editor": "✏️", "edit_file": "✏️",
    "read_file": "📖", "file_read": "📖",
    "web_search": "🔍", "search": "🔍",
    "browser": "🌐", "web_fetch": "🌐",
    "memory": "🧠", "remember": "🧠",
    "compress": "🗜️", "summarize": "🗜️",
    "delegate": "👥", "spawn_agent": "👥", "subagent": "👥",
    "skill_view": "📚", "skill_run": "📚",
    "python": "🐍",
    "default": "🔧",
}


@dataclass
class ActivityEvent:
    timestamp: str
    event_type: str   # tool_call | tool_result | message | user_message | error
    icon: str
    title: str
    detail: str
    tool_name: str = ""
    is_error: bool = False
    files_touched: list[str] = field(default_factory=list)
    # True only when `timestamp` is a real recorded emit-time. False means it's
    # Hermes's coarse batch-flush time (all events in a turn cluster together) —
    # the UI shows those as approximate rather than pretending they're exact.
    time_exact: bool = False


def _truncate(text: str, n: int = 160) -> str:
    text = str(text).strip().replace("\n", " ")
    return text[:n] + "…" if len(text) > n else text

def _clean(text: str) -> str:
    """Return text stripped but with newlines preserved (for message display)."""
    return str(text).strip()


def _tool_detail(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("bash", "execute_command", "run_command", "terminal"):
        return _truncate(tool_input.get("command", tool_input.get("cmd", str(tool_input))))
    if tool_name in ("write_file", "create_file", "file_write", "str_replace_editor", "edit_file", "read_file", "file_read"):
        path = tool_input.get("path") or tool_input.get("file_path") or tool_input.get("filename") or ""
        return path or _truncate(str(tool_input))
    if tool_name in ("web_search", "search"):
        return _truncate(tool_input.get("query", str(tool_input)))
    return _truncate(str(tool_input))


def _files_from_tool(tool_name: str, tool_input: dict) -> list[str]:
    for key in ("path", "file_path", "filename", "filepath"):
        val = tool_input.get(key)
        if val and isinstance(val, str):
            return [val]
    return []


def parse_activity(messages: list[Message]) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []
    tool_call_map: dict[str, dict] = {}  # id → {name, input}

    for msg in messages:
        ts = msg.timestamp

        # ── User message ─────────────────────────────────────────────────────
        if msg.role == "user":
            if msg.content and msg.content.strip():
                clean = strip_injected_prefix(msg.content)
                if clean:
                    events.append(ActivityEvent(
                        timestamp=ts,
                        event_type="user_message",
                        icon="👤",
                        title="User",
                        detail=_clean(clean),
                    ))

        # ── Assistant message (may have tool_calls and/or text) ───────────────
        elif msg.role == "assistant":
            # Reasoning/thinking trace — emit first (it precedes the response) as a
            # collapsible step so the full trace stays retrievable after the live
            # stream ends. Carries the complete (untruncated) text in `detail`.
            reasoning = (msg.reasoning_content or "").strip()
            if reasoning:
                events.append(ActivityEvent(
                    timestamp=ts,
                    event_type="thinking_start",
                    icon="💭",
                    title="Reasoning",
                    detail=reasoning,
                ))

            for tc in msg.tool_calls:
                if tc.get("type") != "function":
                    continue
                fn = tc.get("function", {})
                tool_name = fn.get("name", "unknown")
                args_raw = fn.get("arguments", "{}")
                try:
                    tool_input = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except Exception:
                    tool_input = {}

                tool_call_map[tc.get("id", "")] = {"name": tool_name, "input": tool_input}
                icon = TOOL_ICONS.get(tool_name, TOOL_ICONS["default"])
                files = _files_from_tool(tool_name, tool_input)

                events.append(ActivityEvent(
                    timestamp=ts,
                    event_type="tool_call",
                    icon=icon,
                    title=f"calling {tool_name}",
                    detail=_tool_detail(tool_name, tool_input),
                    tool_name=tool_name,
                    files_touched=files,
                ))

            if msg.content and msg.content.strip() and len(msg.content.strip()) > 5:
                text = msg.content.strip()
                is_compression = any(kw in text.lower() for kw in ("compressing", "context compressed", "summarizing context"))
                events.append(ActivityEvent(
                    timestamp=ts,
                    event_type="compression" if is_compression else "message",
                    icon="🗜️" if is_compression else "🤖",
                    title="Context compressed" if is_compression else "Agent",
                    detail=_truncate(text, 300) if is_compression else _clean(text),
                ))

        # ── Tool result ───────────────────────────────────────────────────────
        elif msg.role == "tool":
            call = tool_call_map.get(msg.tool_call_id or "", {})
            tool_name = msg.tool_name or call.get("name", "tool")
            content = msg.content or ""

            is_error = False
            try:
                result = json.loads(content)
                if isinstance(result, dict):
                    is_error = bool(result.get("error")) and not result.get("success", True)
            except Exception:
                pass

            icon = TOOL_ICONS.get(tool_name, TOOL_ICONS["default"])
            events.append(ActivityEvent(
                timestamp=ts,
                event_type="tool_result",
                icon="❌" if is_error else icon,
                title=f"{tool_name} {'failed' if is_error else 'done'}",
                detail=_truncate(content, 200),
                tool_name=tool_name,
                is_error=is_error,
            ))

    return events
