"""
Worker that runs a **Claude Code agent** via the Claude Agent SDK, emitting the
SAME newline-delimited JSON event protocol as ``hermes_worker.py`` so the FastAPI
server can stream it over the activity WebSocket *unchanged* (the server never
imports the agent — it only reads these stdout events and writes stdin commands).

Run with a Python (>=3.10) that has ``claude-agent-sdk`` installed AND the
``claude`` CLI on PATH — i.e. the GUI conda env after
``pip install claude-agent-sdk``, NOT the Hermes venv.

Event shapes (identical to hermes_worker.py — the server is agent-agnostic):
  {"type": "session_id", "id": "<id>"}      — once the SDK reports the session id
  {"type": "log",        "msg": "..."}      — status / diagnostics
  {"type": "token",      "text": "..."}     — streaming assistant text delta
  {"type": "thinking",   "text": "..."}     — streaming reasoning delta
  {"type": "tool_start", "name": "...",
                         "args": "..."}     — tool call starting (Read/Bash/Edit/…)
  {"type": "tool_done",  "name": "...",
                         "result": "..."}   — tool finished (result truncated)
  {"type": "done"}                          — one-shot session complete
  {"type": "turn_done"}                     — turn boundary (persistent mode)
  {"type": "error",      "msg": "..."}      — fatal; worker exits 1

CLI (mirrors hermes_worker.py so the server spawn is a drop-in):
  claude_worker.py <message>                       — one-shot, new session
  claude_worker.py --resume <session_id> <message> — one-shot, resume
  claude_worker.py --persistent [<session_id>]     — long-lived; turns arrive as
                                                     JSON commands on stdin:
                                                       {"cmd":"run","message":"..."}
                                                       {"cmd":"interrupt"}
                                                       {"cmd":"shutdown"}

Design notes:
  - Token/thinking text streams from the raw ``StreamEvent`` deltas
    (``include_partial_messages=True``); tool calls come from the *complete*
    ``AssistantMessage`` blocks (we need the parsed tool input), and tool results
    from the subsequent ``UserMessage``. We never emit text from both the deltas
    and the complete blocks — that would double every token.
  - ``interrupt`` is handled OUT OF BAND from the reader thread (it must reach the
    SDK while a turn is mid-stream), exactly like hermes_worker's interrupt path.
  - Auth: the SDK shells out to the ``claude`` binary, which resolves credentials
    like the interactive CLI. The CLI's precedence is API key > OAuth token >
    ``/login``, so this worker SCRUBS ``ANTHROPIC_API_KEY`` (plus
    ``ANTHROPIC_AUTH_TOKEN`` and the Bedrock/Vertex/Foundry flags) from its env on
    startup — a Claude SDK desk is OAuth / subscription-only and can't be silently
    switched onto API billing or a stale key that points at a disabled org. Auth
    therefore resolves to ``CLAUDE_CODE_OAUTH_TOKEN`` (from ``claude setup-token``)
    or an existing ``claude /login`` subscription.
"""

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

# Saved before anything else so emit() always writes the real worker→server pipe.
_real_stdout = sys.stdout

# Stream token/thinking text from StreamEvent deltas (live), not from the complete
# AssistantMessage blocks — avoids double-emitting every token.
_PARTIAL = True

try:
    from claude_agent_sdk import (  # noqa: PLC0415
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        StreamEvent,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    _IMPORT_ERR = None
except Exception as exc:  # noqa: BLE001 — surfaced as an "error" event in main()
    _IMPORT_ERR = exc

# Credentials that outrank OAuth / `claude /login` in the claude CLI's precedence
# (API key > OAuth token > /login). A Claude SDK desk is subscription-only; the SDK
# inherits this process's full os.environ (its transport merges os.environ then
# options.env — override-only, it can't delete keys), so we drop these from our own
# env before connecting. Kept identical to server._CLAUDE_OAUTH_ONLY_SCRUB_KEYS.
OAUTH_ONLY_SCRUB_KEYS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
)


def _force_oauth_only(env=None):
    """Strip every credential that outranks OAuth so the claude CLI falls through to
    ``CLAUDE_CODE_OAUTH_TOKEN`` / `claude /login`. Mutates and returns the mapping
    (defaults to ``os.environ``)."""
    target = os.environ if env is None else env
    for k in OAUTH_ONLY_SCRUB_KEYS:
        target.pop(k, None)
    return target

# ── Per-desk persistence ─────────────────────────────────────────────────────
# Give Claude desks the SAME per-desk state.db a Hermes desk gets, so the GUI's
# db.py reader surfaces their history / Overview / Files / counts / search with no
# server changes. Best-effort: if the module can't be imported, the worker still
# streams live — it just doesn't persist.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from agent_gui.claude_state import ClaudeStateWriter
except Exception:  # noqa: BLE001
    ClaudeStateWriter = None


def _make_writer() -> "ClaudeStateWriter | None":
    """A state.db writer for this desk, or None when persistence can't/shouldn't run.

    Writes ``$HERMES_HOME/state.db`` — the exact per-desk path db.py reads. The
    session row id is the GUI/desk id (``HERMES_SESSION_ID`` or the home dir name),
    mirroring a Hermes desk's root session id == desk id. Refuses to write the
    SHARED ``~/.hermes/state.db`` so a standalone run can't mix into other desks.
    """
    if ClaudeStateWriter is None:
        return None
    home = os.environ.get("HERMES_HOME", "").strip()
    if not home:
        return None
    home_path = Path(home)
    try:
        if home_path.resolve() == (Path.home() / ".hermes").resolve():
            return None  # shared home, not a per-desk sandbox — skip
    except OSError:
        pass
    sid = os.environ.get("HERMES_SESSION_ID", "").strip() or home_path.name
    if not sid:
        return None
    return ClaudeStateWriter(
        home_path / "state.db", sid,
        model=os.environ.get("CLAUDE_AGENT_MODEL", "").strip(),
        cwd=os.environ.get("HERMES_WORKDIR") or None,
    )


def emit(obj: dict) -> None:
    """Write one NDJSON event to the real stdout the server reads."""
    # Stamp the wall-clock time so the server records true per-event timing
    # (matches hermes_worker — Hermes batch-flushes its DB with one clustered ts).
    obj.setdefault("ts", time.time())
    _real_stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    _real_stdout.flush()


def _truncate(s: str, n: int = 4000) -> str:
    return s if len(s) <= n else s[:n] + f"… ({len(s)} chars)"


def _stringify(content) -> str:
    """Flatten SDK content (str | list[block]) to text for a tool_done result."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                parts.append(blk.get("text", "") if blk.get("type") == "text"
                             else json.dumps(blk, default=str))
            else:
                parts.append(str(blk))
        return "\n".join(p for p in parts if p)
    return str(content)


def _handle_message(msg, tool_names: dict, state: dict) -> dict:
    """Translate one SDK message into emit() events, and persist it to state.db.

    ``tool_names`` maps tool_use_id → tool name so a later tool_result can name
    its tool. ``state`` carries the discovered session id (emitted once) and the
    optional ``writer`` (a ClaudeStateWriter, or None when persistence is off).
    Persistence reads the COMPLETE messages (AssistantMessage / UserMessage /
    ResultMessage) — independent of the live token/thinking deltas — so the DB
    holds the final text exactly once. Returns {"done": bool, "error": str|None}.
    """
    out = {"done": False, "error": None}
    writer = state.get("writer")

    if isinstance(msg, SystemMessage):
        if msg.subtype == "init":
            sid = (msg.data or {}).get("session_id")
            if sid and not state.get("sid"):
                state["sid"] = sid
                emit({"type": "session_id", "id": sid})
                if writer is not None:
                    writer.set_meta("claude_session_id", sid)
        return out

    if isinstance(msg, StreamEvent):
        ev = msg.event or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta" and delta.get("text"):
                emit({"type": "token", "text": delta["text"]})
            elif dt == "thinking_delta" and delta.get("thinking"):
                emit({"type": "thinking", "text": delta["thinking"]})
        return out

    if isinstance(msg, AssistantMessage):
        text_parts: list[str] = []
        think_parts: list[str] = []
        tool_calls: list[dict] = []
        for blk in msg.content:
            if isinstance(blk, ToolUseBlock):
                tool_names[blk.id] = blk.name
                try:
                    args = json.dumps(blk.input, ensure_ascii=False, default=str)
                except Exception:
                    args = str(blk.input)
                emit({"type": "tool_start", "name": blk.name, "args": _truncate(args)})
                # Hermes/OpenAI shape so activity_parser + file_parser read it
                # unchanged (arguments is a JSON STRING, exactly as Hermes persists).
                tool_calls.append({"id": blk.id, "type": "function",
                                   "function": {"name": blk.name, "arguments": args}})
            elif isinstance(blk, TextBlock) and blk.text:
                text_parts.append(blk.text)
                if not _PARTIAL:
                    emit({"type": "token", "text": blk.text})
            elif isinstance(blk, ThinkingBlock) and blk.thinking:
                think_parts.append(blk.thinking)
                if not _PARTIAL:
                    emit({"type": "thinking", "text": blk.thinking})
        if writer is not None and (text_parts or think_parts or tool_calls):
            writer.record_assistant(text="".join(text_parts),
                                    reasoning="".join(think_parts),
                                    tool_calls=tool_calls or None)
        return out

    if isinstance(msg, UserMessage):
        if isinstance(msg.content, list):
            for blk in msg.content:
                if isinstance(blk, ToolResultBlock):
                    name = tool_names.get(blk.tool_use_id, "tool")
                    full = _stringify(blk.content)
                    ev = {"type": "tool_done", "name": name, "result": _truncate(full)}
                    if blk.is_error:
                        ev["is_error"] = True
                    emit(ev)
                    if writer is not None:
                        writer.record_tool_result(blk.tool_use_id, name, full)
        return out

    if isinstance(msg, ResultMessage):
        out["done"] = True
        if msg.session_id and not state.get("sid"):
            state["sid"] = msg.session_id
            emit({"type": "session_id", "id": msg.session_id})
            if writer is not None:
                writer.set_meta("claude_session_id", msg.session_id)
        if writer is not None:
            usage = getattr(msg, "usage", None)
            in_tok = out_tok = 0
            if isinstance(usage, dict):
                in_tok = int(usage.get("input_tokens") or 0)
                out_tok = int(usage.get("output_tokens") or 0)
            writer.finalize_turn(input_tokens=in_tok, output_tokens=out_tok)
        if msg.is_error:
            out["error"] = msg.result or msg.subtype or "agent error"
        return out

    return out  # UserMessage(str), RateLimitEvent, etc. — nothing to render


async def _drain_turn(client, tool_names: dict, state: dict) -> dict:
    """Consume one turn's messages up to and including its ResultMessage."""
    info = {"error": None}
    async for msg in client.receive_response():
        r = _handle_message(msg, tool_names, state)
        if r["error"]:
            info["error"] = r["error"]
        if r["done"]:
            break
    return info


async def _run_oneshot(message: str, options) -> int:
    tool_names: dict = {}
    state: dict = {"writer": _make_writer()}
    info = {"error": None}
    try:
        async with ClaudeSDKClient(options=options) as client:
            if state["writer"] is not None and message and message.strip():
                state["writer"].record_user(message)
            await client.query(message)
            info = await _drain_turn(client, tool_names, state)
    finally:
        if state.get("writer") is not None:
            state["writer"].close()
    if info["error"]:
        emit({"type": "error", "msg": info["error"]})
        return 1
    emit({"type": "done"})
    return 0


async def _run_persistent(options) -> int:
    """Long-lived worker: connect once (context is retained across turns), then
    run turns as JSON commands arrive on stdin."""
    tool_names: dict = {}
    state: dict = {"writer": _make_writer()}
    client = ClaudeSDKClient(options=options)
    await client.connect()
    emit({"type": "log", "msg": "[claude-worker] persistent worker ready"})

    loop = asyncio.get_event_loop()
    run_q: asyncio.Queue = asyncio.Queue()

    async def _safe_interrupt() -> None:
        try:
            await client.interrupt()
        except Exception:
            pass

    def _emit_inspect_unsupported(cmd: dict) -> None:
        # The Inspect REPL drives Hermes read-only tools; not wired for Claude yet.
        # Answer so the /inspect endpoint returns instead of timing out.
        emit({"type": "inspect_result", "id": cmd.get("id"), "ok": False,
              "error": "inspect is not supported for Claude desks"})

    def _reader() -> None:
        # interrupt/inspect are handled inline (out of band) so they reach the SDK
        # while a turn is mid-stream; runs are queued and processed one at a time.
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                cmd = json.loads(raw)
            except Exception:
                continue
            c = cmd.get("cmd")
            if c == "run":
                loop.call_soon_threadsafe(run_q.put_nowait, cmd)
            elif c == "interrupt":
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_safe_interrupt()))
            elif c == "inspect":
                loop.call_soon_threadsafe(_emit_inspect_unsupported, cmd)
            elif c == "shutdown":
                loop.call_soon_threadsafe(run_q.put_nowait, {"cmd": "shutdown"})
            # inspect_stop: nothing to do
        loop.call_soon_threadsafe(run_q.put_nowait, {"cmd": "shutdown"})

    threading.Thread(target=_reader, daemon=True).start()

    while True:
        cmd = await run_q.get()
        if cmd.get("cmd") == "shutdown":
            break
        try:
            message = cmd.get("message", "")
            if state.get("writer") is not None and message and message.strip():
                state["writer"].record_user(message)
            await client.query(message)
            await _drain_turn(client, tool_names, state)
        except Exception as exc:  # noqa: BLE001 — a failed turn must not kill the worker
            emit({"type": "error", "msg": str(exc), "traceback": traceback.format_exc()})
        emit({"type": "turn_done"})

    if state.get("writer") is not None:
        state["writer"].close()
    try:
        await client.disconnect()
    except Exception:
        pass
    return 0


def main() -> None:
    argv = sys.argv[1:]
    resume = None
    message = None
    persistent = False
    if argv and argv[0] == "--persistent":
        persistent = True
        if len(argv) >= 2:
            resume = argv[1]
    elif len(argv) >= 3 and argv[0] == "--resume":
        resume, message = argv[1], argv[2]
    elif argv:
        message = argv[0]
    else:
        emit({"type": "error", "msg": "usage: claude_worker.py "
              "[--resume <id> <msg> | --persistent [<id>] | <msg>]"})
        sys.exit(1)

    emit({"type": "log", "msg": "[claude-worker] starting…"})

    if _IMPORT_ERR is not None:
        emit({"type": "error", "msg":
              f"claude-agent-sdk import failed: {_IMPORT_ERR} — "
              f"`pip install claude-agent-sdk` in the GUI env (needs Python >=3.10 "
              f"and the `claude` CLI on PATH)"})
        sys.exit(1)

    # Claude SDK desks are OAuth / `claude /login` subscription-only — strip every
    # credential that outranks OAuth so a stale/disabled key can't silently shadow
    # the login or switch the desk onto API billing. The server already scrubs these
    # for the desk env; this backstops the other spawn paths and standalone runs.
    _force_oauth_only()

    cwd = os.environ.get("HERMES_WORKDIR") or os.getcwd()
    try:
        os.chdir(cwd)
    except Exception:
        pass

    kwargs = dict(
        cwd=cwd,
        permission_mode=os.environ.get("CLAUDE_AGENT_PERMISSION_MODE", "bypassPermissions"),
        include_partial_messages=_PARTIAL,
    )
    model = os.environ.get("CLAUDE_AGENT_MODEL")
    if model:
        kwargs["model"] = model
    if resume:
        kwargs["resume"] = resume
    options = ClaudeAgentOptions(**kwargs)

    try:
        rc = asyncio.run(_run_persistent(options) if persistent
                         else _run_oneshot(message, options))
    except Exception as exc:  # noqa: BLE001
        emit({"type": "error", "msg": str(exc), "traceback": traceback.format_exc()})
        rc = 1
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
