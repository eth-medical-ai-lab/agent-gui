"""Tests for scripts/count_tokens.py (token estimate + README badge update)."""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "count_tokens.py"
_spec = importlib.util.spec_from_file_location("count_tokens", _SCRIPT)
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)  # type: ignore[union-attr]


def test_human_formatting():
    assert ct.human(500) == "500"
    assert ct.human(1000) == "1k"
    assert ct.human(121086) == "121k"


def test_update_badge_rewrites_count(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("x\n![Codebase](https://img.shields.io/badge/codebase-~62k_tokens-F59E0B)\ny\n")
    assert ct.update_badge(readme, 121000) is True
    assert "codebase-~121k_tokens" in readme.read_text()


def test_update_badge_noop_when_absent(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("no badge here\n")
    assert ct.update_badge(readme, 121000) is False


def test_tracked_source_files_nonempty_and_filtered():
    files = ct.tracked_source_files()
    assert len(files) > 10
    assert all(f.suffix.lower() in ct.SOURCE_EXTS for f in files)
    assert not any("node_modules" in str(f) for f in files)
