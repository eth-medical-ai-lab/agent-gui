"""Tests for POST /api/workspace/open."""
import sys
import unittest.mock as mock
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
import pytest_asyncio

from agent_gui.server import create_app


@pytest.fixture()
def app(tmp_path: Path):
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    return create_app(hermes_home=str(tmp_path), workspace_root=str(ws_root))


@pytest_asyncio.fixture()
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_open_missing_path_returns_400(client: AsyncClient):
    r = await client.post("/api/workspace/open", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_open_nonexistent_path_returns_404(client: AsyncClient):
    r = await client.post("/api/workspace/open", json={"path": "/nonexistent/path/that/cannot/exist"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_open_valid_directory_calls_popen(client: AsyncClient, tmp_path: Path):
    target = tmp_path / "my-workspace"
    target.mkdir()

    with mock.patch("subprocess.Popen") as mock_popen:
        r = await client.post("/api/workspace/open", json={"path": str(target)})

    assert r.status_code == 200
    assert r.json()["ok"] is True
    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert str(target) in cmd


@pytest.mark.asyncio
async def test_open_uses_platform_command(client: AsyncClient, tmp_path: Path):
    target = tmp_path / "ws"
    target.mkdir()

    # macOS
    with mock.patch("sys.platform", "darwin"), mock.patch("subprocess.Popen") as mp:
        await client.post("/api/workspace/open", json={"path": str(target)})
    assert mp.call_args[0][0][0] == "open"

    # Linux
    with mock.patch("sys.platform", "linux"), mock.patch("subprocess.Popen") as mp:
        await client.post("/api/workspace/open", json={"path": str(target)})
    assert mp.call_args[0][0][0] == "xdg-open"

    # Windows
    with mock.patch("sys.platform", "win32"), mock.patch("subprocess.Popen") as mp:
        await client.post("/api/workspace/open", json={"path": str(target)})
    assert mp.call_args[0][0][0] == "explorer"


@pytest.mark.asyncio
async def test_open_valid_file_also_works(client: AsyncClient, tmp_path: Path):
    """The endpoint accepts files, not just directories."""
    f = tmp_path / "TASK.md"
    f.write_text("# Task")

    with mock.patch("subprocess.Popen") as mock_popen:
        r = await client.post("/api/workspace/open", json={"path": str(f)})

    assert r.status_code == 200
    mock_popen.assert_called_once()


@pytest.mark.asyncio
async def test_open_path_outside_allowed_roots_is_forbidden(client: AsyncClient, tmp_path_factory):
    """An existing path outside the workspace root / hermes home must be rejected (403),
    not opened — guards against the arbitrary-path-open weakness."""
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("nope")

    with mock.patch("subprocess.Popen") as mock_popen:
        r = await client.post("/api/workspace/open", json={"path": str(outside)})

    assert r.status_code == 403
    mock_popen.assert_not_called()


# ── POST /api/workspace/open-terminal ────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_terminal_macos_opens_dir(client: AsyncClient, tmp_path: Path):
    target = tmp_path / "ws-term"
    target.mkdir()
    with mock.patch("sys.platform", "darwin"), mock.patch("subprocess.Popen") as mp:
        r = await client.post("/api/workspace/open-terminal", json={"path": str(target)})
    assert r.status_code == 200
    cmd = mp.call_args[0][0]
    assert cmd[:3] == ["open", "-a", "Terminal"]
    assert str(target) in cmd


@pytest.mark.asyncio
async def test_open_terminal_uses_parent_dir_for_file(client: AsyncClient, tmp_path: Path):
    f = tmp_path / "out.txt"
    f.write_text("x")
    with mock.patch("sys.platform", "darwin"), mock.patch("subprocess.Popen") as mp:
        r = await client.post("/api/workspace/open-terminal", json={"path": str(f)})
    assert r.status_code == 200
    cmd = mp.call_args[0][0]
    assert str(tmp_path) in cmd and str(f) not in cmd


@pytest.mark.asyncio
async def test_open_terminal_outside_roots_forbidden(client: AsyncClient, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-term")
    with mock.patch("subprocess.Popen") as mp:
        r = await client.post("/api/workspace/open-terminal", json={"path": str(outside)})
    assert r.status_code == 403
    mp.assert_not_called()
