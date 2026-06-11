import { useState } from "react";
import type { FileNode, FilePreviewData } from "../types";
import { api } from "../api/client";

interface Props {
  nodes: FileNode[];
  onPreview: (data: FilePreviewData) => void;
}

const EXT_ICONS: Record<string, string> = {
  py: "🐍", ts: "📘", tsx: "⚛️", js: "📜", jsx: "⚛️",
  json: "📋", yaml: "📋", yml: "📋", toml: "📋",
  md: "📝", mdx: "📝", txt: "📄", pdf: "📕",
  png: "🖼️", jpg: "🖼️", jpeg: "🖼️", svg: "🎨",
  sh: "⚡", bash: "⚡", zsh: "⚡",
  rs: "🦀", go: "🐹", cpp: "⚙️", c: "⚙️",
  css: "🎨", scss: "🎨", html: "🌐",
  sql: "🗃️", db: "🗃️",
};

function extIcon(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return EXT_ICONS[ext] || "📄";
}

function FileItem({ node, depth, onPreview }: {
  node: FileNode;
  depth: number;
  onPreview: (d: FilePreviewData) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  const [loading, setLoading] = useState(false);

  async function handlePreview() {
    if (node.is_dir || !node.preview_type || node.preview_type === "none") return;
    // Images and PDFs: FilePreview renders them via URL — no JSON API call needed.
    if (node.preview_type === "image") {
      onPreview({ type: "image", path: node.path, name: node.name });
      return;
    }
    if (node.preview_type === "pdf") {
      onPreview({ type: "pdf", path: node.path, name: node.name });
      return;
    }
    setLoading(true);
    try {
      const data = await api.file.preview(node.path);
      onPreview(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  const indent = depth * 14;
  const canPreview = !node.is_dir && node.preview_type && node.preview_type !== "none";

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "3px 8px 3px 0",
          paddingLeft: indent + 8,
          borderRadius: 4,
          cursor: node.is_dir ? "pointer" : canPreview ? "pointer" : "default",
          fontSize: 12,
          color: canPreview ? "var(--accent2)" : "var(--text-dim)",
          transition: "background 0.1s",
        }}
        onMouseEnter={(e) => {
          if (canPreview) (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.06)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.background = "transparent";
        }}
        onClick={node.is_dir ? () => setOpen(!open) : handlePreview}
      >
        <span style={{ fontSize: 10, opacity: 0.5, width: 10 }}>
          {node.is_dir ? (open ? "▾" : "▸") : ""}
        </span>
        <span>{node.is_dir ? (open ? "📂" : "📁") : extIcon(node.name)}</span>
        <span style={{ marginLeft: 4, flex: 1 }}>{node.name}</span>
        {node.operation === "write" && !node.is_dir && (
          <span style={{ fontSize: 9, color: "var(--green)", opacity: 0.7 }}>✎</span>
        )}
        {loading && <span style={{ fontSize: 9 }}>…</span>}
      </div>
      {node.is_dir && open && node.children && (
        <div>
          {node.children.map((child) => (
            <FileItem key={child.path} node={child} depth={depth + 1} onPreview={onPreview} />
          ))}
        </div>
      )}
    </div>
  );
}

export function FileExplorer({ nodes, onPreview }: Props) {
  if (!nodes.length) {
    return (
      <div style={{ padding: "12px 8px", color: "var(--text-dim)", fontSize: 12 }}>
        No files touched in this session
      </div>
    );
  }

  return (
    <div style={{ padding: "4px 0" }}>
      {nodes.map((node) => (
        <FileItem key={node.path} node={node} depth={0} onPreview={onPreview} />
      ))}
    </div>
  );
}
