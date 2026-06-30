"""FastAPI server for Hermes GUI."""
import asyncio
import base64
import hashlib
import io
import json
import mimetypes
import tarfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent_gui.activity_parser import (
    MIN_MESSAGE_LEN,
    TOOL_ICONS,
    ActivityEvent,
    _files_from_tool,
    _tool_detail,
    _truncate,
    parse_activity,
)
from agent_gui.llm_backend import is_ollama_backend, should_apply_reasoning_effort
from agent_gui.llm_models import (
    fetch_llm_models,
    is_gemini_backend,
    read_profile_env,
    read_profile_provider,
    read_profile_providers,
)
from agent_gui.agent_capabilities import agent_capabilities
from agent_gui.agents import (
    PROFILE_PROTOTYPES,
    _read_model_info,
    create_profile_via_hermes,
    delete_profile,
    list_agents,
    list_prototypes,
    profile_dir,
    read_persona,
    resolve_agent_profile_dir,
    validate_new_profile_id,
    write_gui_meta,
    write_model_config,
    write_persona,
)
from agent_gui.desk_toolsets import (
    DESK_TOOLSETS,
    hermes_sets_for_ui_names,
    lean_enabled_names,
    lean_hermes_toolsets,
    parse_tools_marker,
    profile_config_sources,
    ui_names_from_legacy_disabled,
)
from agent_gui.db import HermesDB
from agent_gui.file_parser import build_file_tree, can_preview_file, extract_touched_files
from agent_gui.gui_config import GuiConfig, hermes_model_config, load_gui_config

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
_WORKER_SCRIPT = Path(__file__).parent / "hermes_worker.py"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _worker_repo_env() -> dict[str, str]:
    """Ensure worker subprocesses can ``import agent_gui`` (desk_toolsets, etc.)."""
    root = str(_REPO_ROOT)
    cur = os.environ.get("PYTHONPATH", "")
    parts = [root]
    if cur:
        parts.extend(p for p in cur.split(os.pathsep) if p and p != root)
    return {"PYTHONPATH": os.pathsep.join(parts)}
# Hermes ships its own venv; use that Python so all hermes deps are available.
_HERMES_VENV_PY = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python3"

# Claude Agent SDK agent runs in a SEPARATE worker, under the GUI's own
# interpreter (which has claude-agent-sdk) rather than the Hermes venv. A desk is a
# "Claude SDK desk" when its agent id is one of these reserved ids; such a desk
# carries HERMES_GUI_AGENT_KIND=claude in its env so the spawn picks the right
# worker. claude_worker.py speaks the SAME stdout/stdin protocol as hermes_worker.
# NOTE: the bare id "claude" is deliberately NOT reserved — it belongs to a Hermes
# profile that talks to the Anthropic API (install_profile.sh).
_CLAUDE_WORKER_SCRIPT = Path(__file__).parent / "claude_worker.py"
_CLAUDE_WORKER_PY = sys.executable
_CLAUDE_AGENT_IDS = frozenset({"claude-sdk", "claude-agent-sdk", "claude-code"})

# Credentials that outrank OAuth / `claude /login` in the claude CLI's precedence
# (API key > OAuth token > /login). A Claude SDK desk is subscription-only, so we
# strip every one of these from its worker env — a stale/wrong key (e.g. one that
# points at a disabled org) would otherwise silently shadow the login. Kept
# identical to claude_worker.OAUTH_ONLY_SCRUB_KEYS (asserted in tests).
_CLAUDE_OAUTH_ONLY_SCRUB_KEYS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
)


def _scrub_claude_oauth_only(env: dict) -> None:
    """Drop API-key / gateway / cloud creds from a Claude SDK desk's worker env so
    the CLI falls through to OAuth / `claude /login`. Mutates ``env`` in place.

    The SDK inherits the worker's full ``os.environ`` and its ``options.env`` can
    only OVERRIDE values (not delete them), so keeping these OUT of the spawned env
    is the only reliable way to force subscription-only auth."""
    for k in _CLAUDE_OAUTH_ONLY_SCRUB_KEYS:
        env.pop(k, None)

# Built-in roster card so the Claude Agent SDK agent shows up in the desk/agent picker exactly
# like a Hermes profile — selecting it sends agent="claude-sdk" through the normal
# new-desk flow (which places the desk on the office floor).
_CLAUDE_AGENT_CARD = {
    "id": "claude-sdk",
    "name": "Claude Agent SDK",
    "tagline": "Anthropic Claude Agent SDK · runs on your Claude login (no API key)",
    "color": "#d97757",
    "available": True,
    "model": "sonnet",
    "base_url": "claude-agent-sdk",
    "profile_path": "",
    "is_prototype": False,
    "clone_from": None,
}

# Selectable Claude model aliases for the desk model picker AND _claude_model's
# allowlist (one source of truth). The Claude Agent SDK resolves each alias to its
# latest snapshot, so these stay current without pinning a dated id. Sonnet is the
# default (see _CLAUDE_AGENT_CARD["model"]).
_CLAUDE_MODELS = ("sonnet", "opus", "haiku")

# Fallback agent for a new desk that doesn't explicitly pick a profile (the
# "Default" tile sends agent=""). Empty (the default) means: use the Hermes base
# config / Global Default Persona — so configuring that persona actually takes
# effect and the Claude Agent SDK runs only when it's explicitly chosen. Set
# AGENT_GUI_DEFAULT_AGENT="claude-sdk" to make brand-new desks default to the SDK.
_DEFAULT_AGENT = os.environ.get("AGENT_GUI_DEFAULT_AGENT", "").strip().lower()


def _claude_model(raw: str) -> str:
    """Keep only a Claude-valid model; drop anything else (e.g. an Ollama id the GUI
    sends as its default) so the Claude SDK uses the Claude Code default, not a 400."""
    m = (raw or "").strip()
    low = m.lower()
    return m if (low in _CLAUDE_MODELS or low.startswith("claude")) else ""


def _is_claude_agent(agent_id: str) -> bool:
    return (agent_id or "").strip().lower() in _CLAUDE_AGENT_IDS


def _worker_cmd(env: dict) -> "tuple[str, str]":
    """(interpreter, worker script) for this desk's agent kind.

    Claude desks (``HERMES_GUI_AGENT_KIND=claude``) run claude_worker.py under the
    GUI interpreter; every other desk runs hermes_worker.py under the Hermes venv."""
    if env.get("HERMES_GUI_AGENT_KIND") == "claude":
        return _CLAUDE_WORKER_PY, str(_CLAUDE_WORKER_SCRIPT)
    venv_py = str(_HERMES_VENV_PY)
    if not Path(venv_py).exists():
        venv_py = shutil.which("python3") or "python3"
    return venv_py, str(_WORKER_SCRIPT)

# Processes started from the workbench (keyed by session_id). With persistent
# workers this holds the proc only while a TURN is in flight (is_running semantics).
_running_procs: dict[str, asyncio.subprocess.Process] = {}

# ── Persistent per-desk workers (opt-in) ──────────────────────────────────────
# When HERMES_GUI_PERSISTENT_WORKER=1, each desk keeps one long-lived worker
# process that handles successive turns over stdin — so the heavy Hermes import +
# AIAgent init happens once and the model/connection stay warm (fast follow-ups).
_PERSISTENT_WORKERS = os.environ.get("HERMES_GUI_PERSISTENT_WORKER", "") == "1"
_persistent_procs: dict[str, asyncio.subprocess.Process] = {}   # session_id → warm proc
_turn_done_events: dict[str, asyncio.Event] = {}                # set by the pump on turn end

# Cap the Ollama context window for GUI desks. Hermes otherwise runs the model at
# its full GGUF max (e.g. 262K tokens → ~20 GB KV cache → a ~56 s cold load on every
# context-size change). The worker pins this on the agent (agent._ollama_num_ctx) so
# Ollama loads a small, fixed KV cache.
#
# This value is shared by EVERY GUI→Ollama call — the worker turn, the startup
# keepalive warm, title generation, and the auto-continue judge. They MUST all use
# the same number: Ollama keys a resident model by its context size, so a single
# mismatched request evicts the warm instance and forces a multi-second reload, and
# bouncing between two sizes reloads twice per turn. 65536 stays above Hermes'
# 64K MINIMUM_CONTEXT_LENGTH tool-use floor (below it Hermes refuses to run with
# tools loaded) while keeping the model at ~11 GB and ~3 s to load.
_NUM_CTX = os.environ.get("HERMES_GUI_NUM_CTX", "65536")
# Lean toolset, ON by default ("1"). Each enabled tool's JSON schema is injected
# into every request's prompt; on a local model that prompt-eval dominates time to
# first token. The lean set keeps file/terminal/search/todo/skills/memory/vision/etc.
# and drops browser automation and tts. Set HERMES_GUI_LEAN_TOOLS=0
# for the full toolset (e.g. desks that need browser automation). The worker reads this.
_LEAN_TOOLS = os.environ.get("HERMES_GUI_LEAN_TOOLS", "1")


def _profile_default_tools_enabled(
    profiles_root: Path, hermes_home: Path, agent_id: str,
) -> list[str] | None:
    """UI tool names from an agent profile's default preset (chat/lean/full)."""
    try:
        pdir = resolve_agent_profile_dir(profiles_root, hermes_home, agent_id)
        cap = agent_capabilities(pdir)
        preset = str(cap.get("default_preset") or "lean")
        presets = cap.get("presets") or {}
        val = presets.get(preset)
        return [str(x) for x in val] if isinstance(val, list) else None
    except Exception:
        return None


def _write_desk_tools_marker(ws: Path, enabled: list[str]) -> None:
    """Persist enabled UI toolset names for this desk (empty list = chat / zero tools)."""
    (ws / ".hermes_tools").write_text(",".join(enabled), encoding="utf-8")


def _default_desk_tools_for_agent(
    profiles_root: Path, hermes_home: Path, agent_id: str,
) -> list[str]:
    """Tool marker to write when a desk's agent assignment changes."""
    if agent_id:
        prof = _profile_default_tools_enabled(profiles_root, hermes_home, agent_id)
        if prof is not None:
            return prof
    if _LEAN_TOOLS == "1":
        return lean_enabled_names()
    return []


def _apply_toolset_profile(env: dict, tools_enabled: list[str] | None) -> None:
    """Set worker tool env from an explicit enabled list or the lean/full default.

    Uses an enabled-toolset allowlist so tools shared across Hermes toolsets
    (e.g. web_search in both ``search`` and ``browser``) are not stripped when
    browser is off.
    """
    env.pop("HERMES_GUI_DISABLED_TOOLSETS", None)
    env.pop("HERMES_GUI_ENABLED_TOOLSETS", None)
    if tools_enabled is not None:
        env["HERMES_GUI_ENABLED_TOOLSETS"] = ",".join(hermes_sets_for_ui_names(tools_enabled))
    elif _LEAN_TOOLS == "1":
        env["HERMES_GUI_ENABLED_TOOLSETS"] = ",".join(lean_hermes_toolsets())


# Per-session queues fed by _pump_worker; consumed by the activity WebSocket.
_live_queues: dict[str, asyncio.Queue] = {}
# Pending inspect requests: request id → Future resolved by the worker pump when
# the matching `inspect_result` event arrives. Lets the inspect endpoint correlate
# its reply without it leaking into the turn's activity stream.
_inspect_waiters: dict[str, asyncio.Future] = {}
# Per-session replay buffer of the CURRENT (uncommitted) turn's reconstructable
# events — partial reply tokens, reasoning, and tool calls. Hermes only flushes a
# turn's messages to its DB at the turn boundary, so without this a page reload
# mid-turn would show only the user bubble. A reconnecting activity WS replays this
# to restore the in-progress turn; it's cleared once the turn commits to the DB.
_live_event_buffer: dict[str, list[dict]] = {}
# Cap so a pathological turn can't grow the buffer without bound.
_LIVE_BUFFER_MAX_ENTRIES = 4000
_LIVE_BUFFER_MAX_TEXT = 200_000  # chars per coalesced token/thinking run
# Orphaned feed events. A turn that ends abnormally (API error, kill, Stop,
# barge-in) is never flushed to Hermes' DB, so the next DB snapshot would silently
# erase everything the user just watched stream. At such a turn boundary the live
# replay buffer is converted into ActivityEvent dicts (plus an Error/Interrupted
# marker saying why the turn ended) and kept here + in a per-desk sidecar file, to
# be merged into every DB-derived feed snapshot. Memory is a cache of the sidecar.
_orphan_feed_events: dict[str, list[dict]] = {}
_ORPHAN_FEED_MARKER = ".hermes_feed_orphans.json"
_ORPHAN_MAX_EVENTS = 400        # per desk, so the sidecar can't grow unbounded
_ORPHAN_MAX_DETAIL = 80_000     # chars kept per preserved message/reasoning event
# Subagent traces. When an agent calls delegate_task, Hermes spawns child agents
# whose lifecycle (start/thinking/tool/progress/complete) the worker relays as
# {"type":"subagent", ...} events. Unlike the parent's stream, these are kept
# durably per desk so each subagent gets its own persistent tab that survives
# turn boundaries, panel reopen, reconnect, AND a server restart. Memory is a
# cache of the sidecar; keyed session_id → {subagent_id → record}.
_subagent_records: dict[str, dict[str, dict]] = {}
_SUBAGENT_MARKER = ".hermes_subagents.json"
_SUBAGENT_MAX = 64              # distinct subagents kept per desk
_SUBAGENT_MAX_EVENTS = 600      # timeline events kept per subagent
_SUBAGENT_MAX_TEXT = 8_000      # chars kept per timeline entry
# Sessions whose in-flight turn was deliberately interrupted (Stop / barge-in /
# reassign / worker teardown). Consumed at the turn boundary: it tells the pump
# the turn did NOT commit to the DB, so its buffer must be preserved as orphans.
_session_turn_interrupted: set[str] = set()
# session_id → workspace path (populated on new_session, survives across requests)
_session_workspaces: dict[str, str] = {}
# Fingerprint of TERMINAL_DOCKER_VOLUMES each persistent worker was spawned with.
_session_docker_vols: dict[str, str] = {}
# reasoning_effort, api_mode, and enabled-toolsets fingerprint the warm worker was spawned with.
_session_worker_opts: dict[str, tuple[str, str, str]] = {}
# Team file repos: a shared, copy-in set of files every desk in a team can read.
# Frontend `team.id` → set of session_ids in that team; reverse map for cleanup.
_team_sessions: dict[str, set[str]] = {}
_session_team: dict[str, str] = {}
# Subdirectory inside each desk workspace where a team's shared files are copied.
_TEAM_FILES_SUBDIR = "team_files"
# Path agents should use inside the desk Docker container.
_DOCKER_WORKSPACE = "/workspace"
# Persisted in each desk workspace so team membership survives server restarts.
_DESK_TEAM_MARKER = ".hermes_team_id"
# Browser streams cancelled by the graceful-shutdown timeout, by kind ("activity",
# "console", …) and by session id. Purely diagnostic: uvicorn only reports "Cancel
# N running task(s)", and N is the number of open WebSockets (≈3 per desk panel,
# per tab) — not running agents — which reads alarmingly. _shutdown_cleanup prints
# a breakdown from these instead.
_ws_cancelled_kinds: dict[str, int] = {}
_ws_cancelled_sids: set[str] = set()

def _note_ws_cancelled(kind: str, session_id: str) -> None:
    _ws_cancelled_kinds[kind] = _ws_cancelled_kinds.get(kind, 0) + 1
    _ws_cancelled_sids.add(session_id)

# Per-session list of queues for terminal WebSocket subscribers
_terminal_queues: dict[str, list[asyncio.Queue]] = {}
# Per-session list of queues for the clean console WebSocket (shell tool I/O only)
_console_queues: dict[str, list[asyncio.Queue]] = {}
# ── Persisted Agent Console + Debug terminal logs ─────────────────────────────
# The console/terminal WS streams are otherwise ephemeral: token/thinking/stderr
# output is never in Hermes' state.db, and an interrupted turn isn't committed at
# all — so reopening a desk after a refresh used to show nothing (interrupted) or
# only the post-refresh tail (in-flight). Fix: tee every broadcast into the current
# turn's in-memory buffer (replayed to a reconnecting WS so the in-flight turn is
# whole), and flush that buffer to a per-desk on-disk log at each turn boundary —
# committed OR interrupted — so the FULL verbatim history survives reloads. The log
# lives in the desk's private HERMES_HOME (outside the agent's /workspace), seeded
# once from the DB for desks that already had committed turns. See get_console /
# get_terminal (read the log) and the console/terminal WS endpoints (replay buffer).
# Value shape: session_id → [list[str] chunks, int total_chars] for the live turn.
_console_turn_buf: dict[str, list] = {}
_terminal_turn_buf: dict[str, list] = {}
_log_seeded: set[str] = set()                  # desks whose on-disk log was seeded this run
_CONSOLE_LOG_NAME = ".hermes_gui_console.log"
_TERMINAL_LOG_NAME = ".hermes_gui_terminal.log"
_TURN_LOG_MAX_CHARS = 1_000_000   # in-memory per-turn buffer cap (chars), per kind
_DESK_LOG_MAX_BYTES = 8_000_000   # on-disk log cap (bytes), per kind; front-trimmed
# Sessions the user has explicitly sent to sleep — auto-resume is blocked until wake.
# Persisted to ~/.hermes/gui_sleeping.json so restarts don't lose the sleeping state.
_session_sleeping: set[str] = set()
_SLEEPING_FILE = Path.home() / ".hermes" / "gui_sleeping.json"

def _load_sleeping() -> None:
    try:
        data = json.loads(_SLEEPING_FILE.read_text())
        _session_sleeping.update(data if isinstance(data, list) else [])
    except Exception:
        pass

def _save_sleeping() -> None:
    try:
        _SLEEPING_FILE.write_text(json.dumps(sorted(_session_sleeping)))
    except Exception:
        pass

_load_sleeping()
# session_id → generated short title
_session_titles: dict[str, str] = {}
# session_id → latest structured manager audit (see _run_audit). In-memory; the
# human-readable form is also persisted to AUDIT.md in the workspace.
_session_audits: dict[str, dict] = {}
# session_id → consecutive failing audits that triggered a manager nudge. Bounds
# the audit→fix→re-audit loop so the manager and agent can't ping-pong forever;
# resets to 0 once an audit passes. See _MANAGER_MAX_INTERVENTIONS.
# session_id → consecutive *no-progress* failing audits. Resets when the agent
# improves (more checks pass) so the manager keeps nudging a task toward done;
# only genuinely stuck work (no improvement) hits the cap and escalates.
_session_manager_resumes: dict[str, int] = {}
# session_id → best (max) number of checks ever passed, used to detect progress.
_session_audit_best: dict[str, int] = {}
_MANAGER_MAX_INTERVENTIONS = 3
# task_solved is persisted as a tiny sidecar file in the workspace (durable across
# server restarts) rather than only in memory. Written when an audit passes every
# check; removed when an audit finds issues or the agent is resumed (does more
# work). The leading dot keeps it out of the state hash and the file tree.
_SOLVED_MARKER = ".audit_passed"


def _save_attachments(attachments: list, workspace_dir: "Path",
                      *, is_claude: bool = False) -> tuple[list[str], str]:
    """Save base64-encoded image attachments to workspace_dir.

    Returns (saved_host_paths, image_note_text).  safe against path traversal.
    The note is agent-specific: Hermes desks get a ``vision_analyze`` hint; the
    Claude Code agent (``is_claude``) is told to open the files with its Read tool.
    """
    MAX_ATTACH_BYTES = 25 * 1024 * 1024
    saved: list[str] = []
    for att in attachments:
        name = Path(att.get("name", "").replace("\\", "/")).name
        name = "".join(" " if (ord(c) > 127 and c.isspace()) or c == " " else c for c in name)
        data = att.get("data", "")
        if not name or name in (".", "..") or not data:
            continue
        try:
            raw_b64 = data.split(",", 1)[1] if "," in data else data
            img_bytes = base64.b64decode(raw_b64)
            if len(img_bytes) > MAX_ATTACH_BYTES:
                continue
            dest = workspace_dir / name
            dest.write_bytes(img_bytes)
            saved.append(str(dest))
        except Exception:
            pass
    if saved:
        note = ("\nAttached images saved to your workspace — open them with the Read tool:\n"
                if is_claude else
                "\nAttached images saved to workspace (use these host paths with vision_analyze):\n")
        for p in saved:
            note += f"  - {p}\n"
    else:
        note = ""
    return saved, note


def _mark_solved(ws: "Path", solved: bool, state_hash: str = "") -> None:
    try:
        marker = ws / _SOLVED_MARKER
        if solved:
            marker.write_text(state_hash)
        else:
            marker.unlink(missing_ok=True)
    except Exception:
        pass


def _append_task_request(ws: "Path", request: str) -> None:
    """Fold a new user request into TASK.md as a follow-up entry.

    When a desk's task was already solved, a new chat request is a NEW goal — but
    nothing used to update TASK.md, so both the agent and the manager kept evaluating
    the old (already-done) task and stayed idle. We append the request to the *human
    task* portion of TASK.md, inserted BEFORE any appended manager block so
    `_clean_goal` keeps it (the auditor's clean goal — and the agent — now reflect it).
    """
    task_md = ws / "TASK.md"
    try:
        existing = (task_md.read_text(encoding="utf-8", errors="replace")
                    if task_md.exists() else "# Task\n")
    except Exception:
        existing = "# Task\n"
    block = f"\n\n## Follow-up request ({time.strftime('%Y-%m-%d %H:%M')})\n{request.strip()}\n"
    # Same cut point _clean_goal uses for appended manager blocks ("\n---\n📋 …").
    cut = re.search(r"\n-{3,}\s*\n\s*📋", existing)
    new = (existing[:cut.start()] + block + existing[cut.start():]) if cut \
        else (existing.rstrip() + block)
    try:
        task_md.write_text(new, encoding="utf-8")
    except Exception:
        pass


RUN_HISTORY_MARKER = ".hermes_run_history.json"


def _append_run_history(ws: "Path | None", entry: dict) -> None:
    """Append one run (a start or a resume) to the desk's run-history log.

    Resumes reuse the desk's session id (no new db row per resume in persistent
    mode), so the desk db can't reconstruct the run history. This append-only log
    records EVERY run with its timestamp, profile, model, and session id — so a
    desk that ran 5 times shows 5 entries even if profile/model never changed.
    """
    if ws is None:
        return
    try:
        path = ws / RUN_HISTORY_MARKER
        log: list = []
        if path.exists():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, list):
                    log = parsed
            except Exception:
                log = []
        log.append(entry)
        path.write_text(json.dumps(log), encoding="utf-8")
    except Exception:
        pass


def _load_run_history(ws: "Path | None") -> "list[dict]":
    if ws is None:
        return []
    path = ws / RUN_HISTORY_MARKER
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [e for e in parsed if isinstance(e, dict)] if isinstance(parsed, list) else []


PROFILE_HISTORY_MARKER = ".hermes_profile_history.json"


def _append_profile_history(ws: "Path | None", profile: str) -> None:
    """Record the desk's profile whenever it changes, with a timestamp.

    The desk db stores a *model* per session row but not the agent profile, so
    to show which profile each session-row ran under we log every profile change
    here and later map each row to the profile in effect when it started.
    Consecutive duplicates (no change) are skipped.
    """
    if ws is None:
        return
    try:
        path = ws / PROFILE_HISTORY_MARKER
        log: list = []
        if path.exists():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, list):
                    log = parsed
            except Exception:
                log = []
        prof = profile or ""
        if log and isinstance(log[-1], dict) and log[-1].get("profile", "") == prof:
            return
        log.append({"at": time.time(), "profile": prof})
        path.write_text(json.dumps(log), encoding="utf-8")
    except Exception:
        pass


def _load_profile_history(ws: "Path | None") -> "list[tuple[float, str]]":
    """Profile-change log as sorted (epoch, profile) pairs (oldest first)."""
    if ws is None:
        return []
    path = ws / PROFILE_HISTORY_MARKER
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: "list[tuple[float, str]]" = []
    if isinstance(parsed, list):
        for e in parsed:
            if not isinstance(e, dict):
                continue
            try:
                out.append((float(e["at"]), str(e.get("profile", ""))))
            except Exception:
                continue
    out.sort(key=lambda x: x[0])
    return out


def _profile_at(plog: "list[tuple[float, str]]", started_at_iso: str, fallback: str) -> str:
    """The profile in effect when a session row started — the most recent change
    at-or-before its start time. Falls back to the current marker if unlogged."""
    if not plog:
        return fallback
    try:
        ep = datetime.fromisoformat((started_at_iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        ep = None
    chosen = plog[0][1]
    for at, prof in plog:
        if ep is None or at <= ep:
            chosen = prof
        else:
            break
    return chosen


_AUDIT_ENTRY_MARKER = "<!-- audit goal="


def _write_audit_md(ws: "Path", entry_body: str, goal_hash: str) -> None:
    """Append a new audit entry to AUDIT.md, never clobbering entries for *other*
    tasks. Re-auditing the SAME task (same goal_hash) replaces only that task's
    latest entry; a NEW task (different goal_hash) is appended below the history.
    """
    path = ws / "AUDIT.md"
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        existing = ""

    # If the most recent entry is for the same task, drop it so we re-write it
    # fresh; otherwise keep everything and append below.
    idx = existing.rfind(_AUDIT_ENTRY_MARKER)
    if idx != -1:
        m = re.match(r"<!-- audit goal=([0-9a-f]+) -->", existing[idx:])
        if m and m.group(1) == goal_hash:
            existing = existing[:idx]

    entry = f"{_AUDIT_ENTRY_MARKER}{goal_hash} -->\n{entry_body.rstrip()}\n"
    head = "# Manager audits\n\n" if not existing.strip() else existing.rstrip() + "\n\n"
    try:
        path.write_text(head + entry, encoding="utf-8")
    except Exception:
        pass
# session_id → session token count when its title was last (re)generated, used to
# throttle periodic title refreshes to roughly once per _TITLE_TOKEN_INTERVAL tokens.
_session_title_tokens: dict[str, int] = {}
_TITLE_TOKEN_INTERVAL = 10_000
# Set in create_app so the module-level title helpers can read recent activity.
_db_ref: "HermesDB | None" = None
_home_ref: Path | None = None
_gui_cfg: GuiConfig | None = None

# ── Real per-event timestamps (in-memory, Option 2) ───────────────────────────
# Hermes batch-flushes a whole turn's messages to its DB at the turn boundary, so
# every event in a turn shares one clustered timestamp (and several tool calls in
# one assistant message are truly indistinguishable). The ONLY source of genuine
# timing is the worker's live event stream, which now stamps each event with its
# real emit time (`ts`). We record those here as ordered per-kind markers and
# overlay them onto the DB-derived feed (see _apply_real_times), so the app shows
# true times when known and clearly-marked approximate times otherwise — never a
# fabricated one. In-memory only: after a server restart, sessions from earlier
# runs fall back to the coarse flush times (shown as approximate).
_session_event_times: dict[str, list[tuple[str, float, str]]] = {}  # sid → [(kind, ts, tool_name)]


def _record_event_time(session_id: str, kind: str, ts: "float | None" = None,
                       tool_name: str = "") -> None:
    """Append a real per-event time marker (kind ∈ user_message/message/tool_call/tool_result)."""
    if kind == "user_message":
        # Turn start: fold any markers persisted by a PRIOR server run into memory
        # before this turn records its first marker, so the on-disk store (rewritten
        # at turn end) is never clobbered with only the new run's markers. The
        # load runs exactly once per desk per run and only seeds an empty in-memory
        # list, so it can't duplicate live markers. Idempotent + cheap.
        _load_event_times(session_id)
    _session_event_times.setdefault(session_id, []).append(
        (kind, float(ts if ts is not None else time.time()), tool_name))


def _record_worker_evt_time(session_id: str, evt: dict, state: dict) -> None:
    """Turn a live worker event into a feed-time marker, using its real `ts`.

    `state` carries per-turn run tracking across calls (mutable dict): a run of
    `token` events = one assistant 'message' and a run of `thinking` events = one
    'thinking_start' reasoning step, so we mark only the first event of each run.
    Tool boundaries and switching between text/thinking end the current run.

    Recording thinking_start times matters: without a real time, reasoning events
    keep Hermes's batch-flush timestamp (≈ end of turn), and the overview's
    timestamp sort then shoves all reasoning to the back of the turn (the feed,
    which isn't sorted, stays correct). log/status aren't feed events.
    """
    t = evt.get("type")
    ts = evt.get("ts")
    if t == "tool_start":
        state["in_text"] = state["in_thinking"] = False
        _record_event_time(session_id, "tool_call", ts, evt.get("name", ""))
    elif t == "tool_done":
        state["in_text"] = state["in_thinking"] = False
        _record_event_time(session_id, "tool_result", ts, evt.get("name", ""))
    elif t == "token":
        state["in_thinking"] = False
        # A run of `token` events = one assistant 'message' — but record its marker
        # only once the run is substantive, mirroring the thinking branch below.
        # parse_activity emits a message event only when content's stripped length
        # exceeds MIN_MESSAGE_LEN; a run that stays whitespace-only or tiny (e.g. a
        # stray space a model emits right before a tool call) would otherwise add a
        # surplus 'message' marker. _apply_real_times matches markers FIFO per kind,
        # so each surplus marker shifts every later message onto an earlier run's
        # time — which is what desynced agent messages from their tool calls.
        # Applying the SAME threshold keeps the marker count aligned 1:1.
        if not state.get("in_text"):
            state["in_text"] = True
            state["text_marked"] = False
            state["text_run"] = ""
        if not state.get("text_marked"):
            state["text_run"] = (state.get("text_run") or "") + (evt.get("text") or "")
            if len(state["text_run"].strip()) > MIN_MESSAGE_LEN:
                state["text_marked"] = True
                _record_event_time(session_id, "message", ts)
    elif t == "thinking":
        state["in_text"] = False
        # A new reasoning run starts here, but DON'T record its marker yet —
        # wait for the first NON-whitespace token. parse_activity drops
        # whitespace-only reasoning (the empty `<think>\n\n</think>` qwen3-style
        # models emit on most tool-calling turns: `if (reasoning := …strip()):`),
        # so a marker per whitespace run would make thinking_start markers
        # outnumber the feed's reasoning events. _apply_real_times matches them
        # FIFO per kind, so the surplus early markers shifted every real reasoning
        # step's time to an earlier run's — every trace after the first showed a
        # stuck, too-early timestamp. Recording only substantive runs keeps the
        # marker count aligned 1:1 with the parsed events.
        if not state.get("in_thinking"):
            state["in_thinking"] = True
            state["thinking_marked"] = False
        if not state.get("thinking_marked") and (evt.get("text") or "").strip():
            state["thinking_marked"] = True
            _record_event_time(session_id, "thinking_start", ts)


# ── Persisting per-event time markers (so they survive a server restart) ──────
# The in-memory markers above are wiped on restart — which is exactly when the
# overview/feed lose their real per-event timing and fall back to Hermes's coarse
# batch-flush times (the "Overview collapses to a sliver" bug). We mirror the
# markers to a small GUI-owned per-desk file so the *recording* path stays
# authoritative across restarts WITHOUT touching Hermes's state.db (we're
# read-only, pinned to v0.15.1) or parsing agent.log. The file lives in the desk's
# private HERMES_HOME (gui_sandboxes/<sid>), OUTSIDE the agent's /workspace bind
# mount, so it's invisible to the agent + Files tab and is removed with the
# sandbox on desk delete. Stored as a JSON array of [kind, ts, tool_name] rows,
# matching the in-memory `_session_event_times` shape 1:1.
_EVENT_TIMES_NAME = ".hermes_gui_event_times.json"
_event_times_loaded: set[str] = set()  # desks whose on-disk markers we've folded in


def _event_times_path(session_id: str) -> "Path | None":
    # Canonical per-desk private home first — resolves even when _session_workspaces
    # isn't populated yet (e.g. the first read right after a server restart), which
    # _desk_state_dir depends on. Each desk reads/writes ONLY its own store, so the
    # overview's per-desk merge never pools markers across related sessions
    # (DESK_ISOLATION_AUDIT invariant).
    db = _db_ref
    if db is not None:
        cand = db._sandbox_root / session_id
        if cand.is_dir():
            return cand / _EVENT_TIMES_NAME
    # Legacy slug layout (no gui_sandboxes base): the workspace-derived state dir.
    base = _desk_state_dir(session_id)
    return base / _EVENT_TIMES_NAME if base else None


def _load_event_times(session_id: str) -> None:
    """Fold a desk's persisted markers into memory once per server run.

    Always runs BEFORE this run records any marker for the desk — at turn start
    (via _record_event_time's user_message branch), at pump start, and on the read
    path (via _apply_real_times) — so it only ever seeds an EMPTY in-memory list
    and can't duplicate markers already recorded live. After a restart this
    restores the full per-event timing the overview/feed need; for a desk that
    already streamed this run (in-memory non-empty) the disk copy is a subset, so
    we skip to avoid double-counting."""
    if session_id in _event_times_loaded:
        return
    _event_times_loaded.add(session_id)
    if _session_event_times.get(session_id):
        return  # live markers already recorded this run — disk would duplicate them
    path = _event_times_path(session_id)
    if path is None or not path.exists():
        return
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    markers: list[tuple[str, float, str]] = []
    for row in parsed if isinstance(parsed, list) else []:
        if isinstance(row, list) and len(row) >= 2:
            try:
                markers.append((str(row[0]), float(row[1]),
                                str(row[2]) if len(row) > 2 and row[2] else ""))
            except (TypeError, ValueError):
                continue
    if markers:
        _session_event_times[session_id] = markers


def _persist_event_times(session_id: str) -> None:
    """Mirror a desk's in-memory markers to its private store at a turn boundary.

    Called AFTER any orphan-turn marker pruning, so the file always equals the
    authoritative in-memory list (committed-turn markers only). Atomic rewrite
    (temp + os.replace) so a crash mid-write can't leave a torn file that would
    read back as 'no timing'. No-op when there's nothing recorded, so an empty turn
    never wipes a good file."""
    recorded = _session_event_times.get(session_id)
    if not recorded:
        return
    path = _event_times_path(session_id)
    if path is None:
        return
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps([[k, t, n] for k, t, n in recorded]),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _buffer_live_event(session_id: str, evt: dict) -> None:
    """Accumulate the current turn's reconstructable events for replay on reload.

    Consecutive `token`/`thinking` events are coalesced into a single growing run
    (so we replay one event, not thousands); `tool_start`/`tool_done` are kept as-is
    to preserve ordering. Transient events (log/status/heartbeat) are intentionally
    skipped — they carry no conversation state worth restoring. Must run BEFORE the
    event is put on the live queue so the buffer is always a superset of any queue
    backlog (lets a reconnecting WS discard the backlog and replay the buffer with
    no duplicates)."""
    t = evt.get("type")
    if t in ("token", "thinking"):
        buf = _live_event_buffer.setdefault(session_id, [])
        if buf and buf[-1].get("type") == t:
            prev = buf[-1].get("text") or ""
            if len(prev) < _LIVE_BUFFER_MAX_TEXT:
                buf[-1]["text"] = prev + (evt.get("text") or "")
        else:
            # Keep the run's start time so orphan preservation can give the event
            # its real position in the feed timeline.
            buf.append({"type": t, "text": evt.get("text") or "",
                        "ts": evt.get("ts") or time.time()})
    elif t in ("tool_start", "tool_done"):
        buf = _live_event_buffer.setdefault(session_id, [])
        if len(buf) < _LIVE_BUFFER_MAX_ENTRIES:
            buf.append({**evt, "ts": evt.get("ts") or time.time()})


def _clear_live_buffer(session_id: str) -> None:
    _live_event_buffer.pop(session_id, None)


def _orphan_sidecar_path(session_id: str) -> "Path | None":
    ws = _session_workspaces.get(session_id)
    return Path(ws) / _ORPHAN_FEED_MARKER if ws else None


def _load_orphan_feed(session_id: str, ws: "Path | None" = None) -> list[dict]:
    """Orphaned feed events for a desk: memory cache first, else the sidecar."""
    cached = _orphan_feed_events.get(session_id)
    if cached is not None:
        return cached
    path = (ws / _ORPHAN_FEED_MARKER) if ws else _orphan_sidecar_path(session_id)
    events: list[dict] = []
    if path is not None and path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                events = [e for e in parsed if isinstance(e, dict)]
        except Exception:
            events = []
    _orphan_feed_events[session_id] = events
    return events


def _append_orphan_feed(session_id: str, new_events: list[dict]) -> None:
    events = _load_orphan_feed(session_id)
    events.extend(new_events)
    del events[:-_ORPHAN_MAX_EVENTS]
    _orphan_feed_events[session_id] = events
    path = _orphan_sidecar_path(session_id)
    if path is None:
        return
    try:
        path.write_text(json.dumps(events), encoding="utf-8")
    except OSError:
        pass


def _orphan_event(ts: float, event_type: str, icon: str, title: str, detail: str,
                  tool_name: str = "", is_error: bool = False,
                  files_touched: "list[str] | None" = None) -> dict:
    return {
        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "event_type": event_type, "icon": icon, "title": title,
        "detail": detail[:_ORPHAN_MAX_DETAIL], "tool_name": tool_name,
        "is_error": is_error, "files_touched": files_touched or [],
        "time_exact": True,
    }


def _subagent_sidecar_path(session_id: str) -> "Path | None":
    ws = _session_workspaces.get(session_id)
    return Path(ws) / _SUBAGENT_MARKER if ws else None


def _load_subagents(session_id: str, ws: "Path | None" = None) -> dict[str, dict]:
    """Subagent records for a desk: memory cache first, else the sidecar."""
    cached = _subagent_records.get(session_id)
    if cached is not None:
        return cached
    path = (ws / _SUBAGENT_MARKER) if ws else _subagent_sidecar_path(session_id)
    recs: dict[str, dict] = {}
    if path is not None and path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                recs = {k: v for k, v in parsed.items() if isinstance(v, dict)}
        except Exception:
            recs = {}
    _subagent_records[session_id] = recs
    return recs


def _persist_subagents(session_id: str) -> None:
    path = _subagent_sidecar_path(session_id)
    if path is None:
        return
    try:
        path.write_text(json.dumps(_subagent_records.get(session_id, {})),
                        encoding="utf-8")
    except OSError:
        pass


def _buffer_subagent_event(session_id: str, evt: dict) -> None:
    """Fold a {"type":"subagent"} worker event into its desk's durable record.

    Builds/updates one record per subagent_id (goal=input, output, status, a
    capped timeline) and writes it to the per-desk sidecar so the tab and its
    I/O survive reloads and restarts. Runs alongside _buffer_live_event in the
    pumps; the live copy still streams to the client for real-time updates."""
    if evt.get("type") != "subagent":
        return
    sid = evt.get("subagent_id")
    if not sid:
        return
    recs = _load_subagents(session_id)
    ev = evt.get("event")
    try:
        ts = float(evt.get("ts") or time.time())
    except (TypeError, ValueError):
        ts = time.time()
    rec = recs.get(sid)
    if rec is None:
        if len(recs) >= _SUBAGENT_MAX:
            return  # cap distinct subagents per desk
        rec = {"subagent_id": sid, "goal": "", "status": "running",
               "started_at": ts, "ended_at": None, "output": "", "events": []}
        recs[sid] = rec
    for k in ("parent_id", "depth", "model", "task_index", "task_count"):
        if evt.get(k) is not None:
            rec[k] = evt[k]
    if ev == "start":
        rec["goal"] = evt.get("goal") or rec.get("goal") or ""
        rec["status"] = "running"
        rec.setdefault("started_at", ts)
    elif ev == "complete":
        rec["status"] = evt.get("status") or "ok"
        rec["output"] = str(evt.get("output") or rec.get("output") or "")[:_SUBAGENT_MAX_TEXT]
        rec["ended_at"] = ts
        for k in ("duration_seconds", "output_tokens", "api_calls", "cost_usd"):
            if evt.get(k) is not None:
                rec[k] = evt[k]
    timeline = rec.setdefault("events", [])
    if len(timeline) < _SUBAGENT_MAX_EVENTS:
        slim: dict = {"event": ev, "ts": ts}
        for k in ("text", "tool_name", "preview", "goal", "status",
                  "output", "duration_seconds"):
            val = evt.get(k)
            if val is not None:
                slim[k] = str(val)[:_SUBAGENT_MAX_TEXT] if isinstance(val, str) else val
        timeline.append(slim)
    _subagent_records[session_id] = recs
    _persist_subagents(session_id)
def _persist_claude_user_message(session_id: str, message: str) -> None:
    """Persist a Claude desk's user prompt to its orphan feed.

    Claude desks have no state.db, so the orphan feed is their ONLY durable store.
    The prompt is never part of the live event buffer (that holds worker output —
    tokens/thinking/tool calls), so without this a reloaded feed would show the
    agent's reply with no question. Strips the injected workspace/attachment header
    exactly as ``parse_activity`` does for DB-backed user messages, so the bubble
    matches a Hermes desk's; a header-only/blank message records nothing."""
    clean = strip_injected_prefix(message or "")
    if not clean:
        return
    _append_orphan_feed(session_id, [
        _orphan_event(time.time(), "user_message", "👤", "User", clean),
    ])


def _preserve_orphan_turn(session_id: str, *, error_msg: "str | None" = None,
                          interrupted: bool = False) -> None:
    """Keep a turn that will never reach Hermes' DB visible in the Activity feed.

    Converts the live replay buffer (partial reply, reasoning, tool calls) into
    persistent feed events, appends an Error/Interrupted marker explaining why the
    turn ended, and stores them for merging into every later DB snapshot. Clears
    the buffer; no-op when there is nothing to record."""
    buf = _live_event_buffer.pop(session_id, [])
    out: list[dict] = []
    pending_calls: dict[str, dict] = {}   # tool name → its tool_call event
    for evt in buf:
        t = evt.get("type")
        try:
            ts = float(evt.get("ts") or time.time())
        except (TypeError, ValueError):
            ts = time.time()
        if t == "token":
            text = (evt.get("text") or "").strip()
            if text:
                out.append(_orphan_event(ts, "message", "🤖", "Agent", text))
        elif t == "thinking":
            text = (evt.get("text") or "").strip()
            if text:
                out.append(_orphan_event(ts, "thinking_start", "💭", "Reasoning", text))
        elif t == "tool_start":
            name = evt.get("name") or "tool"
            ev = _orphan_event(ts, "tool_call",
                               TOOL_ICONS.get(name, TOOL_ICONS["default"]),
                               f"calling {name}", "", tool_name=name)
            pending_calls[name] = ev
            out.append(ev)
        elif t == "tool_done":
            name = evt.get("name") or "tool"
            args_raw = evt.get("args") or ""
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) and args_raw else {}
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            # tool_start events carry no args — backfill the call's detail now.
            call_ev = pending_calls.pop(name, None)
            if call_ev is not None and args:
                call_ev["detail"] = _tool_detail(name, args)
                call_ev["files_touched"] = _files_from_tool(name, args)
            result = evt.get("result") or ""
            is_err = False
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    is_err = bool(parsed.get("error")) and not parsed.get("success", True)
            except Exception:
                pass
            out.append(_orphan_event(
                ts, "tool_result",
                "❌" if is_err else TOOL_ICONS.get(name, TOOL_ICONS["default"]),
                f"{name} {'failed' if is_err else 'done'}",
                _truncate(result, 200), tool_name=name, is_error=is_err))
    if error_msg is not None:
        out.append(_orphan_event(time.time(), "error", "❌", "Error",
                                 _truncate(error_msg, 2000), is_error=True))
    elif interrupted:
        out.append(_orphan_event(time.time(), "message", "⏸", "Interrupted",
                                 "Turn stopped before completion."))
    if not out:
        return
    # The preserved events carry their real emit-times, so drop this turn's
    # live-recorded markers — otherwise _apply_real_times would hand them to the
    # NEXT turn's DB events and shift every later "exact" time backwards.
    buf_ts = [float(e["ts"]) for e in buf if e.get("ts")]
    if buf_ts:
        cutoff = min(buf_ts) - 0.001
        rec = _session_event_times.get(session_id)
        if rec:
            _session_event_times[session_id] = [
                m for m in rec if m[0] == "user_message" or m[1] < cutoff
            ]
    _append_orphan_feed(session_id, out)


def _merge_orphan_feed(events: "list[ActivityEvent]", session_id: str,
                       ws: "Path | None" = None) -> "list[ActivityEvent]":
    """Merge preserved orphan-turn events into a DB-derived feed, in time order.

    DB event order is preserved; each orphan is inserted before the first DB event
    with a later timestamp. An orphan whose exact content already appears in the
    DB is dropped — covers the race where a turn was flagged interrupted but
    actually committed, which would otherwise duplicate it."""
    orphans = _load_orphan_feed(session_id, ws)
    if not orphans:
        return events

    def _key(ts: str) -> float:
        return _iso_to_epoch(ts) or 0.0

    seen = {(e.event_type, (e.detail or "").strip()) for e in events}
    merged: list[ActivityEvent] = []
    i = 0
    for o in orphans:
        try:
            oe = ActivityEvent(**o)
        except TypeError:
            continue
        if oe.detail and (oe.event_type, oe.detail.strip()) in seen:
            continue
        ok = _key(oe.timestamp)
        while i < len(events) and _key(events[i].timestamp) <= ok:
            merged.append(events[i])
            i += 1
        merged.append(oe)
    merged.extend(events[i:])
    return merged


# The num_ctx-capped Ollama model variant the GUI runs (see _ensure_capped_model).
# Set at startup; falls back to the configured default model if capping fails.
_EFFECTIVE_MODEL: str = ""
_capped_cache: dict[str, str] = {}   # base model name → capped variant (or base)
# Team manager backend. When a profile is selected, its config.yaml + .env drive
# the aux (audit / judge / title) calls — so the manager can run on Gemini, vLLM,
# or Ollama, independent of the global default. Empty profile = default backend.
_MANAGER_PROFILE: str = ""        # selected profile id ("" = default ~/.hermes)
_MGR_BASE_URL: str = ""           # OpenAI-compat chat base for the selected profile
_MGR_MODEL: str = ""              # effective model (num_ctx-capped for Ollama)
_MGR_MODEL_DISPLAY: str = ""      # human-facing model name
_MGR_API_KEY: str = ""            # Bearer key injected into aux calls (e.g. Gemini)
_MGR_PROVIDER: str = ""

# Per-desk Docker: mount HERMES_WORKDIR → /workspace; reuse container across
# worker processes so pip/apt installs survive follow-ups (see HERMES_GUI_FORCE_DOCKER_RESET).
# NOTE: TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES stays "true" regardless of the
# cleanup policy below — it governs *within-desk* reuse (a follow-up turn's fresh
# worker reattaches to the same warm container). Tearing it down per turn would
# make every follow-up cold. Lifetime cleanup is GUI-driven instead (see below).
_DESK_DOCKER_ENV = {
    "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE": "true",
    "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES": "true",
}


# Optional GPU pinning for host-run agent workers. The Claude Code desk runs its
# Bash/training commands directly on the host (claude_worker.py — no Docker), so
# any CUDA work inherits the worker's environment. Set
# HERMES_GUI_CUDA_VISIBLE_DEVICES (e.g. "2", or "2,3") to confine every agent's
# GPU use to those devices. CUDA_DEVICE_ORDER=PCI_BUS_ID makes the index match
# `nvidia-smi` numbering instead of CUDA's fastest-first default, so "2" reliably
# means nvidia-smi GPU 2. Applied to the worker spawn only (not exported
# process-wide) so it doesn't also pin the server's own Ollama/aux model calls.
# Unset → no pinning (prior behavior, agent sees all GPUs).
def _gpu_worker_env() -> "dict[str, str]":
    dev = os.environ.get("HERMES_GUI_CUDA_VISIBLE_DEVICES", "").strip()
    if not dev:
        return {}
    return {"CUDA_VISIBLE_DEVICES": dev, "CUDA_DEVICE_ORDER": "PCI_BUS_ID"}


_GPU_ENV = _gpu_worker_env()

# Whether to KEEP a desk's sandbox container alive after the GUI is done with it.
#   off (default): the GUI reaps a desk's `hermes-*` container when the desk is
#     deleted, and reaps all of them on server shutdown — so containers don't
#     accumulate across desks/runs. A deleted desk no longer orphans its container.
#   on: legacy behavior — containers are kept warm across desk deletion and server
#     restarts; only the UI Reset button or a manual `docker rm` removes them.
# Within an *active* desk the container is always reused across turns either way;
# this only controls end-of-life cleanup. Toggleable at runtime from the ⚙ menu
# (GET/POST /api/docker/config); the choice is persisted to a sidecar so it sticks
# across restarts, with HERMES_GUI_DOCKER_PERSIST as the initial default (off).
# See DOCKER_MANAGEMENT.md.
_DOCKER_PERSIST_FILE = Path.home() / ".hermes" / "gui_docker_persist.json"

def _load_docker_persist() -> bool:
    try:
        return bool(json.loads(_DOCKER_PERSIST_FILE.read_text()).get("persist"))
    except Exception:
        return os.environ.get(
            "HERMES_GUI_DOCKER_PERSIST", "0").strip().lower() in ("1", "true", "yes")

def _save_docker_persist() -> None:
    try:
        _DOCKER_PERSIST_FILE.write_text(json.dumps({"persist": _DOCKER_PERSIST}))
    except Exception:
        pass

_DOCKER_PERSIST = _load_docker_persist()


def _is_ollama_url(base_url: str, provider: str = "") -> bool:
    """True when the configured backend is local Ollama."""
    return is_ollama_backend(base_url, provider)


def _ollama_root_from_base(base_url: str) -> str:
    """Ollama host root (…:11434) from an OpenAI-compat base URL."""
    from urllib.parse import urlparse

    p = urlparse((base_url or "").strip())
    host = p.hostname or "127.0.0.1"
    port = p.port or 11434
    scheme = p.scheme or "http"
    return f"{scheme}://{host}:{port}"


def _llm_health_url(base_url: str, provider: str = "") -> str:
    """Health-probe URL for the configured LLM backend.

    Ollama answers ``/api/version``; OpenAI-compatible servers (vLLM, Codex,
    etc.) do NOT — they answer ``/v1/models``. Probing ``/api/version`` against a
    vLLM server just spams its log with 404s (the "ollama artifact"), so route the
    probe by backend type instead.
    """
    from urllib.parse import urlparse

    base = (base_url or "").strip()
    use_ollama = (not base) or is_ollama_backend(base, provider)
    if use_ollama:
        if base:
            return f"{_ollama_root_from_base(base)}/api/version"
        return "http://127.0.0.1:11434/api/version"
    v1 = base.rstrip("/")
    if not v1.endswith("/v1"):
        v1 = f"{v1}/v1"
    return f"{v1}/models"


def _openai_models_url(base_url: str) -> str:
    v1 = (base_url or "").strip().rstrip("/")
    if not v1.endswith("/v1"):
        v1 = f"{v1}/v1"
    return f"{v1}/models"


async def _fetch_llm_models(base_url: str) -> list[str]:
    """Backward-compat wrapper — prefer agent_gui.llm_models.fetch_llm_models."""
    return await fetch_llm_models(base_url)


async def _resolve_gui_model(model: str, base_url: str, ollama_root: str) -> str:
    """Cap Ollama models only; vLLM/OpenAI-compat models pass through unchanged."""
    if not model:
        return ""
    if _is_ollama_url(base_url):
        return await _ensure_capped_model(model, ollama_root)
    return model


def _gui_num_ctx() -> int:
    """The context-window cap (tokens) all GUI Ollama calls must share."""
    try:
        return int(_NUM_CTX) if str(_NUM_CTX).strip() else 0
    except (TypeError, ValueError):
        return 0


def _capped_model_name(base: str) -> str:
    """Name of the num_ctx-capped variant of `base` (e.g. qwen3.5:9b → qwen3.5-9b:guictx65536)."""
    safe = base.replace(":", "-").replace("/", "-")
    return f"{safe}:guictx{_gui_num_ctx()}"


def _strip_guictx(model: str) -> str:
    """Drop the GUI's ``:guictx<N>`` suffix for display (qwen-9b:guictx65536 → qwen-9b)."""
    return re.sub(r":guictx\d+$", "", model or "", flags=re.IGNORECASE)


async def _ensure_capped_model(base: str, ollama_url: str) -> str:
    """Idempotently create a context-capped variant of an Ollama model.

    The single biggest source of GUI latency is Ollama loading the model at its
    full GGUF context (e.g. qwen3.5:9b → 262K tokens → ~20 GB KV cache → a ~56 s
    cold load), exactly like the difference between `ollama run` (defaults to a
    tiny 4K context, loads in ~2 s) and the agent path. The per-request num_ctx
    knob does NOT work: Ollama's OpenAI-compat /v1 endpoint (which Hermes uses)
    silently ignores options.num_ctx, and Hermes bypasses our local proxy. The
    ONLY lever Ollama honors on /v1 is a num_ctx baked into the model itself.

    So we derive a sibling model `<base>:guictx<N>` with `PARAMETER num_ctx N`.
    It shares the base model's weight layers (creation is ~instant and adds no
    disk), and every /v1 load of it pins the small context → ~3 s cold, ~11 GB.
    Idempotent and cached; returns the variant name, or `base` on any failure.
    """
    if not base:
        return base
    # Already a capped variant (e.g. a resumed desk passing back its own model, or
    # the frontend echoing _EFFECTIVE_MODEL) — don't cap a cap.
    if ":guictx" in base:
        return base
    if base in _capped_cache:
        return _capped_cache[base]
    variant = _capped_model_name(base)
    num_ctx = _gui_num_ctx()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            tags = (await client.get(f"{ollama_url}/api/tags")).json()
            names = {m.get("name", "") for m in tags.get("models", [])}
            if variant not in names:
                if base not in names:
                    # Not an Ollama model we can derive from — use it as-is.
                    _capped_cache[base] = base
                    return base
                r = await client.post(
                    f"{ollama_url}/api/create",
                    json={"model": variant, "from": base,
                          "parameters": {"num_ctx": num_ctx}},
                )
                if r.status_code != 200 or '"error"' in r.text:
                    _capped_cache[base] = base
                    return base
                # Confirm the variant is registered before returning it — create can
                # succeed asynchronously and a premature name causes 400/404 on /api/chat.
                tags = (await client.get(f"{ollama_url}/api/tags")).json()
                names = {m.get("name", "") for m in tags.get("models", [])}
                if variant not in names:
                    _capped_cache[base] = base
                    return base
        _capped_cache[base] = variant
        return variant
    except Exception:
        _capped_cache[base] = base
        return base


async def _ollama_model_installed(base_url: str, model: str) -> bool:
    """True when ``model`` is listed in Ollama /api/tags."""
    if not model or not _is_ollama_url(base_url):
        return True
    root = _ollama_root_from_base(base_url)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            tags = (await client.get(f"{root}/api/tags")).json()
            names = {m.get("name", "") for m in tags.get("models", [])}
            return model in names
    except Exception:
        return True


async def _fallback_ollama_model(base_url: str, model: str) -> str:
    """Return a model name Ollama can load — fall back from missing capped variants."""
    if not model or not _is_ollama_url(base_url):
        return model
    if await _ollama_model_installed(base_url, model):
        return model
    if ":guictx" in model:
        for base, variant in _capped_cache.items():
            if variant == model:
                if await _ollama_model_installed(base_url, base):
                    return base
                break
    return model


def _is_native_anthropic_provider(provider: str) -> bool:
    """True for Hermes' native Anthropic provider — the ``claude`` profile that
    install_profile.sh writes (``provider: anthropic``, ANTHROPIC_API_KEY).

    api.anthropic.com has no OpenAI /chat/completions route (→404), so a manager
    running on this profile must use the Anthropic Messages transport (x-api-key,
    /v1/messages), not _openai_aux_chat. Aliases mirror hermes_worker._is_native_
    anthropic so "native anthropic" means the same thing on both paths.
    """
    return (provider or "").strip().lower() in (
        "anthropic", "claude", "claude-oauth", "claude-code")


async def _ollama_aux_chat(base_url: str, model: str, prompt: str,
                           max_tokens: int) -> str:
    """Run a short auxiliary completion (title, judge, audit, progress).

    Uses OpenAI-compat ``/v1/chat/completions`` for every backend (Ollama and
    vLLM); the one exception is the native Anthropic manager profile, which routes
    through _anthropic_aux_chat (x-api-key, /v1/messages — api.anthropic.com has
    no /chat/completions route). Auxiliary calls do not need native ``/api/chat``
    or live thinking; routing them through ``/v1`` avoids 400s from missing capped
    variants, unsupported ``think`` on non-reasoning models (e.g. llama3.2:3b),
    and empty-model races at turn end. Returns "" on error.
    """
    if not base_url or not (model or "").strip():
        return ""
    # Only ever set when a manager profile is selected; the installed `claude`
    # profile is the one that needs the Messages transport.
    if _is_native_anthropic_provider(_MGR_PROVIDER):
        return await _anthropic_aux_chat(base_url, model.strip(), prompt, max_tokens)
    model = await _fallback_ollama_model(base_url, model.strip())
    return await _openai_aux_chat(base_url, model, prompt, max_tokens)


async def _openai_aux_chat(base_url: str, model: str, prompt: str,
                           max_tokens: int) -> str:
    """Auxiliary completion against an OpenAI-compatible backend (vLLM, etc.).

    Used for the manager's title/judge/audit/progress calls when the backend is
    not Ollama. Reasoning models (Qwen3) emit <think> blocks inline in the
    content; callers strip them, but we give a generous token budget so a short
    answer isn't entirely consumed by reasoning. Returns "" on error.
    """
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        # Accept either ".../v1" or a bare host; normalize to the chat route.
        url = url.split("/chat/completions", 1)[0].rstrip("/")
    endpoint = f"{url}/chat/completions"
    # Reasoning models (Qwen3) emit a long chain-of-thought before the answer —
    # and when vLLM runs WITHOUT --reasoning-parser that thinking lands inline in
    # `content`, eating the token budget before the JSON/answer appears. Pad
    # generously so the visible payload survives; capped to bound latency.
    budget = min(max(max_tokens * 12, 2048), 8192)
    headers = {"Content-Type": "application/json"}
    # Cloud manager profiles (e.g. Gemini's OpenAI-compat endpoint) need auth.
    if _MGR_API_KEY:
        headers["Authorization"] = f"Bearer {_MGR_API_KEY}"
    try:
        # 300 s: these calls share the agents' backend, so they queue behind any
        # in-flight agent generation. 120 s made busy-box audits time out → ""
        # → the manager's patrol ended with "Couldn't audit just now".
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                endpoint,
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "max_tokens": budget,
                    "temperature": 0.2,
                },
            )
        if r.status_code != 200:
            return ""
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content", "") or "").strip()
    except Exception:
        return ""


async def _anthropic_aux_chat(base_url: str, model: str, prompt: str,
                              max_tokens: int) -> str:
    """Auxiliary completion against the Anthropic Messages API.

    The ``claude`` profile (install_profile.sh) talks to api.anthropic.com, which
    has no OpenAI /chat/completions route — it uses x-api-key auth and POST
    /v1/messages with a distinct request/response shape. Mirrors the transport
    hermes_worker forces for the agent (api_mode: anthropic_messages). The key is
    read from the profile's ANTHROPIC_API_KEY into _MGR_API_KEY. Returns "" on error.
    """
    root = base_url.rstrip("/")
    # base_url is the API root (https://api.anthropic.com); tolerate a trailing /v1.
    if root.endswith("/v1"):
        root = root[: -len("/v1")].rstrip("/")
    endpoint = f"{root}/v1/messages"
    budget = min(max(max_tokens * 12, 2048), 8192)
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if _MGR_API_KEY:
        headers["x-api-key"] = _MGR_API_KEY
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                endpoint,
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": budget,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if r.status_code != 200:
            return ""
        data = r.json()
        # content is a list of blocks; concatenate the text blocks.
        blocks = data.get("content") or []
        text = "".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        )
        return text.strip()
    except Exception:
        return ""

# ── Heartbeat auto-continue ───────────────────────────────────────────────────
# Opt-in per desk: when an agent finishes a turn while still having unfinished
# work in TASK.md, automatically resume it so long/multi-step tasks keep going
# instead of idling. Bounded to avoid runaway loops/cost.
_session_autocontinue: dict[str, bool] = {}     # session_id → enabled
_session_continue_count: dict[str, int] = {}    # session_id → auto-resumes used
_session_user_stopped: set[str] = set()         # sessions the user explicitly stopped
_AUTO_CONTINUE_MAX = 25                          # hard cap on consecutive auto-resumes


def _iso_to_epoch(ts: str) -> "float | None":
    """Parse an ISO-8601 timestamp to epoch seconds, or None if unparseable."""
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _apply_real_times(events: list, session_id: str) -> None:
    """Overlay real recorded emit-times onto DB-derived feed events.

    Replaces the old synthetic redistribution. Markers are matched to events in
    order, independently per kind (a tool_call event takes the next recorded
    tool_call time, etc.), which is robust to parse-vs-stream ordering differences
    between event types. Matched events get the true time and `time_exact=True`;
    unmatched events keep Hermes's coarse batch-flush timestamp and stay
    `time_exact=False`, so the UI marks them approximate instead of inventing a
    time. No recorded data (e.g. after a server restart) → everything stays
    approximate.

    User messages get a guard the other kinds don't need. Hermes persists each
    user turn at turn-START with its own real timestamp (only the assistant/tool
    half of a turn is batch-flushed at one clustered time), so a user message's DB
    time is already accurate. The matching `user_message` marker is recorded
    server-side at that same turn-start, so for the correct pairing marker_time <=
    db_time always. We therefore let a marker only REFINE a user message earlier,
    never push it LATER: a marker sitting after a user message's DB time belongs
    to a later turn, so we skip it (leaving it for the message it really matches).
    Without this, a mid-desk server restart that wiped the original prompt's
    marker (in-memory only) — while the prompt's user row survived in the DB —
    left just the resume turn's marker, which FIFO then handed to the prompt,
    dragging its displayed time forward past the interrupted turn's orphan events.
    The feed then showed "orphans, then prompt, then resumed work" instead of
    prompt-first.
    """
    _load_event_times(session_id)  # restore markers persisted by a prior run (once)
    recorded = _session_event_times.get(session_id)
    if not recorded:
        return
    from collections import deque
    queues: dict[str, deque] = {}
    for kind, ts, _name in recorded:
        queues.setdefault(kind, deque()).append(ts)
    for e in events:
        kind = "message" if e.event_type == "compression" else e.event_type
        dq = queues.get(kind)
        if not dq:
            continue
        if e.event_type == "user_message":
            db_t = _iso_to_epoch(e.timestamp)
            # 1s slack absorbs same-host clock granularity; a mismatched
            # later-turn marker is minutes off, far beyond this.
            if db_t is not None and dq[0] > db_t + 1.0:
                continue
        try:
            e.timestamp = datetime.fromtimestamp(dq.popleft(), tz=timezone.utc).isoformat()
            e.time_exact = True
        except Exception:
            pass


def _evt_to_terminal(evt: dict) -> str:
    """Format a worker event as human-readable terminal text."""
    t = evt.get("type", "")
    if t == "log":
        return evt.get("msg", "") + "\n"
    if t == "token":
        return evt.get("text", "")
    if t == "thinking":
        return evt.get("text", "")
    if t == "tool_start":
        return f"\n── {evt.get('name', '')} ──\n"
    if t == "tool_done":
        r    = evt.get("result", "")
        a    = evt.get("args", "")
        name = evt.get("name", "")
        parts = [f"── {name} result ──"]
        if a:
            parts.append(f"args: {a}")
        if r:
            parts.append(r)
        return "\n".join(parts) + "\n\n"
    if t == "status":
        msg = evt.get("msg", "")
        return f"[status] {evt.get('event', '')} {msg}\n" if msg else ""
    if t == "error":
        return f"[error] {evt.get('msg', '')}\n"
    return ""


_SHELL_TOOLS = {"terminal", "execute_code", "bash", "process", "run_code", "patch"}

def _evt_to_console(session_id: str, evt: dict) -> str | None:
    """Return clean shell I/O text for the console WebSocket, or None to skip."""
    t = evt.get("type", "")
    name = evt.get("name", "")
    if t == "tool_start" and name in _SHELL_TOOLS:
        return f"\033[2m▶ {name}\033[0m\n"
    if t != "tool_done" or name not in _SHELL_TOOLS:
        return None
    args_raw = evt.get("args", "")
    result_raw = evt.get("result", "")
    try:
        args_dict = json.loads(args_raw) if args_raw else {}
    except Exception:
        args_dict = {}
    command = (args_dict.get("command") or args_dict.get("code") or
               args_dict.get("cmd") or args_dict.get("script") or "")
    try:
        result_dict = json.loads(result_raw) if result_raw else {}
        output = (result_dict.get("output") or result_dict.get("stdout") or
                  result_dict.get("result") or result_raw)
    except Exception:
        output = result_raw
    parts: list[str] = []
    if command:
        parts.append(f"\033[32m$\033[0m {command}")
    if output:
        parts.append(str(output).rstrip("\n"))
    parts.append("")
    return "\n".join(parts) + "\n"


def _messages_to_events(messages: list) -> list[dict]:
    """Reconstruct worker-style events from stored DB messages (best effort).

    Live token/thinking/log streaming is never persisted, so the rebuilt stream
    is coarser than the real-time one — but tool calls, their args, results, and
    assistant text are all in the DB, which is enough to repopulate the Console
    (shell I/O) and Debug terminal when a finished session is reopened.
    """
    events: list[dict] = []
    call_args: dict[str, tuple[str, str]] = {}  # tool_call_id -> (name, args_json)
    for msg in messages:
        if msg.role == "assistant":
            if msg.content and msg.content.strip():
                events.append({"type": "log", "msg": msg.content.strip()})
            for tc in (msg.tool_calls or []):
                if tc.get("type") != "function":
                    continue
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", "") or ""
                call_args[tc.get("id", "")] = (name, args)
                events.append({"type": "tool_start", "name": name})
        elif msg.role == "tool":
            name, args = call_args.get(
                msg.tool_call_id or "", (msg.tool_name or "tool", ""))
            events.append({
                "type": "tool_done", "name": name,
                "args": args, "result": msg.content or "",
            })
    return events


def _backfill_terminal(messages: list) -> str:
    """Rebuild the Debug terminal text from stored DB messages."""
    return "".join(_evt_to_terminal(e) for e in _messages_to_events(messages))


def _backfill_console(session_id: str, messages: list) -> str:
    """Rebuild the Agent Console (shell I/O only) text from stored DB messages."""
    out: list[str] = []
    for e in _messages_to_events(messages):
        text = _evt_to_console(session_id, e)
        if text:
            out.append(text)
    return "".join(out)


def _desk_state_dir(session_id: str) -> "Path | None":
    """The desk's private HERMES_HOME (``gui_sandboxes/<sid>``) — where GUI-only
    console/terminal logs live. It sits OUTSIDE the agent's ``/workspace`` bind
    mount, so the logs are invisible to the agent and the Files tab, and are removed
    with the sandbox on desk delete. Anchored on the same workspace map the orphan
    feed uses (set by the time a worker streams). Falls back to the workspace dir
    for the legacy slug layout (no ``gui_sandboxes`` base)."""
    ws = _session_workspaces.get(session_id)
    if not ws:
        return None
    p = Path(ws)
    try:
        if (p.name == "workspace" and p.parent.name == "default"
                and p.parents[1].name == "docker"):
            return p.parents[2]
    except IndexError:
        pass
    return p


def _desk_log_path(session_id: str, kind: str) -> "Path | None":
    base = _desk_state_dir(session_id)
    if base is None:
        return None
    return base / (_CONSOLE_LOG_NAME if kind == "console" else _TERMINAL_LOG_NAME)


def _read_desk_log(session_id: str, kind: str) -> str:
    path = _desk_log_path(session_id, kind)
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _append_desk_log(session_id: str, kind: str, text: str) -> None:
    """Append console/terminal text to a desk's on-disk log, capping growth by
    keeping the tail (the oldest output is the most expendable)."""
    if not text:
        return
    path = _desk_log_path(session_id, kind)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
        if path.stat().st_size > _DESK_LOG_MAX_BYTES:
            with path.open("rb") as fh:
                fh.seek(-_DESK_LOG_MAX_BYTES, os.SEEK_END)
                tail = fh.read()
            nl = tail.find(b"\n")          # drop a partial first line for cleanliness
            if 0 <= nl < len(tail) - 1:
                tail = tail[nl + 1:]
            path.write_bytes(tail)
    except OSError:
        pass


def _ensure_log_seeded(session_id: str) -> None:
    """Seed a desk's on-disk logs from its DB the first time we touch them this run,
    so a desk that already has committed turns (pre-feature, or after a restart with
    no log yet) keeps that history. Called at turn START, where the DB holds only
    PRIOR turns — the in-flight turn is flushed from the live buffer at turn end, so
    this can't duplicate it. No-op once seeded, when a log already exists, or before
    the workspace is known (retried on a later turn)."""
    if session_id in _log_seeded:
        return
    cpath = _desk_log_path(session_id, "console")
    tpath = _desk_log_path(session_id, "terminal")
    if cpath is None or tpath is None:
        return  # workspace not resolvable yet — retry on a later turn
    _log_seeded.add(session_id)
    if cpath.exists() and tpath.exists():
        return  # already populated (e.g. survived a server restart) — don't reseed
    db = _db_ref
    if db is None:
        return
    try:
        if db._desk_db(session_id):
            messages = db.get_desk_messages(session_id, limit=5000)
        else:
            messages = db.get_messages(session_id, limit=5000)
    except Exception:
        return
    try:
        if not cpath.exists():
            seed = _backfill_console(session_id, messages)
            if seed:
                cpath.write_text(seed, encoding="utf-8")
        if not tpath.exists():
            seed = _backfill_terminal(messages)
            if seed:
                tpath.write_text(seed, encoding="utf-8")
    except OSError:
        pass


def _buffer_turn_log(session_id: str, kind: str, text: str) -> None:
    """Accumulate the current turn's console/terminal text for WS replay + flush,
    capping the in-memory buffer by dropping the oldest chunks."""
    if not text:
        return
    store = _console_turn_buf if kind == "console" else _terminal_turn_buf
    entry = store.setdefault(session_id, [[], 0])
    entry[0].append(text)
    entry[1] += len(text)
    while entry[1] > _TURN_LOG_MAX_CHARS and len(entry[0]) > 1:
        entry[1] -= len(entry[0].pop(0))


def _turn_log_text(session_id: str, kind: str) -> str:
    """The current (in-flight) turn's buffered console/terminal text, or ''."""
    store = _console_turn_buf if kind == "console" else _terminal_turn_buf
    entry = store.get(session_id)
    return "".join(entry[0]) if entry else ""


def _flush_turn_log(session_id: str) -> None:
    """At a turn boundary, persist the buffered console/terminal text to the desk's
    on-disk log and clear the buffer. Runs for committed AND interrupted turns — an
    interrupted turn never reaches the DB, so the log is its only durable record."""
    for kind, store in (("console", _console_turn_buf), ("terminal", _terminal_turn_buf)):
        entry = store.pop(session_id, None)
        if entry and entry[0]:
            _append_desk_log(session_id, kind, "".join(entry[0]))


async def _pump_worker(session_id: str, proc: asyncio.subprocess.Process,
                       queue: asyncio.Queue) -> None:
    """Background task: read worker stdout and put events into the live queue."""
    def _broadcast_terminal(text: str) -> None:
        _buffer_turn_log(session_id, "terminal", text)
        for tq in list(_terminal_queues.get(session_id, [])):
            tq.put_nowait(text)

    def _broadcast_console(text: str) -> None:
        _buffer_turn_log(session_id, "console", text)
        for cq in list(_console_queues.get(session_id, [])):
            cq.put_nowait(text)

    async def _drain_stderr() -> None:
        try:
            assert proc.stderr
            async for raw in proc.stderr:
                text = raw.decode("utf-8", errors="replace").rstrip("\n")
                if text:
                    _broadcast_terminal("[stderr] " + text + "\n")
        except Exception:
            pass

    stderr_task = asyncio.create_task(_drain_stderr())
    terminal_type: str | None = None  # last event the worker emitted ("done"/"error"); None if killed
    error_msg: str | None = None      # message of the worker's fatal "error" event
    _evt_state: dict = {}  # per-turn text-run tracking for real-time recording
    _clear_live_buffer(session_id)  # fresh turn — drop any stale replay buffer
    _ensure_log_seeded(session_id)  # seed the on-disk console/terminal log once
    _load_event_times(session_id)   # fold prior-run markers in before this turn records any
    try:
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                _broadcast_terminal(line + "\n")
                continue
            # session_id event was already consumed by new_session; skip here
            if evt.get("type") == "session_id":
                continue
            # Buffer BEFORE enqueueing so the buffer is always a superset of the
            # queue (see _buffer_live_event), then enqueue for the live WS.
            _buffer_live_event(session_id, evt)
            _buffer_subagent_event(session_id, evt)  # durable per-subagent trace
            await queue.put(evt)
            _record_worker_evt_time(session_id, evt, _evt_state)
            term_text = _evt_to_terminal(evt)
            if term_text:
                _broadcast_terminal(term_text)
            console_text = _evt_to_console(session_id, evt)
            if console_text:
                _broadcast_console(console_text)
            if evt.get("type") in ("done", "error"):
                terminal_type = evt.get("type")
                if terminal_type == "error":
                    error_msg = evt.get("msg") or "Worker error"
                break
    except Exception:
        pass
    finally:
        stderr_task.cancel()
        _flush_turn_log(session_id)  # persist this turn's console/terminal output
        if terminal_type == "done":
            # Turn committed: Hermes flushed it to the DB, so the DB snapshot now
            # covers it — drop the replay buffer so a reconnect doesn't double-show it.
            _clear_live_buffer(session_id)
            _session_turn_interrupted.discard(session_id)
        else:
            # Errored ("error") or killed mid-turn (None: Stop / barge-in / crash) —
            # nothing was flushed to the DB. Preserve the streamed content as orphan
            # feed events so the next snapshot doesn't sweep it from the feed.
            _session_turn_interrupted.discard(session_id)
            _preserve_orphan_turn(session_id, error_msg=error_msg,
                                  interrupted=terminal_type is None)
        # Mirror this turn's per-event time markers to disk so they survive a
        # restart. Runs after the orphan branch above, whose pruning is already
        # reflected in the in-memory list this rewrites from.
        _persist_event_times(session_id)
        # Always push a sentinel so the WS loop can exit cleanly
        await queue.put({"type": "done"})
        # Only clear the registry if it still points at THIS worker — a barge-in
        # (interrupt + immediate same-session resume) may have already registered a
        # newer worker, which this stale cleanup must not evict.
        if _live_queues.get(session_id) is queue:
            _live_queues.pop(session_id, None)
        if _running_procs.get(session_id) is proc:
            _running_procs.pop(session_id, None)
        # Signal all terminal subscribers to close
        for tq in list(_terminal_queues.pop(session_id, [])):
            try:
                tq.put_nowait(None)
            except Exception:
                pass
        # Refresh the short title in the background (non-blocking). This runs only
        # at a turn boundary — after a response, never before the first — and at
        # most once per ~10k new tokens, so a long-running desk's title tracks the
        # work as it clarifies (e.g. "Untitled task" → "Paper research") without
        # spamming the model or adding any latency to the first token.
        asyncio.create_task(_maybe_refresh_title(session_id))
        # Refresh the agent's progress report after a completed turn (non-blocking).
        if terminal_type == "done":
            asyncio.create_task(_refresh_progress_bg(session_id))
        # Heartbeat auto-continue: if this turn finished cleanly (not killed/errored,
        # not user-stopped) and the desk is in auto-continue mode, check whether the
        # TASK.md goal is actually done — and if not, resume the session so long /
        # multi-step tasks don't stall after one turn. Bounded by _AUTO_CONTINUE_MAX.
        if (terminal_type == "done"
                and session_id not in _session_user_stopped
                and _session_autocontinue.get(session_id)
                and _session_continue_count.get(session_id, 0) < _AUTO_CONTINUE_MAX):
            asyncio.create_task(_heartbeat_check(session_id))
        _session_user_stopped.discard(session_id)


def _fire_turn_end(session_id: str) -> None:
    """Shared turn-boundary side effects: title + progress refresh + heartbeat."""
    asyncio.create_task(_maybe_refresh_title(session_id))
    asyncio.create_task(_refresh_progress_bg(session_id))
    if (session_id not in _session_user_stopped
            and _session_autocontinue.get(session_id)
            and _session_continue_count.get(session_id, 0) < _AUTO_CONTINUE_MAX):
        asyncio.create_task(_heartbeat_check(session_id))
    _session_user_stopped.discard(session_id)


async def _persistent_pump(session_id: str, proc: asyncio.subprocess.Process) -> None:
    """Read a long-lived worker's stdout across many turns, routing each event to
    the CURRENT turn's live queue. A 'turn_done' event ends the turn (pushes the
    'done' sentinel to the WS, fires turn-end side effects) but keeps the process
    warm for the next turn. Only process exit tears the worker down."""
    def _broadcast_terminal(text: str) -> None:
        _buffer_turn_log(session_id, "terminal", text)
        for tq in list(_terminal_queues.get(session_id, [])):
            tq.put_nowait(text)

    def _broadcast_console(text: str) -> None:
        _buffer_turn_log(session_id, "console", text)
        for cq in list(_console_queues.get(session_id, [])):
            cq.put_nowait(text)

    async def _drain_stderr() -> None:
        try:
            assert proc.stderr
            async for raw in proc.stderr:
                text = raw.decode("utf-8", errors="replace").rstrip("\n")
                if text:
                    _broadcast_terminal("[stderr] " + text + "\n")
        except Exception:
            pass

    stderr_task = asyncio.create_task(_drain_stderr())
    _evt_state: dict = {}  # per-turn text-run tracking for real-time recording
    turn_error: str | None = None  # "error" event msg of the current turn, if any
    _ensure_log_seeded(session_id)  # seed the on-disk console/terminal log once
    _load_event_times(session_id)   # fold prior-run markers in before this turn records any
    try:
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                _broadcast_terminal(line + "\n")
                continue
            t = evt.get("type")
            if t == "session_id":
                continue
            if t == "inspect_result":
                # Operator inspect reply — resolve the waiting request, keep it out
                # of the turn's activity stream.
                fut = _inspect_waiters.pop(evt.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(evt)
                continue
            if t == "error":
                turn_error = evt.get("msg") or "Worker error"
            if t == "turn_done":
                _evt_state["in_text"] = False  # next turn starts a fresh text run
                _flush_turn_log(session_id)  # persist the finished turn's output
                interrupted = session_id in _session_turn_interrupted
                _session_turn_interrupted.discard(session_id)
                if turn_error is not None or interrupted:
                    # The turn errored or was soft-interrupted — never flushed to
                    # the DB, so keep its streamed content in the feed.
                    _preserve_orphan_turn(session_id, error_msg=turn_error,
                                          interrupted=turn_error is None)
                else:
                    # Clean turn: it committed to the desk's state.db — Hermes, and
                    # now Claude too via claude_worker — so the DB snapshot covers
                    # it; drop the replay buffer and the next turn rebuilds it.
                    _clear_live_buffer(session_id)
                # Mirror this turn's markers to disk (after orphan pruning) so they
                # outlive a restart — same as the one-shot pump's finally block.
                _persist_event_times(session_id)
                turn_error = None
                q = _live_queues.get(session_id)
                if q:
                    await q.put({"type": "done"})
                ev = _turn_done_events.get(session_id)
                if ev:
                    ev.set()
                if _running_procs.get(session_id) is proc:
                    _running_procs.pop(session_id, None)
                _fire_turn_end(session_id)
                continue
            # Buffer BEFORE enqueueing so the buffer stays a superset of the queue.
            _buffer_live_event(session_id, evt)
            _buffer_subagent_event(session_id, evt)  # durable per-subagent trace
            q = _live_queues.get(session_id)
            if q:
                await q.put(evt)
            _record_worker_evt_time(session_id, evt, _evt_state)
            term_text = _evt_to_terminal(evt)
            if term_text:
                _broadcast_terminal(term_text)
            console_text = _evt_to_console(session_id, evt)
            if console_text:
                _broadcast_console(console_text)
    except Exception:
        pass
    finally:
        stderr_task.cancel()
        _flush_turn_log(session_id)  # persist a turn left in flight by process exit
        # Process died. If a turn was in flight (non-empty buffer / pending error /
        # interrupt flag), it never reached the DB — preserve it for the feed.
        interrupted = session_id in _session_turn_interrupted
        _session_turn_interrupted.discard(session_id)
        if _live_event_buffer.get(session_id) or turn_error is not None or interrupted:
            _preserve_orphan_turn(session_id, error_msg=turn_error,
                                  interrupted=turn_error is None)
        _persist_event_times(session_id)  # outlive the restart that killed this worker
        if _persistent_procs.get(session_id) is proc:
            _persistent_procs.pop(session_id, None)
        if _running_procs.get(session_id) is proc:
            _running_procs.pop(session_id, None)
        ev = _turn_done_events.get(session_id)
        if ev:
            ev.set()
        q = _live_queues.get(session_id)
        if q:
            await q.put({"type": "done"})
        for tq in list(_terminal_queues.pop(session_id, [])):
            try:
                tq.put_nowait(None)
            except Exception:
                pass


async def _send_cmd(proc: asyncio.subprocess.Process, obj: dict) -> None:
    """Write one newline-delimited JSON command to a persistent worker's stdin."""
    try:
        assert proc.stdin
        proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        await proc.stdin.drain()
    except Exception:
        pass


async def _maybe_refresh_title(session_id: str) -> None:
    """(Re)generate the title on the first turn, then again only once the session
    has accrued ~_TITLE_TOKEN_INTERVAL more tokens since the last generation."""
    try:
        tokens_now = 0
        if _db_ref is not None:
            s = _db_ref.get_session(session_id)
            tokens_now = (s.token_estimate if s else 0) or 0
        last = _session_title_tokens.get(session_id)
        if last is not None and tokens_now - last < _TITLE_TOKEN_INTERVAL:
            return
        _session_title_tokens[session_id] = tokens_now
        await _generate_title(session_id)
    except Exception:
        pass


def _recent_assistant_text(session_id: str) -> str:
    """Latest prose assistant message (skips tool-call/JSON blocks), for context."""
    if _db_ref is None:
        return ""
    try:
        for m in reversed(_db_ref.get_messages(session_id, limit=2000)):
            if m.role != "assistant" or not m.content:
                continue
            c = m.content.strip()
            if len(c) > 20 and not c.startswith(("{", "[")):
                return c[:400]
    except Exception:
        pass
    return ""


async def _generate_title(session_id: str) -> None:
    """Call the LLM to generate a 3–5 word title for the session."""
    try:
        ws = _session_workspaces.get(session_id)
        if not ws:
            return
        task_md = Path(ws) / "TASK.md"
        if not task_md.exists():
            return
        raw = task_md.read_text(encoding="utf-8", errors="replace")
        # Strip markdown header
        text = re.sub(r"^#\s*Task\s*\n+", "", raw).strip()[:300]
        if not text:
            return
        recent = _recent_assistant_text(session_id)

        # Title-gen runs on the manager's own model (selected profile, or the
        # configured default backend) — see _aux_model_config.
        base_url, model = _aux_model_config()

        prompt = (
            f"Generate a short title (3-5 words max) describing what this AI agent "
            f"session is about. Reply with ONLY the title, nothing else.\n\nTask: {text}"
        )
        if recent:
            prompt += f"\n\nLatest progress (use it to sharpen the title): {recent}"

        # Native /api/chat with the shared num_ctx — never reload the model.
        title = await _ollama_aux_chat(base_url, model, prompt, max_tokens=30)
        if title:
            # Strip quotes, thinking tags, etc.
            title = re.sub(r"^[\"'`]+|[\"'`]+$", "", title).strip()
            title = re.sub(r"<think>.*?</think>", "", title, flags=re.DOTALL).strip()
            if title and len(title) < 60:
                _session_titles[session_id] = title
                # Persist to workspace
                try:
                    (Path(ws) / ".hermes_title").write_text(title)
                except Exception:
                    pass
    except Exception:
        pass


# Reference to create_app's _spawn_resume_worker so the module-level heartbeat can
# resume a session (set in create_app).
_spawn_resume_ref = None


async def _judge_complete(goal: str, recent: str) -> "tuple[bool, str] | None":
    """Ask the local model whether the goal is fully complete.

    Returns (done, remaining_step) or None if the model is unreachable — in which
    case the caller does nothing (fail safe: never loop on an unknown verdict).
    """
    try:
        # The manager's own model (selected profile, or configured default backend).
        base_url, model = _aux_model_config()
        prompt = (
            "You supervise an autonomous agent. Decide if its GOAL is FULLY and "
            "verifiably complete.\n\n"
            f"GOAL (TASK.md):\n{goal[:1500]}\n\n"
            f"AGENT'S MOST RECENT OUTPUT:\n{recent[:1500] or '(none)'}\n\n"
            "Reply EXACTLY 'DONE' if everything in the goal is finished. Otherwise reply "
            "'CONTINUE: <one short sentence naming the single most important remaining step>'."
        )
        # Native /api/chat with the shared num_ctx — never reload the model.
        text = await _ollama_aux_chat(base_url, model, prompt, max_tokens=60)
        if not text:
            return None
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        up = text.upper()
        if up.startswith("CONTINUE"):
            remaining = text.split(":", 1)[1].strip() if ":" in text else "continue the task"
            return (False, remaining[:300])
        # "DONE" or anything ambiguous → treat as done (stop) to avoid runaway loops.
        return (True, "")
    except Exception:
        return None


def _aux_model_config() -> "tuple[str, str]":
    """(base_url, model) for the manager's LLM calls (title, judge, progress, audits).

    Always the manager's *own* model — there is no separate small "aux" model.
    Every manager call (titles, the done-judge, progress, and accuracy-critical
    audits) uses the SAME model, so they're all exactly as capable as whatever you
    pointed the manager at.

    Resolution order:
      1. A user-selected manager profile — its backend AND model verbatim. (Its
         backend may serve only its own model, so we never substitute another.)
      2. ``<hermes_home>/config.yaml`` model.base_url / model.default — the
         configured default backend (via hermes_model_config).
    """
    # A user-selected manager profile overrides the default backend entirely.
    if _MGR_BASE_URL and _MGR_MODEL:
        return _MGR_BASE_URL, _MGR_MODEL
    home = _home_ref or Path.home() / ".hermes"
    base_url, model = hermes_model_config(home)
    return base_url, (_EFFECTIVE_MODEL or model)


async def _aux_json(base_url: str, model: str, prompt: str, max_tokens: int,
                    retries: int = 2) -> "Any":
    """_ollama_aux_chat + robust JSON parse, retried if the model emits bad JSON.

    The local model occasionally wraps JSON in prose or truncates it; a single
    retry with a stricter nudge recovers almost all of those. Returns the parsed
    value, or None if every attempt fails.
    """
    p = prompt
    for attempt in range(retries + 1):
        raw = await _ollama_aux_chat(base_url, model, p, max_tokens=max_tokens)
        parsed = _extract_json(raw)
        if parsed is not None:
            return parsed
        p = prompt + "\n\nIMPORTANT: Your previous reply was not valid JSON. Reply with ONLY the JSON, no prose, no markdown fences."
    return None


def _extract_json(text: str) -> "Any":
    """Best-effort parse of a JSON array/object the model embedded in prose.

    Strips <think> blocks and code fences, then grabs the outermost [...] or {...}.
    Returns None if nothing parses.
    """
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?", "", text)
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start = text.find(open_c)
        end = text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
    return None


# Our own artifacts — never audit these as if they were the agent's output.
_AUDIT_SKIP_FILES = {"TASK.md", "AUDIT.md", "PROGRESS.md", ".hermes_title"}
# Team repo scaffolding — not agent output.
_AUDIT_SKIP_REPO_FILES = {"README.md"}


def _team_repo_for_ws(ws: Path) -> Path | None:
    """Canonical team File Repo for a desk workspace, if any."""
    home = _home_ref or Path.home() / ".hermes"
    try:
        tid = (ws / _DESK_TEAM_MARKER).read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not tid or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", tid) or tid in (".", ".."):
        return None
    repo = home / "gui_team_repos" / tid
    return repo if repo.is_dir() else None


def _resolve_audit_file(ws: Path, rel: str) -> Path | None:
    """Resolve a workspace-relative audit path, including ``team_files/`` repo mounts."""
    rel = rel.replace("\\", "/").lstrip("/")
    if rel.startswith(f"{_TEAM_FILES_SUBDIR}/"):
        repo = _team_repo_for_ws(ws)
        if not repo:
            return None
        sub = rel[len(_TEAM_FILES_SUBDIR) + 1:]
        try:
            target = (repo / sub).resolve()
            target.relative_to(repo.resolve())
            return target if target.is_file() else None
        except Exception:
            return None
    try:
        target = (ws / rel).resolve()
        target.relative_to(ws.resolve())
        return target if target.is_file() else None
    except Exception:
        return None


def _read_audit_file_bytes(ws: Path, rel: str) -> bytes:
    target = _resolve_audit_file(ws, rel)
    if target is None:
        return b""
    try:
        return target.read_bytes()
    except Exception:
        return b""


def _clean_goal(raw_task: str) -> str:
    """The human's task spec, stripped of manager notes/audits we appended to TASK.md.

    Otherwise the auditor reads its own previous notes back as part of the task.
    """
    # Drop everything from the first appended manager block onward.
    cut = re.search(r"\n-{3,}\s*\n\s*📋", raw_task)
    text = raw_task[: cut.start()] if cut else raw_task
    text = re.sub(r"^#\s*Task\s*\n+", "", text).strip()
    return text


def _audit_files(ws: Path, max_entries: int = 200) -> list[str]:
    """Relative paths of real output files (excludes our artifacts + dotfiles).

    Includes team File Repo contents under ``team_files/`` — the host mount point
    is empty; canonical bytes live in ``gui_team_repos/<team_id>/``.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(rel: str) -> None:
        norm = rel.replace("\\", "/").lstrip("/")
        if not norm or norm in seen:
            return
        if norm in _AUDIT_SKIP_FILES or any(part.startswith(".") for part in Path(norm).parts):
            return
        seen.add(norm)
        out.append(norm)

    for p in sorted(ws.rglob("*")):
        if len(out) >= max_entries:
            break
        if not p.is_file() or p.name in _AUDIT_SKIP_FILES:
            continue
        rel = str(p.relative_to(ws)).replace("\\", "/")
        if rel == _TEAM_FILES_SUBDIR or rel.startswith(f"{_TEAM_FILES_SUBDIR}/"):
            continue
        add(rel)

    repo = _team_repo_for_ws(ws)
    if repo:
        for p in sorted(repo.rglob("*")):
            if len(out) >= max_entries:
                break
            if not p.is_file() or p.name.startswith(".") or p.name in _AUDIT_SKIP_REPO_FILES:
                continue
            add(f"{_TEAM_FILES_SUBDIR}/{p.relative_to(repo)}".replace("\\", "/"))

    return out


def _build_transcript(session_id: str, max_chars: int = 6000) -> "tuple[str, int]":
    """Render the agent's work (assistant messages + tool results) as audit evidence.

    Most non-file tasks (answer a question, tell a joke) deliver their result in the
    conversation, not on disk — so this is an essential evidence source. Returns
    (transcript_text, message_count). Keeps the most recent text when truncating.
    """
    if _db_ref is None:
        return "", 0
    try:
        msgs = _db_ref.get_messages(session_id, limit=2000)
    except Exception:
        return "", 0
    parts: list[str] = []
    for m in msgs:
        role = getattr(m, "role", "") or ""
        content = (getattr(m, "content", "") or "").strip()
        if not content:
            continue
        if role == "assistant":
            parts.append(f"ASSISTANT: {content}")
        elif role == "tool":
            # Tool results capture file reads, terminal/bash output, etc.
            name = getattr(m, "tool_name", "") or "?"
            parts.append(f"TOOL[{name}]: {content[:600]}")
        elif role == "user" and not content.lstrip().startswith("[Workspace"):
            parts.append(f"USER: {content[:300]}")
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = "…(earlier turns omitted)\n\n" + text[-max_chars:]
    return text, len(msgs)


def _audit_state(session_id: str, ws: Path) -> "tuple[str, str, list[str], int]":
    """Compute (state_hash, clean_goal, output_files, message_count) for a session.

    The hash is a Makefile-style fingerprint of everything an audit depends on:
    the task spec, every output file's content, and the conversation length. If it
    matches a cached audit's hash, the work hasn't changed and we skip re-auditing.
    """
    task_md = ws / "TASK.md"
    raw_task = task_md.read_text(encoding="utf-8", errors="replace") if task_md.exists() else ""
    goal = _clean_goal(raw_task)
    files = _audit_files(ws)
    _, msg_count = _build_transcript(session_id)
    h = hashlib.sha256()
    h.update(goal.encode("utf-8", "replace"))
    h.update(f"|msgs={msg_count}|".encode())
    for rel in files:
        h.update(rel.encode("utf-8", "replace") + b"=" + hashlib.sha256(_read_audit_file_bytes(ws, rel)).digest())
    return h.hexdigest()[:16], goal, files, msg_count


def _read_evidence(ws: Path, rel: str, max_chars: int = 4000) -> "str | None":
    """Read a workspace file for audit evidence, guarding against path traversal."""
    rel = rel.replace("\\", "/").lstrip("/")
    if rel in _AUDIT_SKIP_FILES:
        return None
    target = _resolve_audit_file(ws, rel)
    if target is None:
        return None
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return content[:max_chars] + ("\n…(truncated)" if len(content) > max_chars else "")


async def _run_audit(session_id: str, ws: Path, force: bool = False) -> "dict | None":
    """Orchestrated, evidence-based manager audit of a session.

    decompose (criteria from the task) → gather evidence from the RIGHT sources
    (conversation transcript AND output files, never our own TASK.md/AUDIT.md) →
    adjudicate (pass/fail/unsure with cited evidence). Caches by a state hash so an
    unchanged state is never re-audited (Makefile semantics). Returns the structured
    audit, or None if there's no task to audit / the model is unreachable.
    """
    state_hash, goal, files, msg_count = _audit_state(session_id, ws)
    if not goal:
        return None

    # Auto patrol only — never run (or re-nudge from) an audit while a turn is
    # in flight. Explicit "Ask manager" passes force=True and may audit a running
    # desk for guidance; the frontend must not resume/kill it when is_running.
    if not force and session_id in _running_procs:
        cached = _session_audits.get(session_id)
        if cached:
            return {**cached, "cached": True, "should_intervene": False}
        return {
            "session_id": session_id,
            "state_hash": state_hash,
            "skipped_running": True,
            "should_intervene": False,
            "summary": {"passed": 0, "failed": 0, "unsure": 0, "total": 0},
            "results": [],
        }

    # ── Makefile-style cache: same state → reuse the existing audit, no LLM ──────
    # State hasn't changed since we last audited. If the desk is still RUNNING, the
    # agent is mid-turn — don't re-nudge identical work. If it's IDLE, the agent has
    # STOPPED without changing anything, so the manager should resume it to make
    # progress (the default action on an idle, unfinished task) — bounded by the
    # intervention cap so a genuinely stuck agent escalates to the human instead of
    # looping forever.
    cached = _session_audits.get(session_id)
    if not force and cached and cached.get("state_hash") == state_hash:
        if session_id in _running_procs:
            return {**cached, "cached": True, "should_intervene": False}
        summ = cached.get("summary") or {}
        has_issues = (summ.get("failed", 0) + summ.get("unsure", 0)) > 0
        if not has_issues:
            return {**cached, "cached": True, "should_intervene": False}
        count = _session_manager_resumes.get(session_id, 0) + 1
        _session_manager_resumes[session_id] = count
        return {
            **cached,
            "cached": True,
            "should_intervene": count <= _MANAGER_MAX_INTERVENTIONS,
            "intervention_count": count,
            "max_interventions": _MANAGER_MAX_INTERVENTIONS,
        }

    # The manager audits on its own model — the selected profile's, or the
    # configured default-backend model (same model it uses for titles/judging).
    base_url, model = _aux_model_config()

    transcript, _ = _build_transcript(session_id)
    sources_inspected = {
        "task_spec": True,
        "conversation_messages": msg_count,
        "output_files": files,
    }

    # ── 1. Decompose into machine-checkable criteria, each naming WHERE to check ─
    decompose_prompt = (
        "You are a meticulous reviewer. First identify the DISTINCT tasks the user "
        "asked for and number them in order (Task 1, Task 2, …). Then break each task "
        "into concrete, individually verifiable acceptance criteria — exactly what "
        "must be true for that task to be complete. Derive criteria ONLY from what the "
        "task explicitly asks for; do NOT invent requirements (e.g. extra files, or "
        "that two files must match) that the task never stated.\n\n"
        "For each criterion, name where the evidence lives via \"source\":\n"
        '  - "conversation"  → the agent answered in chat (jokes, answers, explanations)\n'
        '  - "<relative/path>" → a specific output file the agent should have written\n\n'
        f"TASK:\n{goal[:2000]}\n\n"
        f"OUTPUT FILES PRESENT (excludes TASK.md/AUDIT.md):\n{chr(10).join(files) or '(none)'}\n\n"
        f"A conversation transcript {'IS' if transcript else 'is NOT'} available.\n\n"
        "Reply with ONLY a JSON array, no prose. Each item: "
        '{"id": <int>, "task": "Task <n>: <short task label>", '
        '"criterion": "<what must be true>", "source": "conversation"|"<file>"}.'
    )
    criteria = await _aux_json(base_url, model, decompose_prompt, max_tokens=900)
    if not isinstance(criteria, list) or not criteria:
        return None

    # ── 2. Gather evidence from the named sources ────────────────────────────────
    file_evidence: dict[str, str] = {}
    needs_conversation = False
    for c in criteria:
        if not isinstance(c, dict):
            continue
        src = (c.get("source") or "").strip()
        if src == "conversation" or not src:
            needs_conversation = True
        elif src not in file_evidence:
            body = _read_evidence(ws, src)
            file_evidence[src] = body if body is not None else "(FILE NOT FOUND)"

    evidence_parts: list[str] = []
    if needs_conversation or not file_evidence:
        evidence_parts.append(
            "=== CONVERSATION TRANSCRIPT (agent's chat output) ===\n"
            + (transcript or "(no conversation recorded)")
        )
    for rel, body in file_evidence.items():
        evidence_parts.append(f"=== FILE: {rel} ===\n{body}")
    evidence_block = "\n\n".join(evidence_parts)

    # Soft context only: the agent's own progress report. It must NOT sway verdicts
    # (it's a self-report, not proof) but can make fix hints more targeted — e.g.
    # acknowledging a blocker the agent already flagged.
    progress_note = ""
    try:
        pmd = ws / "PROGRESS.md"
        if pmd.exists():
            progress_note = pmd.read_text(encoding="utf-8", errors="replace")[:1500]
    except Exception:
        progress_note = ""

    # ── 3. Adjudicate strictly against the gathered evidence ─────────────────────
    adjudicate_prompt = (
        "You audit whether an agent satisfied each acceptance criterion. Judge ONLY "
        "from the EVIDENCE below. CRITICAL: mark 'pass' only if the evidence clearly "
        "and explicitly shows the criterion is met. If the required output is absent "
        "from the evidence, mark 'fail' — never assume it was done. If the evidence "
        "is present but ambiguous, mark 'unsure'.\n\n"
        f"CRITERIA (JSON, each with its source):\n{json.dumps(criteria)[:3000]}\n\n"
        f"EVIDENCE:\n{evidence_block[:9000]}\n\n"
    )
    if progress_note:
        adjudicate_prompt += (
            "AGENT'S SELF-REPORTED PROGRESS (context only — NOT proof; never base a "
            "'pass' on it. Use it only to make 'fix_hint' more specific, e.g. by "
            f"acknowledging a blocker the agent noted):\n{progress_note}\n\n"
        )
    adjudicate_prompt += (
        "Reply with ONLY a JSON array, one object per criterion id: "
        '{"id": <int>, "verdict": "pass"|"fail"|"unsure", '
        '"evidence": "<quote/cite what in the EVIDENCE proves your verdict>", '
        '"fix_hint": "<if fail/unsure, the concrete fix; else empty>"}.'
    )
    results = await _aux_json(base_url, model, adjudicate_prompt, max_tokens=1400)
    if not isinstance(results, list):
        results = []

    # Merge criteria text + source into results by id for a self-contained record.
    crit_by_id = {c.get("id"): c for c in criteria if isinstance(c, dict)}
    merged = []
    for r in results:
        if not isinstance(r, dict):
            continue
        cid = r.get("id")
        crit = crit_by_id.get(cid) or {}
        merged.append({
            "id": cid,
            "task": crit.get("task", "") or "Task 1",
            "criterion": crit.get("criterion", ""),
            "source": crit.get("source", ""),
            "verdict": r.get("verdict", "unsure"),
            "evidence": r.get("evidence", ""),
            "fix_hint": r.get("fix_hint", ""),
        })

    if not merged:
        # Adjudication produced no usable verdicts (timeout / unparseable JSON).
        # Fail like the decompose path: cache NOTHING and write no AUDIT.md — a
        # cached 0/0 audit would keep matching the idle desk's state hash and
        # make it permanently un-auditable until its state changes.
        return None

    summary = {
        "passed": sum(1 for r in merged if r["verdict"] == "pass"),
        "failed": sum(1 for r in merged if r["verdict"] == "fail"),
        "unsure": sum(1 for r in merged if r["verdict"] == "unsure"),
        "total": len(merged),
    }

    # ── Loop guard: bound how many times the manager re-nudges this session ──────
    # Each failing audit that nudges the agent counts once; a pass resets it. Once
    # the cap is hit, should_intervene goes False so the manager stops resuming and
    # escalates to the human instead of looping with the agent forever.
    has_issues = (summary["failed"] + summary["unsure"]) > 0
    solved = summary["total"] > 0 and not has_issues
    prev_best = _session_audit_best.get(session_id, -1)
    if has_issues:
        _mark_solved(ws, False)
        if summary["passed"] > prev_best:
            # Agent improved since the last audit → reset the no-progress counter so
            # the manager keeps nudging a still-improving task toward completion.
            count = 0
        else:
            count = _session_manager_resumes.get(session_id, 0) + 1
        _session_manager_resumes[session_id] = count
        _session_audit_best[session_id] = max(prev_best, summary["passed"])
    else:
        count = 0
        _session_manager_resumes[session_id] = 0
        _session_audit_best.pop(session_id, None)
        _mark_solved(ws, solved, state_hash)
    # Nudge while improving or within the stuck-cap; escalate to the human only when
    # the agent is genuinely stuck (no progress across _MANAGER_MAX_INTERVENTIONS).
    should_intervene = has_issues and count <= _MANAGER_MAX_INTERVENTIONS

    audit = {
        "session_id": session_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state_hash": state_hash,
        "goal": goal,
        "sources_inspected": sources_inspected,
        "results": merged,
        "summary": summary,
        "should_intervene": should_intervene,
        "intervention_count": count,
        "max_interventions": _MANAGER_MAX_INTERVENTIONS,
        "task_solved": solved,
    }
    _session_audits[session_id] = audit

    # Persist a transparent, human-readable record — appended, grouped by task, so
    # earlier tasks' audits are preserved when a new task arrives.
    try:
        src_files = sources_inspected["output_files"]
        lines = [
            f"## Audit — {audit['generated_at']}", "",
            f"**{summary['passed']}/{summary['total']} passed** "
            f"· {summary['failed']} failed · {summary['unsure']} unsure", "",
            "_Audited: task spec"
            f" · {msg_count} conversation message(s)"
            f" · files: {', '.join(src_files) if src_files else '(none)'}_", "",
        ]
        # Group checks under their task heading, preserving first-seen task order.
        seen_tasks: list[str] = []
        for r in merged:
            if r["task"] not in seen_tasks:
                seen_tasks.append(r["task"])
        for task in seen_tasks:
            lines.append(f"### {task}")
            for r in (x for x in merged if x["task"] == task):
                mark = {"pass": "✅", "fail": "❌"}.get(r["verdict"], "❓")
                where = f" _(source: {r['source']})_" if r["source"] else ""
                lines.append(f"- {mark} **{r['criterion']}**{where} — {r['evidence']}")
                if r["fix_hint"] and r["verdict"] != "pass":
                    lines.append(f"    - 🔧 {r['fix_hint']}")
            lines.append("")
        goal_hash = hashlib.sha256(goal.encode("utf-8", "replace")).hexdigest()[:12]
        _write_audit_md(ws, "\n".join(lines), goal_hash)
    except Exception:
        pass

    return audit


async def _generate_progress(session_id: str, ws: Path) -> "str | None":
    """Summarise the agent's work so far into a PROGRESS.md report.

    Built from the conversation transcript + task spec via the agent's model — a
    concise, structured status useful to the user, the manager, and the agent
    itself when it resumes. Returns the markdown, or None if there's nothing to
    report / the model is unreachable.
    """
    task_md = ws / "TASK.md"
    goal = _clean_goal(task_md.read_text(encoding="utf-8", errors="replace")) if task_md.exists() else ""
    transcript, msg_count = _build_transcript(session_id, max_chars=8000)
    if not goal and msg_count == 0:
        return None
    # Use the small aux model: progress refreshes on every turn, so we must not
    # evict the agent's warm KV slot (unlike the infrequent, accuracy-critical
    # audit, which uses the agent's own model).
    base_url, model = _aux_model_config()
    prompt = (
        "Write a concise PROGRESS REPORT for this agent's work, in GitHub-flavored "
        "markdown. Base it strictly on the task and the work transcript below — do "
        "not invent progress. Use exactly these sections (omit a section only if "
        "truly empty), each as short bullet points:\n\n"
        "## Tasks tackled\n## Challenges & how they were resolved\n"
        "## Unresolved / blocked\n## Remaining steps\n\n"
        f"TASK:\n{goal[:1500] or '(none stated)'}\n\n"
        f"WORK TRANSCRIPT (agent messages + tool results):\n{transcript or '(no work yet)'}\n\n"
        "Reply with ONLY the markdown report, no preamble."
    )
    text = await _ollama_aux_chat(base_url, model, prompt, max_tokens=900)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text).strip()
    if not text:
        return None
    header = (f"# Progress report\n_Generated {datetime.now(timezone.utc).isoformat()} "
              f"from {msg_count} message(s)._\n\n")
    content = header + text + "\n"
    try:
        (ws / "PROGRESS.md").write_text(content, encoding="utf-8")
    except Exception:
        pass
    return content


async def _refresh_progress_bg(session_id: str) -> None:
    """Fire-and-forget progress refresh at a turn boundary (never blocks the turn)."""
    ws = _session_workspaces.get(session_id)
    if not ws:
        return
    try:
        await _generate_progress(session_id, Path(ws))
    except Exception:
        pass


async def _heartbeat_check(session_id: str) -> None:
    """When an auto-continue desk finishes a turn, resume it if TASK.md isn't done."""
    try:
        ws = _session_workspaces.get(session_id)
        if not ws:
            return
        task_md = Path(ws) / "TASK.md"
        if not task_md.exists():
            return
        goal = re.sub(r"^#\s*Task\s*\n+", "",
                      task_md.read_text(encoding="utf-8", errors="replace")).strip()
        if not goal:
            return
        verdict = await _judge_complete(goal, _recent_assistant_text(session_id))
        if verdict is None:
            return  # model unreachable — don't loop
        done, remaining = verdict
        # Re-check guards (state may have changed while the judge ran).
        if (done or _spawn_resume_ref is None
                or session_id in _running_procs
                or session_id in _session_user_stopped
                or session_id in _session_sleeping
                or not _session_autocontinue.get(session_id)
                or _session_continue_count.get(session_id, 0) >= _AUTO_CONTINUE_MAX):
            return
        _session_continue_count[session_id] = _session_continue_count.get(session_id, 0) + 1
        nudge = (
            "[auto-continue] The goal in TASK.md is not finished yet. "
            f"Most important remaining step: {remaining}. "
            "Continue working autonomously and execute the next steps now — only stop "
            "once the entire TASK.md goal is fully achieved."
        )
        await _spawn_resume_ref(session_id, nudge)
    except Exception:
        pass


_STOP_WORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with","by",
    "from","is","are","was","were","be","do","does","did","have","has","had",
    "i","me","my","we","you","it","that","this","can","will","please","just",
    "make","create","write","build","get","set","run","use","add","help",
}

def _make_workspace_dir(task_text: str, workspace_root: Path) -> Path:
    """Create a uniquely-named workspace directory derived from the task text."""
    words = re.findall(r"[a-z0-9]+", task_text.lower())
    slug_words = [w for w in words if w not in _STOP_WORDS and len(w) > 2][:5]
    base = "-".join(slug_words) if slug_words else "task"
    base = base[:48]
    workspace_root.mkdir(parents=True, exist_ok=True)
    # Atomically claim a unique dir: mkdir(exist_ok=False) is the check-and-create
    # in one step, so concurrent new_session calls can't collide on the same slug
    # (the old exists()-then-mkdir had a TOCTOU race that raised FileExistsError).
    candidates = [workspace_root / base] + [workspace_root / f"{base}-{n}" for n in range(2, 100)]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    # Last resort: append random suffix (uuid imported at module scope)
    candidate = workspace_root / f"{base}-{uuid.uuid4().hex[:6]}"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _walk_dir(d: Path, depth: int = 0) -> list[dict]:
    """Recursive directory listing for the file/workspace tree views.

    Depth-limited (≤5), capped at 100 entries/level, dotfiles skipped. Each file
    node carries a `preview_type` so the frontend knows what's clickable.
    """
    if depth > 5:
        return []
    try:
        entries = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name))
    except (PermissionError, OSError):
        return []
    nodes: list[dict] = []
    for entry in entries[:100]:
        if entry.name.startswith("."):
            continue
        node: dict = {
            "name": entry.name,
            "path": str(entry),
            "is_dir": entry.is_dir(),
            "preview_type": can_preview_file(str(entry)) if entry.is_file() else None,
        }
        if entry.is_dir():
            node["children"] = _walk_dir(entry, depth + 1)
        nodes.append(node)
    return nodes


def _list_hermes_containers() -> list[str]:
    """IDs of all hermes-* sandbox containers (running or stopped). [] if docker
    is missing/unreachable."""
    try:
        out = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=hermes-"],
            capture_output=True, text=True, timeout=10,
        )
        return [c for c in out.stdout.split() if c]
    except Exception:
        return []


def _remove_containers(ids: list[str]) -> int:
    """Force-remove the given containers; returns how many docker reported removed."""
    if not ids:
        return 0
    try:
        r = subprocess.run(
            ["docker", "rm", "-f", *ids],
            capture_output=True, text=True, timeout=60,
        )
        return len([line for line in r.stdout.split() if line])
    except Exception:
        return 0


def _remove_desk_container(sid: str) -> int:
    """Force-remove only THIS desk's sandbox container, by its per-desk label.

    The worker labels its container ``hermes-task-id=<desk key>`` where the key is
    the basename of the desk's ``TERMINAL_SANDBOX_DIR``/``HERMES_HOME``
    (``gui_sandboxes/<sid>``) — i.e. the session id (see
    ``hermes_worker._desk_container_key``). Filtering on that label scopes removal
    to one desk so sibling desks are never touched. No-op if docker is missing or
    sid is empty. Returns how many containers were removed."""
    if not sid:
        return 0
    try:
        out = subprocess.run(
            ["docker", "ps", "-aq",
             "--filter", "label=hermes-agent=1",
             "--filter", f"label=hermes-task-id={sid}"],
            capture_output=True, text=True, timeout=10,
        )
        ids = [c for c in out.stdout.split() if c]
    except Exception:
        return 0
    return _remove_containers(ids)


def create_app(
    hermes_home: str | None = None,
    hermes_api_url: str = "http://localhost:9119",
    workspace_root: str | None = None,
    allowed_origins: list[str] | None = None,
    gui_config: GuiConfig | None = None,
    experimental: bool = False,
) -> FastAPI:
    global _db_ref, _home_ref, _gui_cfg
    cfg = gui_config or load_gui_config(hermes_home)
    home = cfg.hermes_home
    _home_ref = home
    _gui_cfg = cfg
    # Experimental/developmental features gate (CLI: --experimental). Currently it
    # gates the Claude Code SDK agent: when off (default) the claude/claude-code
    # card is withheld from the desk roster (_roster_agents) and new Claude desks
    # are refused (POST /api/sessions/new), so the released app exposes only the
    # stable Hermes agents. Nested route handlers close over this local.
    experimental = bool(experimental)
    ws_root = Path(workspace_root) if workspace_root else home / "sandboxes" / "docker" / "default" / "workspace"
    db = HermesDB(home)
    _db_ref = db  # let module-level title helpers read recent activity

    def _safe_path(raw: str) -> Path:
        """Resolve `raw` and confirm it stays within an allowed root.

        Allowed roots are the workspace root and the Hermes home dir — the only
        places the GUI ever needs to read/open. This blocks arbitrary-file access
        (e.g. ~/.ssh, /etc) via the file-preview / file-tree / workspace-open
        endpoints. Raises 403 if the resolved path escapes every allowed root.
        Symlinks are followed (.resolve) so a symlink can't be used to escape.
        """
        p = Path(raw).resolve()
        for root in (ws_root, home):
            try:
                p.relative_to(root.resolve())
                return p
            except ValueError:
                continue
        raise HTTPException(403, "Path outside allowed roots")

    def _desk_paths(sid: str) -> tuple[Path, Path]:
        """Per-desk sandbox base + its docker workspace dir.

        Each desk gets its OWN `TERMINAL_SANDBOX_DIR` (the base) so Hermes
        bind-mounts a *private* `/workspace` — agents can't see other desks'
        files. The workspace dir doubles as the host dir for TASK.md and file
        tools (`HERMES_WORKDIR`), so file tools and bash share one location.
        """
        base = home / "gui_sandboxes" / sid
        return base, base / "docker" / "default" / "workspace"

    # Resources Hermes reads from HERMES_HOME that must stay SHARED across desks
    # (API keys, the active config, installed skills/binaries). state.db, memories,
    # sessions/ and kanban.db are deliberately NOT seeded so they stay desk-private.
    _DESK_SHARED_RESOURCES = ("auth.json", "auth.lock", "config.yaml", ".env",
                              "skills", "bin", "cache")

    def _link_desk_resource(dst: Path, src: Path) -> None:
        """Symlink ``src`` into a desk HERMES_HOME, replacing a stale symlink."""
        if not src.exists():
            return
        try:
            if dst.is_symlink():
                if dst.resolve() == src.resolve():
                    return
                dst.unlink()
            elif dst.exists():
                return
            dst.symlink_to(src)
        except OSError:
            pass

    def _seed_desk_home(
        base: Path,
        *,
        profile_dir: Path | None = None,
        agent_profile: bool = False,
    ) -> None:
        """Symlink shared ~/.hermes resources into a per-desk HERMES_HOME.

        Agent-profile desks symlink the canonical Hermes profile's config.yaml and
        .env (``~/.hermes/profiles/<id>/``) so tool gates match ``hermes chat -p``.
        """
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        cfg_src, env_src = profile_config_sources(
            profile_dir, home, agent_profile=agent_profile,
        )
        overrides = {"config.yaml": cfg_src, ".env": env_src}
        for name in _DESK_SHARED_RESOURCES:
            src = overrides.get(name, home / name)
            self_dst = base / name
            if name in overrides:
                _link_desk_resource(self_dst, src)
            elif not src.exists() or self_dst.exists() or self_dst.is_symlink():
                continue
            else:
                try:
                    self_dst.symlink_to(src)
                except OSError:
                    pass

    def _apply_desk_home(env: dict, sandbox_base: Path, agent_assigned: bool) -> None:
        """Scope a worker to a per-desk HERMES_HOME (private state.db + memories)."""
        env["HERMES_HOME"] = str(sandbox_base)
        profile_dir: Path | None = None
        if agent_assigned:
            raw = env.get("HERMES_GUI_CONFIG_HOME", "").strip()
            if raw:
                profile_dir = Path(raw)
        elif "HERMES_GUI_CONFIG_HOME" not in env:
            env["HERMES_GUI_CONFIG_HOME"] = str(home)
        _seed_desk_home(
            sandbox_base,
            profile_dir=profile_dir,
            agent_profile=agent_assigned,
        )

    def _sandbox_base_of(ws: Path) -> Path | None:
        """If `ws` is a per-desk gui-sandbox workspace, return its TERMINAL_SANDBOX_DIR."""
        try:
            if (ws.name == "workspace" and ws.parent.name == "default"
                    and ws.parents[1].name == "docker"
                    and ws.parents[3].name == "gui_sandboxes"):
                return ws.parents[2]
        except Exception:
            pass
        return None

    # ── Team file repos ───────────────────────────────────────────────────────
    # Canonical store: ~/.hermes/gui_team_repos/<team_id>/.
    # Each desk workspace gets workspace/team_files → symlink to that repo (one copy
    # per team, not per desk). Host file tools and Docker bind-mount both follow the
    # symlink on the host filesystem when resolving paths under /workspace.
    _TEAM_REPOS_ROOT = home / "gui_team_repos"

    def _safe_team_id(team_id: str) -> str:
        tid = (team_id or "").strip()
        if not tid or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", tid) or tid in (".", ".."):
            raise HTTPException(400, "invalid team id")
        return tid

    def _team_repo_dir(team_id: str) -> Path:
        return _TEAM_REPOS_ROOT / _safe_team_id(team_id)

    def _safe_team_relpath(repo: Path, rel: str) -> Path:
        """Resolve `rel` inside a team repo, rejecting traversal/absolute paths."""
        rel = (rel or "").replace("\\", "/").strip().lstrip("/")
        if not rel or ".." in Path(rel).parts:
            raise HTTPException(400, "invalid path")
        target = (repo / rel).resolve()
        try:
            target.relative_to(repo.resolve())
        except ValueError:
            raise HTTPException(403, "path outside team repo") from None
        return target

    def _load_desk_team_id(ws: Path) -> str | None:
        """Read a desk's team id from its workspace marker (survives server restarts)."""
        try:
            tid = (ws / _DESK_TEAM_MARKER).read_text(encoding="utf-8").strip()
            return tid if tid else None
        except Exception:
            return None

    def _save_desk_team_id(ws: Path, team_id: str) -> None:
        try:
            (ws / _DESK_TEAM_MARKER).write_text(team_id, encoding="utf-8")
        except Exception:
            pass

    def _register_session_team(sid: str, team_id: str, ws: Path | None = None) -> None:
        """Track a desk as belonging to a team (in-memory + workspace marker)."""
        tid = _safe_team_id(team_id)
        _session_team[sid] = tid
        _team_sessions.setdefault(tid, set()).add(sid)
        if ws is None:
            ws = _find_workspace(sid)
        if ws:
            _save_desk_team_id(ws, tid)

    def _sessions_for_team(team_id: str) -> list[str]:
        """All session ids belonging to a team — memory + persisted workspace markers."""
        tid = _safe_team_id(team_id)
        found: set[str] = set(_team_sessions.get(tid, set()))
        sandboxes = home / "gui_sandboxes"
        if sandboxes.is_dir():
            for d in sandboxes.iterdir():
                if not d.is_dir():
                    continue
                ws = d / "docker" / "default" / "workspace"
                if not ws.is_dir():
                    continue
                try:
                    if (ws / _DESK_TEAM_MARKER).read_text(encoding="utf-8").strip() == tid:
                        found.add(d.name)
                except Exception:
                    pass
        # Keep the in-memory index warm for later syncs this run.
        if found:
            _team_sessions[tid] = found
            for sid in found:
                _session_team[sid] = tid
        return sorted(found)

    def _team_repo_has_files(team_id: str) -> bool:
        repo = _team_repo_dir(team_id)
        if not repo.is_dir():
            return False
        try:
            return any(
                p.name != "README.md" and not p.name.startswith(".")
                for p in repo.iterdir()
            )
        except OSError:
            return False

    def _inject_team_repo_into_tree(nodes: list[dict], ws: Path, team_id: str) -> list[dict]:
        """List team File Repo contents under ``team_files/`` in the workspace tree.

        ``workspace/team_files/`` on the host is an empty Docker bind-mount point;
        canonical files live in ``gui_team_repos/<team_id>/``.  File nodes use repo
        paths so preview/open stay under ``~/.hermes``.
        """
        repo = _team_repo_dir(team_id)
        if not repo.is_dir():
            return nodes
        children = _walk_dir(repo)
        if not children:
            return nodes
        tf_path = str((ws / _TEAM_FILES_SUBDIR).resolve())
        for node in nodes:
            if node.get("name") == _TEAM_FILES_SUBDIR and node.get("is_dir"):
                node["children"] = children
                return nodes
        nodes.append({
            "name": _TEAM_FILES_SUBDIR,
            "path": tf_path,
            "is_dir": True,
            "preview_type": None,
            "children": children,
        })
        nodes.sort(key=lambda n: (not n["is_dir"], n["name"].lower()))
        return nodes

    def _team_files_note(ws: Path, *, is_claude: bool = False) -> str:
        """Workspace-header hint when a desk's team has files in the File Repo.

        Hermes desks reach the repo through the Docker mount; the Claude Code agent
        runs on the host, where ``team_files/`` is a real folder in its cwd."""
        tid = _load_desk_team_id(ws)
        if not tid or not _team_repo_has_files(tid):
            return ""
        if is_claude:
            host_tf = ws / _TEAM_FILES_SUBDIR
            return (
                f"\nShared team files (File Repo) are in {host_tf}/\n"
                f"  - It's a folder in your workspace — use the relative path "
                f"{_TEAM_FILES_SUBDIR}/ with your normal tools.\n"
            )
        docker_tf = f"{_DOCKER_WORKSPACE}/{_TEAM_FILES_SUBDIR}"
        return (
            f"\nShared team files (File Repo, mounted at {docker_tf}/):\n"
            f"  - terminal, read_file, search_files, write_file, patch, "
            f"vision_analyze, video_analyze: {docker_tf}/\n"
            f"  - Use container paths like {docker_tf}/<folder>/ — "
            f"host macOS paths do not exist inside Docker.\n"
        )

    def _workspace_path_note(ws: Path, *, include_task_hint: bool = False,
                             is_claude: bool = False) -> str:
        """Standard workspace path hint prepended to agent messages.

        Hermes agents run their tools inside Docker, so they get ``/workspace``
        paths. The Claude Code agent runs on the host with this directory as its
        cwd and uses its own native tools (Read/Edit/Bash/…) — give it a plain
        host-path note with no Docker or Hermes-tool references."""
        if is_claude:
            lines = [
                f"[Workspace: your working directory is {ws}",
                "  - You're already in it right now — run `pwd`/`ls` to see where "
                "you are; don't assume a hard-coded /workspace path.",
                "  - You are running on the host (not in a container); relative "
                "paths resolve here. Use your normal tools (Read, Edit, Bash, …).",
            ]
        else:
            docker_ws = _DOCKER_WORKSPACE
            lines = [
                f"[Workspace: all tools run inside Docker — use {docker_ws}/ paths.",
                f"  - Workspace root: {docker_ws}/",
            ]
        if include_task_hint:
            lines.append(
                "Your task is provided below — start working on it immediately "
                "without reading any files first."
            )
        team_note = _team_files_note(ws, is_claude=is_claude)
        if team_note:
            lines.append(team_note.strip())
        lines.append("]")
        return "\n".join(lines)

    def _write_team_files_readme(repo: Path) -> None:
        """Drop a short pointer file in the team repo root for agent discovery."""
        if not repo.is_dir():
            return
        readme = repo / "README.md"
        try:
            entries = sorted(p.name for p in repo.iterdir()
                             if p.name != "README.md" and not p.name.startswith("."))
        except OSError:
            entries = []
        listing = "\n".join(f"- `{n}/`" if (repo / n).is_dir() else f"- `{n}`"
                            for n in entries[:40])
        body = (
            "# Team shared files\n\n"
            "This directory is the team's File Repo. Every desk on the team sees it "
            "via `/workspace/team_files/` inside Docker (bind-mounted from this folder).\n\n"
        )
        if listing:
            body += f"## Contents\n\n{listing}\n"
        try:
            readme.write_text(body, encoding="utf-8")
        except OSError:
            pass

    def _prepare_team_files_mount(team_id: str, workspace_dir: Path) -> None:
        """Make workspace/team_files a host-visible relative symlink to the team repo.

        The repo is ALSO bind-mounted into the container at ``/workspace/team_files``
        (see ``_apply_team_repo_env``), which is what the agent's tools actually use.
        A host symlink here means a host terminal / the file tree show the same files
        instead of an empty bind-mount point: inside the container the nested bind
        mount overlays the symlink (the mount, not the symlink target, provides the
        bytes), while on the host the symlink resolves to the canonical repo. The
        target is *relative* so it survives moving/copying ``~/.hermes``.

        (Verified on macOS Docker Desktop. On Linux, bind-mounting onto a symlinked
        subpath resolves differently — revisit if this ever runs on Linux.)
        """
        repo = _team_repo_dir(team_id)
        try:
            repo.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        dest = workspace_dir / _TEAM_FILES_SUBDIR
        try:
            rel = os.path.relpath(repo.resolve(), workspace_dir.resolve())
        except OSError:
            return
        try:
            if dest.is_symlink():
                if os.readlink(dest) == rel:
                    return
                dest.unlink()
            elif dest.is_dir():
                # Old empty bind-mount point: remove if empty; never clobber content.
                try:
                    dest.rmdir()
                except OSError:
                    return
            elif dest.exists():
                dest.unlink()
            dest.symlink_to(rel)
        except OSError:
            pass

    def _apply_profile_terminal_env(env: dict, config_home: Path | None) -> None:
        """Bridge profile config.yaml ``terminal:`` section into worker env."""
        if not config_home:
            return
        cfg_path = config_home / "config.yaml"
        if not cfg_path.is_file():
            return
        try:
            import yaml  # noqa: PLC0415
            term = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}).get("terminal") or {}
        except Exception:
            return
        if not isinstance(term, dict):
            return
        backend = term.get("backend") or term.get("env_type")
        if backend:
            env["TERMINAL_ENV"] = str(backend)
        for key, env_var in (
            ("docker_image", "TERMINAL_DOCKER_IMAGE"),
            ("docker_mount_cwd_to_workspace", "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"),
            ("container_persistent", "TERMINAL_CONTAINER_PERSISTENT"),
            ("timeout", "TERMINAL_TIMEOUT"),
            ("lifetime_seconds", "TERMINAL_LIFETIME_SECONDS"),
        ):
            if key in term:
                env[env_var] = str(term[key]).lower() if isinstance(term[key], bool) else str(term[key])

    def _apply_team_repo_env(env: dict, team_id: str, workspace_dir: Path | None = None) -> None:
        """Wire team File Repo into worker env for host writes + Docker bind-mount.

        ``team_files/`` on the host is an empty mount-point directory. The canonical
        repo lives at ``gui_team_repos/<team_id>/`` and is bind-mounted into Docker
        at ``/workspace/team_files`` via ``TERMINAL_DOCKER_VOLUMES``. ``HERMES_GUI_TEAM_REPO``
        tells ``hermes_worker`` to allow writes into the repo.

        Hermes treats any volume containing ``:/workspace`` as an explicit workspace
        mount and skips ``TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE``.  We must therefore
        include *both* ``<desk-workspace>:/workspace`` and
        ``<team-repo>:/workspace/team_files`` in ``TERMINAL_DOCKER_VOLUMES`` — listing
        only the team_files mount leaves ``/workspace`` empty inside the container.
        """
        repo = _team_repo_dir(team_id)
        ws = workspace_dir or Path(env.get("HERMES_WORKDIR", ""))
        try:
            repo.mkdir(parents=True, exist_ok=True)
            repo_real = str(repo.resolve())
            ws_real = str(ws.resolve()) if ws else ""
        except OSError:
            return
        env["HERMES_GUI_TEAM_REPO"] = repo_real
        env["HERMES_GUI_DOCKER_WORKSPACE"] = _DOCKER_WORKSPACE
        volumes: list[str] = []
        if ws_real:
            volumes.append(f"{ws_real}:{_DOCKER_WORKSPACE}")
        volumes.append(f"{repo_real}:{_DOCKER_WORKSPACE}/{_TEAM_FILES_SUBDIR}")
        env["TERMINAL_DOCKER_VOLUMES"] = json.dumps(volumes)
        env.setdefault("TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES", "true")
        env["TERMINAL_ENV"] = env.get("TERMINAL_ENV") or "docker"

    def _maybe_force_docker_reset(env: dict, sid: str) -> None:
        """Only nuke the sandbox container when bind-mounts changed for this desk."""
        vols = env.get("TERMINAL_DOCKER_VOLUMES", "")
        prev = _session_docker_vols.get(sid)
        if prev is not None and vols != prev:
            env["HERMES_GUI_FORCE_DOCKER_RESET"] = "1"

    def _sync_team_repo_to_live_desks(team_id: str) -> int:
        """Ensure every desk on the team has a team_files mount-point directory."""
        n = 0
        for sid in _sessions_for_team(team_id):
            ws = _find_workspace(sid)
            if ws and ws.exists():
                _prepare_team_files_mount(team_id, ws)
                n += 1
        return n

    async def _restart_team_desk_workers(team_id: str) -> int:
        """Drop warm workers so the next turn recreates Docker with fresh volume mounts."""
        n = 0
        for sid in _sessions_for_team(team_id):
            if sid in _persistent_procs or sid in _running_procs:
                await _terminate_session_workers(sid)
                n += 1
        return n

    def _find_workspace(sid: str) -> Path | None:
        if sid in _session_workspaces:
            p = Path(_session_workspaces[sid])
            if p.exists():
                return p
        # Per-desk sandbox layout (current): ~/.hermes/gui_sandboxes/<sid>/docker/default/workspace
        cand = _desk_paths(sid)[1]
        if cand.exists():
            _session_workspaces[sid] = str(cand)
            return cand
        # Legacy layout: a slug dir under the shared ws_root, tagged with a marker.
        if ws_root.exists():
            for d in ws_root.iterdir():
                if d.is_dir():
                    marker = d / ".hermes_session_id"
                    try:
                        if marker.exists() and marker.read_text().strip() == sid:
                            _session_workspaces[sid] = str(d)
                            return d
                    except Exception:
                        pass
        return None

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # Startup + shutdown live in _startup_tasks / _shutdown_cleanup, defined
        # later in this factory; closures resolve them at call time. (Replaces the
        # deprecated @app.on_event hooks.)
        await _startup_tasks()
        try:
            yield
        finally:
            await _shutdown_cleanup()

    app = FastAPI(title="Hermes GUI", version="0.1.0", lifespan=_lifespan)
    # In normal use no cross-origin browser request is ever made: production
    # serves the SPA from this same origin, and the Vite dev server proxies
    # /api and /ws. So we only need to allow localhost dev origins — a wildcard
    # would let any website the user is browsing read API responses (e.g. file
    # contents) cross-origin. Callers may override via `allowed_origins`.
    if allowed_origins is None:
        allowed_origins = [
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in (8765, 5173)  # backend default + Vite dev server
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    def _load_persisted_title(sid: str) -> str | None:
        """Try to load a previously generated title from disk."""
        if sid in _session_titles:
            return _session_titles[sid]
        ws = _find_workspace(sid)
        if ws:
            title_file = ws / ".hermes_title"
            if title_file.exists():
                try:
                    title = title_file.read_text().strip()
                    if title:
                        _session_titles[sid] = title
                        return title
                except Exception:
                    pass
        return None

    def _load_agent_marker(ws: Path | None) -> str | None:
        if not ws:
            return None
        pf = ws / ".hermes_profile"
        if not pf.is_file():
            return None
        try:
            agent = pf.read_text(encoding="utf-8").strip()
            return agent or None
        except Exception:
            return None

    def _agent_runtime_info(agent_id: str, ws: Path | None = None) -> dict:
        """Model routing for a profile (for API / UI display)."""
        if _is_claude_agent(agent_id):
            model = ""
            if ws is not None:
                try:
                    model = (ws / ".claude_model").read_text(encoding="utf-8").strip()
                except OSError:
                    pass
            return {"agent": agent_id, "agent_model": model or "Claude default",
                    "agent_base_url": "claude-agent-sdk"}
        try:
            pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id)
        except (ValueError, FileNotFoundError):
            return {}
        model, base_url = _read_model_info(pdir)
        if ws is not None:
            mf = ws / ".hermes_model"
            if mf.is_file():
                try:
                    override = mf.read_text(encoding="utf-8").strip()
                    if override:
                        model = override
                except OSError:
                    pass
        return {"agent": agent_id, "agent_model": model, "agent_base_url": base_url}

    def _apply_desk_model_marker(env: dict, ws: Path, agent_id: str, model: str) -> None:
        """Apply a per-desk model override.

        Claude desks pin the model via ``.claude_model`` + ``CLAUDE_AGENT_MODEL`` (the
        Claude worker reads those and ignores HERMES_MODEL); every other desk uses
        ``.hermes_model`` + ``HERMES_MODEL`` (Ollama or vLLM/OpenAI-compat)."""
        if not model:
            return
        if _is_claude_agent(agent_id):
            env["CLAUDE_AGENT_MODEL"] = model
            marker = ".claude_model"
        else:
            env["HERMES_MODEL"] = model
            marker = ".hermes_model"
        try:
            (ws / marker).write_text(model, encoding="utf-8")
        except OSError:
            pass

    def _clear_agent_markers(ws: Path) -> None:
        for name in (".hermes_profile", ".hermes_model"):
            marker = ws / name
            if marker.is_file():
                try:
                    marker.unlink()
                except OSError:
                    pass

    def _iter_desk_workspaces() -> "list[tuple[Path, str]]":
        """Every known desk workspace as ``(workspace_dir, session_id)``, deduped."""
        seen: set[Path] = set()
        out: list[tuple[Path, str]] = []

        def _add(ws: Path, sid: str) -> None:
            try:
                rp = ws.resolve()
            except OSError:
                rp = ws
            if rp in seen:
                return
            seen.add(rp)
            out.append((ws, sid))

        for sid, ws_path in list(_session_workspaces.items()):
            _add(Path(ws_path), sid)
        sandboxes = home / "gui_sandboxes"
        if sandboxes.is_dir():
            for sid_dir in sandboxes.iterdir():
                if not sid_dir.is_dir():
                    continue
                ws = sid_dir / "docker" / "default" / "workspace"
                if ws.is_dir():
                    _add(ws, sid_dir.name)
        if ws_root.is_dir():
            for d in ws_root.iterdir():
                if not d.is_dir():
                    continue
                marker = d / ".hermes_session_id"
                try:
                    sid = marker.read_text(encoding="utf-8").strip() if marker.is_file() else d.name
                except OSError:
                    sid = d.name
                _add(d, sid)
        return out

    def _unbind_agent_from_desks(agent_id: str) -> list[str]:
        """Drop agent profile binding from every desk workspace using it."""
        aid = (agent_id or "").strip().lower()
        if not aid:
            return []
        affected: list[str] = []
        for ws, sid in _iter_desk_workspaces():
            if _load_agent_marker(ws) != aid:
                continue
            _clear_agent_markers(ws)
            if sid not in affected:
                affected.append(sid)
        return affected

    async def _resolve_desk_model(agent_id: str, raw_model: str) -> str:
        """Resolve model for a new/resumed desk (profile override or GUI default)."""
        model = (raw_model or "").strip()
        ollama_root = _ollama_url_from_cfg()
        if agent_id:
            if not model:
                return ""
            try:
                pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id)
                _, base_url = _read_model_info(pdir)
            except (ValueError, FileNotFoundError):
                return model
            return await _resolve_gui_model(model, base_url, ollama_root)
        if model:
            hermes_cfg_base, _ = hermes_model_config(home)
            return await _resolve_gui_model(model, hermes_cfg_base, ollama_root)
        return _EFFECTIVE_MODEL or ""

    def _apply_claude_agent_env(env: dict, ws: Path, agent_id: str) -> None:
        """Configure a desk to run the Claude Code agent (Claude Agent SDK).

        Claude does NOT use Hermes' layered config/profile dir — it resolves its own
        model, tools, and auth. We only stamp the agent KIND (so ``_worker_cmd`` picks
        the Claude worker), the model alias, the permission mode, and the profile
        marker so resume keeps treating this desk as Claude."""
        env["HERMES_GUI_AGENT_KIND"] = "claude"
        env["HERMES_GUI_AGENT"] = agent_id
        env.setdefault("CLAUDE_AGENT_PERMISSION_MODE", "bypassPermissions")
        try:
            m = (ws / ".claude_model").read_text(encoding="utf-8").strip()
        except OSError:
            m = ""
        if m:
            env["CLAUDE_AGENT_MODEL"] = m
        else:
            env.pop("CLAUDE_AGENT_MODEL", None)
        # Force this Claude SDK desk onto OAuth / `claude /login` subscription auth —
        # an inherited ANTHROPIC_API_KEY (or gateway/cloud cred) would otherwise
        # shadow it (precedence: API key > OAuth token > /login). Scoped to the Claude
        # SDK desk; Hermes-on-Claude-API desks (read_profile_env) are unaffected.
        _scrub_claude_oauth_only(env)
        try:
            (ws / ".hermes_profile").write_text(agent_id, encoding="utf-8")
        except OSError:
            pass

    def _desk_is_claude(sid: str) -> bool:
        """True if this desk's saved agent is the Claude Code agent."""
        return _is_claude_agent(_load_agent_marker(_find_workspace(sid)) or "")

    def _apply_agent_profile(env: dict, ws: Path, agent_id: str) -> None:
        """Point worker at a Hermes profile dir; profile config owns model/base_url."""
        if _is_claude_agent(agent_id):
            _apply_claude_agent_env(env, ws, agent_id)
            return
        try:
            pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc)) from exc
        env["HERMES_GUI_AGENT"] = agent_id
        env["HERMES_GUI_CONFIG_HOME"] = str(pdir.resolve())
        env.pop("HERMES_MODEL", None)
        _apply_profile_terminal_env(env, pdir)
        try:
            (ws / ".hermes_profile").write_text(agent_id, encoding="utf-8")
        except Exception:
            pass

    def _set_session_agent(ws: Path, agent_id: str) -> None:
        """Switch (or clear) a desk's assigned agent profile, persisted in .hermes_profile.

        Resuming reads this marker to pick the profile, so rewriting it hands the
        desk's existing conversation to a *different* agent on the next turn. Passing
        an empty id clears the marker (back to the GUI default model).
        """
        # Switching profile hands the model to the new profile (or the GUI default);
        # drop any per-desk model pinned for the old one so it can't shadow it.
        try:
            (ws / ".hermes_model").unlink(missing_ok=True)
        except OSError:
            pass
        marker = ws / ".hermes_profile"
        if _is_claude_agent(agent_id):
            # Claude has no Hermes profile dir to resolve — just record the marker.
            try:
                marker.write_text(agent_id, encoding="utf-8")
            except OSError:
                pass
            _append_profile_history(ws, agent_id)
            return
        if agent_id:
            try:
                resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(400, str(exc)) from exc
            try:
                marker.write_text(agent_id, encoding="utf-8")
            except Exception:
                pass
        else:
            try:
                marker.unlink(missing_ok=True)
            except Exception:
                pass
        # Log the switch so per-session history can show this profile for the
        # session rows created after it.
        _append_profile_history(ws, agent_id)

    def _log_desk_run(ws: "Path | None", sid: str, kind: str, profile: str) -> None:
        """Record one run (start/resume) in the desk's run-history log, resolving
        the model in effect (pinned marker → profile default → GUI default)."""
        if ws is None:
            return
        model = ""
        mf = ws / ".hermes_model"
        if mf.is_file():
            try:
                model = mf.read_text(encoding="utf-8").strip()
            except OSError:
                model = ""
        if not model and profile:
            model = _agent_runtime_info(profile, ws).get("agent_model", "") or ""
        if not model:
            model = _EFFECTIVE_MODEL or ""
        _append_run_history(ws, {
            "at": time.time(),
            "kind": kind,
            "profile": profile or "",
            "model": model,
            "session_id": sid,
        })

    def _load_desk_tools_enabled(ws: Path | None) -> list[str]:
        if not ws:
            return []
        tf = ws / ".hermes_tools"
        if not tf.is_file():
            return []
        try:
            raw = tf.read_text(encoding="utf-8").strip()
        except OSError:
            return []
        parsed = parse_tools_marker(raw)
        if parsed is not None:
            return parsed
        legacy = ui_names_from_legacy_disabled(
            [t.strip() for t in raw.split(",") if t.strip()],
        )
        return legacy if legacy is not None else []

    def _enrich_session(d: dict) -> dict:
        sid = d.get("id", "")
        d["is_running"] = sid in _running_procs
        d["title_summary"] = _load_persisted_title(sid)
        d["auto_continue"] = _session_autocontinue.get(sid, False)
        ws = _find_workspace(sid)
        d["task_solved"] = bool(ws and (ws / _SOLVED_MARKER).exists())
        d["workspace_path"] = str(ws) if ws else None
        # Expose the desk's team (from its .hermes_team_id marker) so the frontend can
        # reconstruct teams it doesn't have locally — e.g. desks created via the API /
        # a script, which never went through the browser's localStorage team flow.
        d["team_id"] = _load_desk_team_id(ws) if ws else None
        d["is_sleeping"] = sid in _session_sleeping
        agent = _load_agent_marker(ws)
        if agent:
            d.update(_agent_runtime_info(agent, ws))
        elif ws:
            model = (d.get("model") or "").strip()
            mf = ws / ".hermes_model"
            if mf.is_file():
                try:
                    override = mf.read_text(encoding="utf-8").strip()
                    if override:
                        model = override
                except OSError:
                    pass
            if model:
                d["agent_model"] = model
        if ws and (ws / ".hermes_tools").is_file():
            d["desk_tools"] = _load_desk_tools_enabled(ws)
        # Activity span (first command → last activity) across the whole desk
        # lineage, same bounds the Overview chart uses. Lets the desk-card timer
        # show actual execution time instead of wall-clock-since-spawn, so an idle
        # desk freezes at its last activity rather than counting overnight hours.
        first_at, last_at = db.get_desk_time_bounds(sid)
        if first_at:
            d["first_activity_at"] = first_at
        if last_at:
            d["last_activity_at"] = last_at
        return d

    # Claude desks have no Hermes state.db, so db.list_sessions() can't see them.
    # We synthesize a Session-shaped entry from the on-disk markers + live process
    # state so the desk renders and streams. (No DB → no persisted history; this is
    # the agreed MVP tradeoff.) Tracked only for desks live in this server process.
    def _claude_desk_ids() -> "list[str]":
        ids = {sid for sid in (set(_session_workspaces) | set(_persistent_procs)
                               | set(_running_procs)) if _desk_is_claude(sid)}
        # Discover on disk too, so Claude desks survive a server restart (no state.db
        # to enumerate them like Hermes desks). Reads one marker per unknown desk.
        try:
            for d in (home / "gui_sandboxes").iterdir():
                if not d.is_dir() or d.name in ids:
                    continue
                marker = d / "docker" / "default" / "workspace" / ".hermes_profile"
                try:
                    if marker.is_file() and _is_claude_agent(marker.read_text(encoding="utf-8")):
                        ids.add(d.name)
                except OSError:
                    pass
        except OSError:
            pass
        return list(ids)

    def _claude_desk_title(ws: Path) -> str:
        try:
            for line in (ws / "TASK.md").read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    return s[:120]
        except OSError:
            pass
        return ""

    def _claude_desk_stub(sid: str) -> "dict | None":
        ws = _find_workspace(sid)
        if ws is None or not _is_claude_agent(_load_agent_marker(ws) or ""):
            return None
        try:
            started = (datetime.strptime(sid[:15], "%Y%m%d_%H%M%S")
                       .replace(tzinfo=timezone.utc).isoformat())
        except ValueError:
            started = datetime.now(timezone.utc).isoformat()
        try:
            model = (ws / ".claude_model").read_text(encoding="utf-8").strip()
        except OSError:
            model = ""
        return _enrich_session({
            "id": sid, "started_at": started, "ended_at": None,
            "source": "workbench", "model": model, "parent_session_id": None,
            "title": _load_persisted_title(sid) or _claude_desk_title(ws) or "Claude task",
            "message_count": 0, "token_estimate": 0,
        })

    # ── Sessions ──────────────────────────────────────────────────────────────

    @app.get("/api/sessions")
    def list_sessions(limit: int = 50, offset: int = 0):
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        sessions = db.list_sessions(limit=limit, offset=offset)
        out = [_enrich_session(asdict(s)) for s in sessions]
        seen = {d["id"] for d in out}
        claude = [stub for sid in _claude_desk_ids() if sid not in seen
                  for stub in (_claude_desk_stub(sid),) if stub is not None]
        claude.sort(key=lambda d: d.get("started_at", ""), reverse=True)
        return claude + out

    @app.get("/api/sessions/saved")
    def list_saved_desks():
        """Desk archives in the repo's ``saved/`` directory (Load desk default folder)."""
        saved = _REPO_ROOT / "saved"
        if not saved.is_dir():
            return {"dir": str(saved), "archives": []}
        archives = []
        for p in sorted(saved.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file():
                continue
            n = p.name.lower()
            if not (n.endswith(".tar.gz") or n.endswith(".tgz")):
                continue
            st = p.stat()
            archives.append({
                "filename": p.name,
                "size": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
        return {"dir": str(saved), "archives": archives}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        s = db.get_session(session_id)
        if s:
            return _enrich_session(asdict(s))
        stub = _claude_desk_stub(session_id)  # DB-free Claude desk
        if stub:
            return stub
        raise HTTPException(404, "Session not found")

    @app.patch("/api/sessions/{session_id}/desk-config")
    async def patch_session_desk_config(session_id: str, body: dict):
        """Update per-desk profile/model/tools markers (applies on next worker spawn)."""
        ws = _find_workspace(session_id)
        if not ws:
            raise HTTPException(404, "Session workspace not found")
        restart_worker = False

        if "agent" in body:
            aid = (body.get("agent") or "").strip().lower()
            prev = _load_agent_marker(ws) or ""
            if aid != prev:
                _set_session_agent(ws, aid)
                restart_worker = True
                if "tools" not in body:
                    try:
                        _write_desk_tools_marker(
                            ws,
                            _default_desk_tools_for_agent(cfg.agent_profiles_dir, home, aid),
                        )
                    except OSError as exc:
                        raise HTTPException(500, str(exc)) from exc

        if "model" in body:
            aid_model = _load_agent_marker(ws) or ""
            is_claude_desk = _is_claude_agent(aid_model)
            model = (body.get("model") or "").strip()
            if is_claude_desk:
                model = _claude_model(model)        # only opus/sonnet/haiku/claude-*
            marker = ws / (".claude_model" if is_claude_desk else ".hermes_model")
            prev_model = ""
            try:
                prev_model = marker.read_text(encoding="utf-8").strip()
            except OSError:
                pass
            if model:
                _apply_desk_model_marker({}, ws, aid_model, model)
            else:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass
            # A Claude desk's warm worker pins the model in-process at spawn, so the
            # change only lands once it restarts — terminate it (as agent/tool changes
            # already do) so the next turn runs on the newly chosen model.
            if is_claude_desk and model != prev_model:
                restart_worker = True

        if "tools" in body:
            tools = body.get("tools")
            if isinstance(tools, list):
                enabled = [str(t).strip() for t in tools if str(t).strip()]
                try:
                    _write_desk_tools_marker(ws, enabled)
                except OSError as exc:
                    raise HTTPException(500, str(exc)) from exc
                restart_worker = True

        if restart_worker:
            await _terminate_session_workers(session_id)

        s = db.get_session(session_id)
        if s:
            return _enrich_session(asdict(s))
        stub = _claude_desk_stub(session_id)   # DB-free Claude desk (no state.db row)
        if stub:
            return stub
        raise HTTPException(404, "Session not found")

    @app.get("/api/sessions/{session_id}/activity")
    def get_activity(session_id: str, limit: int = 1_000_000, tail: bool = False):
        # Desk feed shows the FULL conversation (effectively unlimited) — long
        # multi-hour tasks shouldn't have their early messages truncated. The cap
        # is just a sanity bound far above any realistic desk size.
        limit = max(1, min(limit, 1_000_000))
        messages = db.get_messages(session_id, limit=limit, tail=tail)
        events = parse_activity(messages)
        # Overlay real recorded emit-times (exact) where we have them; events
        # without a match keep Hermes's coarse flush time, marked approximate.
        _apply_real_times(events, session_id)
        # Fold in turns that never reached Hermes' DB (errored / interrupted) so
        # they stay visible instead of being swept on the next snapshot.
        events = _merge_orphan_feed(events, session_id, _find_workspace(session_id))
        return [asdict(e) for e in events]

    @app.get("/api/sessions/{session_id}/overview")
    def get_overview(session_id: str, limit: int = 10000):
        """Workspace-scoped activity for the Overview chart.

        Merges every session id that shares this desk's workspace lineage (same
        folder or same ``.hermes_workspace_key``). Desks are never linked by
        task content — same-team desks with identical prompts stay separate.
        """
        limit = max(1, min(limit, 10000))
        ws = _find_workspace(session_id)
        if ws:
            HermesDB.ensure_workspace_key(ws)
        legacy_dirs = [d for d in ws_root.iterdir() if d.is_dir()] if ws_root.is_dir() else []
        related = db.find_related_session_ids(
            session_id, ws, extra_workspace_dirs=legacy_dirs,
        )
        events = []
        messages: list = []
        for sid in related:
            # Read each desk db in full — Hermes may store multiple session_id
            # values inside one sandbox db across resumes; filtering the merged
            # message list by folder session id would drop prior runs.
            if db._desk_db(sid):
                sid_msgs = db.get_desk_messages(sid, limit=limit)
            else:
                sid_msgs = db.get_messages(sid, limit=limit)
            evts = parse_activity(sid_msgs)
            _apply_real_times(evts, sid)
            for inner_sid in {m.session_id for m in sid_msgs}:
                if inner_sid != sid:
                    _apply_real_times(evts, inner_sid)
            # Fold in this desk's interrupted/errored turns (the orphan feed) so a
            # run that was stopped half-way isn't a blank gap on the chart — the
            # same per-desk merge the Feed uses. Orphans already carry real exact
            # times, so they land at their true spot (and the per-desk scope keeps
            # the workspace-lineage merge from pooling one desk's turns into
            # another — DESK_ISOLATION_AUDIT). Runs even when sid_msgs is empty, so
            # a desk whose only turn was interrupted (nothing committed to the DB)
            # still charts.
            evts = _merge_orphan_feed(evts, sid, _find_workspace(sid))
            if not evts:
                continue
            messages.extend(sid_msgs)
            events.extend(evts)
        events.sort(key=lambda e: db._timestamp_sort_key(e.timestamp))
        if len(events) > limit:
            events = events[:limit]
        if len(messages) > limit:
            messages = sorted(
                messages,
                key=lambda m: db._timestamp_sort_key(m.timestamp),
            )[:limit]
        started_at, last_at = db.get_workspace_time_bounds(related)
        return {
            "events": [asdict(e) for e in events],
            "started_at": started_at,
            "last_at": last_at,
            "message_count": len(messages),
            "session_ids": related,
            "truncated": len(messages) >= limit,
        }

    @app.get("/api/sessions/{session_id}/console")
    def get_console(session_id: str, limit: int = 2000):
        """Restore the Agent Console (clean shell I/O) when a desk is reopened.

        Prefer the persisted on-disk log: it holds the FULL verbatim stream of every
        turn — including interrupted turns that never reach Hermes' DB. Fall back to
        a DB reconstruction for desks that haven't streamed under this server yet."""
        logged = _read_desk_log(session_id, "console")
        if logged:
            return {"text": logged}
        limit = max(1, min(limit, 5000))
        # Read the whole desk db (all session ids across resumes), matching the
        # Activity overview — a session-scoped read would drop prior runs and
        # leave the panel showing only the current session after a refresh.
        if db._desk_db(session_id):
            messages = db.get_desk_messages(session_id, limit=limit)
        else:
            messages = db.get_messages(session_id, limit=limit)
        return {"text": _backfill_console(session_id, messages)}

    @app.get("/api/sessions/{session_id}/terminal")
    def get_terminal(session_id: str, limit: int = 2000):
        """Rebuild the Debug terminal stream for reopened sessions.

        Prefer the persisted on-disk log (full verbatim history, incl. interrupted
        turns); fall back to a DB reconstruction when no log exists yet."""
        logged = _read_desk_log(session_id, "terminal")
        if logged:
            return {"text": logged}
        limit = max(1, min(limit, 5000))
        # Whole-desk read (see get_console) so the Debug terminal restores every
        # prior run's output after a page refresh, not just the current session.
        if db._desk_db(session_id):
            messages = db.get_desk_messages(session_id, limit=limit)
        else:
            messages = db.get_messages(session_id, limit=limit)
        return {"text": _backfill_terminal(messages)}

    @app.get("/api/sessions/{session_id}/todos")
    def get_todos(session_id: str):
        messages = db.get_messages(session_id, limit=2000)
        tasks: dict[str, dict] = {}
        all_assistant_texts: list[str] = []

        for msg in messages:
            # ── Structured task tool calls (Claude Code / TaskCreate) ──────────
            for tc in (msg.tool_calls or []):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except Exception:
                    args = {}
                tc_id = tc.get("id", f"t{len(tasks)}")
                if name == "TaskCreate":
                    tasks[tc_id] = {
                        "id": tc_id,
                        "title": args.get("title", args.get("description", "Task")),
                        "status": args.get("status", "pending"),
                        "parent_id": args.get("parent_task_id"),
                    }
                elif name == "TaskUpdate":
                    target = args.get("task_id", "")
                    if target in tasks and "status" in args:
                        tasks[target]["status"] = args["status"]

            # ── Collect assistant text (try JSON content blocks too) ───────────
            if msg.role == "assistant" and msg.content:
                raw = msg.content
                text = ""
                if isinstance(raw, str):
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            text = "\n".join(
                                b.get("text", "") for b in parsed
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                    except Exception:
                        text = raw
                if text.strip() and len(text.strip()) > 10:
                    all_assistant_texts.append(text.strip())

        last_text = all_assistant_texts[-1] if all_assistant_texts else ""

        # ── Extract markdown tasks from last assistant reply ───────────────────
        md_tasks: list[dict] = []
        if not tasks and last_text:
            for line in last_text.split("\n"):
                # Checkboxes: - [ ] or - [x]
                m = re.match(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$", line)
                if m:
                    done = m.group(1).lower() == "x"
                    md_tasks.append({
                        "id": f"md-{len(md_tasks)}",
                        "title": m.group(2).strip(),
                        "status": "completed" if done else "pending",
                        "parent_id": None,
                    })
                    continue
                # Numbered steps: 1. 2. 3.  (min 15 chars to skip noise)
                m2 = re.match(r"^\s*\d+\.\s+(.{15,})$", line)
                if m2:
                    title = m2.group(1).strip()[:120]
                    md_tasks.append({
                        "id": f"num-{len(md_tasks)}",
                        "title": title,
                        "status": "pending",
                        "parent_id": None,
                    })

        task_list = list(tasks.values()) or md_tasks
        # Always surface the last assistant reply as handoff context
        return {
            "tasks": task_list,
            "summary": last_text[:1200],
        }

    def _drop_session_state(sid: str) -> None:
        """Clear all in-memory bookkeeping for a deleted session."""
        _session_workspaces.pop(sid, None)
        if sid in _session_sleeping:
            _session_sleeping.discard(sid)
            _save_sleeping()
        _session_titles.pop(sid, None)
        _session_audits.pop(sid, None)
        _session_manager_resumes.pop(sid, None)
        _session_audit_best.pop(sid, None)
        _session_title_tokens.pop(sid, None)
        _session_event_times.pop(sid, None)
        _event_times_loaded.discard(sid)
        _session_autocontinue.pop(sid, None)
        _session_continue_count.pop(sid, None)
        _session_user_stopped.discard(sid)
        _live_queues.pop(sid, None)
        _live_event_buffer.pop(sid, None)
        _orphan_feed_events.pop(sid, None)
        _subagent_records.pop(sid, None)
        _session_turn_interrupted.discard(sid)
        _terminal_queues.pop(sid, None)
        _console_queues.pop(sid, None)
        _console_turn_buf.pop(sid, None)
        _terminal_turn_buf.pop(sid, None)
        _log_seeded.discard(sid)
        _turn_done_events.pop(sid, None)
        tid = _session_team.pop(sid, None)
        if tid and tid in _team_sessions:
            _team_sessions[tid].discard(sid)
            if not _team_sessions[tid]:
                _team_sessions.pop(tid, None)

    async def _terminate_session_workers(sid: str) -> None:
        """Stop any worker (persistent or one-shot) tied to this session."""
        _session_autocontinue[sid] = False
        if _running_procs.get(sid) is not None:   # a turn is in flight
            _session_turn_interrupted.add(sid)
        q = _live_queues.get(sid)
        if q:
            try:
                await q.put({"type": "interrupted"})
            except Exception:
                pass
        for store in (_persistent_procs, _running_procs):
            proc = store.pop(sid, None)
            if not proc:
                continue
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
        _session_worker_opts.pop(sid, None)

    def _force_rmtree(path: Path) -> bool:
        """Remove a directory tree and report whether it's ACTUALLY gone.

        Hermes' bootstrap puts a macOS ``user:… deny delete`` ACL on a desk's
        cache/skills dirs. A plain ``shutil.rmtree(ignore_errors=True)`` then
        fails with EPERM but swallows the error, leaving the sandbox behind — so a
        later desk re-load wrongly reports "already loaded — delete it first".
        Strip ACLs across the tree first (no-op off macOS), then remove and verify
        instead of blindly claiming success."""
        if not path.exists():
            return True
        try:
            if os.uname().sysname == "Darwin":
                subprocess.run(["chmod", "-RN", str(path)],
                               check=False, capture_output=True)
        except Exception:
            pass
        shutil.rmtree(path, ignore_errors=True)
        return not path.exists()

    def _remove_session_artifacts(sid: str) -> dict:
        """Delete on-disk workspace/sandbox/transcript files for a GUI desk."""
        removed: dict[str, bool] = {"sandbox": False, "workspace": False, "transcripts": False}
        sandbox = home / "gui_sandboxes" / sid
        if sandbox.exists():
            removed["sandbox"] = _force_rmtree(sandbox)
        ws = _find_workspace(sid)
        if ws and ws.exists():
            sb = _sandbox_base_of(ws)
            # Legacy slug workspaces live under ws_root, not gui_sandboxes/<sid>.
            if not sb:
                removed["workspace"] = _force_rmtree(ws)
        sessions_dir = home / "sessions"
        if sessions_dir.is_dir():
            for pattern in (f"{sid}.json", f"{sid}.jsonl"):
                p = sessions_dir / pattern
                if p.exists():
                    try:
                        p.unlink()
                        removed["transcripts"] = True
                    except OSError:
                        pass
            try:
                for p in sessions_dir.glob(f"request_dump_{sid}_*.json"):
                    p.unlink(missing_ok=True)
                    removed["transcripts"] = True
            except OSError:
                pass
        return removed

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        """Delete a desk session: stop its worker, purge DB row, remove sandbox/workspace.

        Always reaps the desk's sandbox container too — once the desk is gone its
        container can never be reused (its per-desk key is gone), so leaving it
        would orphan a `hermes-*` container forever. This runs regardless of
        `HERMES_GUI_DOCKER_PERSIST` (persist only keeps containers of *live* desks).
        """
        had = bool(db.get_session(session_id)) or (home / "gui_sandboxes" / session_id).exists()
        await _terminate_session_workers(session_id)
        deleted_db = db.delete_session(session_id)
        removed = _remove_session_artifacts(session_id)
        removed["container"] = _remove_desk_container(session_id) > 0
        _drop_session_state(session_id)
        if not had and not deleted_db and not any(removed.values()):
            raise HTTPException(404, "Session not found")
        return {"ok": True, "deleted": deleted_db, **removed}

    @app.post("/api/sessions/{session_id}/interrupt")
    async def interrupt_session(session_id: str):
        # An explicit user stop also halts the heartbeat auto-continue loop.
        _session_user_stopped.add(session_id)
        _session_autocontinue[session_id] = False
        if _running_procs.get(session_id) is not None:   # a turn is in flight
            _session_turn_interrupted.add(session_id)
        # Push the interrupted event BEFORE stopping so the WS stream shows it
        q = _live_queues.get(session_id)
        if q:
            await q.put({"type": "interrupted"})
        # Persistent worker: stop the current turn but keep the process warm.
        if _PERSISTENT_WORKERS:
            pproc = _persistent_procs.get(session_id)
            if pproc is not None:
                await _send_cmd(pproc, {"cmd": "interrupt"})
            _running_procs.pop(session_id, None)
            return {"ok": True}
        proc = _running_procs.pop(session_id, None)
        # Don't pop _live_queues here — _pump_worker's finally block cleans it up
        # after SIGTERM, ensuring the "done" sentinel reaches the WS client.
        if proc:
            try:
                proc.terminate()  # SIGTERM → hermes cleans up its Docker container
            except Exception:
                pass
            # Force-kill after 5 s if hermes cleanup stalls
            async def _force_kill(p: asyncio.subprocess.Process) -> None:
                await asyncio.sleep(5)
                try:
                    p.kill()
                except Exception:
                    pass
            asyncio.create_task(_force_kill(proc))
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/arrive")
    async def agent_arrive(session_id: str):
        """Inject an 'agent arrived' marker into the live event stream for a desk."""
        q = _live_queues.get(session_id)
        if q:
            await q.put({"type": "agent_arrived"})
        return {"ok": True}

    def _ollama_url_from_cfg() -> str:
        """Resolve the Ollama host root (…:11434) from the configured base_url."""
        ollama_url = "http://127.0.0.1:11434"
        cfg_path = home / "config.yaml"
        if cfg_path.exists():
            try:
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                base = cfg.get("model", {}).get("base_url", "")
                if base:
                    from urllib.parse import urlparse
                    p = urlparse(base)
                    ollama_url = f"{p.scheme}://{p.hostname}:{p.port or 11434}"
            except Exception:
                pass
        return ollama_url

    async def _prewarm_once() -> bool:
        """Spawn one throwaway worker so Ollama (a) loads the capped model into VRAM
        and (b) caches the system-prompt + tool-schema prefix that EVERY desk shares.

        That prefix is the bulk of an agent request (~10 K tokens of Hermes system
        prompt + tool schemas); on a local model, evaluating it cold is the dominant
        time-to-first-token. By running an identical-shaped request before the user
        opens their first desk, Ollama keeps that prefix in its KV slot, so the real
        desk only has to evaluate its short user message. Uses the exact same env a
        real desk would (capped model, lean tools, num_ctx) so the prefix matches.
        Returns True if the warmup completed, False otherwise.
        """
        if not _EFFECTIVE_MODEL:
            return False
        import tempfile  # noqa: PLC0415
        ws = Path(tempfile.gettempdir()) / "agent_gui_prewarm"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "TASK.md").write_text("# Task\n\nReply with the single word OK.\n")
        venv_py = str(_HERMES_VENV_PY)
        if not Path(venv_py).exists():
            venv_py = shutil.which("python3") or "python3"
        env = {**os.environ, **_worker_repo_env(), "HERMES_WORKDIR": str(ws),
               # Confine host-side write_file/patch to this desk's workspace —
               # the file tools run on the host (not in Docker), so without this
               # an absolute path could write anywhere the user can. See FR.
               "HERMES_WRITE_SAFE_ROOT": str(ws),
               "TERMINAL_SANDBOX_DIR": str(ws / "sb"),
               "HERMES_GUI_NUM_CTX": _NUM_CTX,
               "HERMES_GUI_LEAN_TOOLS": _LEAN_TOOLS,
               "HERMES_MODEL": _EFFECTIVE_MODEL}
        _apply_toolset_profile(env, None)
        try:
            proc = await asyncio.create_subprocess_exec(
                venv_py, str(_WORKER_SCRIPT), "Reply with the single word OK.",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                cwd=str(ws), env=env)
            assert proc.stdout
            async for raw in proc.stdout:
                try:
                    evt = json.loads(raw.decode("utf-8", "replace").strip())
                except Exception:
                    continue
                if evt.get("type") in ("done", "error"):
                    break
            try:
                proc.terminate()
            except Exception:
                pass
            await proc.wait()
            return True
        except Exception:
            return False

    async def _apply_manager_profile(profile_id: str, *, persist: bool = True) -> dict:
        """Point the team manager's aux calls at an installed profile's backend.

        Resolves the profile's model / base_url / provider / API key from its
        config.yaml + .env. Ollama models are num_ctx-capped; Gemini is routed
        through its OpenAI-compat endpoint with a Bearer key; the native `claude`
        profile keeps api.anthropic.com + ANTHROPIC_API_KEY for the Messages
        transport (see _anthropic_aux_chat). Empty id → default.
        """
        global _MANAGER_PROFILE, _MGR_BASE_URL, _MGR_MODEL, _MGR_MODEL_DISPLAY
        global _MGR_API_KEY, _MGR_PROVIDER
        pid = (profile_id or "").strip().lower()

        if not pid:
            _MANAGER_PROFILE = _MGR_BASE_URL = _MGR_MODEL = ""
            _MGR_MODEL_DISPLAY = _MGR_API_KEY = _MGR_PROVIDER = ""
        else:
            pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, pid)
            model, base_url = _read_model_info(pdir)
            provider = read_profile_provider(pdir)
            env = read_profile_env(pdir)
            gemini = is_gemini_backend(base_url, provider)
            anthropic = _is_native_anthropic_provider(provider)
            chat_base = base_url.rstrip("/")
            api_key = ""
            if gemini:
                # Gemini exposes an OpenAI-compatible surface under /openai.
                chat_base = f"{chat_base}/openai"
                api_key = env.get("GEMINI_API_KEY", "")
            elif anthropic:
                # The `claude` profile uses the Anthropic Messages API (x-api-key,
                # /v1/messages) — aux calls route through _anthropic_aux_chat. Keep
                # the bare api.anthropic.com root and read its ANTHROPIC_API_KEY.
                chat_base = chat_base or "https://api.anthropic.com"
                api_key = env.get("ANTHROPIC_API_KEY") or env.get("API_KEY") or ""
            elif not is_ollama_backend(base_url, provider):
                api_key = env.get("OPENAI_API_KEY") or env.get("API_KEY") or ""
            eff_model = model
            if is_ollama_backend(base_url, provider):
                eff_model = await _ensure_capped_model(model, _ollama_root_from_base(base_url))
            _MANAGER_PROFILE = pid
            _MGR_BASE_URL = chat_base
            _MGR_MODEL = eff_model
            _MGR_MODEL_DISPLAY = model
            _MGR_API_KEY = api_key
            _MGR_PROVIDER = provider

        if persist:
            try:
                (home / "gui_manager_model.json").write_text(json.dumps({"profile": _MANAGER_PROFILE}))
            except Exception:
                pass
        return {"profile": _MANAGER_PROFILE, "model": _MGR_MODEL_DISPLAY}

    async def _startup_tasks() -> None:
        """Create the GUI's context-capped model variant and pre-warm it.

          1. Derive `<model>:guictx<N>` from the configured default, capping the
             Ollama context so the model loads small/fast (~3 s / 11 GB) instead of
             at its full GGUF context (~56 s / 20 GB). Stored in _EFFECTIVE_MODEL
             and used by every worker, title, and judge call.
          2. Pre-warm via a throwaway worker (see _prewarm_once) so the model is
             resident AND the shared system-prompt/tool prefix is cached before the
             user's first desk — turning a cold first turn into a warm one. Refreshed
             periodically while idle so the prefix doesn't go stale.
        """
        cfg_path = home / "config.yaml"
        cfg_model = ""
        cfg_base_url = ""
        if cfg_path.exists():
            try:
                with open(cfg_path) as f:
                    hermes_cfg = yaml.safe_load(f) or {}
                m = hermes_cfg.get("model", {}) or {}
                cfg_model = m.get("default", "")
                cfg_base_url = m.get("base_url", "")
            except Exception:
                pass
        ollama_url = _ollama_url_from_cfg()

        global _EFFECTIVE_MODEL
        if cfg_model:
            _EFFECTIVE_MODEL = await _resolve_gui_model(cfg_model, cfg_base_url, ollama_url)

        # Persisted manager profile selection (UI gear). Empty = default backend.
        try:
            mm_path = home / "gui_manager_model.json"
            if mm_path.exists():
                prof = (json.loads(mm_path.read_text()).get("profile") or "").strip()
                if prof:
                    await _apply_manager_profile(prof, persist=False)
        except Exception:
            pass

        async def _keepalive_loop() -> None:
            if not _EFFECTIVE_MODEL:
                return
            # Prime immediately, then refresh every ~50 min — but only while idle, so
            # the prewarm never competes with a real desk for Ollama's single slot.
            await _prewarm_once()
            while True:
                await asyncio.sleep(50 * 60)
                if not _running_procs:
                    await _prewarm_once()

        asyncio.create_task(_keepalive_loop())

    async def _shutdown_cleanup() -> None:
        """Terminate all live workers gracefully, then (unless persistence is on)
        reap the desk sandbox containers so they don't linger across GUI runs."""
        procs = list(_running_procs.values()) + list(_persistent_procs.values())
        _running_procs.clear()
        _persistent_procs.clear()
        _live_queues.clear()
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        if procs:
            # Wait for workers to exit, but bounded — stop.sh only grants the
            # server a finite grace period before SIGKILL, and the container
            # reaping below must still fit inside it.
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(p.wait() for p in procs), return_exceptions=True),
                    timeout=4,
                )
            except asyncio.TimeoutError:
                pass
            for p in procs:
                if p.returncode is None:
                    try:
                        p.kill()
                    except Exception:
                        pass
        # With persistence off, don't leave hermes-* containers behind when the
        # GUI stops. Workers are down now, so it's safe to remove them all.
        if not _DOCKER_PERSIST:
            removed = _remove_containers(_list_hermes_containers())
            if removed:
                print(f"[shutdown] reaped {removed} hermes-* sandbox container(s) "
                      f"(set HERMES_GUI_DOCKER_PERSIST=1 to keep them warm).")

    @app.post("/api/sessions/{session_id}/sleep")
    async def sleep_session(session_id: str):
        """Mark a session as sleeping — blocks all automatic resumes."""
        _session_sleeping.add(session_id)
        _save_sleeping()
        return {"ok": True, "session_id": session_id}

    @app.post("/api/sessions/{session_id}/wake")
    async def wake_session(session_id: str):
        """Clear the sleeping flag, allowing the session to be resumed."""
        _session_sleeping.discard(session_id)
        _save_sleeping()
        return {"ok": True, "session_id": session_id}

    @app.post("/api/sessions/{session_id}/resume")
    async def resume_session(session_id: str, body: dict):
        """Resume an existing session with a new message (persistent or one-shot)."""
        content = body.get("content", "")
        if not content:
            raise HTTPException(400, "content is required")
        if session_id in _running_procs:
            raise HTTPException(409, "Session already has a running worker")
        if session_id in _session_sleeping:
            raise HTTPException(423, "Session is sleeping — wake it first")
        # Optional: hand this desk to a different agent profile for the resumed turn.
        agent_override = body.get("agent")
        await _resume_router(session_id, content,
                             body.get("reasoning_effort", ""), body.get("api_mode", ""),
                             attachments=body.get("attachments") or [],
                             agent=agent_override if agent_override is not None else None)
        return {"ok": True, "session_id": session_id}

    @app.post("/api/sessions/{session_id}/inspect")
    async def inspect_session(session_id: str, body: dict):
        """Operator 'inspect' REPL: run one read-only tool in this desk's sandbox.

        Routes the call through the desk's persistent worker so it executes against
        the SAME Docker container, path translation, and read guards the agent
        uses (inspect can't read outside the workspace/team repo any more than the
        agent can). Spawns a warm worker if none exists and keeps it warm for
        follow-up calls. Allowed tools are whitelisted worker-side
        (read_file, search_files, list_files, terminal — no writes).
        """
        tool = (body.get("tool") or "").strip()
        args = body.get("args") or {}
        if not tool:
            raise HTTPException(400, "tool is required")
        if not isinstance(args, dict):
            raise HTTPException(400, "args must be an object")
        ws = _find_workspace(session_id)
        if ws is None:
            raise HTTPException(404, "no sandbox for this desk yet — start it first")

        # Ensure a warm persistent worker (reuses the desk's exact env + container).
        proc = _persistent_procs.get(session_id)
        if proc is None or proc.returncode is not None:
            env, _ws_dir = _session_env(session_id)
            venv_py = str(_HERMES_VENV_PY)
            if not Path(venv_py).exists():
                venv_py = shutil.which("python3") or "python3"
            proc = await asyncio.create_subprocess_exec(
                venv_py, str(_WORKER_SCRIPT), "--persistent", session_id,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=env.get("HERMES_WORKDIR") or str(ws_root),
                env=env,
            )
            _persistent_procs[session_id] = proc
            _session_docker_vols[session_id] = env.get("TERMINAL_DOCKER_VOLUMES", "")
            asyncio.create_task(_persistent_pump(session_id, proc))

        rid = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        _inspect_waiters[rid] = fut
        await _send_cmd(proc, {"cmd": "inspect", "id": rid, "tool": tool, "args": args})
        try:
            # Generous timeout: a freshly spawned worker pays the one-time hermes
            # import before it can answer.
            evt = await asyncio.wait_for(fut, timeout=90)
        except asyncio.TimeoutError:
            _inspect_waiters.pop(rid, None)
            raise HTTPException(504, "inspect timed out") from None
        if not evt.get("ok"):
            return {"ok": False, "tool": tool, "error": evt.get("error", "inspect failed")}
        return {"ok": True, "tool": tool, "result": evt.get("result")}

    @app.post("/api/sessions/{session_id}/inspect/stop")
    async def inspect_stop(session_id: str):
        """Abort any in-flight inspect tool call on this desk (e.g. a runaway
        terminal script). Signals Hermes' per-thread interrupt in the worker,
        which kills the in-container process group; the pending /inspect request
        then returns with the interrupted output."""
        proc = _persistent_procs.get(session_id)
        if proc is None or proc.returncode is not None:
            return {"ok": False, "error": "no running inspect worker for this desk"}
        await _send_cmd(proc, {"cmd": "inspect_stop"})
        return {"ok": True}

    @app.post("/api/sessions/reassign")
    async def reassign_session(body: dict):
        """Atomically interrupt one session and resume another.

        Interrupts `from_id` (if running), waits for the process to exit so
        its Docker cleanup finishes, then spawns a new worker for `to_id`.
        This prevents the race where a SIGTERM cleanup and a new worker startup
        overlap and contend for the same container/workspace.
        """
        from_id  = body.get("from_id", "")
        to_id    = body.get("to_id", "")
        message  = body.get("message", "Continue.")
        reasoning_effort = body.get("reasoning_effort", "")
        api_mode         = body.get("api_mode", "")

        # ── Step 1: interrupt from_id ─────────────────────────────────────────
        if from_id and from_id != to_id:
            q = _live_queues.get(from_id)
            if q:
                await q.put({"type": "interrupted"})
            if _PERSISTENT_WORKERS:
                pp = _persistent_procs.get(from_id)
                if pp is not None:
                    if _running_procs.get(from_id) is not None:
                        _session_turn_interrupted.add(from_id)
                    await _send_cmd(pp, {"cmd": "interrupt"})
                _running_procs.pop(from_id, None)
            else:
                await _interrupt_and_wait(from_id)

        # ── Step 2: resume to_id ──────────────────────────────────────────────
        if to_id in _running_procs:
            return {"ok": True, "session_id": to_id}
        await _resume_router(to_id, message, reasoning_effort, api_mode)
        return {"ok": True, "session_id": to_id}

    async def _interrupt_and_wait(sid: str) -> None:
        """Interrupt a session's running worker and wait for it to exit so its
        Docker cleanup finishes before a replacement worker starts."""
        q = _live_queues.get(sid)
        if q:
            await q.put({"type": "interrupted"})
        proc = _running_procs.pop(sid, None)
        if not proc:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass

    def _maybe_set_reasoning_env(env: dict, reasoning_effort: str) -> None:
        """Set HERMES_REASONING_EFFORT only when the desk model supports thinking."""
        if not reasoning_effort:
            return
        model = (env.get("HERMES_MODEL") or _EFFECTIVE_MODEL or "").strip()
        base_url = ""
        provider = ""
        cfg_home = env.get("HERMES_GUI_CONFIG_HOME", "").strip()
        if cfg_home:
            try:
                pdir = Path(cfg_home)
                prof_model, prof_base = _read_model_info(pdir)
                if prof_model and not env.get("HERMES_MODEL"):
                    model = prof_model
                base_url = (prof_base or "").strip()
                provider = read_profile_provider(pdir)
            except Exception:
                pass
        if not base_url:
            try:
                base_url, _ = hermes_model_config(home)
                cfg_path = home / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path) as f:
                        m = (yaml.safe_load(f) or {}).get("model", {}) or {}
                    provider = (m.get("provider") or "").strip()
            except Exception:
                pass
        if should_apply_reasoning_effort(model, base_url, provider):
            env["HERMES_REASONING_EFFORT"] = reasoning_effort

    def _session_env(sid: str, reasoning_effort: str = "", api_mode: str = "") -> "tuple[dict, str]":
        """Build the worker env for an existing session (workspace, sandbox, model)."""
        ws = _find_workspace(sid)
        workspace_dir = ws if ws else ws_root
        env = {**os.environ, **_worker_repo_env(), "HERMES_WORKDIR": str(workspace_dir),
               # Confine host-side write_file/patch to this session's workspace.
               "HERMES_WRITE_SAFE_ROOT": str(workspace_dir),
               "TERMINAL_CWD": str(workspace_dir),
               "HERMES_GUI_NUM_CTX": _NUM_CTX, "HERMES_GUI_LEAN_TOOLS": _LEAN_TOOLS,
               **_DESK_DOCKER_ENV, **_GPU_ENV}
        _sb = _sandbox_base_of(Path(workspace_dir))
        if _sb:
            env["TERMINAL_SANDBOX_DIR"] = str(_sb)
        if ws:
            agent = _load_agent_marker(ws)
            if agent:
                _apply_agent_profile(env, ws, agent)
                mf = ws / ".hermes_model"
                if mf.is_file():
                    try:
                        m = mf.read_text(encoding="utf-8").strip()
                        if m:
                            env["HERMES_MODEL"] = m
                    except OSError:
                        pass
            else:
                mf = ws / ".hermes_model"
                if mf.exists():
                    try:
                        m = mf.read_text().strip()
                        if m:
                            env["HERMES_MODEL"] = m
                    except Exception:
                        pass
            # Reuse the desk's saved toolset profile so resumes keep the same tools.
            saved_tools: list[str] | None = None
            tf = ws / ".hermes_tools"
            if tf.exists():
                try:
                    raw = tf.read_text().strip()
                    parsed = parse_tools_marker(raw)
                    if parsed is not None:
                        saved_tools = parsed
                    else:
                        legacy = [t.strip() for t in raw.split(",") if t.strip()]
                        saved_tools = ui_names_from_legacy_disabled(legacy)
                except Exception:
                    pass
            _apply_toolset_profile(env, saved_tools)
        # Desks without an agent profile and no saved model use the GUI default.
        if "HERMES_MODEL" not in env and "HERMES_GUI_CONFIG_HOME" not in env and _EFFECTIVE_MODEL:
            env["HERMES_MODEL"] = _EFFECTIVE_MODEL
        # Desk-private HERMES_HOME so this session's conversation + memory can't leak
        # into (or from) other desks via Hermes' shared state.db / memory store.
        if _sb:
            _apply_desk_home(env, _sb, agent_assigned="HERMES_GUI_CONFIG_HOME" in env)
        tid = (_load_desk_team_id(Path(workspace_dir)) if ws else None) or _session_team.get(sid)
        if tid:
            _apply_team_repo_env(env, tid, Path(workspace_dir))
        _maybe_set_reasoning_env(env, reasoning_effort)
        if api_mode:
            env["HERMES_API_MODE"] = api_mode
        if "HERMES_GUI_CONFIG_HOME" not in env:
            _apply_profile_terminal_env(env, home)
        _maybe_force_docker_reset(env, sid)
        return env, str(workspace_dir)

    async def _run_turn(session_id: str, message: str, *, env: dict,
                        resume_id: "str | None", interrupting: bool,
                        images: "list | None" = None) -> None:
        """Run one turn on the desk's persistent worker, spawning it if needed.

        Reuses a warm process so the heavy import/init happens once. For a barge-in,
        stops the in-flight turn and waits for it to end before sending the next."""
        proc = _persistent_procs.get(session_id)
        vols = env.get("TERMINAL_DOCKER_VOLUMES", "")
        desired_reasoning = env.get("HERMES_REASONING_EFFORT", "")
        desired_api = env.get("HERMES_API_MODE", "")
        desired_tools = env.get("HERMES_GUI_ENABLED_TOOLSETS", "__unset__")
        if proc is not None and proc.returncode is None:
            prev_vols = _session_docker_vols.get(session_id)
            prev_opts = _session_worker_opts.get(session_id, ("", "", "__unset__"))
            if prev_vols is not None and vols != prev_vols:
                env["HERMES_GUI_FORCE_DOCKER_RESET"] = "1"
                await _terminate_session_workers(session_id)
                proc = None
            elif prev_opts != (desired_reasoning, desired_api, desired_tools):
                await _terminate_session_workers(session_id)
                proc = None
        if proc is None or proc.returncode is not None:
            py, script = _worker_cmd(env)
            args = [py, script, "--persistent"]
            # Claude's warm worker keeps context in-process; the GUI session id is NOT
            # a valid Claude SDK session id, so never hand it over as --resume.
            if resume_id and env.get("HERMES_GUI_AGENT_KIND") != "claude":
                args.append(resume_id)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=env.get("HERMES_WORKDIR") or str(ws_root),
                env=env,
            )
            _persistent_procs[session_id] = proc
            _session_docker_vols[session_id] = vols
            _session_worker_opts[session_id] = (desired_reasoning, desired_api, desired_tools)
            asyncio.create_task(_persistent_pump(session_id, proc))
        if interrupting and _running_procs.get(session_id) is proc:
            ev = _turn_done_events.get(session_id)
            _session_turn_interrupted.add(session_id)
            await _send_cmd(proc, {"cmd": "interrupt"})
            if ev:
                try:
                    await asyncio.wait_for(ev.wait(), timeout=6.0)
                except asyncio.TimeoutError:
                    pass
        queue: asyncio.Queue = asyncio.Queue()
        _live_queues[session_id] = queue
        _turn_done_events[session_id] = asyncio.Event()
        _running_procs[session_id] = proc
        _record_event_time(session_id, "user_message")  # real turn-start time
        cmd: dict = {"cmd": "run", "message": message}
        if images:
            cmd["images"] = images
        await _send_cmd(proc, cmd)

    async def _spawn_resume_worker(sid: str, content: str,
                                   reasoning_effort: str = "", api_mode: str = "",
                                   images: "list | None" = None) -> None:
        """One-shot resume: spawn a fresh worker that runs a single turn and exits."""
        env, workspace_dir = _session_env(sid, reasoning_effort, api_mode)
        if images:
            env = {**env, "HERMES_GUI_RESUME_IMAGES": json.dumps(images)}
        py, script = _worker_cmd(env)
        proc = await asyncio.create_subprocess_exec(
            py, script, "--resume", sid, content,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace_dir,
            env=env,
        )
        queue: asyncio.Queue = asyncio.Queue()
        _live_queues[sid] = queue
        _running_procs[sid] = proc
        _session_docker_vols[sid] = env.get("TERMINAL_DOCKER_VOLUMES", "")
        _record_event_time(sid, "user_message")  # real turn-start time
        asyncio.create_task(_pump_worker(sid, proc, queue))

    async def _resume_router(sid: str, content: str,
                             reasoning_effort: str = "", api_mode: str = "",
                             attachments: list | None = None,
                             agent: "str | None" = None) -> None:
        """Resume `sid` via the persistent worker when enabled, else one-shot.

        When `agent` is given (and differs from the desk's current profile), the
        desk is handed to that agent: the .hermes_profile marker is rewritten and
        any warm worker is dropped so the new profile's model/config takes effect.
        """
        if agent is not None:
            ws_agent = _find_workspace(sid)
            if ws_agent:
                prev = _load_agent_marker(ws_agent) or ""
                if (agent or "") != prev:
                    _set_session_agent(ws_agent, agent)
                    # Worker env (model/base_url/config home) is fixed at spawn, so a
                    # warm/persistent worker must be dropped for the switch to apply.
                    await _terminate_session_workers(sid)
        # Note: a profile may run on several desks at once. Each desk is fully
        # isolated — its own private HERMES_HOME (state.db, memories, sessions) and a
        # per-desk Docker container keyed to its unique session id — so concurrent
        # desks on the same profile share no storage/cache. No one-desk lock here.
        # The agent is about to do more work → any prior "task_solved" is now stale.
        ws_solved = _find_workspace(sid)
        was_solved = bool(ws_solved and (ws_solved / _SOLVED_MARKER).exists())
        if ws_solved:
            _mark_solved(ws_solved, False)
        # Team-manager messages carry a "👩‍💼 [Team manager]" marker that the feed
        # keys off to render a distinct manager bubble. They must NOT get the workspace
        # header prepended — that both pushes the marker off the front (so the feed
        # falls back to a plain user bubble) and clutters the message. They also never
        # carry attachments or need vision-path reminders.
        is_manager = content.lstrip().startswith("👩")
        # If the desk's task was already SOLVED, a new user request is a new goal.
        # Fold it into TASK.md and drop the cached audit so the agent and the manager
        # re-engage against the updated goal instead of idling on the done task.
        # Skip system/auto resumes ("Continue.", the TASK.md-saved nudge) and manager msgs.
        _SYS_RESUME = ("Continue.", "TASK.md has been updated")
        if (was_solved and ws_solved and not is_manager and content.strip()
                and not content.strip().startswith(_SYS_RESUME)):
            _append_task_request(ws_solved, content)
            _session_audits.pop(sid, None)
            _session_audit_best.pop(sid, None)
        # Save any newly-attached images to workspace.
        ws = _find_workspace(sid)
        if ws:
            HermesDB.ensure_workspace_key(ws)
        # Keep team repo files fresh on every resume (uploads may have landed while
        # idle, or the desk may have been re-registered after a server restart).
        if ws:
            tid = _load_desk_team_id(ws) or _session_team.get(sid)
            if tid:
                _register_session_team(sid, tid, ws)
                _prepare_team_files_mount(tid, ws)
        is_claude_desk = _is_claude_agent(_load_agent_marker(ws) or "") if ws else False
        if ws and attachments:
            _, attach_note = _save_attachments(attachments, ws, is_claude=is_claude_desk)
            if attach_note:
                content = attach_note.strip() + "\n\n" + content
        # Re-inject workspace paths + image reminders so context compression can't
        # lose the file references the agent needs (vision_analyze for Hermes, the
        # Read tool for Claude).
        if ws and not is_manager:
            _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
            try:
                images = [p for p in ws.iterdir()
                          if p.is_file() and p.suffix.lower() in _IMAGE_EXTS]
            except Exception:
                images = []
            header = _workspace_path_note(ws, is_claude=is_claude_desk).rstrip("]")
            if images:
                header += ("\n  - Attached images in your workspace (open with the Read tool):"
                           if is_claude_desk else
                           "\n  - vision_analyze on workspace images (host path):")
                for img in images[:10]:
                    header += f"\n    - {img}"
            header += "\n]"
            content = header + "\n\n" + content
        # Record this run in the desk's history log — every resume is its own
        # entry (profile + model as of this run), even if unchanged.
        _log_desk_run(ws, sid, "resume", _load_agent_marker(ws) or "")
        # Pass raw image data to the worker so it can embed them as vision content
        # blocks (the model sees pixels, not just a path hint).
        img_payloads = [{"name": a.get("name", ""), "data": a.get("data", "")}
                        for a in (attachments or []) if a.get("data")]
        if _PERSISTENT_WORKERS or _desk_is_claude(sid):
            env, _ = _session_env(sid, reasoning_effort, api_mode)
            await _run_turn(sid, content, env=env, resume_id=sid, interrupting=False,
                            images=img_payloads or None)
        else:
            await _spawn_resume_worker(sid, content, reasoning_effort, api_mode,
                                       images=img_payloads or None)

    # Expose the resume path to the module-level heartbeat (auto-continue).
    global _spawn_resume_ref
    _spawn_resume_ref = _resume_router

    @app.post("/api/sessions/{session_id}/autocontinue")
    async def set_autocontinue(session_id: str, body: dict):
        """Enable/disable heartbeat auto-continue for a desk. When enabled, the
        server checks TASK.md when a turn ends and auto-resumes until the goal is
        judged complete (capped at _AUTO_CONTINUE_MAX resumes)."""
        enabled = bool(body.get("enabled"))
        _session_autocontinue[session_id] = enabled
        if enabled:
            _session_continue_count[session_id] = 0       # fresh budget
            _session_user_stopped.discard(session_id)
        return {"ok": True, "enabled": enabled, "max": _AUTO_CONTINUE_MAX}

    @app.post("/api/sessions/{session_id}/redirect")
    async def redirect_session(session_id: str, body: dict):
        """Barge-in: interrupt this session's in-flight turn and immediately resume
        it with a new message. Mirrors reassign but targets the same session, so
        the user can redirect an agent without waiting for the current turn to end."""
        content = body.get("content", "")
        if not content:
            raise HTTPException(400, "content is required")
        reasoning_effort = body.get("reasoning_effort", "")
        api_mode         = body.get("api_mode", "")
        attachments      = body.get("attachments") or []
        # Save any attached images to workspace before interrupting.
        ws = _find_workspace(session_id)
        if ws and attachments:
            _, attach_note = _save_attachments(
                attachments, ws,
                is_claude=_is_claude_agent(_load_agent_marker(ws) or ""))
            if attach_note:
                content = attach_note.strip() + "\n\n" + content
        img_payloads = [{"name": a.get("name", ""), "data": a.get("data", "")}
                        for a in attachments if a.get("data")]
        if _PERSISTENT_WORKERS or _desk_is_claude(session_id):
            env, _ = _session_env(session_id, reasoning_effort, api_mode)
            await _run_turn(session_id, content, env=env, resume_id=session_id, interrupting=True,
                            images=img_payloads or None)
        else:
            await _interrupt_and_wait(session_id)
            await _spawn_resume_worker(session_id, content, reasoning_effort, api_mode,
                                       images=img_payloads or None)
        return {"ok": True, "session_id": session_id}

    @app.get("/api/sessions/{session_id}/taskfile")
    def get_taskfile(session_id: str):
        ws = _find_workspace(session_id)
        if not ws:
            raise HTTPException(404, "Workspace not found for session")
        task_md = ws / "TASK.md"
        content = task_md.read_text(encoding="utf-8", errors="replace") if task_md.exists() else ""
        return {"content": content, "path": str(task_md), "workspace": str(ws)}

    @app.put("/api/sessions/{session_id}/taskfile")
    def put_taskfile(session_id: str, body: dict):
        ws = _find_workspace(session_id)
        if not ws:
            raise HTTPException(404, "Workspace not found for session")
        task_md = ws / "TASK.md"
        task_md.write_text(body.get("content", ""), encoding="utf-8")
        return {"ok": True}

    def _desk_history_payload(session_id: str) -> dict:
        """Desk run history, oldest first. Each entry is one run (start or resume)
        with the profile + model it used. Primary source is the run-history log
        (records every run, even when resumes reuse the session id). Falls back to
        the desk db's session rows for older desks that predate the log."""
        ws = _find_workspace(session_id)
        current_profile = (_load_agent_marker(ws) if ws else None) or ""
        runs = _load_run_history(ws) if ws else []
        if runs:
            sessions = []
            for i, e in enumerate(runs):
                at = e.get("at")
                try:
                    started = (datetime.fromtimestamp(float(at), tz=timezone.utc).isoformat()
                               if at is not None else "")
                except (TypeError, ValueError, OSError):
                    started = ""
                is_root = e.get("kind") == "start" or i == 0
                sessions.append({
                    "id": e.get("session_id", session_id),
                    "started_at": started,
                    "ended_at": None,
                    "model": e.get("model", ""),
                    "profile": e.get("profile", ""),
                    "parent_session_id": None if is_root else session_id,
                    "message_count": 0,
                    "is_root": is_root,
                    "kind": e.get("kind", "resume"),
                })
            return {"desk_id": session_id, "profile": current_profile, "sessions": sessions}

        # Fallback for pre-run-log desks: reconstruct from the db session rows,
        # mapping each to the profile in effect from the profile-change log.
        entries = db.get_desk_session_history(session_id)
        plog = _load_profile_history(ws) if ws else []
        return {
            "desk_id": session_id,
            "profile": current_profile,
            "sessions": [
                {
                    "id": e.id,
                    "started_at": e.started_at,
                    "ended_at": e.ended_at,
                    "model": e.model,
                    "profile": _profile_at(plog, e.started_at, current_profile),
                    "parent_session_id": e.parent_session_id,
                    "message_count": e.message_count,
                    "is_root": e.is_root,
                    "kind": "start" if e.is_root else "resume",
                }
                for e in entries
            ],
        }

    @app.get("/api/sessions/{session_id}/history")
    def desk_history(session_id: str):
        """Desk history log: every session id this desk ran (root + each
        resume/model-switch), with start time and model — the lineage stored in
        the desk's private state.db."""
        return _desk_history_payload(session_id)

    @app.get("/api/sessions/{session_id}/export")
    def export_desk(session_id: str):
        """Export a desk to a single JSON document: its config, TASK.md goal, and
        full session-lineage history. Saved client-side as a downloadable file."""
        ws = _find_workspace(session_id)
        history = _desk_history_payload(session_id)
        task = ""
        tools: list[str] | None = None
        model = ""
        if ws:
            task_md = ws / "TASK.md"
            if task_md.exists():
                task = task_md.read_text(encoding="utf-8", errors="replace")
            tools_marker = ws / ".hermes_tools"
            if tools_marker.exists():
                raw = tools_marker.read_text(encoding="utf-8", errors="replace").strip()
                tools = [t for t in raw.split(",") if t] if raw else []
            model_marker = ws / ".hermes_model"
            if model_marker.exists():
                model = model_marker.read_text(encoding="utf-8", errors="replace").strip()
        sess = db.get_session(session_id)
        return {
            "format": "agent-gui-desk/v1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "desk_id": session_id,
            "title": sess.title if sess else "",
            "profile": history["profile"],
            "model": model,
            "tools": tools,
            "task": task,
            "sessions": history["sessions"],
        }

    _DESK_ARCHIVE_FORMAT = "agent-gui-desk-archive/v1"
    _MAX_DESK_ARCHIVE_BYTES = 1024 * 1024 * 1024  # 1 GB cap on import

    @app.get("/api/sessions/{session_id}/archive")
    def archive_desk(session_id: str):
        """Save EVERYTHING about one desk to a downloadable .tar.gz: the entire
        sandbox (private state.db = session history + model calls, the workspace
        snapshot, run/profile history, and all markers), plus a manifest. Like a
        Snapshot, but for a single desk — restore it later via /import.

        The desk's team files live at ``workspace/team_files`` — normally a
        symlink into the shared ``gui_team_repos/<team_id>/`` store, which tar
        would save as a (soon-dangling) symlink, losing the files. So we follow
        the symlink and bundle its real files at ``workspace/team_files/``, so a
        loaded desk keeps the team files in its own workspace and sees them on
        the next resume."""
        base, ws = _desk_paths(session_id)
        if not base.exists():
            raise HTTPException(404, "Desk sandbox not found")
        sess = db.get_session(session_id)
        manifest = {
            "format": _DESK_ARCHIVE_FORMAT,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "desk_id": session_id,
            "title": sess.title if sess else "",
            "profile": (_load_agent_marker(ws) or "") if ws.exists() else "",
            "team_id": (_load_desk_team_id(ws) if ws.exists() else None),
        }
        # team_files is a symlink to the shared repo (or, for an already-loaded
        # archive, a plain dir). Drop the whole subtree from the recursive add and
        # re-add its dereferenced contents, so the archive holds the real files —
        # exactly once — at workspace/team_files/.
        team_files_arc = "sandbox/" + os.path.relpath(
            ws / _TEAM_FILES_SUBDIR, base,
        ).replace(os.sep, "/")

        def _drop_team_files(ti: tarfile.TarInfo):
            n = ti.name
            return None if n == team_files_arc or n.startswith(team_files_arc + "/") else ti

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = json.dumps(manifest, indent=2).encode("utf-8")
            info = tarfile.TarInfo("desk_manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            # Whole sandbox under "sandbox/" (recursive), minus the team_files
            # subtree (re-added below as the symlink's real, dereferenced files).
            tar.add(str(base), arcname="sandbox", recursive=True, filter=_drop_team_files)
            team_files = ws / _TEAM_FILES_SUBDIR
            if team_files.exists():  # follows the symlink; False if dangling
                target = team_files.resolve()
                if target.is_dir() and any(target.iterdir()):
                    tar.add(str(target), arcname=team_files_arc, recursive=True)
        buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="desk-{session_id}.tar.gz"'},
        )

    def _safe_extract_desk_archive(tar: tarfile.TarFile, base: Path) -> None:
        """Extract the archive's sandbox/ tree into `base`, guarding against path
        traversal and skipping symlinks/special members."""
        base.mkdir(parents=True, exist_ok=True)
        base_resolved = base.resolve()
        for member in tar.getmembers():
            name = member.name
            if not name.startswith("sandbox/"):
                continue
            rel = name[len("sandbox/"):].lstrip("/")
            if not rel:
                continue
            target = (base / rel).resolve()
            if base_resolved != target and base_resolved not in target.parents:
                continue  # path escapes the desk dir — skip
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    continue
                with src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            # symlinks / devices / fifos are intentionally skipped

    def _finish_desk_import(desk_id: str, manifest: dict) -> dict:
        _session_workspaces.pop(desk_id, None)
        team_id = manifest.get("team_id")
        _base, ws = _desk_paths(desk_id)
        if team_id and ws.exists():
            _register_session_team(desk_id, team_id, ws)
        return {
            "ok": True,
            "session_id": desk_id,
            "workspace_path": str(ws) if ws.exists() else None,
            "team_id": team_id,
        }

    def _import_desk_tar(tar: tarfile.TarFile) -> dict:
        mf = tar.extractfile("desk_manifest.json") if "desk_manifest.json" in tar.getnames() else None
        if mf is None:
            raise HTTPException(400, "Desk archive is missing its manifest")
        try:
            manifest = json.loads(mf.read().decode("utf-8"))
        except Exception:
            raise HTTPException(400, "Desk archive manifest is unreadable")
        desk_id = str(manifest.get("desk_id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", desk_id):
            raise HTTPException(400, "Desk archive has an invalid desk id")
        base, ws = _desk_paths(desk_id)
        if base.exists():
            raise HTTPException(409, f"Desk {desk_id} is already loaded — delete it first")
        # An archive saved with team files carries them as a real
        # sandbox/.../workspace/team_files/ tree (the symlink was dereferenced on
        # save). Those bytes are the desk's own copy now: extracting them leaves a
        # plain team_files/ folder the resumed desk reads directly.
        team_files_arc = "sandbox/" + os.path.relpath(
            ws / _TEAM_FILES_SUBDIR, base,
        ).replace(os.sep, "/")
        bundled_team_files = any(
            m.name == team_files_arc or m.name.startswith(team_files_arc + "/")
            for m in tar.getmembers()
        )
        _safe_extract_desk_archive(tar, base)
        if bundled_team_files:
            # Detach from the shared team repo so the bundled files (a real
            # workspace/team_files/ dir) aren't shadowed by the team's Docker
            # bind-mount of gui_team_repos/<team_id>/ at /workspace/team_files.
            manifest["team_id"] = None
            try:
                (ws / _DESK_TEAM_MARKER).unlink()
            except OSError:
                pass
        return _finish_desk_import(desk_id, manifest)

    def _saved_desks_dir() -> Path:
        return _REPO_ROOT / "saved"

    def _is_desk_archive_name(name: str) -> bool:
        n = name.lower()
        return n.endswith(".tar.gz") or n.endswith(".tgz")

    def _resolve_saved_archive(filename: str) -> Path:
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            raise HTTPException(400, "Invalid archive filename")
        saved = _saved_desks_dir().resolve()
        path = (saved / filename).resolve()
        if saved != path and saved not in path.parents:
            raise HTTPException(400, "Invalid archive path")
        if not path.is_file():
            raise HTTPException(404, f"Archive not found in saved/: {filename}")
        if not _is_desk_archive_name(filename):
            raise HTTPException(400, "Not a desk archive (.tar.gz)")
        return path

    @app.post("/api/sessions/import")
    async def import_desk(file: UploadFile = File(...)):
        """Load a desk saved by /archive: unpack its sandbox under the original
        desk id and re-register it. The desk's session ids reference its dir, so
        it's restored under its original id; if one already exists, refuse (delete
        it first) rather than silently overwrite. Any bundled team files come back
        as a plain workspace/team_files/ folder owned by this desk (it loads
        detached from the original shared team repo)."""
        raw = await file.read()
        if len(raw) > _MAX_DESK_ARCHIVE_BYTES:
            raise HTTPException(413, "Desk archive too large")
        try:
            tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
        except tarfile.TarError:
            raise HTTPException(400, "Not a valid desk archive (.tar.gz)")
        with tar:
            return _import_desk_tar(tar)

    @app.post("/api/sessions/import-saved")
    def import_saved_desk(body: dict):
        """Import a desk archive from ``saved/<filename>`` in the repo."""
        filename = str(body.get("filename", "")).strip()
        path = _resolve_saved_archive(filename)
        if path.stat().st_size > _MAX_DESK_ARCHIVE_BYTES:
            raise HTTPException(413, "Desk archive too large")
        try:
            tar = tarfile.open(path, mode="r:gz")
        except tarfile.TarError:
            raise HTTPException(400, "Not a valid desk archive (.tar.gz)")
        with tar:
            return _import_desk_tar(tar)

    @app.post("/api/sessions/{session_id}/audit")
    async def audit_session(session_id: str, force: bool = False):
        """Run an orchestrated, evidence-based manager audit of the session.

        Returns the cached audit (no LLM) when the state is unchanged, unless
        `force=true`.
        """
        ws = _find_workspace(session_id)
        if not ws:
            raise HTTPException(404, "Workspace not found for session")
        audit = await _run_audit(session_id, ws, force=force)
        if audit is None:
            raise HTTPException(422, "Nothing to audit (no TASK.md or model unreachable)")
        return audit

    @app.get("/api/sessions/{session_id}/audit")
    def get_audit(session_id: str):
        """Return the last cached audit for this session (or 404 if none yet)."""
        audit = _session_audits.get(session_id)
        if audit is None:
            raise HTTPException(404, "No audit yet for session")
        return audit

    @app.get("/api/sessions/{session_id}/progress")
    def get_progress(session_id: str):
        """Return the current PROGRESS.md (empty content if not generated yet)."""
        ws = _find_workspace(session_id)
        if not ws:
            raise HTTPException(404, "Workspace not found for session")
        p = ws / "PROGRESS.md"
        content = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        return {"content": content, "exists": p.exists()}

    @app.post("/api/sessions/{session_id}/progress")
    async def make_progress(session_id: str):
        """(Re)generate the agent's PROGRESS.md from its work so far."""
        ws = _find_workspace(session_id)
        if not ws:
            raise HTTPException(404, "Workspace not found for session")
        content = await _generate_progress(session_id, ws)
        if content is None:
            raise HTTPException(422, "Nothing to report yet (no task/work or model unreachable)")
        return {"content": content, "exists": True}

    @app.get("/api/sessions/{session_id}/audit/status")
    def audit_status(session_id: str):
        """Cheap (no LLM) check of whether the current state already has an audit.

        Lets the team manager skip re-auditing a desk whose work hasn't changed
        since the last audit (Makefile semantics).
        """
        ws = _find_workspace(session_id)
        if not ws:
            raise HTTPException(404, "Workspace not found for session")
        state_hash, goal, _, _ = _audit_state(session_id, ws)
        cached = _session_audits.get(session_id)
        # Require the AUDIT.md record to still exist on disk — if it was lost (server
        # restart, deletion, agent overwrite), re-audit so there's always a record
        # backing an "already audited / all passed" claim.
        audited = (
            bool(goal)
            and cached is not None
            and cached.get("state_hash") == state_hash
            and (ws / "AUDIT.md").exists()
        )
        return {
            "current_hash": state_hash,
            "auditable": bool(goal),
            "audited": audited,
            "summary": cached.get("summary") if audited else None,
        }

    @app.post("/api/workspace/open")
    async def open_workspace(body: dict):
        path = body.get("path", "").strip()
        if not path:
            raise HTTPException(400, "path required")
        p = Path(path)
        if not p.exists():
            raise HTTPException(404, "Path not found")
        p = _safe_path(path)
        import sys
        if sys.platform == "darwin":
            cmd = ["open", str(p)]
        elif sys.platform == "win32":
            cmd = ["explorer", str(p)]
        else:
            cmd = ["xdg-open", str(p)]
        subprocess.Popen(cmd)
        return {"ok": True}

    @app.post("/api/workspace/open-terminal")
    async def open_terminal(body: dict):
        """Open the default terminal app at `path` (or its parent if a file)."""
        path = body.get("path", "").strip()
        if not path:
            raise HTTPException(400, "path required")
        p = Path(path)
        if not p.exists():
            raise HTTPException(404, "Path not found")
        p = _safe_path(path)
        d = p if p.is_dir() else p.parent
        import sys
        if sys.platform == "darwin":
            cmd = ["open", "-a", "Terminal", str(d)]
        elif sys.platform == "win32":
            cmd = ["cmd", "/c", "start", "cmd", "/k", f"cd /d {d}"]
        else:
            # Best-effort across common Linux terminal emulators.
            term = next((t for t in ("x-terminal-emulator", "gnome-terminal",
                                     "konsole", "xterm") if shutil.which(t)), None)
            if not term:
                raise HTTPException(503, "no terminal emulator found")
            cmd = [term, "--working-directory", str(d)] if term == "gnome-terminal" else [term]
        subprocess.Popen(cmd, cwd=str(d))
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/files")
    def get_files(session_id: str):
        messages = db.get_messages(session_id, limit=2000)
        touched = extract_touched_files(messages)
        tree = build_file_tree(touched)
        return [asdict(n) for n in tree]

    @app.get("/api/sessions/{session_id}/workspace_tree")
    def get_workspace_tree(session_id: str):
        """Real directory tree of the session's workspace — dirs, subdirs, and all
        files created/visible this session (dotfiles skipped). Returns [] when the
        workspace can't be resolved (e.g. older CLI sessions), so the client can
        fall back to the tool-call-derived file list."""
        ws = _find_workspace(session_id)
        if not ws or not ws.exists():
            return []
        tree = _walk_dir(ws)
        tid = _load_desk_team_id(ws) or _session_team.get(session_id)
        if tid:
            tree = _inject_team_repo_into_tree(tree, ws, tid)
        return tree

    # ── Team file repo endpoints ──────────────────────────────────────────────

    @app.get("/api/teams/{team_id}/files")
    def list_team_files(team_id: str):
        """Tree of a team's shared File Repo (empty list if the repo has no files)."""
        repo = _team_repo_dir(team_id)
        if not repo.is_dir():
            return {"files": [], "root": str(repo)}
        return {"files": _walk_dir(repo), "root": str(repo)}

    @app.post("/api/teams/{team_id}/files")
    async def upload_team_file(team_id: str, body: dict):
        """Add one file to a team's File Repo (a copy — the user's original is untouched).

        Body: {path: <relative path incl. subdirs>, data: <base64 or data-URL>}.
        Directories are uploaded as their individual files (the client walks them).
        After writing, the file is synced into every live desk of the team.
        """
        repo = _team_repo_dir(team_id)
        rel = body.get("path", "")
        data = body.get("data", "")
        if not rel or not data:
            raise HTTPException(400, "path and data are required")
        target = _safe_team_relpath(repo, rel)
        MAX_BYTES = 100 * 1024 * 1024  # 100 MB/file guard
        try:
            raw_b64 = data.split(",", 1)[1] if "," in data and data.startswith("data:") else data
            blob = base64.b64decode(raw_b64)
        except Exception:
            raise HTTPException(400, "data must be base64") from None
        if len(blob) > MAX_BYTES:
            raise HTTPException(413, "file too large (limit 100 MB)")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        _write_team_files_readme(repo)
        synced = _sync_team_repo_to_live_desks(team_id)
        restarted = await _restart_team_desk_workers(team_id)
        return {"ok": True, "path": str(target), "synced_desks": synced, "restarted_workers": restarted}

    @app.delete("/api/teams/{team_id}/files")
    async def delete_team_file(team_id: str, path: str):
        """Remove a file or directory from a team's File Repo (by repo-relative path)."""
        repo = _team_repo_dir(team_id)
        if not repo.is_dir():
            raise HTTPException(404, "team repo not found")
        target = _safe_team_relpath(repo, path)
        if not target.exists():
            raise HTTPException(404, "not found")
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            try:
                target.unlink()
            except OSError:
                raise HTTPException(500, "delete failed") from None
        _write_team_files_readme(repo)
        synced = _sync_team_repo_to_live_desks(team_id)
        restarted = await _restart_team_desk_workers(team_id)
        return {"ok": True, "synced_desks": synced, "restarted_workers": restarted}

    @app.post("/api/teams/{team_id}/sync")
    async def sync_team_files(team_id: str):
        """Re-link team_files/ symlinks on every live desk of that team."""
        _safe_team_id(team_id)
        synced = _sync_team_repo_to_live_desks(team_id)
        restarted = await _restart_team_desk_workers(team_id)
        return {"ok": True, "synced_desks": synced, "restarted_workers": restarted}

    @app.post("/api/teams/{team_id}/register")
    def register_team_desks(team_id: str, body: dict):
        """Associate desk sessions with a team and sync the File Repo into them.

        Called by the frontend on workbench restore so desks created before a
        server restart (or before files were uploaded) still receive team_files/.
        """
        tid = _safe_team_id(team_id)
        raw = body.get("session_ids") or []
        if not isinstance(raw, list):
            raise HTTPException(400, "session_ids must be a list")
        registered = 0
        for raw_sid in raw:
            sid = str(raw_sid).strip()
            if not sid:
                continue
            ws = _find_workspace(sid)
            if not ws:
                continue
            _register_session_team(sid, tid, ws)
            _prepare_team_files_mount(tid, ws)
            registered += 1
        return {"ok": True, "registered": registered}

    def _hermes_bin() -> str:
        p = shutil.which("hermes")
        if not p:
            raise HTTPException(503, "hermes binary not found in PATH")
        return p

    def _extract_response(text: str) -> str:
        """Strip Hermes CLI metadata header and return just the agent response."""
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.strip().startswith("session_id:"):
                return "".join(lines[i + 1:]).lstrip("\n")
        return text.strip()

    @app.post("/api/sessions/new")
    async def new_session(body: dict):
        content = body.get("content", "")
        if not content:
            raise HTTPException(400, "content is required")
        reasoning_effort = body.get("reasoning_effort", "")
        api_mode         = body.get("api_mode", "")
        agent_id         = (body.get("agent") or body.get("profile") or "").strip().lower()
        if not agent_id and _DEFAULT_AGENT:
            agent_id = _DEFAULT_AGENT   # only when AGENT_GUI_DEFAULT_AGENT is set
        if _is_claude_agent(agent_id) and not experimental:
            # The Claude Code SDK agent is gated behind --experimental; refuse to
            # spawn one otherwise (defense in depth — the roster already hides it).
            raise HTTPException(
                403,
                "The Claude Code SDK agent is an experimental feature. Restart the "
                "server with --experimental (e.g. ./start.sh --experimental) to enable it.",
            )
        team_id          = (body.get("team_id") or "").strip()
        attachments      = body.get("attachments", [])
        # Optional per-desk toolset profile: `tools` is the ENABLED toolset list
        # (from a UI preset or custom profile). None = fall back to the server-wide
        # lean/full default. An explicit [] is valid ("chat" — zero tools).
        tools_enabled    = body.get("tools")
        if tools_enabled is not None and not isinstance(tools_enabled, list):
            raise HTTPException(400, "tools must be a list of toolset names")
        if tools_enabled is None and agent_id and not _is_claude_agent(agent_id):
            tools_enabled = _profile_default_tools_enabled(cfg.agent_profiles_dir, home, agent_id)

        raw_model = (body.get("model") or "").strip()
        if _is_claude_agent(agent_id):
            # Claude model alias ("opus"/"sonnet"/"haiku") or full id only; drop a
            # non-Claude model (e.g. the GUI's Ollama default) so it never reaches the SDK.
            model = _claude_model(raw_model)
        elif agent_id:
            model = await _resolve_desk_model(agent_id, raw_model)
        elif raw_model:
            model = await _resolve_desk_model("", raw_model)
        else:
            model = _EFFECTIVE_MODEL or ""

        # Pre-generate session_id (same format hermes uses) so we can return it to
        # the client immediately AND give this desk its own isolated sandbox dir.
        now = datetime.now(timezone.utc)
        session_id = f"{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        sandbox_base, workspace_dir = _desk_paths(session_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        HermesDB.ensure_workspace_key(workspace_dir)

        # Save any attached images to the workspace so the agent can use them
        MAX_ATTACH_BYTES = 25 * 1024 * 1024  # 25 MB/image — guards against memory-pressure DoS
        saved_images: list[str] = []
        for att in attachments:
            # Basename only: strips directory components (POSIX "/" and Windows "\")
            # and ".." parents so an attachment name can't escape the workspace dir.
            name = Path(att.get("name", "").replace("\\", "/")).name
            # Normalize non-standard whitespace (e.g. U+00A0 non-breaking space
            # in macOS screenshot filenames) so vision_analyze path validation passes.
            name = "".join(" " if (ord(c) > 127 and c.isspace()) or c == " " else c for c in name)
            data = att.get("data", "")
            if not name or name in (".", "..") or not data:
                continue
            try:
                raw_b64 = data.split(",", 1)[1] if "," in data else data
                img_bytes = base64.b64decode(raw_b64)
                if len(img_bytes) > MAX_ATTACH_BYTES:
                    continue
                dest = workspace_dir / name
                dest.write_bytes(img_bytes)
                saved_images.append(str(dest))
            except Exception:
                pass

        # Write TASK.md immediately so the agent can see it from the start
        task_md = workspace_dir / "TASK.md"
        task_md.write_text(f"# Task\n\n{content}\n")

        # Seed the desk's profile-history log with its initial profile ("" =
        # Default) so the root session row maps to the right profile even if it's
        # later switched.
        _append_profile_history(workspace_dir, agent_id)
        _log_desk_run(workspace_dir, session_id, "start", agent_id)

        # Team file repo: symlink the team's shared files into this desk's workspace.
        if team_id:
            _register_session_team(session_id, team_id, workspace_dir)
            _prepare_team_files_mount(team_id, workspace_dir)

        is_claude_desk = _is_claude_agent(agent_id)
        image_note = ""
        if saved_images:
            image_note = (
                "\nAttached images saved to your workspace — open them with the Read tool:\n"
                if is_claude_desk else
                "\nAttached images saved to workspace "
                f"(vision_analyze host path: {workspace_dir}/<filename>):\n"
            )
            for p in saved_images:
                image_note += f"  - {p}\n"
        path_header = _workspace_path_note(
            workspace_dir, include_task_hint=True, is_claude=is_claude_desk).rstrip("]")
        augmented_content = (
            f"{path_header}{image_note}]\n\n{content}"
        )

        # Pass the pre-assigned session_id and per-session overrides to the worker.
        # TERMINAL_SANDBOX_DIR isolates this desk's Docker /workspace from others.
        env = {**os.environ, **_worker_repo_env(), "HERMES_WORKDIR": str(workspace_dir),
               # Confine host-side write_file/patch to this session's workspace.
               "HERMES_WRITE_SAFE_ROOT": str(workspace_dir),
               "HERMES_SESSION_ID": session_id,
               "TERMINAL_SANDBOX_DIR": str(sandbox_base),
               "TERMINAL_CWD": str(workspace_dir),
               "HERMES_GUI_NUM_CTX": _NUM_CTX,
               "HERMES_GUI_LEAN_TOOLS": _LEAN_TOOLS,
               **_DESK_DOCKER_ENV, **_GPU_ENV}
        if api_mode:
            env["HERMES_API_MODE"] = api_mode
        _apply_toolset_profile(env, tools_enabled if isinstance(tools_enabled, list) else None)
        if agent_id:
            _apply_agent_profile(env, workspace_dir, agent_id)
        if _is_claude_agent(agent_id):
            # Claude desk: persist the chosen model alias for resume and set it for
            # this first turn. Skips the Hermes profile-model pin logic below.
            try:
                if model:
                    (workspace_dir / ".claude_model").write_text(model, encoding="utf-8")
                    env["CLAUDE_AGENT_MODEL"] = model
            except OSError:
                pass
        elif model:
            env["HERMES_MODEL"] = model
            # Pin .hermes_model ONLY when the user explicitly chose a model different
            # from the profile's configured default. A plain default echo is NOT
            # pinned, so the desk tracks its profile config live (model + base_url
            # together) and editing the backend can't desync them (vLLM↔Ollama → 404).
            try:
                if agent_id:
                    prof_model, _ = _read_model_info(
                        resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id))
                else:
                    _, prof_model = hermes_model_config(home)
            except Exception:
                prof_model = ""
            if raw_model and raw_model.strip() != (prof_model or "").strip():
                try:
                    (workspace_dir / ".hermes_model").write_text(model, encoding="utf-8")
                except OSError:
                    pass
        # Give this desk a PRIVATE HERMES_HOME (its sandbox) so its conversation and
        # Hermes memory store are isolated — otherwise every desk shares ~/.hermes and
        # one desk's task (e.g. "train a CNN on MNIST") leaks into another's via memory.
        _apply_desk_home(env, sandbox_base, agent_assigned=bool(agent_id))
        if team_id:
            _apply_team_repo_env(env, team_id, workspace_dir)
        elif not agent_id:
            _apply_profile_terminal_env(env, home)
        _maybe_set_reasoning_env(env, reasoning_effort)
        _maybe_force_docker_reset(env, session_id)

        # Register the workspace + markers before spawning (so _find_workspace works).
        _session_workspaces[session_id] = str(workspace_dir)
        try:
            (workspace_dir / ".hermes_session_id").write_text(session_id)
        except Exception:
            pass
        # Persist enabled UI toolset names so resumes keep the same tools.
        try:
            if isinstance(tools_enabled, list):
                _write_desk_tools_marker(workspace_dir, tools_enabled)
            elif _LEAN_TOOLS == "1":
                _write_desk_tools_marker(workspace_dir, lean_enabled_names())
        except Exception:
            pass

        if _PERSISTENT_WORKERS or _is_claude_agent(agent_id):
            # Spawn the desk's long-lived worker and run the first turn on it. Claude
            # desks ALWAYS use this path: the warm ClaudeSDKClient retains conversation
            # context across turns in-process (no DB-backed resume needed for the MVP).
            await _run_turn(session_id, augmented_content, env=env, resume_id=None, interrupting=False)
        else:
            py, script = _worker_cmd(env)
            proc = await asyncio.create_subprocess_exec(
                py, script, augmented_content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_dir),
                env=env,
            )
            queue: asyncio.Queue = asyncio.Queue()
            _live_queues[session_id] = queue
            _running_procs[session_id] = proc
            _session_docker_vols[session_id] = env.get("TERMINAL_DOCKER_VOLUMES", "")
            _record_event_time(session_id, "user_message")  # real turn-start time
            asyncio.create_task(_pump_worker(session_id, proc, queue))

        # Return a provisional session object so the frontend doesn't need a
        # second GET /sessions/{id} round-trip (session may not be in DB yet).
        provisional = {
            "id": session_id,
            "started_at": now.isoformat(),
            "ended_at": None,
            "source": "workbench",
            "model": model or None,
            "parent_session_id": None,
            "title": (content[:80].replace("\n", " ").strip() or "Untitled task"),
            "message_count": 0,
            "token_estimate": 0,
            "is_running": True,
            "title_summary": None,
        }
        if agent_id:
            provisional.update(_agent_runtime_info(agent_id, workspace_dir))
        return {"session_id": session_id, "workspace_path": str(workspace_dir),
                "response": "", "session": provisional, "agent": agent_id or None}

    @app.post("/api/docker/cleanup")
    def docker_cleanup():
        """Remove leaked hermes-* sandbox containers the GUI no longer needs.

        Safe by default: if any GUI worker is currently running we can't tell which
        container it's using (containers carry no session label and share the
        default workspace), so we refuse and report the count left untouched.
        """
        ids = _list_hermes_containers()
        active = len(_running_procs)
        if active:
            return {"removed": 0, "kept": len(ids), "skipped": True,
                    "reason": f"{active} task(s) running — stop them, then reset again"}
        removed = _remove_containers(ids)
        return {"removed": removed, "kept": 0, "skipped": False}

    @app.get("/api/docker/config")
    def get_docker_config():
        """Current Docker cleanup policy. `persist=false` (default) reaps a desk's
        sandbox container on delete and all of them on server shutdown."""
        return {"persist": _DOCKER_PERSIST}

    @app.post("/api/docker/config")
    def set_docker_config(body: dict):
        """Toggle whether desk sandbox containers persist across delete/shutdown.

        Runtime equivalent of HERMES_GUI_DOCKER_PERSIST; persisted so it sticks
        across restarts. (Desk-delete reaping is unaffected — it always runs.)"""
        global _DOCKER_PERSIST
        if "persist" in body:
            _DOCKER_PERSIST = bool(body["persist"])
            _save_docker_persist()
        return {"persist": _DOCKER_PERSIST}

    @app.post("/api/warmup")
    async def warmup():
        """Pre-import hermes modules in the venv Python to warm OS page cache.

        The worker's first import takes ~1-2 s cold; running this at app start
        warms the filesystem cache so subsequent imports are nearly instant.
        """
        venv_py = str(_HERMES_VENV_PY)
        if not Path(venv_py).exists():
            return {"ok": True, "skipped": "venv not found"}
        try:
            import_cmd = (
                "import sys; sys.path.insert(0, __import__('os').path.expanduser("
                "'~/.hermes/hermes-agent')); "
                "from run_agent import AIAgent; "
                "print('ok')"
            )
            proc = await asyncio.create_subprocess_exec(
                venv_py, "-c", import_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
        except Exception:
            pass
        return {"ok": True}

    @app.websocket("/ws/terminal/{session_id}")
    async def terminal_ws(websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in _running_procs:
            await websocket.send_text(
                "(terminal output only available for sessions started from this workbench)\n"
            )
            await websocket.close()
            return

        tq: asyncio.Queue = asyncio.Queue()
        _terminal_queues.setdefault(session_id, []).append(tq)   # capture live first…
        replay = _turn_log_text(session_id, "terminal")          # …then the in-flight turn
        if replay:
            try:
                await websocket.send_text(replay)
            except Exception:
                pass
        try:
            while True:
                try:
                    text = await asyncio.wait_for(tq.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Keep-alive — send empty string to detect disconnects
                    try:
                        await websocket.send_text("")
                    except Exception:
                        break
                    continue
                if text is None:
                    # Worker finished
                    break
                try:
                    await websocket.send_text(text)
                except Exception:
                    break
        except asyncio.CancelledError:
            # Server shutdown (timeout_graceful_shutdown) — close quietly.
            _note_ws_cancelled("terminal", session_id)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            queues = _terminal_queues.get(session_id, [])
            if tq in queues:
                queues.remove(tq)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/ws/console/{session_id}")
    async def console_ws(websocket: WebSocket, session_id: str):
        """Clean shell I/O only — no agent chatter, no tool metadata."""
        await websocket.accept()
        cq: asyncio.Queue = asyncio.Queue()
        _console_queues.setdefault(session_id, []).append(cq)   # capture live first…
        replay = _turn_log_text(session_id, "console")          # …then the in-flight turn
        if replay:
            try:
                await websocket.send_text(replay)
            except Exception:
                pass
        try:
            while True:
                try:
                    text = await asyncio.wait_for(cq.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    try:
                        await websocket.send_text("")
                    except Exception:
                        break
                    continue
                if text is None:
                    break
                try:
                    await websocket.send_text(text)
                except Exception:
                    break
        except asyncio.CancelledError:
            # Server shutdown (timeout_graceful_shutdown) — close quietly.
            _note_ws_cancelled("console", session_id)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            queues = _console_queues.get(session_id, [])
            if cq in queues:
                queues.remove(cq)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/ws/tail/{session_id}")
    async def tail_ws(websocket: WebSocket, session_id: str, file: str = ""):
        """Stream a workspace file line-by-line as it grows (like `tail -f`).

        Query param ?file=relative/path relative to the session workspace.
        Reconnect-safe: starts from the current end of the file.
        """
        await websocket.accept()
        ws_path = _find_workspace(session_id)
        if not ws_path or not file:
            await websocket.send_text("[tail] workspace or file not found\n")
            await websocket.close()
            return
        target = (ws_path / file).resolve()
        # Safety: must stay inside workspace
        try:
            target.relative_to(ws_path.resolve())
        except ValueError:
            await websocket.send_text("[tail] path outside workspace\n")
            await websocket.close()
            return
        # Wait up to 10 s for the file to appear
        for _ in range(20):
            if target.exists():
                break
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
            except Exception:
                pass
        if not target.exists():
            await websocket.send_text(f"[tail] waiting for {file}…\n")
        try:
            with target.open("r", errors="replace") as fh:
                fh.seek(0, 2)  # start at end
                while True:
                    line = fh.readline()
                    if line:
                        try:
                            await websocket.send_text(line)
                        except Exception:
                            break
                    else:
                        try:
                            await asyncio.wait_for(websocket.receive_text(), timeout=0.25)
                        except asyncio.TimeoutError:
                            pass
                        except Exception:
                            break
        except asyncio.CancelledError:
            # Server shutdown (timeout_graceful_shutdown) — close quietly.
            _note_ws_cancelled("tail", session_id)
        except Exception:
            pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    @app.post("/api/sessions/{session_id}/message")
    async def send_message(session_id: str, body: dict):
        content = body.get("content", "")
        if not content:
            raise HTTPException(400, "content is required")
        hermes = _hermes_bin()

        # Resume reads the session's conversation from its HERMES_HOME — point the
        # CLI at this desk's private home so it finds the per-desk state.db.
        msg_env = {**os.environ}
        _msg_ws = _find_workspace(session_id)
        _msg_sb = _sandbox_base_of(Path(_msg_ws)) if _msg_ws else None
        if _msg_sb:
            msg_env["HERMES_HOME"] = str(_msg_sb)

        async def _stream():
            try:
                proc = await asyncio.create_subprocess_exec(
                    hermes, "chat", "-q", content, "--resume", session_id, "-Q",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=msg_env,
                )
                stdout, _ = await proc.communicate()
                text = stdout.decode("utf-8", errors="replace")
                response = _extract_response(text)
                if response:
                    chunk = json.dumps({"choices": [{"delta": {"content": response}}]})
                    yield f"data: {chunk}\n\n".encode()
            except Exception as exc:
                # json.dumps so a quote/newline in the message can't break the SSE payload
                err = json.dumps({"error": str(exc)})
                yield f"data: {err}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @app.get("/api/search")
    def search(q: str = Query(...)):
        sessions = db.search_sessions(q)
        return [asdict(s) for s in sessions]

    # ── File preview ──────────────────────────────────────────────────────────

    @app.get("/api/file/preview")
    def preview_file(path: str):
        p = Path(path)
        if not p.exists():
            raise HTTPException(404, f"File not found: {path}")
        p = _safe_path(path)
        preview_type = can_preview_file(str(p))
        if preview_type == "none":
            raise HTTPException(400, "File type not previewable")
        if preview_type in ("code", "markdown", "text"):
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                raise HTTPException(500, str(e))
            return {"type": preview_type, "content": content, "path": path, "name": p.name}
        if preview_type in ("image", "pdf"):
            return FileResponse(str(p), media_type=mimetypes.guess_type(str(p))[0] or "application/octet-stream")
        raise HTTPException(400, "Unknown preview type")

    @app.get("/api/file/tree")
    def file_tree(root: str):
        p = Path(root)
        if not p.exists() or not p.is_dir():
            raise HTTPException(404, "Directory not found")
        p = _safe_path(root)
        return _walk_dir(p)

    # ── Hermes config ─────────────────────────────────────────────────────────

    @app.get("/api/config")
    def get_config():
        cfg_path = home / "cli-config.yaml"
        if not cfg_path.exists():
            return {}
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}

    def _roster_agents() -> "list[dict]":
        """Desk-assignable agents: the built-in Claude card + installed Hermes
        profiles. Shared by /api/agents AND /api/gui-config — the roster reads
        gui-config, so both must serve the same list or the Claude card silently
        goes missing from the picker.

        The Claude Code SDK card is an experimental/developmental feature: it is
        offered only when the server is started with --experimental (see
        create_app). Otherwise the roster lists the stable Hermes profiles only."""
        profiles = list_agents(cfg.agent_profiles_dir, home)
        if experimental:
            return [dict(_CLAUDE_AGENT_CARD), *profiles]
        # Gated off: withhold the built-in card AND any installed profile whose id
        # collides with the Claude agent id — _is_claude_agent routes those to the
        # SDK worker and desk creation refuses them, so don't offer what we won't
        # spawn.
        return [a for a in profiles if not _is_claude_agent(a.get("id", ""))]

    @app.get("/api/agents")
    def get_agents():
        """Agent profiles available for desk assignment (Claude built-in + Hermes)."""
        return {"agents": _roster_agents()}

    @app.get("/api/agents/prototypes")
    def get_agent_prototypes():
        """Built-in clone sources (coder, researcher)."""
        return {"prototypes": list_prototypes(cfg.agent_profiles_dir, home)}

    @app.get("/api/agents/{agent_id}/capabilities")
    def get_agent_capabilities(agent_id: str):
        """Tool presets (chat/lean/full) and skill bundles for bench preview."""
        if _is_claude_agent(agent_id):
            # Claude uses its own built-in tools (bypassPermissions); Hermes toolset
            # presets don't apply, so report none.
            return {"id": agent_id, "presets": {"chat": [], "lean": [], "full": []},
                    "source": "global", "default_preset": "chat",
                    "profile_disabled_toolsets": [], "skill_bundles": [], "skill_count": 0}
        try:
            pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        caps = agent_capabilities(pdir)
        return {"id": agent_id, **caps}

    @app.get("/api/agents/{agent_id}/persona")
    def get_agent_persona(agent_id: str):
        if _is_claude_agent(agent_id):
            return {"id": agent_id, "profile_path": "", "is_prototype": False,
                    "clone_from": None, "name": _CLAUDE_AGENT_CARD["name"],
                    "tagline": _CLAUDE_AGENT_CARD["tagline"], "model": "",
                    "base_url": "claude-agent-sdk", "soul": "", "memory": ""}
        try:
            pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        persona = read_persona(pdir)
        profile_model, profile_base = _read_model_info(pdir)
        gui = {}
        meta_path = pdir / ".gui-meta.yaml"
        if meta_path.is_file():
            try:
                import yaml as _yaml  # noqa: PLC0415
                gui = _yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except Exception:
                gui = {}
        return {
            "id": agent_id,
            "profile_path": str(pdir),
            "is_prototype": agent_id in PROFILE_PROTOTYPES,
            "clone_from": gui.get("clone_from"),
            "name": gui.get("name"),
            "tagline": gui.get("tagline"),
            "model": profile_model,
            "base_url": profile_base,
            **persona,
        }

    @app.put("/api/agents/{agent_id}/persona")
    async def put_agent_persona(agent_id: str, body: dict):
        try:
            pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, agent_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        soul = body.get("soul")
        memory = body.get("memory")
        model_default = body.get("model_default")
        base_url = body.get("base_url")
        provider = body.get("provider")
        if soul is None and memory is None and model_default is None:
            raise HTTPException(400, "provide soul, memory, and/or model_default")
        try:
            if soul is not None or memory is not None:
                write_persona(pdir, soul=soul if soul is not None else None,
                              memory=memory if memory is not None else None)
            if model_default is not None:
                write_model_config(
                    pdir, str(model_default),
                    base_url=str(base_url) if base_url is not None else None,
                    provider=str(provider) if provider is not None else None,
                )
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"ok": True, "id": agent_id}

    @app.post("/api/agents")
    async def create_agent(body: dict):
        """Clone a prototype profile via ``hermes profile create --clone --clone-from``."""
        raw_id = (body.get("id") or "").strip()
        clone_from = (body.get("clone_from") or "").strip()
        name = (body.get("name") or "").strip()
        tagline = (body.get("tagline") or "").strip()
        soul = body.get("soul")
        memory = body.get("memory")
        model_default = body.get("model_default")
        base_url = body.get("base_url")
        provider = body.get("provider")
        try:
            pid = validate_new_profile_id(raw_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not clone_from:
            raise HTTPException(400, "clone_from is required")
        # Any installed profile (or a built-in prototype) is a valid clone source;
        # create_profile_via_hermes validates existence and raises 400 otherwise.
        try:
            pdir = create_profile_via_hermes(home, pid, clone_from)
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if soul is not None or memory is not None:
            try:
                write_persona(pdir, soul=soul if isinstance(soul, str) else None,
                              memory=memory if isinstance(memory, str) else None)
            except OSError as exc:
                raise HTTPException(500, str(exc)) from exc
        # Point the clone at the chosen provider/model so it launches on that backend.
        if model_default is not None:
            try:
                write_model_config(
                    pdir, str(model_default),
                    base_url=str(base_url) if base_url is not None else None,
                    provider=str(provider) if provider is not None else None,
                )
            except OSError as exc:
                raise HTTPException(500, str(exc)) from exc
        meta = {"clone_from": clone_from}
        if name:
            meta["name"] = name
        if tagline:
            meta["tagline"] = tagline
        if not name and not tagline:
            meta.setdefault("name", pid.replace("-", " ").replace("_", " ").title())
        try:
            write_gui_meta(pdir, meta)
        except OSError:
            pass
        agents = list_agents(cfg.agent_profiles_dir, home)
        created = next((a for a in agents if a["id"] == pid), None)
        return {"ok": True, "agent": created or {"id": pid}}

    @app.delete("/api/agents/{agent_id}")
    async def delete_agent(agent_id: str):
        """Remove an agent profile; unbind it from any desks still using it."""
        aid = (agent_id or "").strip().lower()
        unbound = _unbind_agent_from_desks(aid)
        try:
            delete_profile(cfg.agent_profiles_dir, home, aid, hermes_bin=_hermes_bin())
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"ok": True, "id": aid, "unbound_desks": unbound}

    @app.get("/api/gui-config")
    def get_gui_config():
        """Resolved Agent GUI settings (agent profile dir, manager LLM overrides)."""
        mgr_base, mgr_model = _aux_model_config()
        global_base, global_model = hermes_model_config(home)
        return {
            "hermes_home": str(home),
            "agent_profiles_dir": str(cfg.agent_profiles_dir),
            "agents": _roster_agents(),
            "prototypes": list_prototypes(cfg.agent_profiles_dir, home),
            "desk_default_model": _EFFECTIVE_MODEL or None,
            "global": {
                "base_url": global_base,
                "model": _EFFECTIVE_MODEL or global_model or "",
            },
            "manager": {
                "base_url": mgr_base,
                "model": mgr_model,
                "uses_effective_agent_model": bool(_EFFECTIVE_MODEL),
            },
        }

    @app.get("/api/global/persona")
    def get_global_persona():
        """SOUL + MEMORY for the default Hermes home (~/.hermes/config.yaml) agent."""
        persona = read_persona(home)
        profile_model, profile_base = _read_model_info(home)
        return {
            "id": "",
            "profile_path": str(home),
            "model": profile_model or (_EFFECTIVE_MODEL or ""),
            "base_url": profile_base,
            **persona,
        }

    @app.put("/api/global/persona")
    async def put_global_persona(body: dict):
        soul = body.get("soul")
        memory = body.get("memory")
        model_default = body.get("model_default")
        base_url = body.get("base_url")
        provider = body.get("provider")
        if soul is None and memory is None and model_default is None:
            raise HTTPException(400, "provide soul, memory, and/or model_default")
        try:
            if soul is not None or memory is not None:
                write_persona(
                    home,
                    soul=soul if soul is not None else None,
                    memory=memory if memory is not None else None,
                )
            if model_default is not None:
                write_model_config(
                    home, str(model_default),
                    base_url=str(base_url) if base_url is not None else None,
                    provider=str(provider) if provider is not None else None,
                )
                # Default desks read the cached _EFFECTIVE_MODEL (set at startup),
                # which overrides config — refresh it so new desks use the edit.
                global _EFFECTIVE_MODEL
                eff_base, eff_model = hermes_model_config(home)
                _EFFECTIVE_MODEL = await _resolve_gui_model(
                    eff_model, eff_base, _ollama_url_from_cfg(),
                )
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"ok": True}

    # ── Skills + memory ───────────────────────────────────────────────────────

    @app.get("/api/skills")
    def list_skills():
        skills_dir = home / "skills"
        if not skills_dir.exists():
            return []
        return [
            {"name": p.stem, "path": str(p), "size": p.stat().st_size}
            for p in sorted(skills_dir.glob("*.md"))
        ]

    @app.get("/api/memory")
    def get_memory():
        result = {}
        for fname in ("MEMORY.md", "USER.md"):
            p = home / fname
            if p.exists():
                result[fname] = p.read_text(encoding="utf-8", errors="replace")
        return result

    # ── LLM model listing ─────────────────────────────────────────────────────

    async def _list_models_for_base(
        base_url: str,
        current_hint: str = "",
        *,
        profile_dir: Path | None = None,
        provider: str = "",
    ) -> dict:
        base = (base_url or "").strip()
        current = (current_hint or "").strip()
        if not base:
            cfg_path = home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                m = cfg.get("model", {}) or {}
                if not current:
                    current = m.get("default", "") or ""
                if not base:
                    base = m.get("base_url", "") or ""
            if not base:
                base = "http://127.0.0.1:11434/v1"
        models = await fetch_llm_models(base, profile_dir=profile_dir, provider=provider)
        return {"models": models, "current": current, "base_url": base}

    @app.get("/api/llm/models")
    @app.get("/api/ollama/models")
    async def list_llm_models(
        base_url: str | None = Query(None),
        agent_id: str | None = Query(None),
    ):
        """Return models served by the profile/backend (Ollama, /v1/models, or Gemini)."""
        probe_base = (base_url or "").strip()
        current = ""
        pdir: Path | None = None
        provider = ""
        aid = (agent_id or "").strip().lower()
        # Claude Code agent: not an HTTP backend (the SDK resolves models itself), so a
        # /v1/models probe yields nothing. Serve the selectable Claude aliases so the
        # desk model picker offers opus/sonnet/haiku, not just the desk's current model.
        if _is_claude_agent(aid) or probe_base == _CLAUDE_AGENT_CARD["base_url"]:
            return {"models": list(_CLAUDE_MODELS),
                    "current": _CLAUDE_AGENT_CARD["model"],
                    "base_url": probe_base or _CLAUDE_AGENT_CARD["base_url"]}
        if aid:
            try:
                pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, aid)
                current, profile_base = _read_model_info(pdir)
                provider = read_profile_provider(pdir)
                if not probe_base:
                    probe_base = profile_base
            except (ValueError, FileNotFoundError):
                pass
        return await _list_models_for_base(
            probe_base, current, profile_dir=pdir, provider=provider,
        )

    @app.get("/api/llm/providers")
    def list_llm_providers(agent_id: str | None = Query(None)):
        """Backends from a profile's ``providers:`` block (empty agent_id → Default)."""
        aid = (agent_id or "").strip().lower()
        pdir = home
        if aid:
            try:
                pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, aid)
            except (ValueError, FileNotFoundError):
                return {"providers": [], "active": ""}
        providers = read_profile_providers(pdir)
        active = read_profile_provider(pdir)
        return {"providers": providers, "active": active}

    def _manager_model_display() -> str:
        """Human-facing model name the manager currently runs on."""
        if _MANAGER_PROFILE:
            return _MGR_MODEL_DISPLAY
        # Default backend: reverse a capped variant (qwen-4b:guictx65536) to its
        # base via the cache, since the variant name mangles ':'/'/'.
        _, model = _aux_model_config()
        for base, variant in _capped_cache.items():
            if variant == model:
                return base
        return _strip_guictx(model)

    @app.get("/api/manager/profile")
    async def get_manager_profile():
        """Which profile the team manager runs on ('' = default ~/.hermes backend)."""
        base_url, _ = _aux_model_config()
        return {"profile": _MANAGER_PROFILE, "model": _manager_model_display(), "base_url": base_url}

    @app.post("/api/manager/profile")
    async def set_manager_profile(body: dict):
        """Point the manager's audits/judge/title calls at an installed profile."""
        pid = (body.get("profile") or "").strip()
        if pid.lower() in _CLAUDE_AGENT_IDS:
            # The Claude Agent SDK isn't an LLM endpoint (it resolves models itself —
            # no base_url/model to POST aux calls to), so it can't run the manager.
            raise HTTPException(400, "the Claude Agent SDK can't run the manager")
        if pid:
            try:
                resolve_agent_profile_dir(cfg.agent_profiles_dir, home, pid.lower())
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(400, f"unknown profile: {pid}") from exc
        res = await _apply_manager_profile(pid)
        res["model"] = _manager_model_display()
        return res

    @app.get("/api/models/reasoning")
    async def model_reasoning_options(
        model: str = "", base_url: str = "", agent_id: str = "",
    ):
        """Reasoning-effort options for the desk's *backend* (not just global config).

        Ollama qwen models return Off/On. Other Ollama thinking models use /api/show.
        Non-Ollama backends (vLLM, etc.) return [] so the UI grays the control out.
        Pass ``base_url`` and/or ``agent_id`` from the focused desk so a global vLLM
        default does not hide Ollama profile reasoning controls.
        """
        toggle = [{"value": "none", "label": "off"}, {"value": "medium", "label": "on"}]

        resolved_base = (base_url or "").strip()
        provider = ""
        profile_model = ""

        aid = (agent_id or "").strip().lower()
        if aid:
            try:
                pdir = resolve_agent_profile_dir(cfg.agent_profiles_dir, home, aid)
                profile_model, prof_base = _read_model_info(pdir)
                if not resolved_base:
                    resolved_base = prof_base
                provider = read_profile_provider(pdir)
            except Exception:
                pass

        global_model = ""
        cfg_path = home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                gcfg = yaml.safe_load(f) or {}
            m = gcfg.get("model", {}) or {}
            global_model = m.get("default", "") or ""
            if not resolved_base:
                resolved_base = (m.get("base_url") or "").strip()
            if not provider:
                provider = (m.get("provider") or "").strip()

        target = (model or profile_model or global_model or "").strip()
        if not target:
            return {"options": []}

        if not is_ollama_backend(resolved_base, provider):
            return {"options": []}

        ollama_root = _ollama_root_from_base(resolved_base)

        # Qwen on Ollama: always off/on (guictx aliases break /api/show).
        if "qwen" in target.lower():
            return {"options": toggle}

        show_name = re.sub(r":guictx\d+$", "", target, flags=re.IGNORECASE)
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.post(f"{ollama_root}/api/show", json={"model": show_name})
                if r.status_code == 200:
                    caps = r.json().get("capabilities") or []
                    if "thinking" in caps:
                        return {"options": toggle}
        except Exception:
            pass
        return {"options": []}

    @app.get("/api/toolsets")
    def list_toolsets():
        """Toolsets a desk can toggle, plus the built-in presets, for the Tools UI.

        `lean` is the default fast set; `chat` is zero tools (fastest); `full` is
        everything. The UI builds custom profiles by picking from `toolsets`.
        """
        lean = [t["name"] for t in DESK_TOOLSETS if t["lean"]]
        allt = [t["name"] for t in DESK_TOOLSETS]
        return {
            "toolsets": DESK_TOOLSETS,
            "presets": {"chat": [], "lean": lean, "full": allt},
            "default": "lean" if _LEAN_TOOLS == "1" else "full",
        }

    # ── Agent status ──────────────────────────────────────────────────────────

    @app.get("/api/hermes/status")
    async def hermes_status():
        hermes_ok = shutil.which("hermes") is not None
        llm_ok = False
        try:
            cfg_path = home / "config.yaml"
            base = ""
            provider = ""
            if cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                model_cfg = cfg.get("model", {}) or {}
                base = (model_cfg.get("base_url") or "").strip()
                provider = (model_cfg.get("provider") or "").strip()
            # Probe the right endpoint for the backend: Ollama → /api/version,
            # OpenAI-compatible (vLLM/Codex) → /v1/models. Avoids 404 spam on vLLM.
            probe = _llm_health_url(base, provider)
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(probe)
                llm_ok = r.status_code == 200
        except Exception:
            pass
        # `ollama` key kept for backward-compat; it now means "LLM backend reachable".
        return {"available": hermes_ok and llm_ok, "hermes": hermes_ok, "llm": llm_ok, "ollama": llm_ok}

    # ── WebSocket: live activity poll ─────────────────────────────────────────
    # Pushes a "refresh" ping to clients every 3 s so the activity feed
    # stays live while hermes is running tools in the background.

    @app.websocket("/ws/activity/{session_id}")
    async def activity_ws(websocket: WebSocket, session_id: str):
        """Stream both live worker events and periodic DB-derived events.

        Message shapes sent to the client:
          JSON array  → ActivityEvent[] batch from the DB (existing format)
          {"live": {type, ...}} → raw worker event (token / tool_start / …)
          {"subagents": [record, ...]} → durable delegate_task subagent traces,
              sent once on (re)connect to seed/restore the per-subagent tabs
        """
        await websocket.accept()
        last_count = 0
        last_db_poll = 0.0

        async def send_db_snapshot() -> int:
            nonlocal last_count, last_db_poll
            last_db_poll = asyncio.get_event_loop().time()
            messages = db.get_messages(session_id, limit=500)
            events = parse_activity(messages)
            _apply_real_times(events, session_id)  # exact times where known
            # Include preserved errored/interrupted turns — without them the first
            # snapshot after a failed turn would sweep its content from the feed.
            events = _merge_orphan_feed(events, session_id, _find_workspace(session_id))
            if len(events) != last_count:
                last_count = len(events)
                await websocket.send_text(json.dumps([asdict(e) for e in events]))
            return last_count

        # On (re)connect, restore the in-progress turn: Hermes hasn't flushed it to
        # the DB yet, so without this a mid-turn reload would show only the user
        # bubble. Discard any queue backlog first (the buffer is a superset of it, so
        # replaying both would duplicate), snapshot the buffer, then replay it as
        # live events — the same channel the client already reconstructs turns from.
        # NOTE: the drain + snapshot run with no `await` between them, so the pump
        # cannot interleave and the buffer is guaranteed to cover the drained items.
        q0 = _live_queues.get(session_id)
        if q0 is not None:
            while not q0.empty():
                try:
                    q0.get_nowait()
                except Exception:
                    break
        replay = list(_live_event_buffer.get(session_id, []))
        # Restore any subagent tabs (delegate_task children) recorded for this
        # desk — durable across turns/reloads/restarts via the sidecar. Sent as a
        # distinct {"subagents": [...]} message the client seeds tab state from;
        # subsequent live {"type":"subagent"} events append incrementally.
        subagents = _load_subagents(session_id, _find_workspace(session_id))
        if subagents:
            try:
                await websocket.send_text(
                    json.dumps({"subagents": list(subagents.values())}))
            except Exception:
                pass
        await send_db_snapshot()
        for revt in replay:
            try:
                await websocket.send_text(json.dumps({"live": revt}))
            except Exception:
                break

        try:
            while True:
                queue = _live_queues.get(session_id)

                if queue is not None:
                    # Live session: block until a worker event arrives or 0.5 s passes
                    try:
                        evt = await asyncio.wait_for(queue.get(), timeout=0.5)
                        if evt.get("type") in ("done", "error", "interrupted"):
                            # Session ended or was interrupted — send the event,
                            # do a final DB dump, then close the WS so stale
                            # worker output (during Docker cleanup) never reaches
                            # the client.
                            await websocket.send_text(json.dumps({"live": evt}))
                            await asyncio.sleep(0.3)
                            await send_db_snapshot()
                            break
                        await websocket.send_text(json.dumps({"live": evt}))
                    except asyncio.TimeoutError:
                        pass  # no event yet — fall through to DB poll
                else:
                    await asyncio.sleep(0.5)

                # Rate-limited DB snapshot (every ~0.5 s)
                if asyncio.get_event_loop().time() - last_db_poll >= 0.5:
                    await send_db_snapshot()
                    if queue is None:
                        s = db.get_session(session_id)
                        if s and s.ended_at:
                            break

        except asyncio.CancelledError:
            # Server shutdown (timeout_graceful_shutdown) — close quietly.
            _note_ws_cancelled("activity", session_id)
        except WebSocketDisconnect:
            pass
        except Exception:
            try:
                await websocket.close()
            except Exception:
                pass

    # ── Serve frontend ────────────────────────────────────────────────────────

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str):
            # Don't serve the SPA shell for unmatched API/WS paths — return a real
            # 404 so a typo'd or removed route surfaces instead of a 200 text/html.
            if full_path.startswith(("api/", "ws/")):
                raise HTTPException(404, "Not found")
            # Serve real files that live at the dist root (logo.png, favicon, etc.);
            # Vite copies everything in frontend/public/ here. Without this, /logo.png
            # would fall through to the SPA shell and return index.html.
            if full_path:
                candidate = (FRONTEND_DIST / full_path).resolve()
                try:
                    candidate.relative_to(FRONTEND_DIST.resolve())
                except ValueError:
                    candidate = None  # path traversal attempt — fall through to SPA
                if candidate and candidate.is_file():
                    return FileResponse(str(candidate))
            index = FRONTEND_DIST / "index.html"
            # no-cache so the browser always revalidates the SPA shell and picks up
            # newly-built (content-hashed) JS/CSS bundles instead of a stale cached one.
            return FileResponse(str(index), headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/")
        def dev_hint():
            return JSONResponse({
                "message": "Frontend not built. Run: cd frontend && npm install && npm run build",
                "api_docs": "/docs",
            })

    return app
