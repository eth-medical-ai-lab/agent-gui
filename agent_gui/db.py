"""Read-only access to ~/.hermes/state.db"""
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_gui.message_sanitize import strip_injected_prefix

log = logging.getLogger(__name__)

WORKSPACE_KEY_MARKER = ".hermes_workspace_key"
SESSION_ID_MARKER = ".hermes_session_id"


def _to_iso(value: object) -> str | None:
    """Normalize a SQLite timestamp (float epoch or ISO string) to ISO-8601 UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    s = str(value).strip()
    return s if s else None


@dataclass
class Session:
    id: str
    started_at: str
    ended_at: str | None
    source: str
    model: str
    parent_session_id: str | None
    title: str = ""
    message_count: int = 0
    token_estimate: int = 0


@dataclass
class DeskSessionEntry:
    """One Hermes session row inside a desk's private state.db.

    A GUI *desk* is a single sandbox dir, named by its root/anchor session id.
    Every resume or model-switch starts a NEW Hermes session row in that desk's
    private state.db, with ``parent_session_id`` pointing back to the root — so a
    desk accumulates a lineage of session ids over its life. This entry surfaces
    one such row for the desk-history log.
    """
    id: str
    started_at: str
    ended_at: str | None
    model: str
    parent_session_id: str | None
    message_count: int
    is_root: bool


@dataclass
class Message:
    id: int
    session_id: str
    role: str                          # user | assistant | tool
    content: str | None
    timestamp: str
    tool_calls: list[dict] = field(default_factory=list)   # parsed from tool_calls column
    tool_call_id: str | None = None    # set on role=tool messages
    tool_name: str | None = None       # set on role=tool messages
    reasoning_content: str | None = None  # extended thinking / reasoning trace (assistant)


class HermesDB:
    # Each GUI desk runs with HERMES_HOME=<hermes_home>/gui_sandboxes/<sid>, so its
    # session conversation lives in a *private* state.db (no cross-desk leakage via
    # Hermes' shared session/memory store). The shared <hermes_home>/state.db still
    # holds legacy/CLI sessions, so reads fall back to it when no per-desk db exists.
    def __init__(self, hermes_home: Path):
        self.hermes_home = hermes_home
        self.db_path = hermes_home / "state.db"          # shared (legacy / CLI)
        self._sandbox_root = hermes_home / "gui_sandboxes"

    def _connect(self, db_path: "Path | None" = None) -> sqlite3.Connection:
        path = db_path or self.db_path
        # ``mode=ro`` is correct for finished / checkpointed dbs, but a *live*
        # desk db is in WAL mode and being written by the Hermes worker: a pure
        # read-only open cannot register itself in the WAL shared-memory index
        # (it would need to create/write the -shm file) and SQLite then raises
        # SQLITE_CANTOPEN ("unable to open database file"). That made the
        # Activity feed / Debug terminal go blank on refresh for an active desk.
        # Fall back to a read-write open with PRAGMA query_only — the standard
        # multi-process WAL reader pattern (we never write) — and finally to an
        # immutable open for genuinely read-only files.
        try:
            conn = sqlite3.connect(
                f"file:{path}?mode=ro", uri=True, timeout=5.0,
            )
        except sqlite3.OperationalError:
            try:
                conn = sqlite3.connect(
                    f"file:{path}?mode=rw", uri=True, timeout=5.0,
                )
                conn.execute("PRAGMA query_only = TRUE")
            except sqlite3.OperationalError:
                # immutable=1 ignores the -wal file entirely, so it must never
                # be used on a *live* db: it would "succeed" and serve a stale
                # snapshot (often an empty schema → "no such table: sessions"),
                # blanking the desk instead of erroring. If a non-empty WAL sits
                # next to the file, the db is live — propagate the failure so
                # callers can degrade (skip/404) rather than read wrong data.
                wal = Path(f"{path}-wal")
                try:
                    wal_live = wal.stat().st_size > 0
                except OSError:
                    wal_live = False
                if wal_live:
                    raise
                conn = sqlite3.connect(
                    f"file:{path}?immutable=1", uri=True, timeout=5.0,
                )
        conn.row_factory = sqlite3.Row
        return conn

    def _db_available(self) -> bool:
        return self.db_path.exists()

    def _desk_db(self, session_id: str) -> "Path | None":
        """The per-desk state.db for a session, if it exists on disk."""
        cand = self._sandbox_root / session_id / "state.db"
        return cand if cand.exists() else None

    def _db_for_session(self, session_id: str) -> "Path | None":
        """Resolve which state.db holds a session: its private desk db, else shared."""
        desk = self._desk_db(session_id)
        if desk:
            return desk
        return self.db_path if self.db_path.exists() else None

    def _iter_session_dbs(self) -> "list[Path]":
        """All state.dbs to scan when listing sessions: every per-desk db + shared."""
        dbs: list[Path] = []
        if self._sandbox_root.exists():
            try:
                desks = [d for d in self._sandbox_root.iterdir() if d.is_dir()]
                # Newest desks first so recent work surfaces without scanning everything.
                desks.sort(key=lambda d: d.stat().st_mtime, reverse=True)
                for d in desks:
                    cand = d / "state.db"
                    if cand.exists():
                        dbs.append(cand)
            except OSError:
                pass
        if self.db_path.exists():
            dbs.append(self.db_path)
        return dbs

    def _row_to_session(self, conn: sqlite3.Connection, r: sqlite3.Row) -> Session:
        title = r["title"] or ""
        if not title:
            title = self._infer_title(conn, r["id"])
        return Session(
            id=r["id"],
            started_at=_to_iso(r["started_at"]) or "",
            ended_at=_to_iso(r["ended_at"]),
            source=r["source"] or "cli",
            model=r["model"] or "",
            parent_session_id=r["parent_session_id"],
            title=title,
            message_count=r["message_count"] or 0,
            token_estimate=r["token_estimate"] or 0,
        )

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[Session]:
        # Aggregate across every per-desk db + the shared db, dedup by id (a desk's
        # private db wins), then sort newest-first and paginate in Python.
        seen: set[str] = set()
        collected: list[Session] = []
        # Bound work per db: per-desk dbs hold ~1 session; cap the shared scan.
        for db_path in self._iter_session_dbs():
            try:
                with self._connect(db_path) as conn:
                    rows = conn.execute(
                        f"""
                        SELECT id, started_at, ended_at, source, model,
                               parent_session_id, title,
                               message_count,
                               {self._token_estimate_expr(conn)} AS token_estimate
                        FROM sessions
                        ORDER BY started_at DESC
                        LIMIT ?
                        """,
                        (limit + offset + 200,),
                    ).fetchall()
                    for r in rows:
                        if r["id"] in seen:
                            continue
                        seen.add(r["id"])
                        collected.append(self._row_to_session(conn, r))
            except sqlite3.Error:
                continue
        collected.sort(key=lambda s: s.started_at, reverse=True)
        return collected[offset:offset + limit]

    def _infer_title(self, conn: sqlite3.Connection, session_id: str) -> str:
        row = conn.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row or not row["content"]:
            return "Untitled task"
        text = row["content"]
        if not isinstance(text, str):
            text = str(text)
        text = strip_injected_prefix(text)
        return text[:80].replace("\n", " ").strip() or "Untitled task"

    def _token_estimate_expr(self, conn: sqlite3.Connection) -> str:
        """SQL expression for a session's token estimate.

        input_tokens/output_tokens are present in current Hermes schemas but
        absent in older state.db files — fall back to 0 if so (same pattern as
        get_messages' reasoning_content probe).
        """
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "input_tokens" in cols and "output_tokens" in cols:
            return "COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)"
        return "0"

    def get_session(self, session_id: str) -> Session | None:
        db_path = self._db_for_session(session_id)
        if not db_path:
            return None
        try:
            with self._connect(db_path) as conn:
                row = conn.execute(
                    f"""
                    SELECT id, started_at, ended_at, source, model, parent_session_id, title,
                           message_count,
                           {self._token_estimate_expr(conn)} AS token_estimate
                    FROM sessions WHERE id=?
                    """,
                    (session_id,),
                ).fetchone()
                if not row:
                    return None
                return self._row_to_session(conn, row)
        except sqlite3.Error as e:
            # An unreadable desk db must surface as "session not found" (404),
            # not a 500 — same degradation as list_sessions/get_desk_messages.
            log.warning("skipping unreadable desk db %s: %s", db_path, e)
            return None

    @staticmethod
    def _reasoning_select(conn) -> str:
        """SELECT expression yielding one ``reasoning_content`` value per row.

        Hermes persists the assistant's thinking trace into one of two columns
        depending on which field the backend streams: ``reasoning_content`` (the
        field literally named that — older vLLM, DeepSeek, Anthropic) or
        ``reasoning`` (newer vLLM reasoning-parser / OpenRouter-style, which the
        GUI historically never read — the cause of "no reasoning trace"). Coalesce
        them, preferring reasoning_content, and degrade gracefully when either
        column is absent (older / Claude-only schemas).
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        has_rc = "reasoning_content" in cols
        has_r = "reasoning" in cols
        if has_rc and has_r:
            return "COALESCE(NULLIF(reasoning_content, ''), reasoning) AS reasoning_content"
        if has_rc:
            return "reasoning_content"
        if has_r:
            return "reasoning AS reasoning_content"
        return "NULL AS reasoning_content"

    def get_messages(self, session_id: str, limit: int = 500, *, tail: bool = False) -> list[Message]:
        db_path = self._db_for_session(session_id)
        if not db_path:
            return []
        order = "DESC" if tail else "ASC"
        with self._connect(db_path) as conn:
            reasoning_col = self._reasoning_select(conn)
            rows = conn.execute(
                f"""
                SELECT id, session_id, role, content, tool_calls,
                       tool_call_id, tool_name, timestamp, {reasoning_col}
                FROM messages
                WHERE session_id=?
                ORDER BY id {order}
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            if tail:
                rows = list(reversed(rows))
            messages = []
            for r in rows:
                tool_calls: list[dict] = []
                if r["tool_calls"]:
                    try:
                        parsed = json.loads(r["tool_calls"])
                        if isinstance(parsed, list):
                            tool_calls = parsed
                    except Exception:
                        pass
                messages.append(Message(
                    id=r["id"],
                    session_id=r["session_id"],
                    role=r["role"],
                    content=r["content"],
                    timestamp=_to_iso(r["timestamp"]) or "",
                    tool_calls=tool_calls,
                    tool_call_id=r["tool_call_id"],
                    tool_name=r["tool_name"],
                    reasoning_content=r["reasoning_content"],
                ))
            return messages

    def get_desk_messages(self, anchor_session_id: str, limit: int = 10000) -> list[Message]:
        """All messages in a desk's private state.db, chronological.

        Hermes may use multiple session_id values inside one desk db across
        resumes; the GUI's session id is only used to locate the db file.
        Falls back to session-scoped reads for legacy/shared databases.
        """
        desk_db = self._desk_db(anchor_session_id)
        if not desk_db:
            return self.get_messages(anchor_session_id, limit=limit)
        limit = max(1, limit)
        try:
            with self._connect(desk_db) as conn:
                reasoning_col = self._reasoning_select(conn)
                rows = conn.execute(
                    f"""
                    SELECT id, session_id, role, content, tool_calls,
                           tool_call_id, tool_name, timestamp, {reasoning_col}
                    FROM messages
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as e:
            # A single unreadable desk db (bad perms, WAL/-shm we can't open ro,
            # fd exhaustion, corruption) must not 500 the whole Overview — skip it.
            log.warning("skipping unreadable desk db %s: %s", desk_db, e)
            return []
        messages: list[Message] = []
        for r in rows:
            tool_calls: list[dict] = []
            if r["tool_calls"]:
                try:
                    parsed = json.loads(r["tool_calls"])
                    if isinstance(parsed, list):
                        tool_calls = parsed
                except Exception:
                    pass
            messages.append(Message(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"],
                timestamp=_to_iso(r["timestamp"]) or "",
                tool_calls=tool_calls,
                tool_call_id=r["tool_call_id"],
                tool_name=r["tool_name"],
                reasoning_content=r["reasoning_content"],
            ))
        return messages

    def get_desk_session_history(self, anchor_session_id: str) -> list[DeskSessionEntry]:
        """Every Hermes session row in a desk's private db, oldest first.

        Surfaces the desk's session lineage: the root session plus every
        resume/model-switch (each a new session id, parented to the root). For a
        legacy/shared desk with no per-desk db, returns just the anchor session.
        """
        desk_db = self._desk_db(anchor_session_id)
        if not desk_db:
            s = self.get_session(anchor_session_id)
            if not s:
                return []
            return [DeskSessionEntry(
                id=s.id, started_at=s.started_at, ended_at=s.ended_at,
                model=s.model, parent_session_id=s.parent_session_id,
                message_count=s.message_count,
                is_root=s.parent_session_id is None,
            )]
        try:
            with self._connect(desk_db) as conn:
                rows = conn.execute(
                    """
                    SELECT id, started_at, ended_at, model, parent_session_id,
                           COALESCE(message_count, 0) AS message_count
                    FROM sessions
                    ORDER BY started_at ASC, id ASC
                    """
                ).fetchall()
        except sqlite3.Error as e:
            log.warning("skipping unreadable desk db %s: %s", desk_db, e)
            return []
        entries: list[DeskSessionEntry] = []
        for r in rows:
            entries.append(DeskSessionEntry(
                id=r["id"],
                started_at=_to_iso(r["started_at"]) or "",
                ended_at=_to_iso(r["ended_at"]),
                model=r["model"] or "",
                parent_session_id=r["parent_session_id"],
                message_count=r["message_count"] or 0,
                is_root=r["parent_session_id"] is None,
            ))
        return entries

    def get_desk_time_bounds(self, anchor_session_id: str) -> tuple[str | None, str | None]:
        """Earliest desk start and latest known activity for overview spans."""
        desk_db = self._desk_db(anchor_session_id)
        if not desk_db:
            s = self.get_session(anchor_session_id)
            if not s:
                return None, None
            return s.started_at or None, s.ended_at or s.started_at or None
        try:
            with self._connect(desk_db) as conn:
                # Span start = the FIRST COMMAND (earliest message), not the
                # session row's started_at: workers create their session row at
                # spawn (desk/team creation), which precedes the first user
                # command by the whole worker init — so the Overview chart began
                # a minute-plus before any visible activity. Sessions with no
                # messages yet fall back to started_at.
                row = conn.execute(
                    "SELECT MIN(timestamp) AS t0 FROM messages"
                ).fetchone()
                t0 = _to_iso(row["t0"]) if row and row["t0"] is not None else None
                if t0 is None:
                    row = conn.execute(
                        "SELECT MIN(started_at) AS t0 FROM sessions"
                    ).fetchone()
                    t0 = _to_iso(row["t0"]) if row and row["t0"] is not None else None
                row2 = conn.execute(
                    "SELECT MAX(timestamp) AS ts FROM messages"
                ).fetchone()
                t_msg = _to_iso(row2["ts"]) if row2 and row2["ts"] is not None else None
                row3 = conn.execute(
                    "SELECT MAX(ended_at) AS t1 FROM sessions WHERE ended_at IS NOT NULL"
                ).fetchone()
                t_end = _to_iso(row3["t1"]) if row3 and row3["t1"] is not None else None
        except sqlite3.Error as e:
            log.warning("skipping unreadable desk db %s: %s", desk_db, e)
            return None, None
        # Latest wall-clock point: last message, else last ended_at, else session start.
        last = t_msg or t_end or t0
        return t0, last

    def _timestamp_sort_key(self, ts: str) -> float:
        if not ts:
            return 0.0
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def read_workspace_key(ws: Path) -> str | None:
        key_file = ws / WORKSPACE_KEY_MARKER
        if not key_file.is_file():
            return None
        try:
            key = key_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return key or None

    @staticmethod
    def ensure_workspace_key(ws: Path) -> str:
        """Persist a stable lineage id in the workspace (shared across session ids)."""
        existing = HermesDB.read_workspace_key(ws)
        if existing:
            return existing
        key = uuid.uuid4().hex
        try:
            ws.mkdir(parents=True, exist_ok=True)
            (ws / WORKSPACE_KEY_MARKER).write_text(key, encoding="utf-8")
        except OSError:
            pass
        return key

    def _iter_known_workspaces(
        self, extra_workspace_dirs: list[Path] | None = None,
    ) -> list[tuple[str, Path]]:
        """``(session_id, workspace_dir)`` for every GUI/legacy desk workspace."""
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, Path]] = []

        def _add(sid: str, ws: Path) -> None:
            sid = (sid or "").strip()
            if not sid or not ws.is_dir():
                return
            try:
                rp = str(ws.resolve())
            except OSError:
                rp = str(ws)
            key = (sid, rp)
            if key in seen:
                return
            seen.add(key)
            out.append((sid, ws))

        if self._sandbox_root.is_dir():
            for sid_dir in self._sandbox_root.iterdir():
                if not sid_dir.is_dir():
                    continue
                ws = sid_dir / "docker" / "default" / "workspace"
                if ws.is_dir():
                    _add(sid_dir.name, ws)
        for ws in extra_workspace_dirs or []:
            if not ws.is_dir():
                continue
            marker = ws / SESSION_ID_MARKER
            sid = ws.name
            if marker.is_file():
                try:
                    sid = marker.read_text(encoding="utf-8").strip() or sid
                except OSError:
                    pass
            _add(sid, ws)
        return out

    def find_related_session_ids(
        self,
        anchor_session_id: str,
        anchor_ws: Path | None,
        *,
        extra_workspace_dirs: list[Path] | None = None,
    ) -> list[str]:
        """Session ids that share this desk's workspace lineage (same path or
        same ``.hermes_workspace_key``).

        The key is authoritative: every desk gets a unique key at creation, so
        desks with different keys are different desks. Never link desks by task
        content — same-team desks created with the same prompt are still
        separate desks, and merging them leaked activity across the whole team.
        """
        related: set[str] = {(anchor_session_id or "").strip()} - {""}
        if anchor_ws is None or not anchor_ws.is_dir():
            return sorted(related)
        try:
            anchor_rp = anchor_ws.resolve()
        except OSError:
            anchor_rp = anchor_ws
        anchor_key = self.read_workspace_key(anchor_ws)

        for sid, ws in self._iter_known_workspaces(extra_workspace_dirs):
            try:
                if ws.resolve() == anchor_rp:
                    related.add(sid)
                    continue
            except OSError:
                pass
            if anchor_key and self.read_workspace_key(ws) == anchor_key:
                related.add(sid)
        return sorted(related)

    def get_workspace_messages(
        self, session_ids: list[str], limit: int = 10000,
    ) -> list[Message]:
        """Merge messages from every related session, chronological."""
        limit = max(1, limit)
        ranked: list[tuple[tuple[float, int, str], Message]] = []
        for sid in session_ids:
            if self._desk_db(sid):
                batch = self.get_desk_messages(sid, limit=limit)
            else:
                batch = self.get_messages(sid, limit=limit)
            for m in batch:
                ranked.append((
                    (self._timestamp_sort_key(m.timestamp), m.id, sid),
                    m,
                ))
        ranked.sort(key=lambda item: item[0])
        return [m for _, m in ranked[:limit]]

    def get_workspace_time_bounds(
        self, session_ids: list[str],
    ) -> tuple[str | None, str | None]:
        """Earliest start + latest activity across related sessions."""
        starts: list[str] = []
        ends: list[str] = []
        for sid in session_ids:
            t0, t1 = self.get_desk_time_bounds(sid)
            if t0:
                starts.append(t0)
            if t1:
                ends.append(t1)
        if not starts:
            return None, None
        return min(starts), max(ends)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session row + messages from its desk-private or shared state.db."""
        db_path = self._db_for_session(session_id)
        if not db_path:
            return False
        try:
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if not row or row[0] == 0:
                    return False
                conn.execute(
                    "UPDATE sessions SET parent_session_id = NULL WHERE parent_session_id = ?",
                    (session_id,),
                )
                conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                return True
            finally:
                conn.close()
        except sqlite3.Error:
            return False

    def search_sessions(self, query: str, limit: int = 20) -> list[Session]:
        # Search every per-desk db + the shared db; dedup ids across them.
        ids: list[str] = []
        seen: set[str] = set()
        for db_path in self._iter_session_dbs():
            if len(ids) >= limit:
                break
            try:
                with self._connect(db_path) as conn:
                    try:
                        rows = conn.execute(
                            """
                            SELECT DISTINCT s.id FROM sessions s
                            JOIN messages_fts ON messages_fts.rowid = (
                                SELECT id FROM messages WHERE session_id=s.id
                                AND messages_fts MATCH ? LIMIT 1
                            )
                            LIMIT ?
                            """,
                            (query, limit),
                        ).fetchall()
                    except sqlite3.Error:
                        rows = conn.execute(
                            "SELECT DISTINCT session_id AS id FROM messages WHERE content LIKE ? LIMIT ?",
                            (f"%{query}%", limit),
                        ).fetchall()
            except sqlite3.Error:
                continue
            for r in rows:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    ids.append(r["id"])
        sessions = []
        for sid in ids[:limit]:
            s = self.get_session(sid)
            if s:
                sessions.append(s)
        return sessions
