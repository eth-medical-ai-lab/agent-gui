"""Strip GUI-injected prefixes from user messages before display or title inference."""
import re

# Prepended by server.py before the worker sees the first (or resumed) user turn.
# Current wording: "[Workspace: all tools run inside Docker …]"; older sessions used
# "[Workspace paths: …]" and other bracketed variants.
WORKSPACE_PREFIX_RE = re.compile(
    r"^\[(?:Workspace(?: paths)?:|Working directory:|Task instructions|Your workspace)"
    r"[\s\S]*?\]\s*",
    re.MULTILINE,
)

ATTACHMENT_MARKER_RE = re.compile(r"\[Attached (?:image|file): [^\]]*\]\s*")


def strip_injected_prefix(text: str) -> str:
    """Return user-visible text with workspace/attachment wrappers removed."""
    out = WORKSPACE_PREFIX_RE.sub("", text)
    out = ATTACHMENT_MARKER_RE.sub("", out)
    return out.strip()
