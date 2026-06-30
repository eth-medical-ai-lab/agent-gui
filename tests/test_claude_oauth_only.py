"""OAuth-only auth for Claude Agent SDK desks.

A Claude SDK desk must run on the ``claude /login`` subscription (or
``CLAUDE_CODE_OAUTH_TOKEN``), never on an inherited ``ANTHROPIC_API_KEY`` — the
CLI's credential precedence is API key > OAuth token > /login, so a stale/wrong
key would silently shadow the login. The server scrubs the offending vars from a
desk's worker env (``server._scrub_claude_oauth_only``) and the worker scrubs its
own ``os.environ`` as a backstop (``claude_worker._force_oauth_only``). These tests
pin both layers and guard them against drifting apart.
"""
import os

from agent_gui import claude_worker
from agent_gui import server as srv

# The full set of credentials that outrank OAuth in the claude CLI precedence.
# Hard-coded here on purpose: adding/removing a scrubbed key is a deliberate change
# that should have to update this test too.
_EXPECTED = {
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
}


def test_scrub_lists_cover_api_key_and_stay_in_sync():
    # ANTHROPIC_API_KEY is the one that actually bites; it must be on both layers.
    assert "ANTHROPIC_API_KEY" in srv._CLAUDE_OAUTH_ONLY_SCRUB_KEYS
    assert "ANTHROPIC_API_KEY" in claude_worker.OAUTH_ONLY_SCRUB_KEYS
    # The server hands the worker its env, and the worker re-scrubs its own — if the
    # two lists diverge, one layer could leak a credential the other drops.
    assert set(srv._CLAUDE_OAUTH_ONLY_SCRUB_KEYS) == _EXPECTED
    assert tuple(srv._CLAUDE_OAUTH_ONLY_SCRUB_KEYS) == tuple(claude_worker.OAUTH_ONLY_SCRUB_KEYS)


def test_server_scrub_drops_creds_and_keeps_everything_else():
    env = {k: "secret" for k in _EXPECTED}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "oauth-keep"   # the credential we WANT to survive
    env["PATH"] = "/usr/bin"                         # unrelated var must be untouched
    srv._scrub_claude_oauth_only(env)
    assert not (_EXPECTED & env.keys())              # every outranking cred is gone
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-keep"
    assert env["PATH"] == "/usr/bin"


def test_server_scrub_is_safe_when_creds_absent():
    env = {"PATH": "/usr/bin"}
    srv._scrub_claude_oauth_only(env)               # missing keys must not raise
    assert env == {"PATH": "/usr/bin"}


def test_worker_force_oauth_only_mutates_given_mapping():
    env = {k: "secret" for k in _EXPECTED}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "keep"
    returned = claude_worker._force_oauth_only(env)
    assert returned is env                           # mutates and returns in place
    assert not (_EXPECTED & env.keys())
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep"


def test_worker_force_oauth_only_defaults_to_os_environ(monkeypatch):
    for k in _EXPECTED:
        monkeypatch.setenv(k, "secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "keep")
    claude_worker._force_oauth_only()               # no arg → scrubs os.environ
    for k in _EXPECTED:
        assert k not in os.environ
    assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == "keep"
