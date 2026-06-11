"""Red-team tests for the host-side path-confinement boundary (`_safe_path`).

`_safe_path()` guards the three endpoints that turn a caller-supplied path into a
host filesystem read / open:

    GET  /api/file/preview?path=...     (read file contents)
    GET  /api/file/tree?root=...        (list a directory)
    POST /api/workspace/open            (open in Finder/terminal)

The allowed roots are the workspace root and the Hermes home dir. The interesting
attacks are the ones that *look* like an allowed path but resolve elsewhere:

  * a symlink placed inside the workspace whose target is outside every root,
  * `..` traversal that climbs out of the workspace,
  * an absolute path straight at a host secret.

The fix relies on `Path(raw).resolve()` following symlinks *before* the
containment check. These tests pin that behaviour so a future "optimisation"
that drops the resolve (or checks the literal path) can't silently re-open the
hole. Existing happy-path / basic-rejection coverage lives in test_server.py and
test_workspace_open.py; this file is the adversarial layer.
"""
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent_gui.server import create_app


@pytest.fixture()
def roots(tmp_path):
    """(hermes_home, workspace_root) with the workspace under its own dir.

    Unlike test_server.py (where home == tmp_path, so the workspace sits *inside*
    home), here home and the workspace are siblings and `secrets/` is a third
    sibling outside BOTH roots — a realistic escape target.
    """
    home = tmp_path / "hermes_home"
    ws = tmp_path / "workspace"
    secrets = tmp_path / "secrets"
    for d in (home, ws, secrets):
        d.mkdir()
    (secrets / "id_rsa").write_text("PRIVATE KEY\n")
    return home, ws, secrets


@pytest.fixture()
def app(roots):
    home, ws, _ = roots
    return create_app(hermes_home=str(home), workspace_root=str(ws))


@pytest_asyncio.fixture()
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Symlink escape ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_symlink_out_of_workspace_is_forbidden(client, roots):
    """A symlink that lives inside the workspace but points at a host secret must
    not leak the secret — `.resolve()` follows the link, containment then fails."""
    home, ws, secrets = roots
    link = ws / "innocent.txt"
    link.symlink_to(secrets / "id_rsa")

    r = await client.get("/api/file/preview", params={"path": str(link)})

    assert r.status_code == 403
    assert "PRIVATE KEY" not in r.text


@pytest.mark.asyncio
async def test_tree_symlinked_dir_out_of_workspace_is_forbidden(client, roots):
    """A symlinked directory inside the workspace pointing outside is rejected."""
    home, ws, secrets = roots
    link = ws / "shared"
    link.symlink_to(secrets, target_is_directory=True)

    r = await client.get("/api/file/tree", params={"root": str(link)})

    assert r.status_code == 403
    assert "id_rsa" not in r.text


@pytest.mark.asyncio
async def test_open_symlink_out_of_workspace_is_forbidden(client, roots):
    """workspace/open must refuse a symlink that escapes the allowed roots."""
    home, ws, secrets = roots
    link = ws / "shortcut"
    link.symlink_to(secrets, target_is_directory=True)

    r = await client.post("/api/workspace/open", json={"path": str(link)})

    assert r.status_code == 403


# ── `..` traversal ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_dotdot_traversal_is_forbidden(client, roots):
    """`<ws>/../secrets/id_rsa` resolves outside the roots → 403."""
    home, ws, secrets = roots
    sneaky = ws / ".." / "secrets" / "id_rsa"

    r = await client.get("/api/file/preview", params={"path": str(sneaky)})

    assert r.status_code == 403
    assert "PRIVATE KEY" not in r.text


@pytest.mark.asyncio
async def test_preview_absolute_secret_path_is_forbidden(client, roots):
    """A plain absolute path at a host secret is rejected outright."""
    home, ws, secrets = roots

    r = await client.get("/api/file/preview", params={"path": str(secrets / "id_rsa")})

    assert r.status_code == 403


# ── The boundary must not over-block legitimate access ───────────────────────


@pytest.mark.asyncio
async def test_symlink_within_workspace_is_allowed(client, roots):
    """A symlink whose target is *also* inside the workspace stays allowed — the
    guard blocks escapes, not indirection per se."""
    home, ws, secrets = roots
    real = ws / "real.txt"
    real.write_text("hello from inside\n")
    link = ws / "alias.txt"
    link.symlink_to(real)

    r = await client.get("/api/file/preview", params={"path": str(link)})

    assert r.status_code == 200
    assert "hello from inside" in r.json()["content"]


@pytest.mark.asyncio
async def test_file_under_hermes_home_is_allowed(client, roots):
    """Hermes home is the second allowed root (config/memory previews rely on it)."""
    home, ws, secrets = roots
    cfg = home / "notes.log"
    cfg.write_text("log line\n")

    r = await client.get("/api/file/preview", params={"path": str(cfg)})

    assert r.status_code == 200
    assert "log line" in r.json()["content"]
