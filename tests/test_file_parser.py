"""Unit tests for agent_gui.file_parser — all pure functions, no I/O."""
import pytest
from agent_gui.db import Message
from agent_gui.file_parser import (
    build_file_tree,
    can_preview_file,
    extract_touched_files,
)


def _msg(role: str, tool_calls: list[dict] | None = None) -> Message:
    return Message(
        id=1,
        session_id="s1",
        role=role,
        content=None,
        timestamp="2024-01-01T00:00:00",
        tool_calls=tool_calls or [],
    )


# ── extract_touched_files ──────────────────────────────────────────────────────


def test_write_file_tool_extracted():
    msg = _msg("assistant", [{"name": "write_file", "input": {"path": "/workspace/main.py"}}])
    result = extract_touched_files([msg])
    assert len(result) == 1
    assert result[0].path == "/workspace/main.py"
    assert result[0].operation == "write"


def test_read_file_tool_extracted():
    msg = _msg("assistant", [{"name": "read_file", "input": {"path": "/workspace/README.md"}}])
    result = extract_touched_files([msg])
    assert len(result) == 1
    assert result[0].operation == "read"


def test_non_file_tools_ignored():
    msg = _msg("assistant", [{"name": "bash", "input": {"command": "ls"}}])
    assert extract_touched_files([msg]) == []


def test_user_role_tool_calls_ignored():
    msg = _msg("user", [{"name": "write_file", "input": {"path": "/workspace/x.py"}}])
    assert extract_touched_files([msg]) == []


def test_deduplication():
    """Same path+op pair should appear only once even across multiple messages."""
    msgs = [
        _msg("assistant", [{"name": "write_file", "input": {"path": "/a.py"}}]),
        _msg("assistant", [{"name": "write_file", "input": {"path": "/a.py"}}]),
    ]
    assert len(extract_touched_files(msgs)) == 1


def test_read_and_write_same_path_are_separate():
    msgs = [
        _msg("assistant", [{"name": "write_file", "input": {"path": "/a.py"}}]),
        _msg("assistant", [{"name": "read_file", "input": {"path": "/a.py"}}]),
    ]
    result = extract_touched_files(msgs)
    assert len(result) == 2
    ops = {r.operation for r in result}
    assert ops == {"write", "read"}


def test_alternate_path_keys():
    """file_path / filename / filepath should all be recognized."""
    for key in ("file_path", "filename", "filepath"):
        msg = _msg("assistant", [{"name": "write_file", "input": {key: "/workspace/x.py"}}])
        result = extract_touched_files([msg])
        assert len(result) == 1, f"key={key!r} not recognized"


def test_missing_path_skipped():
    msg = _msg("assistant", [{"name": "write_file", "input": {}}])
    assert extract_touched_files([msg]) == []


# ── build_file_tree ────────────────────────────────────────────────────────────

from agent_gui.file_parser import TouchedFile


def _tf(path: str, op: str = "write") -> TouchedFile:
    return TouchedFile(path=path, operation=op, tool_name="write_file")


def test_flat_files():
    # Paths starting with '/' produce a root node named '/' with 'workspace' as a child dir.
    files = [_tf("/workspace/a.py"), _tf("/workspace/b.py")]
    tree = build_file_tree(files)
    # Top-level must have at least one node
    assert len(tree) >= 1
    # Flatten all names recursively and verify both leaf filenames appear
    def _all_names(nodes):
        for n in nodes:
            yield n.name
            yield from _all_names(n.children)
    names = set(_all_names(tree))
    assert "a.py" in names and "b.py" in names


def test_nested_dirs():
    files = [_tf("/workspace/src/main.py")]
    tree = build_file_tree(files)
    # Should produce at least one dir node containing src
    assert any(n.is_dir for n in tree)


def test_empty_returns_empty():
    assert build_file_tree([]) == []


def test_leaf_nodes_not_dirs():
    files = [_tf("/workspace/foo.py")]
    tree = build_file_tree(files)

    def _all_leaves(nodes):
        for n in nodes:
            if n.children:
                yield from _all_leaves(n.children)
            else:
                yield n

    for leaf in _all_leaves(tree):
        assert not leaf.is_dir


# ── can_preview_file ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    ("/workspace/main.py",    "code"),
    ("/workspace/README.md",  "markdown"),
    ("/workspace/data.json",  "code"),
    ("/workspace/chart.png",  "image"),
    ("/workspace/report.pdf", "pdf"),
    ("/workspace/binary.exe", "none"),
    ("/workspace/archive.zip","none"),
])
def test_can_preview_file(path, expected):
    assert can_preview_file(path) == expected
