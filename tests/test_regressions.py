"""Regression-lock tests for previously-fixed hardening items.

Each of these guards a fix recorded in SECURITY_NOTES.md that had no dedicated
test, so a future refactor can't silently undo it:

  * pagination clamp — `limit`/`offset` are bounded before reaching SQL,
  * SPA catch-all — unknown `/api/*` paths 404 instead of returning the SPA shell,
  * FTS search — punctuation in the query degrades gracefully instead of crashing.
"""
import sqlite3
import unittest.mock as mock
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent_gui import db as dbmod
from agent_gui.db import HermesDB
from agent_gui.server import create_app


@pytest.fixture()
def app(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "workspace"
    home.mkdir()
    ws.mkdir()
    return create_app(hermes_home=str(home), workspace_root=str(ws))


@pytest_asyncio.fixture()
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Pagination clamp ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions_clamps_negative_limit(client):
    """limit=-1 means 'no limit' in SQLite — it must be clamped to >=1."""
    with mock.patch.object(HermesDB, "list_sessions", return_value=[]) as m:
        await client.get("/api/sessions", params={"limit": -1, "offset": -5})
    _, kwargs = m.call_args
    assert kwargs["limit"] == 1
    assert kwargs["offset"] == 0


@pytest.mark.asyncio
async def test_list_sessions_clamps_huge_limit(client):
    with mock.patch.object(HermesDB, "list_sessions", return_value=[]) as m:
        await client.get("/api/sessions", params={"limit": 10_000_000})
    _, kwargs = m.call_args
    assert kwargs["limit"] == 500


@pytest.mark.asyncio
async def test_get_activity_clamps_limit(client):
    with mock.patch.object(HermesDB, "get_messages", return_value=[]) as m:
        await client.get("/api/sessions/abc/activity", params={"limit": -5})
    _, kwargs = m.call_args
    assert kwargs["limit"] == 1


@pytest.mark.asyncio
async def test_get_activity_allows_large_limit(client):
    """Desk feed is effectively unlimited — a large limit is NOT clamped to 5000."""
    with mock.patch.object(HermesDB, "get_messages", return_value=[]) as m:
        await client.get("/api/sessions/abc/activity", params={"limit": 50000})
    _, kwargs = m.call_args
    assert kwargs["limit"] == 50000


@pytest.mark.asyncio
async def test_get_activity_tail_flag(client):
    with mock.patch.object(HermesDB, "get_messages", return_value=[]) as m:
        await client.get("/api/sessions/abc/activity", params={"limit": 100, "tail": "1"})
    _, kwargs = m.call_args
    assert kwargs["limit"] == 100
    assert kwargs["tail"] is True


# ── SPA catch-all returns 404 for unknown API paths ──────────────────────────


@pytest.mark.asyncio
async def test_unknown_api_path_is_404_not_spa_shell(client):
    """A typo'd/removed API route must surface as 404, not a 200 SPA shell."""
    r = await client.get("/api/this-route-does-not-exist")
    assert r.status_code == 404
    assert "<html" not in r.text.lower()


@pytest.mark.asyncio
async def test_unknown_ws_path_is_404(client):
    r = await client.get("/ws/bogus-thing")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unknown_non_api_route_serves_spa(client):
    """A non-API client route still falls through to the SPA shell (200), proving
    the api/ws 404 above is a deliberate discriminator, not a blanket 404."""
    r = await client.get("/some/client-side/route")
    assert r.status_code == 200
    assert "html" in r.headers.get("content-type", "").lower()


# ── FTS search tolerates punctuation ─────────────────────────────────────────


def _fts_db(tmp_path: Path) -> Path:
    """A minimal state.db with a populated FTS5 index, like Hermes' schema."""
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
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("s1", "2026-05-29T00:00:00+00:00", None, "workbench", "m", None, "", 1, 0, 0),
    )
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content) VALUES (?,?,?,?)",
        (1, "s1", "user", "hello world from the office"),
    )
    conn.execute("INSERT INTO messages_fts (rowid, content) VALUES (?,?)",
                 (1, "hello world from the office"))
    conn.commit()
    conn.close()
    return tmp_path


@pytest.mark.parametrize("query", ['hello"', "office*", "near:thing", "a-b", '"open quote'])
def test_search_sessions_tolerates_fts_punctuation(tmp_path, query):
    """FTS operator characters in the query must not raise — they fall back to a
    LIKE scan rather than 500-ing the search."""
    home = _fts_db(tmp_path)
    result = HermesDB(home).search_sessions(query)
    assert isinstance(result, list)  # no exception, always a list


def test_search_sessions_plain_query_matches(tmp_path):
    home = _fts_db(tmp_path)
    result = HermesDB(home).search_sessions("hello")
    assert [s.id for s in result] == ["s1"]
