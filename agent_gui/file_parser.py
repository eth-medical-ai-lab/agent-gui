"""Extract files touched during a session from its tool calls."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agent_gui.db import Message

FILE_WRITE_TOOLS = {
    "write_file", "create_file", "file_write", "str_replace_editor",
    "edit_file", "overwrite_file", "append_file",
}
FILE_READ_TOOLS = {
    "read_file", "file_read", "view_file",
}


@dataclass
class TouchedFile:
    path: str
    operation: str  # write | read
    tool_name: str


@dataclass
class FileNode:
    name: str
    path: str
    is_dir: bool
    children: list["FileNode"] = field(default_factory=list)
    operation: str = ""


def extract_touched_files(messages: list[Message]) -> list[TouchedFile]:
    touched: list[TouchedFile] = []
    seen: set[tuple[str, str]] = set()

    for msg in messages:
        if msg.role != "assistant":
            continue
        for block in msg.tool_calls:
            # DB format (Hermes flush): {"name": "tool", "arguments": "{...}"}
            # OpenAI format: {"type": "function", "function": {"name": "...", "arguments": "..."}}
            if "function" in block:
                fn = block.get("function", {})
                tool_name = fn.get("name", "")
                args_raw = fn.get("arguments", "{}")
            else:
                tool_name = block.get("name", "")
                args_raw = block.get("arguments", block.get("input", {}))

            if isinstance(args_raw, str):
                try:
                    tool_input = json.loads(args_raw)
                except Exception:
                    tool_input = {}
            elif isinstance(args_raw, dict):
                tool_input = args_raw
            else:
                tool_input = {}

            op = None
            if tool_name in FILE_WRITE_TOOLS:
                op = "write"
            elif tool_name in FILE_READ_TOOLS:
                op = "read"

            if op is None:
                continue

            path = (
                tool_input.get("path")
                or tool_input.get("file_path")
                or tool_input.get("filename")
                or tool_input.get("filepath")
            )
            if not path or not isinstance(path, str):
                continue

            key = (path, op)
            if key not in seen:
                seen.add(key)
                touched.append(TouchedFile(path=path, operation=op, tool_name=tool_name))

    return touched


def build_file_tree(files: list[TouchedFile]) -> list[FileNode]:
    """Build a nested tree structure from flat file paths."""
    root: dict = {}

    for f in files:
        parts = Path(f.path).parts
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        leaf = parts[-1] if parts else f.path
        node[leaf] = {"__file__": f}

    def _to_nodes(d: dict, current_path: str = "") -> list[FileNode]:
        nodes = []
        for name, value in sorted(d.items()):
            path = f"{current_path}/{name}".lstrip("/")
            if "__file__" in value:
                tf: TouchedFile = value["__file__"]
                nodes.append(FileNode(name=name, path=tf.path, is_dir=False, operation=tf.operation))
            else:
                children = _to_nodes(value, path)
                nodes.append(FileNode(name=name, path=path, is_dir=True, children=children))
        return nodes

    return _to_nodes(root)


def can_preview_file(path: str) -> str:
    """Return preview type: code | pdf | image | markdown | text | none"""
    ext = Path(path).suffix.lower()
    if ext in (".pdf",):
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        return "image"
    if ext in (".md", ".mdx"):
        return "markdown"
    if ext in (
        ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml",
        ".toml", ".sh", ".bash", ".zsh", ".rs", ".go", ".java", ".cpp",
        ".c", ".h", ".css", ".scss", ".html", ".xml", ".sql", ".r",
        ".rb", ".php", ".swift", ".kt", ".lua", ".vim", ".conf", ".ini",
        ".env", ".txt", ".log",
    ):
        return "code"
    return "none"
