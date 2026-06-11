"""Unit tests for hermes_worker._process_think_content.

The function parses streaming <think>…</think> tokens and routes them to
separate token vs. thinking callbacks. It must handle split boundaries that
arrive across multiple chunks.

We import the function directly — the module redirects sys.stdout at import
time but that's harmless for pytest (captured by pytest's own capture).
"""
import sys

# Preserve stdout before import so pytest's own capture still works.
_real_stdout = sys.stdout
from agent_gui.hermes_worker import _process_think_content  # noqa: E402
sys.stdout = _real_stdout


def _run(chunks: list[str]):
    """Helper: feed chunks through _process_think_content and collect results."""
    tokens: list[str] = []
    thoughts: list[str] = []
    in_thinking = False
    partial = ""
    for chunk in chunks:
        in_thinking, partial = _process_think_content(
            chunk,
            in_thinking=in_thinking,
            partial=partial,
            emit_token=tokens.append,
            thinking_cb=thoughts.append,
        )
    return tokens, thoughts, in_thinking, partial


# ── Basic routing ──────────────────────────────────────────────────────────────


def test_plain_text_emits_tokens():
    tokens, thoughts, _, _ = _run(["hello world"])
    assert "".join(tokens) == "hello world"
    assert thoughts == []


def test_think_block_routes_to_thinking():
    tokens, thoughts, _, _ = _run(["<think>reasoning here</think>"])
    assert thoughts == ["reasoning here"]
    assert tokens == []


def test_text_before_and_after_think():
    tokens, thoughts, _, _ = _run(["before<think>middle</think>after"])
    assert "".join(tokens) == "beforeafter"
    assert "".join(thoughts) == "middle"


def test_think_only_no_close_stays_in_thinking():
    tokens, thoughts, in_thinking, partial = _run(["<think>still thinking"])
    assert in_thinking is True
    assert "".join(thoughts) == "still thinking"


# ── Split boundary handling ────────────────────────────────────────────────────


def test_think_open_tag_split_across_chunks():
    """<think> split as '<thi' + 'nk>text</think>' must not emit '<thi' as a token."""
    tokens, thoughts, _, _ = _run(["<thi", "nk>text</think>"])
    assert "thi" not in "".join(tokens)
    assert "".join(thoughts) == "text"


def test_think_open_tag_split_single_char():
    tokens, thoughts, _, _ = _run(["prefix<", "think>inside</think>"])
    assert "".join(tokens) == "prefix"
    assert "".join(thoughts) == "inside"


def test_close_tag_inside_thinking_completes():
    tokens, thoughts, in_thinking, _ = _run(["<think>part1", "</think>after"])
    assert in_thinking is False
    assert "".join(thoughts) == "part1"
    assert "".join(tokens) == "after"


def test_multiple_think_blocks():
    tokens, thoughts, _, _ = _run(["<think>a</think>x<think>b</think>y"])
    assert "".join(tokens) == "xy"
    assert "".join(thoughts) == "ab"


def test_empty_think_block():
    tokens, thoughts, _, _ = _run(["<think></think>"])
    assert thoughts == []
    assert tokens == []


def test_no_tags_multi_chunk():
    tokens, thoughts, _, _ = _run(["hel", "lo ", "world"])
    assert "".join(tokens) == "hello world"
    assert thoughts == []


def test_partial_open_tag_at_end_held():
    """A trailing '<thi' with no follow-up shouldn't be emitted as a token yet."""
    tokens, thoughts, in_thinking, partial = _run(["prefix<thi"])
    # The partial '<thi' should be held in `partial`, not emitted as a token
    assert "<thi" not in "".join(tokens)
    assert partial == "<thi"
    assert in_thinking is False


def test_partial_held_then_resolved_as_normal_text():
    """'<abc' looks like a partial tag start but '<abc>' is not <think>, so emit it."""
    tokens, thoughts, _, _ = _run(["prefix<abc", ">suffix"])
    full = "".join(tokens)
    # '<abc>' is not a <think> tag — should be emitted as plain text
    assert "prefix" in full
    assert "suffix" in full
