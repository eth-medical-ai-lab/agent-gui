"""SQLite persistence for **Claude Code desks**, schema-compatible with the subset
of Hermes' ``state.db`` that :class:`agent_gui.db.HermesDB` reads.

Why this exists
---------------
A GUI desk renders its history, Overview chart, Files tab, message counts and
search from a per-desk ``~/.hermes/gui_sandboxes/<sid>/state.db`` through the
read-only :class:`agent_gui.db.HermesDB`. Hermes desks get that db for free — the
Hermes ``AIAgent`` writes it. Claude desks ran the Claude Agent SDK with **no such
db** (the "lean orphaned approach"): the desk showed up as a stub with
``message_count: 0`` and lost all history on reload.

This module lets ``claude_worker.py`` write the SAME db a Hermes desk would, so
the entire existing read path (``db.py`` + ``activity_parser`` + ``file_parser`` +
Overview + search + delete) works for Claude desks **unchanged** — no server
changes are required. The desk transparently upgrades from "stub" to "db-backed"
the moment it runs its first turn.

Design
------
* **Pure stdlib** (``sqlite3`` only) and **SDK-free**, so it imports and unit-tests
  without ``claude-agent-sdk`` and the worker can persist independently of the
  agent runtime.
* Writes only the columns ``db.py`` actually reads (see that file's SELECTs);
  extra Hermes columns are omitted. Timestamps are REAL epoch seconds — Hermes'
  convention, and ``db._to_iso`` accepts float epoch or ISO.
* **WAL** mode + ``synchronous=NORMAL``: the worker is the lone writer while
  ``HermesDB`` reads concurrently — the exact multi-process WAL reader/writer
  pattern ``db._connect`` was built around.
* **Best-effort**: every write is wrapped so a db hiccup never aborts an agent
  turn (mirrors Hermes' non-fatal flush). Diagnostics go to ``stderr`` only —
  never ``stdout``, which is the worker→server NDJSON event pipe.
* **One session row whose ``id`` == the GUI/desk session id** (the sandbox dir
  name). This matches how a Hermes desk's ROOT session id equals the desk id, so
  ``HermesDB.get_session(<desk id>)`` resolves. All messages attach to that id;
  the SDK's own (different) session id is stashed in ``state_meta`` for a possible
  future cross-restart resume.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Columns are a deliberate subset of Hermes' schema — only what db.py reads.
_CORE_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    source            TEXT NOT NULL DEFAULT 'workbench',
    model             TEXT,
    parent_session_id TEXT,
    started_at        REAL NOT NULL,
    ended_at          REAL,
    message_count     INTEGER DEFAULT 0,
    tool_call_count   INTEGER DEFAULT 0,
    input_tokens      INTEGER DEFAULT 0,
    output_tokens     INTEGER DEFAULT 0,
    title             TEXT,
    cwd               TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    role              TEXT NOT NULL,
    content           TEXT,
    tool_call_id      TEXT,
    tool_calls        TEXT,
    tool_name         TEXT,
    timestamp         REAL NOT NULL,
    reasoning_content TEXT
);
CREATE TABLE IF NOT EXISTS state_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
"""

# FTS5 mirror + triggers, matching Hermes so db.search_sessions' `messages_fts
# MATCH` works for Claude desks too. Best-effort: a SQLite build without fts5 just
# falls back to db.search_sessions' LIKE path, so this never gates persistence.
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content);
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;
"""


def started_at_from_session_id(session_id: str) -> float:
    """Best-effort epoch from a GUI session id (``YYYYMMDD_HHMMSS_xxxxxx``).

    Matches the server's ``_claude_desk_stub`` so a desk's started_at is stable
    across the stub→db-backed transition. Falls back to now() if unparseable.
    """
    try:
        return (datetime.strptime(session_id[:15], "%Y%m%d_%H%M%S")
                .replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError):
        return time.time()


class ClaudeStateWriter:
    """Append-only writer for a Claude desk's ``state.db``.

    All methods are best-effort: failures are logged to stderr and swallowed so
    persistence can never abort an agent turn. Single-threaded use only (the
    worker drives it from its asyncio loop, never from the stdin reader thread).
    """

    def __init__(
        self,
        db_path: "str | Path",
        session_id: str,
        *,
        model: str = "",
        source: str = "workbench",
        started_at: "float | None" = None,
        cwd: "str | None" = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.session_id = session_id
        self._conn: "sqlite3.Connection | None" = None
        self._ok = False
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._ensure_schema()
            self._ensure_session_row(
                model=model, source=source,
                started_at=started_at if started_at is not None
                else started_at_from_session_id(session_id),
                cwd=cwd,
            )
            self._ok = True
        except sqlite3.Error as exc:
            self._warn(f"init failed for {self.db_path}: {exc}")

    # ── setup ────────────────────────────────────────────────────────────────
    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_CORE_DDL)
        try:
            self._conn.executescript(_FTS_DDL)
        except sqlite3.Error as exc:  # fts5 unavailable → search falls back to LIKE
            self._warn(f"fts5 unavailable, skipping full-text index: {exc}")
        self._conn.commit()

    def _ensure_session_row(self, *, model: str, source: str,
                            started_at: float, cwd: "str | None") -> None:
        assert self._conn is not None
        # INSERT OR IGNORE: on a server restart the row already exists, so prior
        # history is preserved and new turns simply append.
        self._conn.execute(
            """INSERT OR IGNORE INTO sessions
               (id, source, model, parent_session_id, started_at, cwd)
               VALUES (?, ?, ?, NULL, ?, ?)""",
            (self.session_id, source, model or None, started_at, cwd),
        )
        # Fill in the model if it was unknown when the row was first created.
        if model:
            self._conn.execute(
                "UPDATE sessions SET model=? WHERE id=? AND COALESCE(model,'')=''",
                (model, self.session_id),
            )
        self._conn.commit()

    # ── writes ───────────────────────────────────────────────────────────────
    def record_user(self, content: str, ts: "float | None" = None) -> None:
        self._insert(role="user", content=content, ts=ts)

    def record_assistant(
        self,
        text: str = "",
        reasoning: str = "",
        tool_calls: "list[dict] | None" = None,
        ts: "float | None" = None,
    ) -> None:
        """One assistant row: visible text, thinking trace, and/or tool calls.

        ``tool_calls`` must already be in the Hermes/OpenAI shape the parsers read:
        ``[{"id","type":"function","function":{"name","arguments":<json str>}}]``.
        """
        tc_json = json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else None
        self._insert(
            role="assistant",
            content=text or None,
            reasoning_content=reasoning or None,
            tool_calls=tc_json,
            ts=ts,
        )

    def record_tool_result(self, tool_call_id: str, tool_name: str,
                           content: str, ts: "float | None" = None) -> None:
        self._insert(
            role="tool",
            content=content,
            tool_call_id=tool_call_id or None,
            tool_name=tool_name or None,
            ts=ts,
        )

    def set_meta(self, key: str, value: str) -> None:
        """Stash a small key/value (e.g. the SDK's own session id) in state_meta."""
        if not self._ok or self._conn is None or value is None:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO state_meta(key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._warn(f"set_meta({key}) failed: {exc}")

    def finalize_turn(self, *, input_tokens: int = 0, output_tokens: int = 0,
                      ended_at: "float | None" = None) -> None:
        """Record end-of-turn: accumulate token usage and stamp ended_at."""
        if not self._ok or self._conn is None:
            return
        try:
            self._conn.execute(
                """UPDATE sessions
                   SET input_tokens  = COALESCE(input_tokens, 0) + ?,
                       output_tokens = COALESCE(output_tokens, 0) + ?,
                       ended_at      = ?
                   WHERE id = ?""",
                (int(input_tokens or 0), int(output_tokens or 0),
                 ended_at if ended_at is not None else time.time(),
                 self.session_id),
            )
            self._conn.commit()
        except (sqlite3.Error, ValueError, TypeError) as exc:
            self._warn(f"finalize_turn failed: {exc}")

    # ── internals ────────────────────────────────────────────────────────────
    def _insert(self, *, role: str, content: "str | None" = None,
                tool_call_id: "str | None" = None, tool_calls: "str | None" = None,
                tool_name: "str | None" = None, reasoning_content: "str | None" = None,
                ts: "float | None" = None) -> None:
        if not self._ok or self._conn is None:
            return
        try:
            self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, tool_call_id, tool_calls,
                    tool_name, timestamp, reasoning_content)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.session_id, role, content, tool_call_id, tool_calls,
                 tool_name, ts if ts is not None else time.time(), reasoning_content),
            )
            self._refresh_counts()
            self._conn.commit()
        except sqlite3.Error as exc:
            self._warn(f"insert({role}) failed: {exc}")

    def _refresh_counts(self) -> None:
        """Keep sessions.message_count/tool_call_count in sync with the rows.

        Recomputed (not incremented) so the counts stay correct even when prior
        rows already existed from an earlier server run.
        """
        assert self._conn is not None
        self._conn.execute(
            """UPDATE sessions SET
                   message_count   = (SELECT COUNT(*) FROM messages WHERE session_id = ?),
                   tool_call_count = (SELECT COUNT(*) FROM messages
                                      WHERE session_id = ? AND role = 'tool')
               WHERE id = ?""",
            (self.session_id, self.session_id, self.session_id),
        )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    @staticmethod
    def _warn(msg: str) -> None:
        # stderr ONLY — stdout is the worker→server event pipe.
        print(f"[claude-state] {msg}", file=sys.stderr, flush=True)

    def __enter__(self) -> "ClaudeStateWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
