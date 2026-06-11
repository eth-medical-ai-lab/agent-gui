"""_patch_flush_watermark: turns silently never persisting after an interrupt.

Reproduces an interrupted/failed turn leaving the desk db ending in a dangling
user row; on the next turn Hermes crash-persists the new user message (watermark
past it), then repair_message_sequence merges the consecutive user messages
IN PLACE, shrinking `messages` below the watermark — so the final flush writes
nothing and the finished reply never reaches the db.
"""
import sys

# Preserve stdout before import so pytest's own capture still works.
_real_stdout = sys.stdout
from agent_gui.hermes_worker import _patch_flush_watermark  # noqa: E402
sys.stdout = _real_stdout


class FakeHermesAgent:
    """Mimics run_agent.py's watermark-based _flush_messages_to_session_db."""

    def __init__(self):
        self._last_flushed_db_idx = 0
        self.db_rows: list[dict] = []

    def _flush_messages_to_session_db(self, messages, conversation_history=None):
        start_idx = len(conversation_history) if conversation_history else 0
        flush_from = max(start_idx, self._last_flushed_db_idx)
        for msg in messages[flush_from:]:
            self.db_rows.append(msg)
        self._last_flushed_db_idx = len(messages)


def _repair_merge_consecutive_users(messages):
    """Mimics repair_message_sequence pass 2: in-place merge of user runs."""
    merged = []
    for m in messages:
        if merged and m["role"] == "user" and merged[-1]["role"] == "user":
            merged[-1]["content"] += "\n\n" + m["content"]
            continue
        merged.append(m)
    messages[:] = merged


def _msg(role, content):
    return {"role": role, "content": content}


def _run_turn(agent, history, user_text, reply_text):
    """One Hermes turn: prologue crash-persist → repair → reply → final persist."""
    messages = list(history)              # turn_context: same dict objects
    messages.append(_msg("user", user_text))
    agent._flush_messages_to_session_db(messages, history)   # prologue persist
    _repair_merge_consecutive_users(messages)                # pre-API repair
    reply = _msg("assistant", reply_text)
    messages.append(reply)
    agent._flush_messages_to_session_db(messages, history)   # end-of-turn persist
    return messages


def test_unpatched_agent_loses_reply_after_dangling_user():
    """Control: documents the upstream Hermes bug this patch works around."""
    agent = FakeHermesAgent()
    history = [_msg("user", "u1"), _msg("assistant", "a2"), _msg("user", "u3")]
    _run_turn(agent, history, "u4", "the reply")
    contents = [m["content"] for m in agent.db_rows]
    assert "the reply" not in contents          # the bug
    assert contents == ["u4"]


def test_patched_agent_persists_reply_after_dangling_user():
    agent = FakeHermesAgent()
    _patch_flush_watermark(agent)
    # Desk db tail after an interrupted turn: dangling user row u3.
    history = [_msg("user", "u1"), _msg("assistant", "a2"), _msg("user", "u3")]
    _run_turn(agent, history, "u4", "the reply")
    contents = [m["content"] for m in agent.db_rows]
    assert contents == ["u4", "the reply"]      # nothing lost, nothing duplicated


def test_patched_agent_survives_multiple_dangling_users():
    """db tail u3,u4 (two dangling users, like the broken desk): the repair
    collapses three user messages at once and len(history) exceeds the repaired
    list — the floor that re-breaks the unpatched flush."""
    agent = FakeHermesAgent()
    _patch_flush_watermark(agent)
    history = [_msg("user", "u1"), _msg("assistant", "a2"),
               _msg("user", "u3"), _msg("user", "u4")]
    _run_turn(agent, history, "u6", "healed reply")
    contents = [m["content"] for m in agent.db_rows]
    assert contents == ["u6", "healed reply"]


def test_patched_agent_warm_worker_multi_turn_no_duplicates():
    """A persistent worker reloads history (fresh dicts) each turn; later clean
    turns must keep flushing only their own messages."""
    agent = FakeHermesAgent()
    _patch_flush_watermark(agent)
    history = [_msg("user", "u1"), _msg("assistant", "a2"), _msg("user", "u3")]
    _run_turn(agent, history, "u4", "reply A")
    # Turn B: history reloaded from db as NEW dict objects (no dangling tail now).
    history_b = [_msg(m["role"], m["content"]) for m in history] + [
        _msg("user", "u4"), _msg("assistant", "reply A")]
    _run_turn(agent, history_b, "u5", "reply B")
    contents = [m["content"] for m in agent.db_rows]
    assert contents == ["u4", "reply A", "u5", "reply B"]
