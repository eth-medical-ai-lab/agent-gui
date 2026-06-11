/**
 * TeamFileRepo — a per-team shared "File Repo" below the manager in each team column.
 *
 * Drop files or whole folders from the local machine onto it; the bytes are COPIED
 * into the team's server-side cache (~/.hermes/gui_team_repos/<team_id>/) and then
 * synced into every desk's workspace under team_files/, so every agent on the team
 * can read them. The user's original files on disk are never referenced directly.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { FileNode, FilePreviewData } from "../types";
import { api } from "../api/client";
import type { SceneFloorChrome } from "../sceneFloorChrome";

interface Props {
  teamId: string;
  accentColor: string;
  chrome: SceneFloorChrome;
  onPreview: (data: FilePreviewData) => void;
  tileWidth?: number;
  tileMinHeight?: number;
}

interface GatheredFile { relPath: string; file: File; }

/** Read a base64 data-URL for a File. */
function readDataUrl(file: File): Promise<string> {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = (e) => res(e.target!.result as string);
    fr.onerror = () => rej(fr.error);
    fr.readAsDataURL(file);
  });
}

/** Drain a directory reader (it returns entries in batches). */
function readAllEntries(reader: { readEntries: (cb: (e: FileSystemEntry[]) => void, err: (e: unknown) => void) => void }): Promise<FileSystemEntry[]> {
  return new Promise((resolve) => {
    const all: FileSystemEntry[] = [];
    const step = () => reader.readEntries((batch) => {
      if (!batch.length) { resolve(all); return; }
      all.push(...batch);
      step();
    }, () => resolve(all));
    step();
  });
}

/** Recursively collect files (with team-repo-relative paths) from a dropped entry. */
async function gatherEntry(entry: FileSystemEntry, prefix: string, out: GatheredFile[]): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const e = entry as any;
  if (entry.isFile) {
    const file: File = await new Promise((res, rej) => e.file(res, rej));
    out.push({ relPath: prefix + entry.name, file });
  } else if (entry.isDirectory) {
    const children = await readAllEntries(e.createReader());
    for (const child of children) {
      await gatherEntry(child, `${prefix}${entry.name}/`, out);
    }
  }
}

function FileTree({ nodes, root, depth, onPreview, onDelete }: {
  nodes: FileNode[];
  root: string;
  depth: number;
  onPreview: (n: FileNode) => void;
  onDelete: (n: FileNode) => void;
}) {
  return (
    <>
      {nodes.map((n) => {
        const rel = root && n.path.startsWith(root) ? n.path.slice(root.length).replace(/^\//, "") : n.name;
        return (
          <div key={n.path}>
            <div
              style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "2px 4px", paddingLeft: 4 + depth * 12,
                fontSize: 11, color: "var(--text)", borderRadius: 4,
                cursor: n.is_dir ? "default" : (n.preview_type ? "pointer" : "default"),
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.05)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <span style={{ flexShrink: 0 }}>{n.is_dir ? "📁" : "📄"}</span>
              <span
                onClick={() => { if (!n.is_dir && n.preview_type) onPreview(n); }}
                title={n.is_dir ? rel : (n.preview_type ? "Preview" : rel)}
                style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              >
                {n.name}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(n); }}
                title="Remove from team repo"
                style={{
                  flexShrink: 0, width: 16, height: 16, borderRadius: 3, padding: 0,
                  background: "transparent", border: "none", color: "var(--text-dim)",
                  fontSize: 11, lineHeight: 1, cursor: "pointer",
                }}
              >×</button>
            </div>
            {n.is_dir && n.children && n.children.length > 0 && (
              <FileTree nodes={n.children} root={root} depth={depth + 1} onPreview={onPreview} onDelete={onDelete} />
            )}
          </div>
        );
      })}
    </>
  );
}

export function TeamFileRepo({
  teamId, accentColor, chrome, onPreview,
  tileWidth = 108, tileMinHeight = 78,
}: Props) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<FileNode[]>([]);
  const [root, setRoot] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await api.teams.files(teamId);
      setFiles(r.files);
      setRoot(r.root);
    } catch { /* repo may not exist yet */ }
    finally { setRefreshing(false); }
  }, [teamId]);

  useEffect(() => { refresh(); }, [refresh]);

  // Re-list when the panel opens so agent writes into team_files/ show up.
  useEffect(() => {
    if (open) refresh();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Count only top-level entries for the badge.
  const count = files.length;

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const items = Array.from(e.dataTransfer.items || []);
    const gathered: GatheredFile[] = [];
    const entries = items
      .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
      .filter(Boolean) as FileSystemEntry[];
    if (entries.length) {
      for (const entry of entries) await gatherEntry(entry, "", gathered);
    } else {
      // Fallback: flat file list (no directory support in this browser).
      for (const f of Array.from(e.dataTransfer.files)) gathered.push({ relPath: f.name, file: f });
    }
    if (!gathered.length) return;
    setOpen(true);
    let done = 0;
    for (const g of gathered) {
      setBusy(`Copying ${++done}/${gathered.length}: ${g.relPath}`);
      try {
        const dataUrl = await readDataUrl(g.file);
        await api.teams.upload(teamId, g.relPath, dataUrl);
      } catch (err) {
        console.warn("team file upload failed:", g.relPath, err);
      }
    }
    setBusy(null);
    refresh();
  }

  async function handleDelete(n: FileNode) {
    const rel = root && n.path.startsWith(root) ? n.path.slice(root.length).replace(/^\//, "") : n.name;
    if (!window.confirm(`Remove "${rel}" from the team file repo?`)) return;
    try {
      await api.teams.delete(teamId, rel);
      refresh();
    } catch (err) {
      console.warn("team file delete failed:", err);
    }
  }

  async function handlePreview(n: FileNode) {
    try {
      const data = await api.file.preview(n.path);
      onPreview(data);
    } catch { /* unsupported type */ }
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragOver(false); }}
      onDrop={handleDrop}
      style={{
        position: "relative", width: tileWidth, flexShrink: 0,
        userSelect: "none",
      }}
    >
      {/* Filing-cabinet button */}
      <div
        onClick={() => setOpen((o) => !o)}
        title="Team File Repo — drop files or folders here to share with every agent on the team"
        style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          gap: 3, minHeight: tileMinHeight,
          padding: "8px 6px", borderRadius: 8, cursor: "pointer",
          background: dragOver ? "rgba(100,160,255,0.16)" : chrome.controlBg,
          border: `1px ${dragOver ? "dashed" : "solid"} ${dragOver ? chrome.labelAccent : chrome.controlBorder}`,
          transition: "background 0.15s, border-color 0.15s",
        }}
      >
        <svg width="36" height="36" viewBox="0 0 34 34">
          <rect x="5" y="3" width="24" height="28" rx="2" fill="#6b4c2a" />
          <rect x="5" y="3" width="24" height="28" rx="2" fill="none" stroke={accentColor} strokeOpacity="0.5" />
          <rect x="7" y="6" width="20" height="7" rx="1" fill="#8a6038" />
          <rect x="7" y="15" width="20" height="7" rx="1" fill="#8a6038" />
          <rect x="7" y="24" width="20" height="6" rx="1" fill="#8a6038" />
          <rect x="14" y="8" width="6" height="2" rx="1" fill="#d4c890" />
          <rect x="14" y="17" width="6" height="2" rx="1" fill="#d4c890" />
        </svg>
        <div style={{
          fontSize: 9, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase",
          color: dragOver ? chrome.labelAccent : chrome.labelDim, textAlign: "center", lineHeight: 1.2,
        }}>
          File Repo{count > 0 ? ` · ${count}` : ""}
        </div>
      </div>

      {/* Expanded panel */}
      {open && (
        <div
          ref={panelRef}
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute", top: 0, left: tileWidth + 8, width: 268, maxHeight: 320,
            background: "var(--bg2)", border: "1px solid var(--card-border)",
            borderRadius: 8, boxShadow: "0 8px 28px rgba(0,0,0,0.5)", zIndex: 40,
            display: "flex", flexDirection: "column", overflow: "hidden",
          }}
        >
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "8px 10px", borderBottom: "1px solid var(--card-border)", flexShrink: 0,
          }}>
            <span style={{
              fontSize: 12, fontWeight: 600, color: "var(--text)",
              flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              📁 Team File Repo
            </span>
            <button
              type="button"
              onClick={() => refresh()}
              disabled={refreshing || !!busy}
              title="Refresh file list (e.g. after agents add files to team_files/)"
              style={{
                flexShrink: 0, fontSize: 10, fontWeight: 600, padding: "3px 8px", borderRadius: 4,
                background: "rgba(255,255,255,0.08)", border: "1px solid var(--accent2)",
                color: refreshing ? "var(--text-dim)" : "var(--accent2)",
                cursor: refreshing || busy ? "default" : "pointer",
                opacity: refreshing || busy ? 0.6 : 1,
              }}
            >
              {refreshing ? "…" : "↻ Update"}
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              style={{
                flexShrink: 0, width: 18, height: 18, borderRadius: "50%", padding: 0,
                background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)",
                color: "var(--text-dim)", fontSize: 11, cursor: "pointer",
              }}
            >×</button>
          </div>

          <div style={{ overflowY: "auto", padding: "6px 4px", flex: 1 }}>
            {files.length === 0 ? (
              <div style={{
                fontSize: 11, color: "var(--text-dim)", textAlign: "center",
                padding: "20px 12px", lineHeight: 1.5,
              }}>
                Drop files or folders here.<br />
                Shared with every agent on this team (copied into each desk's
                <code style={{ opacity: 0.8 }}> team_files/</code>).
              </div>
            ) : (
              <FileTree nodes={files} root={root} depth={0} onPreview={handlePreview} onDelete={handleDelete} />
            )}
          </div>

          {busy && (
            <div style={{
              fontSize: 10, color: "var(--accent2)", padding: "6px 10px",
              borderTop: "1px solid var(--card-border)", whiteSpace: "nowrap",
              overflow: "hidden", textOverflow: "ellipsis", flexShrink: 0,
            }}>
              {busy}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
