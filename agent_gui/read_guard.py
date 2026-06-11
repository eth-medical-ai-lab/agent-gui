"""Best-effort host-side read/search confinement for the Hermes worker.

**Why this exists.** The Docker sandbox only contains the *shell* tools
(`terminal`/`execute_code`/`process`). Hermes' other workspace tools —
`read_file` and `search_files` — run on the *host*, inside the worker process,
as the launching OS user. Hermes confines *writes* to `HERMES_WRITE_SAFE_ROOT`
(the GUI sets it per desk), but it has **no equivalent read-safe-root**: a
prompt-injected or misbehaving agent can `read_file ~/.ssh/id_rsa` or
`search_files` across the whole home directory and exfiltrate the result through
its normal output. See SECURITY_NOTES.md ("File-tool confinement").

This module is the interim mitigation that note proposes: a host-side guard that
rejects `read_file` / `search_files` targets outside `HERMES_WORKDIR` (the
session workspace), mirroring the write confinement so reads, writes, and search
all share one boundary.

**This is defense in depth, not a hard boundary.** The agent still has a real
shell in the sandbox, and Hermes' own `agent/file_safety.py` is explicit that
file-tool denials are not a security boundary. The goal is narrow: stop the
*host-side* read tools from trivially reading arbitrary host files, and surface
an auditable denial when something tries.

Design: `path_within()` is a pure, unit-tested predicate. `install()` patches the
two live Hermes call sites (`read_file_tool` consults `get_read_block_error`;
`_handle_search_files` calls `search_tool` — both by module-global name, so a
module-attribute patch takes effect at call time). `install()` is best-effort:
it reaches into a separately-versioned dependency, so every hook is wrapped in
try/except and logged. If Hermes' layout changes the guard degrades to a no-op
rather than crashing the worker — the write confinement and sandboxed shell
remain in force regardless.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable


def path_within(path: str, root: str) -> bool:
    """True if `path` resolves to `root` or a descendant.

    Symlinks and `..` are resolved first (`Path.resolve()`), so neither a symlink
    inside the workspace pointing out nor a `<root>/../secret` traversal slips
    through. Any resolution error (e.g. a path that can't be realised) is treated
    as "not within" — fail closed.
    """
    try:
        resolved = Path(path).expanduser().resolve()
        root_resolved = Path(root).expanduser().resolve()
    except Exception:
        return False
    if resolved == root_resolved:
        return True
    try:
        resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


def path_within_any(path: str, roots: list[str]) -> bool:
    """True if `path` resolves into *any* of `roots`. Fail-closed on empty roots."""
    return any(path_within(path, r) for r in roots)


def _denied_msg(path: str, roots: list[str]) -> str:
    where = roots[0] if roots else "?"
    extra = " (or the shared team repo)" if len(roots) > 1 else ""
    return (
        f"Access denied: '{path}' is outside the agent's workspace ({where}){extra}. "
        "read_file and search_files are confined to the workspace; use the "
        "terminal tool if you need sandboxed shell access."
    )


def install(
    workdir: str,
    extra_roots: tuple[str, ...] | list[str] = (),
    log: Callable[[str], None] = lambda _m: None,
) -> None:
    """Confine Hermes' host-side read/search tools to `workdir` (+ `extra_roots`).

    `workdir` is the per-desk session workspace and stays the *primary* root —
    relative search paths resolve against it. `extra_roots` are additional
    explicitly-shared directories the agent is allowed to read/search, e.g. the
    team File Repo (`HERMES_GUI_TEAM_REPO`). This mirrors `_patch_team_repo_writes`,
    which adds the same repo as an extra `HERMES_WRITE_SAFE_ROOT`: reads, writes,
    and search then share one boundary of {desk workspace, this team's repo}.
    Everything else — `~/.ssh`, arbitrary host paths, *other* teams' repos — stays
    denied, so widening to the shared repo is not a confinement regression.

    Must run after `import tools.file_tools` is possible (i.e. inside the worker,
    after the Hermes venv is importable) and before the tools are invoked. Safe to
    call once at worker startup. Each hook is independent and best-effort.
    """
    primary = str(Path(workdir).expanduser().resolve())
    roots = [primary]
    for r in extra_roots:
        if not r:
            continue
        try:
            resolved = str(Path(r).expanduser().resolve())
        except Exception:  # noqa: BLE001 — a bad extra root must not break the guard
            continue
        if resolved not in roots:
            roots.append(resolved)
    scope = "workspace" + (" + team repo" if len(roots) > 1 else "")

    # ── read_file: compose with Hermes' own read-block denylist ──────────────
    # read_file_tool() calls get_read_block_error(resolved_path) and treats a
    # non-None return as a denial. We wrap it: keep Hermes' denylist, then add
    # the workspace-containment check.
    try:
        import tools.file_tools as ft  # type: ignore[import-not-found]

        _orig_block = ft.get_read_block_error

        def _guarded_block(path, _orig=_orig_block, _roots=roots):
            existing = _orig(path)
            if existing:
                return existing
            if not path_within_any(str(path), _roots):
                return _denied_msg(str(path), _roots)
            return None

        ft.get_read_block_error = _guarded_block
        log(f"[read-guard] read_file confined to {scope}")
    except Exception as exc:  # noqa: BLE001 — best-effort, must never crash the worker
        log(f"[read-guard] read_file hook skipped: {exc}")

    # ── search_files: validate the search root before delegating ─────────────
    # _handle_search_files() calls search_tool(path=...). The `path` arg is the
    # escape vector (absolute, or ../ out of the workspace), so we reject it
    # before any filesystem walk happens. Relative paths resolve against the
    # primary workspace (the worker's cwd).
    try:
        import tools.file_tools as ft  # type: ignore[import-not-found]

        _orig_search = ft.search_tool

        def _guarded_search(pattern, target="content", path=".",
                            *args, _orig=_orig_search, _roots=roots,
                            _base=primary, **kwargs):
            candidate = path if os.path.isabs(path) else os.path.join(_base, path)
            if not path_within_any(candidate, _roots):
                return json.dumps({"error": _denied_msg(str(path), _roots)})
            return _orig(pattern, target, path, *args, **kwargs)

        ft.search_tool = _guarded_search
        log(f"[read-guard] search_files confined to {scope}")
    except Exception as exc:  # noqa: BLE001 — best-effort, must never crash the worker
        log(f"[read-guard] search_files hook skipped: {exc}")
