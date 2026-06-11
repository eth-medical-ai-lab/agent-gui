from agent_gui.message_sanitize import strip_injected_prefix

_COMPACT = (
    "[Workspace: all tools run inside Docker — use /workspace/ paths.\n"
    "  - Workspace root: /workspace/]\n\n"
    "Do the thing"
)


def test_strip_compact_workspace():
    assert strip_injected_prefix(_COMPACT) == "Do the thing"


def test_strip_attachment_marker():
    text = "[Attached image: shot.png]\nExplain this"
    assert strip_injected_prefix(text) == "Explain this"
