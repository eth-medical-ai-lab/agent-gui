"""
Worker script run with the hermes venv Python.

Embeds AIAgent directly (no CLI layer) and emits newline-delimited JSON events
to stdout so the FastAPI server can stream them over the activity WebSocket.

Event shapes:
  {"type": "session_id", "id": "<id>"}           — first line, before any LLM call
  {"type": "log",        "msg": "..."}            — verbose status / init progress
  {"type": "token",      "text": "..."}           — streaming text delta
  {"type": "tool_start", "name": "..."}           — tool call starting
  {"type": "tool_done",  "name": "...",
                         "result": "..."}         — tool finished (result truncated)
  {"type": "thinking",   "text": "..."}           — reasoning/scratchpad delta
  {"type": "done"}                                — session complete
  {"type": "error",      "msg": "..."}            — fatal, worker exits 1

Design notes:
  - sys.stdout is captured early so all print() calls from hermes internals
    become "log" events; our emit() writes to the saved real_stdout.
  - agent._vprint is monkey-patched to bypass the stream-consumer suppression
    guard so we see the pre-API-call diagnostics (request size, token count,
    tool count, call timing) that are normally hidden when stream_delta_callback
    is registered.
  - quiet_mode=False enables the _vprint guard-paths in conversation_loop.py.
"""

import http.client
import http.server
import io
import json
import os
import re
import socketserver
import sys
import threading
import time
import traceback
import types
import urllib.parse
import uuid as _uuid_mod
from pathlib import Path


def _process_think_content(text: str, in_thinking: bool, partial: str,
                            emit_token, thinking_cb):
    """Split a content string into thinking vs response tokens, handling <think>
    and </think> boundaries even when they arrive across multiple chunks.
    Returns (in_thinking, partial) updated state.
    """
    text = partial + text
    partial = ""
    while text:
        if not in_thinking:
            idx = text.find("<think>")
            if idx == -1:
                # Check if the tail is a partial start of <think>
                for n in range(min(7, len(text)), 0, -1):
                    if "<think>"[:n] == text[-n:]:
                        if len(text) > n:
                            emit_token(text[:-n])
                        partial = text[-n:]
                        text = ""
                        break
                else:
                    emit_token(text)
                    text = ""
            else:
                if idx > 0:
                    emit_token(text[:idx])
                in_thinking = True
                text = text[idx + 7:]
        else:
            idx = text.find("</think>")
            if idx == -1:
                thinking_cb(text)
                text = ""
            else:
                if idx > 0:
                    thinking_cb(text[:idx])
                in_thinking = False
                text = text[idx + 8:]
    return in_thinking, partial


def _start_openai_proxy(real_base_url: str, thinking_cb) -> str:
    """Proxy that intercepts delta.reasoning from Ollama's OpenAI-compat endpoint.

    Ollama streams qwen3 reasoning tokens as delta.reasoning. This proxy sits
    between hermes and Ollama, extracts those tokens and fires thinking_cb(),
    then renames the field to delta.thinking for hermes to handle too.

    Returns the proxy base URL. Falls back to real_base_url on any setup error.
    """
    try:
        parsed = urllib.parse.urlparse(real_base_url)
        real_host = parsed.hostname or "127.0.0.1"
        real_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path_prefix = parsed.path.rstrip("/")

        def _upstream_path(req_path: str) -> str:
            # Hermes base_url already includes ``/v1``, so client requests arrive as
            # ``/v1/chat/completions``. Prepending path_prefix again produced
            # ``/v1/v1/chat/completions`` → Ollama 404.
            if path_prefix and req_path.startswith(path_prefix):
                return req_path
            return f"{path_prefix}{req_path}" if path_prefix else req_path

        class _Handler(http.server.BaseHTTPRequestHandler):
            disable_nagle_algorithm = True  # per-token frames must not coalesce

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                fwd_headers = {k: v for k, v in self.headers.items()
                               if k.lower() not in ("host", "content-length")}
                fwd_headers["Content-Length"] = str(len(body))
                try:
                    conn = http.client.HTTPConnection(real_host, real_port, timeout=600)
                    conn.request("POST", _upstream_path(self.path), body=body, headers=fwd_headers)
                    resp = conn.getresponse()
                    self.send_response(resp.status)
                    for k, v in resp.getheaders():
                        if k.lower() != "transfer-encoding":
                            self.send_header(k, v)
                    self.end_headers()
                    buf = b""
                    while True:
                        # read1() returns as soon as data is available; plain
                        # read(4096) blocks until 4 KB fill up, batching ~10-20
                        # tokens per flush and making the stream chunky.
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            ls = line.decode("utf-8", "replace").strip()
                            if ls.startswith("data: ") and ls[6:] not in ("[DONE]", ""):
                                try:
                                    d = json.loads(ls[6:])
                                    delta = d.get("choices", [{}])[0].get("delta", {})
                                    reasoning = delta.get("reasoning", "")
                                    if reasoning:
                                        thinking_cb(reasoning)
                                        delta["thinking"] = delta.pop("reasoning")
                                        line = (b"data: "
                                                + json.dumps(d, ensure_ascii=False).encode())
                                except Exception:
                                    pass
                            self.wfile.write(line + b"\n")
                            self.wfile.flush()
                    if buf:
                        self.wfile.write(buf)
                        self.wfile.flush()
                    conn.close()
                except Exception:
                    self.send_response(502)
                    self.end_headers()

            def log_message(self, *args):
                pass  # silence proxy access logs

        srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        return f"http://127.0.0.1:{port}{path_prefix}"
    except Exception:
        return real_base_url  # fall back silently


def _start_ollama_native_proxy(real_base_url: str, thinking_cb,
                               reasoning_effort: str = "", num_ctx: int = 0) -> str:
    """Proxy that calls Ollama's native /api/chat for real-time thinking streaming.

    Receives OpenAI /v1/chat/completions requests from hermes, translates them
    to Ollama's native NDJSON streaming format, extracts <think>...</think>
    tokens in real-time, and converts the response back to OpenAI SSE format.

    This gives live reasoning streaming vs the OpenAI-compat path which batches
    all delta.reasoning events and delivers them after the thinking phase ends.

    Returns the proxy base URL. Falls back to real_base_url on any setup error.
    """
    try:
        parsed = urllib.parse.urlparse(real_base_url)
        real_host = parsed.hostname or "127.0.0.1"
        real_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path_prefix = parsed.path.rstrip("/")

        class _NativeHandler(http.server.BaseHTTPRequestHandler):
            disable_nagle_algorithm = True  # per-token frames must not coalesce

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length)
                try:
                    req = json.loads(body_bytes)
                except Exception:
                    self.send_response(400); self.end_headers(); return

                # Convert OpenAI request → Ollama native format
                native = {
                    "model": req.get("model", ""),
                    "messages": req.get("messages", []),
                    "stream": True,
                }
                if "tools" in req:
                    native["tools"] = req["tools"]
                opts = {}
                if "temperature" in req:  opts["temperature"]  = req["temperature"]
                if "top_p"       in req:  opts["top_p"]        = req["top_p"]
                if "max_tokens"  in req:  opts["num_predict"]  = req["max_tokens"]
                # Cap the context window so Ollama allocates a small KV cache. Without
                # this Hermes runs the model at its full GGUF max (262K) → a ~20 GB KV
                # cache that thrashes and makes every call take minutes.
                if num_ctx:
                    opts["num_ctx"] = num_ctx
                if opts:
                    native["options"] = opts
                # Map reasoning_effort to Ollama think param (top-level, not in options).
                # Only send on models that support it — llama/mistral return 400 otherwise.
                model_id = native.get("model", "")
                if _supports_ollama_think_param(model_id):
                    native["think"] = reasoning_effort != "none"
                native_body = json.dumps(native, ensure_ascii=False).encode()
                emit({"type": "log", "msg": f"[worker] DEBUG native /api/chat options={native.get('options')}"})

                # ── Progress instrumentation ─────────────────────────────────
                # Ollama streams NOTHING until the model is loaded into memory and
                # the entire prompt has been processed. For a cold model or a heavy
                # prompt (e.g. an image fed to vision_analyze) that gap can be
                # minutes of total silence. A heartbeat thread emits elapsed-time
                # logs so the GUI shows "still working" rather than looking hung,
                # and the Ollama `done` stats below break the time into load /
                # prompt-eval / generation so it's clear where it went.
                t0 = time.time()
                hb_stop = threading.Event()
                prog = {"tokens": 0, "first_at": None}
                msg_kb = len(native_body) / 1024.0

                def _heartbeat():
                    while not hb_stop.wait(12):
                        el = time.time() - t0
                        if prog["first_at"] is None:
                            emit({"type": "log", "msg":
                                  f"[worker] ⏳ model loading / processing prompt… {el:.0f}s elapsed "
                                  f"(req {msg_kb:.0f} KB, no output yet)"})
                        else:
                            emit({"type": "log", "msg":
                                  f"[worker] ⏳ generating… {el:.0f}s, {prog['tokens']} tokens so far"})

                def _mark_first(kind: str) -> None:
                    if prog["first_at"] is None:
                        prog["first_at"] = time.time() - t0
                        emit({"type": "log", "msg":
                              f"[worker] ⚡ first {kind} after {prog['first_at']:.1f}s — model is responding"})

                def _emit_timing(d: dict) -> None:
                    sec = lambda ns: (ns or 0) / 1e9  # noqa: E731
                    ec = d.get("eval_count") or 0
                    edd = sec(d.get("eval_duration"))
                    tps = (ec / edd) if edd else 0.0
                    emit({"type": "log", "msg":
                          f"[worker] ✅ done in {time.time() - t0:.1f}s — "
                          f"load {sec(d.get('load_duration')):.1f}s, "
                          f"prompt {d.get('prompt_eval_count') or 0} tok/"
                          f"{sec(d.get('prompt_eval_duration')):.1f}s, "
                          f"gen {ec} tok/{edd:.1f}s ({tps:.1f} tok/s)"})

                threading.Thread(target=_heartbeat, daemon=True).start()
                try:
                    conn = http.client.HTTPConnection(real_host, real_port, timeout=600)
                    conn.request("POST", "/api/chat", body=native_body,
                                 headers={"Content-Type": "application/json",
                                          "Content-Length": str(len(native_body))})
                    resp = conn.getresponse()

                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()

                    chunk_id = f"chatcmpl-{_uuid_mod.uuid4().hex[:8]}"
                    created  = int(time.time())
                    model_id = req.get("model", "")

                    def _sse(delta: dict, finish=None) -> bytes:
                        d = {"id": chunk_id, "object": "chat.completion.chunk",
                             "created": created, "model": model_id,
                             "choices": [{"index": 0, "delta": delta,
                                          "finish_reason": finish}]}
                        return f"data: {json.dumps(d, ensure_ascii=False)}\n\n".encode()

                    raw_buf = b""

                    while True:
                        # read1() returns as soon as data is available; plain
                        # read(4096) blocks until 4 KB fill up, batching ~10-20
                        # tokens per flush and making the stream chunky.
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        raw_buf += chunk
                        while b"\n" in raw_buf:
                            line_b, raw_buf = raw_buf.split(b"\n", 1)
                            ls = line_b.decode("utf-8", "replace").strip()
                            if not ls:
                                continue
                            try:
                                d = json.loads(ls)
                            except Exception:
                                continue

                            msg        = d.get("message", {})
                            # Native Ollama separates thinking from content:
                            # message.thinking = reasoning tokens (real-time)
                            # message.content  = response tokens
                            thinking_tok = msg.get("thinking", "")
                            content      = msg.get("content", "")
                            tool_calls   = msg.get("tool_calls")
                            done         = d.get("done", False)
                            done_rsn     = d.get("done_reason", "stop")

                            if thinking_tok:
                                _mark_first("thinking token")
                                thinking_cb(thinking_tok)

                            if tool_calls:
                                _mark_first("tool call")
                                for i, tc in enumerate(tool_calls):
                                    fn   = tc.get("function", {})
                                    args = fn.get("arguments", {})
                                    args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                                    cid  = f"call_{_uuid_mod.uuid4().hex[:8]}"
                                    self.wfile.write(_sse({"tool_calls": [{
                                        "index": i, "id": cid, "type": "function",
                                        "function": {"name": fn.get("name",""), "arguments": ""},
                                    }]}))
                                    self.wfile.write(_sse({"tool_calls": [{
                                        "index": i, "function": {"arguments": args_str},
                                    }]}))
                                self.wfile.write(_sse({}, "tool_calls"))
                                self.wfile.write(b"data: [DONE]\n\n")
                                self.wfile.flush()
                                conn.close()
                                return

                            if content:
                                _mark_first("token")
                                prog["tokens"] += 1
                                self.wfile.write(_sse({"content": content}))
                                self.wfile.flush()

                            if done:
                                _emit_timing(d)
                                finish = "stop" if done_rsn in ("stop", "") else done_rsn
                                self.wfile.write(_sse({}, finish))
                                self.wfile.write(b"data: [DONE]\n\n")
                                self.wfile.flush()
                                conn.close()
                                return

                    self.wfile.write(_sse({}, "stop"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    conn.close()
                except Exception as e:
                    emit({"type": "log", "msg":
                          f"[worker] ⚠️ proxy error talking to Ollama after "
                          f"{time.time() - t0:.0f}s: {e}"})
                    try: self.send_response(502); self.end_headers()
                    except Exception: pass
                finally:
                    hb_stop.set()

            def log_message(self, *args):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), _NativeHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        return f"http://127.0.0.1:{port}{path_prefix}"
    except Exception:
        return real_base_url  # fall back silently


# ── Save real stdout immediately — emit() will always use this ───────────────
_real_stdout = sys.stdout

# ── Turn heartbeat ───────────────────────────────────────────────────────────
# A turn can go silent for minutes during a slow tool call (e.g. vision_analyze
# cold-loading a local model and prompt-evaluating an image) where nothing
# streams. This watchdog emits a periodic "still working" log so the GUI shows
# live progress instead of looking frozen. It fires only when a turn is active
# AND no event has been emitted for _HB_INTERVAL seconds, so active streaming
# (tokens / reasoning) never triggers it.
_hb_lock = threading.Lock()
_hb_active = False        # True only while a turn is running
_hb_last_emit = 0.0       # wall-clock of the most recent emit() of any kind
_hb_phase = ""            # human label for the current phase (e.g. "running vision_analyze")
_hb_phase_started = 0.0   # wall-clock the current phase began
_hb_started = False       # guard so the daemon thread is launched once
_HB_INTERVAL = 12.0       # seconds of silence before a heartbeat fires


def _hb_set(*, active: "bool | None" = None, phase: "str | None" = None) -> None:
    """Update heartbeat turn-active state and/or the current phase label."""
    global _hb_active, _hb_phase, _hb_phase_started
    with _hb_lock:
        if active is not None:
            _hb_active = active
            if active and not _hb_phase_started:
                _hb_phase_started = time.time()
        if phase is not None and phase != _hb_phase:
            _hb_phase = phase
            _hb_phase_started = time.time()


def _heartbeat_loop() -> None:
    while True:
        time.sleep(3)
        with _hb_lock:
            if not _hb_active:
                continue
            last, phase, started = _hb_last_emit, _hb_phase, _hb_phase_started
        now = time.time()
        if now - last >= _HB_INTERVAL:
            el = now - (started or now)
            emit({"type": "log",
                  "msg": f"[worker] ⏳ still {phase or 'working'}… {el:.0f}s elapsed"})


def _hb_start() -> None:
    global _hb_started
    if _hb_started:
        return
    _hb_started = True
    threading.Thread(target=_heartbeat_loop, daemon=True).start()

HERMES_DIR = os.path.expanduser("~/.hermes/hermes-agent")
sys.path.insert(0, HERMES_DIR)
# Worker subprocess uses the Hermes venv Python, not the GUI conda env — put the
# repo root on sys.path so ``import agent_gui.*`` works without a separate install
# into the Hermes venv (desk_toolsets, llm_backend, etc.).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# Inlined from agent_gui.llm_backend — the worker runs under the Hermes venv and
# cannot rely on ``import agent_gui`` (editable install is in the GUI conda env).
def _is_ollama_backend(base_url: str, provider: str = "") -> bool:
    if (provider or "").strip().lower() == "ollama":
        return True
    return ":11434" in (base_url or "")


def _is_local_openai_compat(base_url: str, provider: str = "") -> bool:
    if _is_ollama_backend(base_url, provider):
        return True
    base = (base_url or "").strip().lower()
    return any(h in base for h in ("localhost", "127.0.0.1", ":8010"))


def _normalize_api_mode(api_mode: str, base_url: str, provider: str = "") -> str:
    mode = (api_mode or "").strip().lower()
    # "chat_completion" is in case of completion mode typos
    if mode in ("openai", "chat_completion"):
        return "chat_completions"
    if mode == "codex_responses" and _is_local_openai_compat(base_url, provider):
        return "chat_completions"
    return mode


def _supports_ollama_think_param(model: str) -> bool:
    m = (model or "").lower()
    return any(k in m for k in ("qwen", "deepseek", "r1"))


def _wants_native_ollama_proxy(cfg_api_mode: str, env_api_mode: str) -> bool:
    for raw in (cfg_api_mode, env_api_mode):
        m = (raw or "").strip().lower()
        if m == "ollama":
            return True
        if m in ("chat_completions", "openai", "codex_responses"):
            return False
    return False


_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Only filter truly unreadable internal noise
_NOISE = (
    "async_generator",
    "🤖 Assistant:",   # redundant — already streamed via on_token callback
)


def emit(obj: dict) -> None:
    # Stamp the real wall-clock emit time so the server can record true per-event
    # times. Hermes batch-flushes messages to its DB at turn end (all with one
    # clustered timestamp), so this stream is the ONLY source of genuine timing.
    obj.setdefault("ts", time.time())
    global _hb_last_emit
    _hb_last_emit = obj["ts"]
    _real_stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    _real_stdout.flush()


def _emit_log(raw: str) -> None:
    msg = _ANSI_RE.sub("", raw).strip()
    if not msg:
        return
    if any(n in msg for n in _NOISE):
        return
    emit({"type": "log", "msg": msg})


def _emit_turn_failure(result: object) -> None:
    """Surface a swallowed terminal failure as an explicit "error" event.

    Hermes does NOT raise when an API call exhausts its retries/fallbacks:
    run_conversation returns ``{"failed": True, "error": ...}`` and the turn ends
    looking like a normal completion. Without this, the GUI sees a clean
    done/turn_done, treats the turn as committed, and the feed shows nothing —
    the user message just sits there with no hint the backend was down."""
    try:
        if not isinstance(result, dict) or not result.get("failed"):
            return
        msg = str(result.get("error") or result.get("final_response")
                  or "API call failed")
        emit({"type": "error", "msg": msg})
    except Exception:
        pass


class _StdoutCapture:
    """Converts all print() calls from hermes into "log" worker events."""

    def __init__(self) -> None:
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            _emit_log(line)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            _emit_log(self._buf)
            self._buf = ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise io.UnsupportedOperation("no fileno")


def _capture_stdout() -> None:
    """Route all print() output (hermes internals) into "log" worker events.

    Done at the start of main() rather than at import so that merely importing
    this module is side-effect-free — importing it must not hijack the stdout of
    whatever process did the import. The only caller that needs the capture is the
    worker subprocess via main(), and hermes itself is imported inside main()
    (after this runs), so module-level hermes prints are still caught.
    """
    sys.stdout = _StdoutCapture()  # type: ignore[assignment]


_SHUTDOWN = object()

# Tools the operator-facing "inspect" REPL may call against a desk's sandbox.
# Read-only by intent (no write_file/patch); `terminal` is included so the panel
# can `ls`/`cat` inside the container. All run through the same registry dispatch
# the agent uses, so they inherit this desk's container, path translation, and
# read guards (inspect can't read ~/.ssh any more than the agent can).
_INSPECT_ALLOWED_TOOLS = frozenset({
    "read_file", "search_files", "list_files", "terminal",
})

# Idents of threads currently running an inspect tool call. A stop request signals
# Hermes' per-thread interrupt for these, which makes the terminal wait-loop kill
# the in-container process group (returncode 130) — letting the operator abort a
# runaway script started from the Inspect panel.
_INSPECT_THREADS: "set[int]" = set()
_INSPECT_THREADS_LOCK = threading.Lock()


def _run_inspect(cmd: dict) -> None:
    """Execute one inspect tool call and emit an ``inspect_result`` event."""
    rid = cmd.get("id")
    tool = (cmd.get("tool") or "").strip()
    args = cmd.get("args") or {}
    if tool not in _INSPECT_ALLOWED_TOOLS:
        emit({"type": "inspect_result", "id": rid, "ok": False,
              "error": f"tool '{tool}' is not allowed for inspect "
                       f"(allowed: {', '.join(sorted(_INSPECT_ALLOWED_TOOLS))})"})
        return
    tid = threading.current_thread().ident
    with _INSPECT_THREADS_LOCK:
        _INSPECT_THREADS.add(tid)
    try:
        from tools.registry import registry  # noqa: PLC0415
        result = registry.dispatch(tool, args if isinstance(args, dict) else {})
        emit({"type": "inspect_result", "id": rid, "ok": True, "tool": tool,
              "result": result})
    except Exception as exc:  # noqa: BLE001 — inspect must never crash the worker
        emit({"type": "inspect_result", "id": rid, "ok": False,
              "error": f"{type(exc).__name__}: {exc}"})
    finally:
        with _INSPECT_THREADS_LOCK:
            _INSPECT_THREADS.discard(tid)
        # Clear this thread's interrupt flag so a future thread reusing the ident
        # isn't spuriously interrupted.
        try:
            from tools.interrupt import set_interrupt  # noqa: PLC0415
            set_interrupt(False)
        except Exception:
            pass


def _kill_inspect_container_procs() -> None:
    """Kill the in-container process tree of any running command wrapper.

    Hermes runs terminal commands via ``docker exec``, which does NOT forward
    signals — killing the host-side client (what the interrupt does) orphans the
    real process inside the container, so a runaway script keeps running. Here we
    kill the process GROUP of each command-wrapper bash (its children — the user's
    command — die with it). Best-effort.
    """
    import shutil as _shutil  # noqa: PLC0415
    import subprocess as _subprocess  # noqa: PLC0415
    docker = _shutil.which("docker")
    key = _desk_container_key()
    if not docker or not key or key == "default":
        return
    try:
        out = _subprocess.run(
            [docker, "ps", "-q", "--filter", "label=hermes-agent=1",
             "--filter", f"label=hermes-task-id={key}"],
            capture_output=True, text=True, timeout=10, check=False)
        ids = out.stdout.split()
        if not ids:
            return
        # `[h]ermes-snap-` (regex char-class) matches the wrapper bash but NOT this
        # kill command itself — the classic `ps | grep [p]attern` self-exclusion.
        script = (
            "for pid in $(pgrep -f '[h]ermes-snap-' 2>/dev/null); do "
            "pgid=$(ps -o pgid= -p \"$pid\" 2>/dev/null | tr -d ' '); "
            "if [ -n \"$pgid\" ]; then kill -TERM -\"$pgid\" 2>/dev/null; "
            "(sleep 0.3; kill -KILL -\"$pgid\" 2>/dev/null) & fi; "
            "done; true"
        )
        _subprocess.run([docker, "exec", ids[0], "sh", "-c", script],
                        capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        pass


def _stop_inspect() -> None:
    """Abort every in-flight inspect call: interrupt the host-side wait loop AND
    kill the orphaned in-container process tree."""
    try:
        from tools.interrupt import set_interrupt  # noqa: PLC0415
        with _INSPECT_THREADS_LOCK:
            idents = list(_INSPECT_THREADS)
        for tid in idents:
            set_interrupt(True, thread_id=tid)
    except Exception:
        idents = []
    _kill_inspect_container_procs()
    emit({"type": "log", "msg": f"[worker] inspect stop signaled ({len(idents)} running)"})


def _persistent_loop(agent, db) -> None:
    """Long-lived turn loop for one desk.

    A daemon thread reads newline-delimited JSON commands from stdin:
      {"cmd": "run", "message": "..."}  — run one turn
      {"cmd": "interrupt"}              — stop the current turn (agent.interrupt)
      {"cmd": "shutdown"}               — exit the process
    The agent (and the heavy Hermes import) is created once; each turn reloads the
    session's history from the DB and calls run_conversation, then emits "turn_done".
    interrupt() is called from the reader thread while run_conversation runs on the
    main thread — exactly the pattern Hermes' interrupt() is designed for.
    """
    import queue as _queue  # noqa: PLC0415

    run_q: "_queue.Queue" = _queue.Queue()
    sid = agent.session_id

    def _reader() -> None:
        try:
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
                    msg_text = cmd.get("message", "")
                    images = cmd.get("images") or []
                    if images:
                        content: list | str = [{"type": "text", "text": msg_text}]
                        for img in images:
                            content.append({"type": "image_url", "image_url": {"url": img.get("data", "")}})
                        run_q.put((content, msg_text))
                    else:
                        run_q.put((msg_text, None))
                elif c == "interrupt":
                    try:
                        agent.interrupt(None)
                    except Exception:
                        pass
                elif c == "inspect":
                    # Run off the reader thread so a slow tool doesn't stall the
                    # command stream; read-only tools are safe to overlap a turn.
                    threading.Thread(target=_run_inspect, args=(cmd,),
                                     daemon=True).start()
                elif c == "inspect_stop":
                    _stop_inspect()
                elif c == "shutdown":
                    run_q.put(_SHUTDOWN)
                    return
        except Exception:
            run_q.put(_SHUTDOWN)

    threading.Thread(target=_reader, daemon=True).start()
    emit({"type": "log", "msg": "[worker] persistent worker ready"})

    while True:
        item = run_q.get()
        if item is _SHUTDOWN:
            break
        msg, persist_text = item if isinstance(item, tuple) else (item, None)
        try:
            try:
                agent.clear_interrupt()
            except Exception:
                pass
            history = None
            try:
                history = db.get_messages_as_conversation(sid)
            except Exception:
                history = None
            _hb_set(active=True, phase="thinking")
            try:
                result = agent.run_conversation(msg, conversation_history=history or None,
                                                persist_user_message=persist_text)
            finally:
                _hb_set(active=False)
            _emit_turn_failure(result)
        except Exception as exc:
            emit({"type": "error", "msg": str(exc), "traceback": traceback.format_exc()})
        # Mark turn boundary; the server keeps this process warm for the next turn.
        emit({"type": "turn_done"})


def _host_path_to_docker(path: str) -> str:
    """Map a host workspace/repo path to the desk Docker ``/workspace`` tree."""
    workdir = os.environ.get("HERMES_WORKDIR", "").strip()
    docker_root = (os.environ.get("HERMES_GUI_DOCKER_WORKSPACE", "/workspace").strip()
                   or "/workspace")
    team_repo = os.environ.get("HERMES_GUI_TEAM_REPO", "").strip()
    if not path or not workdir:
        return path
    try:
        workdir_real = os.path.realpath(os.path.expanduser(workdir))
    except OSError:
        return path
    team_repo_real = ""
    if team_repo:
        try:
            team_repo_real = os.path.realpath(os.path.expanduser(team_repo))
        except OSError:
            pass
    try:
        candidate = os.path.expanduser(path)
        if os.path.isabs(candidate) or os.path.exists(candidate):
            resolved = os.path.realpath(candidate)
        else:
            resolved = candidate
    except OSError:
        resolved = os.path.expanduser(path)
    if resolved.startswith(docker_root + os.sep) or resolved == docker_root:
        return resolved
    if team_repo_real and (resolved == team_repo_real
                           or resolved.startswith(team_repo_real + os.sep)):
        suffix = resolved[len(team_repo_real):].lstrip(os.sep)
        return (f"{docker_root}/team_files/{suffix}" if suffix
                else f"{docker_root}/team_files")
    if resolved == workdir_real or resolved.startswith(workdir_real + os.sep):
        suffix = resolved[len(workdir_real):].lstrip(os.sep)
        return f"{docker_root}/{suffix}" if suffix else docker_root
    return path


def _patch_docker_path_translation() -> None:
    """Rewrite host workspace paths to ``/workspace/...`` for Docker file tools."""
    if not os.environ.get("HERMES_WORKDIR", "").strip():
        return
    try:
        from tools.file_operations import ShellFileOperations  # noqa: PLC0415
    except Exception:
        return
    _orig_expand = ShellFileOperations._expand_path

    def _expand_path(self, path: str) -> str:  # noqa: ANN001
        expanded = _orig_expand(self, path)
        return _host_path_to_docker(expanded)

    ShellFileOperations._expand_path = _expand_path


def _docker_path_to_host(path: str) -> str:
    """Map a container ``/workspace/...`` path back to its host location.

    Inverse of :func:`_host_path_to_docker`. For HOST-side tools like
    ``vision_analyze`` that open the file directly on the host (not via the Docker
    shell), the agent's ``/workspace/...`` paths don't exist. We map them back to
    ``HERMES_WORKDIR/...``; ``/workspace/team_files/...`` then resolves through the
    host ``team_files`` symlink into the team repo. URLs and ``data:`` URIs are
    left untouched (and the vision tool keeps its own SSRF guard on URLs).
    """
    if not path or not isinstance(path, str):
        return path
    if path.startswith(("http://", "https://", "data:")):
        return path
    raw = path[len("file://"):] if path.startswith("file://") else path
    workdir = os.environ.get("HERMES_WORKDIR", "").strip()
    if not workdir:
        return path
    docker_root = (os.environ.get("HERMES_GUI_DOCKER_WORKSPACE", "/workspace").strip()
                   or "/workspace")
    if raw == docker_root:
        return workdir
    if raw.startswith(docker_root + "/"):
        suffix = raw[len(docker_root) + 1:]
        return os.path.normpath(os.path.join(workdir, suffix))
    return path


def _patch_vision_path_translation() -> None:
    """Let host-side vision/video tools open container ``/workspace/...`` paths.

    These tools run in the worker process (not the Docker shell), so the
    ``/workspace`` paths the agent uses everywhere else don't exist on the host.
    We wrap the registered handlers to translate the ``image_url``/``video_url``
    argument back to a host path first. The registry stores a direct handler
    reference, so we mutate the ``ToolEntry`` in place rather than the module attr.
    """
    if not os.environ.get("HERMES_WORKDIR", "").strip():
        return
    try:
        import tools.vision_tools  # noqa: F401, PLC0415  (force tool registration)
        from tools.registry import registry  # noqa: PLC0415
    except Exception:
        return
    for tool_name, url_key in (("vision_analyze", "image_url"),
                               ("video_analyze", "video_url")):
        try:
            entry = registry.get_entry(tool_name)
            if entry is None:
                continue
            _orig = entry.handler

            def _wrapped(args, *a, _orig=_orig, _key=url_key, **kw):  # noqa: ANN001
                try:
                    url = args.get(_key, "")
                    mapped = _docker_path_to_host(url)
                    if mapped != url:
                        args = {**args, _key: mapped}
                except Exception:
                    pass
                return _orig(args, *a, **kw)

            entry.handler = _wrapped
        except Exception as exc:
            emit({"type": "log", "msg": f"[worker] vision path patch skipped for {tool_name}: {exc}"})


# GUI docker env captured before Hermes import; cli.load_cli_config() overwrites
# TERMINAL_DOCKER_VOLUMES from profile config.yaml (docker_volumes: []).
_GUI_DOCKER_SNAPSHOT: dict[str, str] = {}


def _capture_gui_docker_env() -> None:
    """Save GUI-set docker env before Hermes cli import clobbers it."""
    global _GUI_DOCKER_SNAPSHOT
    vols = os.environ.get("TERMINAL_DOCKER_VOLUMES", "").strip()
    if not vols or vols == "[]":
        _GUI_DOCKER_SNAPSHOT = {}
        return
    snap: dict[str, str] = {"TERMINAL_DOCKER_VOLUMES": vols}
    for key in (
        "TERMINAL_CWD",
        "TERMINAL_ENV",
        "HERMES_GUI_TEAM_REPO",
        "HERMES_GUI_DOCKER_WORKSPACE",
    ):
        val = os.environ.get(key, "").strip()
        if val:
            snap[key] = val
    _GUI_DOCKER_SNAPSHOT = snap


def _restore_gui_docker_env() -> None:
    """Re-apply GUI docker settings after cli.load_cli_config() runs at import."""
    if not _GUI_DOCKER_SNAPSHOT:
        return
    for key, val in _GUI_DOCKER_SNAPSHOT.items():
        os.environ[key] = val
    os.environ.setdefault("TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES", "true")


def _patch_gui_docker_config() -> None:
    """Force GUI volume mounts into terminal/file tool docker config.

    Profile ``config.yaml`` lists ``docker_volumes: []``; importing ``cli.py``
    applies that via ``load_cli_config`` even when the GUI server already set
    ``TERMINAL_DOCKER_VOLUMES``.  ``file_tools`` also omits
    ``docker_persist_across_processes`` from ``container_config``, so Hermes
    defaults to cross-process container reuse and attaches to a stale sandbox.
    """
    vols_json = _GUI_DOCKER_SNAPSHOT.get("TERMINAL_DOCKER_VOLUMES", "")
    if not vols_json:
        return
    try:
        parsed_vols = json.loads(vols_json)
    except json.JSONDecodeError:
        return
    if not isinstance(parsed_vols, list) or not parsed_vols:
        return

    try:
        import tools.terminal_tool as tt  # noqa: PLC0415
    except ImportError:
        return

    _orig = tt._get_env_config

    def _get_env_config_gui():  # noqa: ANN202
        cfg = _orig()
        cfg["docker_volumes"] = parsed_vols
        persist = os.environ.get("TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES", "true")
        cfg["docker_persist_across_processes"] = persist.lower() in {"true", "1", "yes"}
        cfg["env_type"] = "docker"
        cfg["docker_mount_cwd_to_workspace"] = False
        cwd = _GUI_DOCKER_SNAPSHOT.get("TERMINAL_CWD") or os.environ.get("TERMINAL_CWD", "")
        if cwd:
            try:
                host_cwd = os.path.abspath(os.path.expanduser(cwd))
                if os.path.isdir(host_cwd):
                    cfg["host_cwd"] = host_cwd
                    cfg["cwd"] = "/workspace"
            except OSError:
                pass
        return cfg

    tt._get_env_config = _get_env_config_gui
    fo = sys.modules.get("tools.file_tools")
    if fo is not None and hasattr(fo, "_get_env_config"):
        fo._get_env_config = _get_env_config_gui


def _desk_container_key() -> str:
    """Stable per-desk identity for Docker container labeling/reuse.

    Hermes reuses containers ACROSS PROCESSES keyed only on the labels
    ``hermes-task-id`` + ``hermes-profile`` (it deliberately ignores mounts and
    the sandbox dir — see ``tools/environments/docker.py:_find_reusable_container``).
    The GUI top-level agent always resolves ``task_id`` to ``"default"``, so every
    desk that shares a profile collides on ``(default, <profile>)`` and gets
    attached to another desk's container — leaking ``/workspace`` and
    ``/workspace/team_files`` across desks and teams.

    Deriving a unique ``task_id`` from this desk's sandbox dir (the server sets a
    distinct ``TERMINAL_SANDBOX_DIR``/``HERMES_HOME`` = ``gui_sandboxes/<sid>`` per
    desk) makes the label unique per desk: warm reuse stays within one desk across
    turns, but reuse never crosses desks/teams. Session ids
    (``YYYYMMDD_HHMMSS_<hex>``) are already Docker-label-safe.
    """
    for var in ("TERMINAL_SANDBOX_DIR", "HERMES_HOME"):
        v = os.environ.get(var, "").strip()
        if v:
            name = os.path.basename(v.rstrip("/"))
            if name:
                return name
    return os.environ.get("HERMES_SESSION_ID", "").strip() or "default"


def _patch_desk_container_identity() -> None:
    """Make Docker container reuse per-desk (root-cause fix for shared storage).

    Patches ``tools.terminal_tool._resolve_container_task_id`` to return this
    desk's stable key instead of ``"default"``. Both the terminal tool and the
    file tools route container creation/reuse through that function (the file
    tools import it lazily at call time), so this single patch keys *all* of a
    desk's Docker work to one per-desk container. Subagent ids collapse to the
    desk key too, preserving Hermes' "subagents share the parent container".
    """
    key = _desk_container_key()
    if not key or key == "default":
        return
    try:
        import tools.terminal_tool as tt  # noqa: PLC0415
    except Exception as exc:
        emit({"type": "log", "msg": f"[worker] desk container identity patch skipped: {exc}"})
        return
    tt._resolve_container_task_id = lambda task_id=None: key  # noqa: E731
    emit({"type": "log", "msg": f"[worker] docker container keyed to desk {key}"})


def _reset_docker_for_team_repo() -> None:
    """Force-remove THIS desk's Docker container when the GUI flagged a mount change.

    Needed because Hermes' cross-process reuse ignores mount changes: when a
    desk's volume *set* changes (e.g. it later joined a team, adding the
    ``team_files`` bind mount) Hermes would otherwise reattach to the desk's own
    stale container. We remove the container directly by its per-desk label so a
    fresh one is created with the new mounts. Per-desk label scoping means this
    never touches sibling desks' containers, and acting on Docker directly (not
    the empty in-process env cache) makes it work on a cold worker start.
    """
    if os.environ.get("HERMES_GUI_FORCE_DOCKER_RESET", "").strip().lower() not in ("1", "true", "yes"):
        return
    if not _GUI_DOCKER_SNAPSHOT and not os.environ.get("HERMES_GUI_TEAM_REPO", "").strip():
        return
    vols = (
        _GUI_DOCKER_SNAPSHOT.get("TERMINAL_DOCKER_VOLUMES")
        or os.environ.get("TERMINAL_DOCKER_VOLUMES", "")
    ).strip()
    if vols:
        emit({"type": "log", "msg": f"[worker] TERMINAL_DOCKER_VOLUMES={vols[:240]}"})
    key = _desk_container_key()
    import shutil as _shutil  # noqa: PLC0415
    import subprocess as _subprocess  # noqa: PLC0415
    docker = _shutil.which("docker")
    if not docker or not key or key == "default":
        return
    try:
        out = _subprocess.run(
            [docker, "ps", "-aq",
             "--filter", "label=hermes-agent=1",
             "--filter", f"label=hermes-task-id={key}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        ids = [c for c in out.stdout.split() if c]
        if ids:
            _subprocess.run([docker, "rm", "-f", *ids],
                            capture_output=True, text=True, timeout=30, check=False)
            emit({"type": "log", "msg": f"[worker] reset {len(ids)} stale container(s) for desk {key}"})
    except Exception as exc:
        emit({"type": "log", "msg": f"[worker] docker reset skipped: {exc}"})


def _patch_team_repo_writes() -> None:
    """Allow write_file/patch into the team File Repo AND the Docker workspace.

    File tools execute inside the Docker sandbox (``ShellFileOperations``), so the
    agent addresses them with container paths (``/workspace/...``,
    ``/workspace/team_files/...``). Hermes' host-side ``is_write_denied`` resolves
    those against the host filesystem, where they fall outside
    ``HERMES_WRITE_SAFE_ROOT`` (the desk's host workspace) and get denied — the
    "guard rail limits files to the docker image" symptom.

    We widen the write boundary to the explicit extra roots that ARE legitimate:
      * the team File Repo (``HERMES_GUI_TEAM_REPO``) — host path of team_files;
      * the Docker workspace root (``HERMES_GUI_DOCKER_WORKSPACE``, e.g.
        ``/workspace``) — the real boundary for sandbox-executed writes.
    For a write under one of these, we swap ``HERMES_WRITE_SAFE_ROOT`` to that
    root before delegating to the original check, so the host denylist (``~/.ssh``,
    control files, …) still applies — only the safe-root containment is relaxed.
    """
    extra_roots: list[str] = []
    team_repo = os.environ.get("HERMES_GUI_TEAM_REPO", "").strip()
    if team_repo:
        try:
            extra_roots.append(os.path.realpath(os.path.expanduser(team_repo)))
        except OSError:
            pass
    docker_ws = (os.environ.get("HERMES_GUI_DOCKER_WORKSPACE", "").strip()
                 or ("/workspace" if team_repo else ""))
    if docker_ws:
        extra_roots.append(docker_ws.rstrip("/") or "/workspace")
    if not extra_roots:
        return

    from agent import file_safety as fs  # noqa: PLC0415

    _orig = fs.is_write_denied

    def is_write_denied(path: str) -> bool:
        try:
            resolved = os.path.realpath(os.path.expanduser(str(path)))
        except OSError:
            resolved = ""
        match = next((r for r in extra_roots
                      if resolved == r or resolved.startswith(r + os.sep)), None)
        if match:
            prev = os.environ.get("HERMES_WRITE_SAFE_ROOT")
            os.environ["HERMES_WRITE_SAFE_ROOT"] = match
            try:
                return _orig(path)
            finally:
                if prev is not None:
                    os.environ["HERMES_WRITE_SAFE_ROOT"] = prev
                else:
                    os.environ.pop("HERMES_WRITE_SAFE_ROOT", None)
        return _orig(path)

    fs.is_write_denied = is_write_denied
    fo_mod = sys.modules.get("tools.file_operations")
    if fo_mod is not None:
        fo_mod._is_write_denied = is_write_denied


def main() -> None:
    # Hijack stdout BEFORE importing hermes (done below) so its prints become logs.
    _capture_stdout()
    # Emit immediately so the activity feed shows something within ~200 ms of spawn
    emit({"type": "log", "msg": "[worker] starting..."})
    _hb_start()  # launch the turn-silence watchdog

    # Parse args:
    #   hermes_worker.py <message>                       — one-shot, new session
    #   hermes_worker.py --resume <session_id> <message> — one-shot, resume
    #   hermes_worker.py --persistent [<session_id>]     — long-lived; turns arrive
    #                                                       as JSON commands on stdin
    resume_session_id = None
    user_message = None
    persistent = False
    argv = sys.argv[1:]
    if argv and argv[0] == "--persistent":
        persistent = True
        if len(argv) >= 2:
            resume_session_id = argv[1]   # resume an existing session id
    elif len(argv) >= 3 and argv[0] == "--resume":
        resume_session_id = argv[1]
        user_message = argv[2]
    elif argv:
        user_message = argv[0]
    else:
        emit({"type": "error", "msg": "usage: hermes_worker.py [--resume <id> <msg> | --persistent [<id>] | <msg>]"})
        sys.exit(1)

    workdir = os.environ.get("HERMES_WORKDIR", os.getcwd())
    try:
        os.chdir(workdir)
    except Exception:
        pass

    _capture_gui_docker_env()
    _patch_docker_path_translation()
    _patch_team_repo_writes()
    if os.environ.get("HERMES_GUI_FORCE_DOCKER_RESET", "").strip().lower() in ("1", "true", "yes"):
        _reset_docker_for_team_repo()

    try:
        from run_agent import AIAgent      # noqa: PLC0415
        from hermes_state import SessionDB  # noqa: PLC0415
    except Exception as exc:
        emit({"type": "error", "msg": f"hermes import failed: {exc}"})
        sys.exit(1)

    # Confine the host-side read/search tools to this desk's workspace, mirroring
    # the write confinement (HERMES_WRITE_SAFE_ROOT). Best-effort defense in depth
    # — see agent_gui/read_guard.py and SECURITY_NOTES.md. Must run before the
    # agent invokes any tool; the Hermes call sites resolve their targets at call
    # time, so patching here (post-import) takes effect.
    # The team File Repo (HERMES_GUI_TEAM_REPO) is an explicitly-shared dir mounted
    # for the whole team; allow reads/search there too, mirroring the write side
    # (_patch_team_repo_writes). Without this, team_files/ reads resolve outside the
    # workspace and the guard denies them — the reason the file toolset was disabled.
    # extra_roots also includes the Docker workspace root: file tools execute in
    # the sandbox, so the agent addresses read_file/search_files with container
    # paths (/workspace/..., /workspace/team_files/...). Those resolve outside the
    # host workspace and the guard would deny them; allow the /workspace root since
    # the Docker mounts are the real boundary for sandbox-executed reads.
    try:
        from agent_gui.read_guard import install as _install_read_guard  # noqa: PLC0415
        _team_repo = os.environ.get("HERMES_GUI_TEAM_REPO", "").strip()
        _docker_ws = (os.environ.get("HERMES_GUI_DOCKER_WORKSPACE", "").strip()
                      or ("/workspace" if _team_repo else ""))
        _extra_roots = [r for r in (_team_repo, _docker_ws) if r]
        _install_read_guard(workdir, extra_roots=_extra_roots,
                            log=lambda m: emit({"type": "log", "msg": m}))
    except Exception as exc:
        emit({"type": "log", "msg": f"[read-guard] install skipped: {exc}"})

    # Hermes cli import clobbers TERMINAL_DOCKER_VOLUMES from profile config — restore
    # GUI mounts and patch _get_env_config() after import so team_files bind-mounts work.
    _restore_gui_docker_env()
    _patch_gui_docker_config()
    # Key this desk's Docker container to a unique per-desk task id so Hermes'
    # cross-process reuse can't attach this desk to another desk/team's container
    # (the shared-storage bug). Must run after terminal_tool is importable.
    _patch_desk_container_identity()
    # vision_analyze/video_analyze run host-side, so let them open the agent's
    # /workspace/... (incl. /workspace/team_files/...) paths by mapping back to host.
    _patch_vision_path_translation()
    if os.environ.get("HERMES_GUI_FORCE_DOCKER_RESET", "").strip().lower() in ("1", "true", "yes"):
        _reset_docker_for_team_repo()

    # ── Env-var overrides (set by the GUI server per-session) ────────────────
    env_reasoning_effort = os.environ.get("HERMES_REASONING_EFFORT", "")
    env_api_mode         = os.environ.get("HERMES_API_MODE", "")   # "openai" | "ollama"
    env_model            = os.environ.get("HERMES_MODEL", "")       # e.g. "qwen3:4b"

    # ── Load hermes config ───────────────────────────────────────────────────
    # HERMES_HOME is the per-desk gui_sandbox (isolated state.db). Config (model,
    # base_url, api_mode) comes from HERMES_GUI_CONFIG_HOME — which points at a
    # profile dir (./profiles/<agent>) when an agent is assigned, else ~/.hermes.
    cfg_model = ""
    cfg_base_url = ""
    cfg_provider = ""
    cfg_api_mode = ""
    cfg_api_key = ""
    cfg_reasoning_effort = ""
    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    config_home = Path(os.environ.get("HERMES_GUI_CONFIG_HOME", str(hermes_home)))
    try:
        import yaml  # noqa: PLC0415
        _cfg_path = config_home / "config.yaml"
        if _cfg_path.exists():
            _cfg = yaml.safe_load(_cfg_path.read_text(encoding="utf-8")) or {}
            _m = _cfg.get("model", {})
            cfg_model    = _m.get("default", "")
            cfg_base_url = _m.get("base_url", "")
            cfg_provider = _m.get("provider", "")
            cfg_api_mode = _m.get("api_mode", "")
            cfg_api_key  = _m.get("api_key", "") or ""
            cfg_reasoning_effort = _cfg.get("agent", {}).get("reasoning_effort", "")
    except Exception as exc:
        emit({"type": "log", "msg": f"[worker] config load warning: {exc}"})
    agent_profile = os.environ.get("HERMES_GUI_AGENT", "")
    emit({"type": "log", "msg": f"[worker] profile={agent_profile or '(default)'} hermes_home={hermes_home} config_home={config_home} model={cfg_model!r} base={cfg_base_url!r}"})

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_token(text: str) -> None:
        _hb_set(phase="responding")
        emit({"type": "token", "text": text})

    def on_tool_start(name: str) -> None:
        _hb_set(phase=f"running {name}")
        emit({"type": "tool_start", "name": name})

    def on_tool_done(*args) -> None:
        # Signature from tool_executor.py: (tc_id, name, args_dict, result)
        try:
            name = str(args[1]) if len(args) > 1 else ""
            tool_args = args[2] if len(args) > 2 else {}
            result = args[3] if len(args) > 3 else ""
            args_str = json.dumps(tool_args, ensure_ascii=False, default=str)[:6000] if tool_args else ""
            result_str = str(result)[:8000] if result else ""
        except Exception:
            name, args_str, result_str = "", "", ""
        _hb_set(phase="thinking")
        emit({"type": "tool_done", "name": name, "args": args_str, "result": result_str})

    def on_thinking(text: str) -> None:
        _hb_set(phase="reasoning")
        emit({"type": "thinking", "text": text})

    def on_status(event_type: str, msg: str = "") -> None:
        emit({"type": "status", "event": str(event_type), "msg": str(msg)})

    # ── Resolve model before reasoning (llama/mistral reject thinking params) ──
    is_ollama = _is_ollama_backend(cfg_base_url, cfg_provider)
    profile_assigned = bool(agent_profile)
    if profile_assigned and not env_model:
        effective_model = cfg_model
    else:
        effective_model = env_model or cfg_model

    # ── Resolve effective reasoning effort & build reasoning_config ──────────
    requested_effort = env_reasoning_effort or cfg_reasoning_effort or ""
    thinking_model = not is_ollama or _supports_ollama_think_param(effective_model)
    if not thinking_model:
        active_effort = ""
    else:
        active_effort = requested_effort or "medium"
    reasoning_config = None
    if active_effort:
        try:
            from hermes_constants import parse_reasoning_effort  # noqa: PLC0415
            reasoning_config = parse_reasoning_effort(active_effort)
        except Exception:
            pass
    if is_ollama and not thinking_model and requested_effort:
        emit({"type": "log", "msg": f"[worker] skipping reasoning for {effective_model!r} (no thinking support)"})
    emit({"type": "log", "msg": f"[worker] reasoning_effort={active_effort or '(none)'}"})

    # ── Resolve proxy mode ────────────────────────────────────────────────────
    # api_mode "ollama" → native /api/chat proxy (real-time thinking)
    # api_mode "chat_completions" / "openai" → OpenAI-compat /v1 (Ollama or vLLM)
    try:
        gui_num_ctx = int(os.environ.get("HERMES_GUI_NUM_CTX", "0") or 0)
    except (TypeError, ValueError):
        gui_num_ctx = 0
    use_native = _wants_native_ollama_proxy(cfg_api_mode, env_api_mode)
    raw_api_mode = cfg_api_mode or env_api_mode or ""
    effective_api_mode = _normalize_api_mode(raw_api_mode, cfg_base_url, cfg_provider)
    if raw_api_mode.strip().lower() == "codex_responses" and effective_api_mode == "chat_completions":
        label = "local Ollama" if is_ollama else "local vLLM"
        emit({"type": "log", "msg": f"[worker] api_mode codex_responses → chat_completions ({label})"})
    if raw_api_mode.strip().lower() == "openai" and effective_api_mode == "chat_completions":
        emit({"type": "log", "msg": "[worker] api_mode openai → chat_completions"})
    emit({"type": "log", "msg": f"[worker] effective_model={effective_model!r} api_mode={effective_api_mode!r} native={use_native} is_ollama={is_ollama} base={cfg_base_url!r}"})

    # ── Force reasoning effort onto Ollama's /v1 request ─────────────────────
    # Ollama /v1 honors top-level ``reasoning_effort`` ("none" → thinking off).
    # It silently ignores extra_body.think and extra_body.reasoning_effort —
    # verified on qwen3.5:4b. Hermes' custom profile only injects extra_body.think,
    # so request_overrides must set the top-level field via api_kwargs merge.
    request_overrides = None
    if is_ollama and active_effort and thinking_model:
        request_overrides = {"reasoning_effort": active_effort}
    # NOTE: current Hermes extracts delta.reasoning_content / delta.reasoning from
    # the chat-completions stream itself and fires reasoning_callback per delta
    # (chat_completion_helpers.py ~1796), so plain OpenAI-compat backends (vLLM,
    # Ollama /v1) stream reasoning live with NO proxy. The proxies below are kept
    # only for the Ollama paths they were built for: the native /api/chat proxy
    # adds think-param + num_ctx injection and <think>-tag extraction; the
    # OpenAI-compat proxy predates Hermes-native extraction and is retained as
    # the known-good Ollama /v1 path.
    effective_base_url = cfg_base_url
    if cfg_base_url and is_ollama:
        if use_native:
            effective_base_url = _start_ollama_native_proxy(cfg_base_url, on_thinking,
                                                             reasoning_effort=active_effort,
                                                             num_ctx=gui_num_ctx)
            emit({"type": "log", "msg": "[worker] using native Ollama proxy (real-time thinking)"})
        else:
            effective_base_url = _start_openai_proxy(cfg_base_url, on_thinking)
            emit({"type": "log", "msg": "[worker] using OpenAI-compat Ollama proxy"})
    elif cfg_base_url:
        emit({"type": "log", "msg": "[worker] direct OpenAI-compat backend (no proxy)"})

    emit({"type": "log", "msg": "[worker] ready, creating session..."})

    # ── Create agent ──────────────────────────────────────────────────────────
    try:
        db = SessionDB()
        # If the server pre-generated a session_id (new session fast-path),
        # use it so the session row matches what the client already knows.
        preassigned_id = os.environ.get("HERMES_SESSION_ID") if not resume_session_id else None
        # Desk isolation: every GUI desk shares one ~/.hermes (one state.db, one
        # memory store), so Hermes' cross-session memory injection and the
        # `session_search` tool otherwise leak one desk's conversation into another
        # (e.g. an old "AI research ideas" desk bleeding into an unrelated task).
        # Disable both for GUI workers; opt back in with HERMES_GUI_SHARE_MEMORY=1.
        share_memory = os.environ.get("HERMES_GUI_SHARE_MEMORY", "") == "1"
        # Toolset selection. Each enabled tool's JSON schema is injected into every
        # request's prompt; on a local model those schemas dominate prompt-eval time
        # (the bulk of time-to-first-token), so loading fewer tools is much faster.
        # Three layers, in priority order:
        #   1. HERMES_GUI_ENABLED_TOOLSETS — allowlist from the GUI (lean/custom/chat).
        #      An empty string means zero tools ("chat"). Presence signals the GUI
        #      chose a profile; use an allowlist so shared tools (web_search lives in
        #      both ``search`` and ``browser``) aren't stripped when browser is off.
        #   2. HERMES_GUI_DISABLED_TOOLSETS — legacy blocklist (old .hermes_tools files).
        #   3. HERMES_GUI_LEAN_TOOLS=1 / otherwise full.
        # session_search is always disabled for desk isolation unless sharing memory.
        base_disabled = [] if share_memory else ["session_search"]
        enabled_toolsets = None
        disabled = base_disabled
        if "HERMES_GUI_ENABLED_TOOLSETS" in os.environ:
            enabled_toolsets = [t.strip() for t in
                                os.environ["HERMES_GUI_ENABLED_TOOLSETS"].split(",") if t.strip()]
            disabled = base_disabled
        elif "HERMES_GUI_DISABLED_TOOLSETS" in os.environ:
            explicit = [t.strip() for t in
                        os.environ["HERMES_GUI_DISABLED_TOOLSETS"].split(",") if t.strip()]
            disabled = sorted(set(base_disabled) | set(explicit))
        elif os.environ.get("HERMES_GUI_LEAN_TOOLS", "") == "1":
            from agent_gui.desk_toolsets import lean_hermes_toolsets
            enabled_toolsets = lean_hermes_toolsets()
            disabled = base_disabled
        api_key = cfg_api_key or None
        if not api_key and cfg_base_url and (cfg_provider in ("custom", "") or "localhost" in cfg_base_url or "127.0.0.1" in cfg_base_url):
            api_key = "no-key-required"
        agent = AIAgent(
            quiet_mode=False,
            model=effective_model or None,
            base_url=effective_base_url or None,
            provider=cfg_provider or None,
            api_key=api_key,
            api_mode=effective_api_mode or None,
            reasoning_config=reasoning_config or None,
            request_overrides=request_overrides,
            session_db=db,
            session_id=resume_session_id or preassigned_id or None,
            stream_delta_callback=on_token,
            tool_gen_callback=on_tool_start,
            tool_complete_callback=on_tool_done,
            thinking_callback=on_thinking,
            reasoning_callback=on_thinking,
            status_callback=on_status,
            skip_memory=not share_memory,
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=disabled or None,
        )

        def _patched_vprint(self, *args, force: bool = False, **kwargs) -> None:
            if args:
                _emit_log(" ".join(str(a) for a in args))

        agent._vprint = types.MethodType(_patched_vprint, agent)  # type: ignore[method-assign]

        # ── Force the Ollama context window directly on the agent ────────────────
        # Hermes normally auto-detects num_ctx by probing the base_url with
        # /api/show (query_ollama_num_ctx). But the GUI hands Hermes the local
        # *proxy* URL (for live thinking), and the proxy doesn't answer those
        # detection probes — so detection returns None, no num_ctx is sent, and
        # Ollama's /v1 endpoint then loads the model at its full GGUF max (262K →
        # ~20 GB KV cache → a ~56 s cold load on every context-size change).
        # Setting agent._ollama_num_ctx here is exactly the value the custom
        # provider injects as extra_body.options.num_ctx, so it pins the runtime
        # context deterministically — no probe, no proxy dependence. Hermes refuses
        # tool use below MINIMUM_CONTEXT_LENGTH (64K), so this must stay ≥ that when
        # tools are loaded; 65536 keeps the model at ~11 GB and ~3 s to load.
        if gui_num_ctx:
            try:
                agent._ollama_num_ctx = int(gui_num_ctx)
                emit({"type": "log", "msg": f"[worker] pinned ollama num_ctx={gui_num_ctx}"})
            except Exception:
                pass

        try:
            agent._ensure_db_session()
        except Exception:
            pass

        emit({"type": "session_id", "id": agent.session_id})

        # Persistent mode: stay alive and process successive turns from stdin so the
        # heavy import + AIAgent init happens once and the model/connection stay warm.
        if persistent:
            _persistent_loop(agent, db)
            return

        # Load conversation history when resuming an existing session
        conversation_history = None
        if resume_session_id:
            try:
                conversation_history = db.get_messages_as_conversation(resume_session_id)
                emit({"type": "log", "msg": f"Resumed session with {len(conversation_history)} messages"})
            except Exception as exc:
                emit({"type": "log", "msg": f"Could not load history: {exc}"})

        # If images were passed via env var, build a vision content list so the
        # model sees the actual pixels rather than just a file-path text hint.
        resume_images_raw = os.environ.get("HERMES_GUI_RESUME_IMAGES", "")
        persist_msg: str | None = None
        run_msg: list | str = user_message
        if resume_images_raw and user_message:
            try:
                images = json.loads(resume_images_raw)
                if images:
                    run_msg = [{"type": "text", "text": user_message}]
                    for img in images:
                        run_msg.append({"type": "image_url", "image_url": {"url": img.get("data", "")}})
                    persist_msg = user_message  # store only text in DB
            except Exception:
                pass
        _hb_set(active=True, phase="thinking")
        try:
            result = agent.run_conversation(run_msg, conversation_history=conversation_history,
                                            persist_user_message=persist_msg)
        finally:
            _hb_set(active=False)
        # A swallowed terminal failure ends the pump at the error event (the
        # server preserves it for the feed); a healthy turn falls through to done.
        _emit_turn_failure(result)
        emit({"type": "done"})

    except Exception as exc:
        emit({"type": "error", "msg": str(exc), "traceback": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
