import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ActivityEvent, ApiMode, DeskHistory, FileNode, FilePreviewData, LiveState, ReasoningEffort, Session, SubagentRecord, WorkerEvent } from "../types";
import { DESK_PANEL_Z_BASE } from "../floatingPanelStack";
import { usePanelDrag } from "../usePanelDrag";
import {
  centeredAnchorToDeskOffset,
  defaultBelowDeskOffset,
  deskOffsetFromViewport,
  useDeskAnchoredRowPosition,
  rowToViewport,
  viewportToRow,
  type DeskOffset,
} from "../deskPanelAnchor";
import { scrollContainerToBottom, scrollIntoContainer } from "../scrollContainer";
import { useTeamRowPanel } from "../TeamRowPanelContext";
import { usePanelResize, type PanelSize } from "../usePanelResize";
import { PanelResizeHandle } from "./PanelResizeHandle";
import { SubagentDesk, applySubagentLive, groupSubagentsIntoRounds } from "./SubagentDesk";
import { api } from "../api/client";
import { ActivityFeed } from "./ActivityFeed";
import { FileExplorer } from "./FileExplorer";
import { InspectPanel } from "./InspectPanel";
import { ActivityOverview } from "./ActivityOverview";
import { MarkdownView } from "./FilePreview";
import { deskDisplayTitle } from "../taskDisplay";
import { toolIcon } from "../toolIcons";

const _TEXT_EXTS = new Set([
  "txt","md","py","js","ts","jsx","tsx","json","csv","yaml","yml",
  "html","css","xml","sh","bash","sql","r","toml","ini","cfg","log",
  "rst","java","c","cpp","h","hpp","go","rs","rb","php","swift","kt",
]);
const _IMAGE_EXTS = new Set(["jpg","jpeg","png","gif","webp","svg"]);

// Heartbeat auto-continue is still unreliable on open-ended/looping tasks
// (the completion judge stops perpetual goals, the 25-resume cap ends loops,
// and errored turns halt it). Keep the UI control hidden until that's fixed.
// The backend remains opt-in and OFF by default, so this is dead code, not a risk.
const AUTO_CONTINUE_UI_ENABLED = false;

// Floating desk panel (opens when you click the desk "monitor"). Anchored below the
// desk in viewport space (portaled to document.body). Double-click / ⊞ maximizes to
// nearly full screen instead of squeezing into the team row.
const PANEL_WIDTH = 480;
// The Inspect tab shows a tool form + a wide command/output area, so it gets a
// roomier panel than the default desk width (still clamps to the viewport).
const INSPECT_PANEL_WIDTH = 640;
const PANEL_MIN_WIDTH = 320;
const PANEL_MIN_HEIGHT = 340;
const PANEL_PREF_HEIGHT = 560;
const PANEL_VIEWPORT_PAD = 16;

function computeFloatingPanelLayout(anchorTop: number, rowHeight: number) {
  // Prefer up to one team-row tall; may extend past the row bottom (overflow visible).
  const height = Math.max(PANEL_MIN_HEIGHT, Math.min(PANEL_PREF_HEIGHT, rowHeight));
  return { top: anchorTop, height };
}

/** Maximized desk panel — nearly full viewport (not the team row). */
function computeMaximizedPanelLayout(viewportW: number, viewportH: number) {
  const pad = PANEL_VIEWPORT_PAD;
  return {
    top: pad,
    left: pad,
    width: viewportW - pad * 2,
    height: viewportH - pad * 2,
  };
}

async function _processFiles(files: File[]): Promise<{ text: string; images: { name: string; data: string; url: string }[] }> {
  const parts: string[] = [];
  const images: { name: string; data: string; url: string }[] = [];
  for (const file of files) {
    const ext = (file.name.split(".").pop() ?? "").toLowerCase();
    if (_IMAGE_EXTS.has(ext) || file.type.startsWith("image/")) {
      const dataUrl = await new Promise<string>((res) => {
        const fr = new FileReader();
        fr.onload = (e) => res(e.target!.result as string);
        fr.readAsDataURL(file);
      });
      images.push({ name: file.name, data: dataUrl, url: dataUrl });
    } else if (_TEXT_EXTS.has(ext) || file.type.startsWith("text/")) {
      const content = await new Promise<string>((res) => {
        const fr = new FileReader();
        fr.onload = (e) => res(e.target!.result as string);
        fr.readAsText(file);
      });
      parts.push(`\`\`\`${ext}\n# ${file.name}\n${content.slice(0, 12000)}\n\`\`\``);
    } else {
      parts.push(`[Attached file: ${file.name}]`);
    }
  }
  return { text: parts.join("\n"), images };
}

interface Props {
  session: Session;
  scene?: string;
  isActive: boolean;
  searchMatch?: boolean;
  index: number;
  autoExpand?: boolean;
  /** Screen coords for the panel top-center when auto-opening after the first prompt. */
  openAnchor?: { top: number; left: number } | null;
  workspacePath?: string;
  taskContent?: string;
  taskImages?: { name: string; url: string }[];
  verbose?: boolean;
  reasoningEffort?: ReasoningEffort;
  apiMode?: ApiMode;
  onPreview: (data: FilePreviewData) => void;
  panelZIndex?: number;
  onPanelActivate?: () => void;
  onSelect: () => void;
  onFocus?: () => void;
  onOpen?: () => void;
  deskFocused?: boolean;
  onClose: () => void;
  /** Fired once after autoExpand opens the panel (so the parent can clear justStartedId). */
  onAutoExpanded?: () => void;
  onActivity?: () => void;
  onAskManager?: () => void;
  onInterrupt?: (id: string) => void;
  // Resolved profile for the desk's "profile · model" status line.
  profileLabel?: string;
  profileColor?: string;
  profileModel?: string;
}

type DeskTab = "activity" | "tasks" | "files" | "console";
// Sub-views inside the merged Console tab: the clean "human's-eye" shell I/O, the
// full worker debug stream (tool calls, args, results, reasoning, logs), and the
// Inspect tool form for ad-hoc command/output probing.
type ConsoleView = "agent" | "debug" | "inspect";

function statusColor(session: Session): string {
  if (session.is_running) return "var(--green)";
  if (session.ended_at) return "var(--text-dim)";
  return "var(--yellow)";
}

function statusLabel(session: Session): string {
  if (session.is_running) return "active";
  if (session.ended_at) return "done";
  return "idle";
}

// Actual execution span, not wall-clock since spawn: start at the first command,
// and for an idle desk stop at its last activity (so it freezes instead of
// counting overnight hours). A live desk keeps ticking to now.
function elapsedLabel(session: Session): string {
  const startIso = session.first_activity_at || session.started_at;
  if (!startIso) return "";
  const start = new Date(startIso).getTime();
  let end: number;
  if (session.is_running) {
    end = Date.now();
  } else {
    const endIso = session.last_activity_at || session.ended_at;
    end = endIso ? new Date(endIso).getTime() : Date.now();
  }
  const mins = Math.round((end - start) / 60000);
  if (mins < 1) return "<1m";
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function stripAnsi(s: string): string {
  return s.replace(/\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, "");
}

function countOccurrences(text: string, q: string): number {
  if (!q) return 0;
  const t = text.toLowerCase(), ql = q.toLowerCase();
  let n = 0, i = t.indexOf(ql);
  while (i !== -1) { n++; i = t.indexOf(ql, i + ql.length); }
  return n;
}

// Highlight registry helpers (CSS Custom Highlight API). No-op where unsupported.
const _cssHL = (): { set: (k: string, h: unknown) => void; delete: (k: string) => void } | null =>
  (typeof CSS !== "undefined" && (CSS as unknown as { highlights?: unknown }).highlights)
    ? (CSS as unknown as { highlights: { set: (k: string, h: unknown) => void; delete: (k: string) => void } }).highlights
    : null;
const _clearFindHL = () => { const h = _cssHL(); if (h) { h.delete("deskfind"); h.delete("deskfind-cur"); } };

// Apply terminal carriage-return semantics: progress bars (e.g. dataset/pip
// downloads) emit "0.3%\r0.7%\r…" expecting each value to overwrite the line in
// place. We capture the raw stream, so collapse each \r-run to its final state
// instead of printing every step on its own line.
function applyCarriageReturns(s: string): string {
  if (!s.includes("\r")) return s;
  return s.split("\n").map((line) => {
    if (!line.includes("\r")) return line;
    let out = "";
    for (const seg of line.split("\r")) out = seg + out.slice(seg.length);
    return out;
  }).join("\n");
}

// Escape HTML before injecting console output via dangerouslySetInnerHTML.
// Agent terminal output is untrusted (it can echo file contents, web results,
// etc.), so raw markup like <img onerror=…> must not reach the DOM. Run this
// BEFORE the `$ command` colorize regex, which intentionally adds <span> markup.
function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : c === '"' ? "&quot;" : "&#39;");
}


function TaskFileEditor({ sessionId, onSaved }: { sessionId: string; onSaved?: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.sessions.taskFile.get(sessionId)
      .then((r) => { setContent(r.content); setDraft(r.content); })
      .catch(() => { setContent(""); setDraft(""); });
  }, [sessionId]);

  async function save() {
    if (saving) return;
    setSaving(true);
    try {
      await api.sessions.taskFile.save(sessionId, draft);
      setContent(draft);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
      onSaved?.();
    } catch {
      // workspace not found for older sessions — silently ignore
    } finally {
      setSaving(false);
    }
  }

  // content===null means still loading; empty string means no workspace (old session)
  if (content === null) return (
    <div style={{ padding: "8px 12px", fontSize: 11, color: "var(--text-dim)" }}>Loading…</div>
  );

  const dirty = draft !== content;

  return (
    <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--card-border)" }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginBottom: 6,
      }}>
        <span style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          TASK.md
        </span>
        {(dirty || saved) && (
          <button
            onClick={save}
            disabled={saving || saved}
            style={{
              fontSize: 10, padding: "2px 8px", borderRadius: 4,
              background: saved ? "rgba(78,204,163,0.2)" : "var(--accent2)",
              color: saved ? "var(--green)" : "white",
              border: `1px solid ${saved ? "var(--green)" : "transparent"}`,
              cursor: saving || saved ? "default" : "pointer",
            }}
          >
            {saving ? "Saving…" : saved ? "✓ Saved" : "Save"}
          </button>
        )}
      </div>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); save(); }
        }}
        placeholder="Describe the task for this desk…"
        style={{
          width: "100%", minHeight: 120, maxHeight: 300,
          background: "var(--bg)", border: "1px solid var(--card-border)",
          borderRadius: 4, padding: "6px 8px",
          fontSize: 11, color: "var(--text)",
          resize: "vertical", fontFamily: "monospace", lineHeight: 1.5,
          outline: "none", boxSizing: "border-box",
        }}
        onFocus={(e) => { e.target.style.borderColor = "var(--accent2)"; }}
        onBlur={(e) => { e.target.style.borderColor = "var(--card-border)"; }}
      />
      <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 3, opacity: 0.6 }}>
        Shift+Enter to save — agent reads this file from its workspace
      </div>
    </div>
  );
}

// Read-only agent progress report (PROGRESS.md). Written by the agent's model —
// useful to the user, the manager, and the agent itself on resume. Auto-refreshes
// after audits; the button regenerates it on demand.
function ProgressView({ sessionId }: { sessionId: string }) {
  const [content, setContent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setContent(null);
    api.sessions.progress.get(sessionId)
      .then((r) => setContent(r.content || ""))
      .catch(() => setContent(""));
  }, [sessionId]);

  async function refresh() {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.sessions.progress.generate(sessionId);
      setContent(r.content || "");
    } catch {
      // 422 = nothing to report yet; leave existing content
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: "10px 12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Agent progress report
        </span>
        <button
          onClick={refresh}
          disabled={busy}
          title="Regenerate the report from the agent's work so far (~1 min)"
          style={{
            fontSize: 10, padding: "3px 10px", borderRadius: 4,
            background: busy ? "rgba(100,100,200,0.15)" : "var(--accent2)",
            color: busy ? "var(--accent2)" : "white",
            border: "1px solid transparent", cursor: busy ? "default" : "pointer",
          }}
        >
          {busy ? "Generating…" : "↻ Refresh"}
        </button>
      </div>
      {content === null ? (
        <div style={{ fontSize: 11, color: "var(--text-dim)" }}>Loading…</div>
      ) : content.trim() === "" ? (
        <div style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.6 }}>
          No progress report yet. It refreshes automatically after an audit, or click
          <strong> ↻ Refresh</strong> to generate one now.
        </div>
      ) : (
        <MarkdownView content={content} />
      )}
    </div>
  );
}

function TasksView({ sessionId, onTaskSaved, onAskManager }: { sessionId: string; onTaskSaved?: () => void; onAskManager?: () => void }) {
  const [asking, setAsking] = useState(false);
  const [view, setView] = useState<"task" | "progress">("task");

  function handleAskManager() {
    if (asking) return;
    setAsking(true);
    onAskManager?.();
    setTimeout(() => setAsking(false), 4000);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {/* Task spec (human-editable) ↔ Progress report (agent-written, read-only) */}
      <div style={{
        display: "flex", gap: 6, alignItems: "center",
        padding: "6px 10px", borderBottom: "1px solid var(--card-border)",
      }}>
        {([["task", "📋 Task"], ["progress", "📈 Progress"]] as const).map(([v, label]) => (
          <button
            key={v}
            onClick={() => setView(v)}
            style={{
              fontSize: 11, padding: "3px 10px", borderRadius: 6, cursor: "pointer",
              background: view === v ? "var(--accent2)" : "transparent",
              color: view === v ? "#fff" : "var(--text-dim)",
              border: `1px solid ${view === v ? "var(--accent2)" : "var(--card-border)"}`,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {view === "progress" ? (
        <ProgressView sessionId={sessionId} />
      ) : (
        <>
          <TaskFileEditor sessionId={sessionId} onSaved={onTaskSaved} />
          {onAskManager && (
            <div style={{ padding: "8px 12px 10px" }}>
              <button
                onClick={handleAskManager}
                disabled={asking}
                title="Ask the team manager to review your tasks and leave guidance"
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  fontSize: 11, padding: "5px 10px", borderRadius: 6,
                  background: asking ? "rgba(100,100,200,0.15)" : "rgba(255,255,255,0.04)",
                  color: asking ? "var(--accent2)" : "var(--text-dim)",
                  border: "1px solid var(--card-border)",
                  cursor: asking ? "default" : "pointer",
                  transition: "background 0.2s, color 0.2s",
                  width: "100%", justifyContent: "center",
                }}
              >
                <span style={{ fontSize: 13 }}>👩‍💼</span>
                {asking ? "Manager on her way…" : "Ask manager for guidance"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// Load the session's real workspace directory tree (dirs + subdirs + all files).
// Falls back to the tool-call-derived file list for sessions whose workspace dir
// can't be resolved server-side (e.g. older CLI sessions).
async function loadWorkspaceFiles(sessionId: string): Promise<FileNode[]> {
  try {
    const tree = await api.sessions.workspaceTree(sessionId);
    if (tree.length > 0) return tree;
  } catch { /* fall through to the touched-files list */ }
  try {
    return await api.sessions.files(sessionId);
  } catch {
    return [];
  }
}

// Map a worker/Hermes log line to a short, honest phase label for the live status,
// so the user sees what's actually happening (not a vague "thinking…") even when
// the model streams no reasoning tokens. Returns null for lines with no clear phase.
function phaseFromLog(msg: string): string | null {
  const m = msg.toLowerCase();
  if (m.includes("api call") || m.includes("calling model")) return "Waiting for model";
  if (m.includes("creating session") || m.includes("ready,")) return "Initializing agent";
  if (m.includes("resumed session") || m.includes("loading history")) return "Loading history";
  if (m.includes("proxy")) return "Connecting to model";
  if (m.includes("starting")) return "Starting up";
  return null;
}

// Derive the workspace dir from the file tree: top-level nodes are direct children
// of the workspace, so the parent of any of them is the workspace dir itself.
function workspaceDirOf(nodes: FileNode[]): string | null {
  if (!nodes.length) return null;
  const p = nodes[0].path;
  const cut = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return cut > 0 ? p.slice(0, cut) : null;
}

function ActionButton({ icon, label, hint, color, onClick }: {
  icon: React.ReactNode; label: string; hint: string; color: string; onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      title={hint}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex", alignItems: "center", gap: 7,
        fontSize: 12, fontWeight: 600, padding: "8px 14px", borderRadius: 8,
        cursor: "pointer", transition: "transform .12s, box-shadow .12s, background .12s, color .12s",
        color: hover ? "#fff" : color,
        background: hover ? color : "rgba(255,255,255,0.05)",
        border: `1px solid ${color}`,
        boxShadow: hover ? `0 3px 12px ${color}55` : "none",
        transform: hover ? "translateY(-1px)" : "none",
      }}
    >
      <span style={{ fontSize: 15, lineHeight: 1 }}>{icon}</span>
      {label}
    </button>
  );
}

function FilesView({ nodes, onPreview, onRefresh, refreshing }: {
  nodes: FileNode[];
  onPreview: (d: FilePreviewData) => void;
  onRefresh: () => void;
  refreshing?: boolean;
}) {
  const [err, setErr] = useState<"folder" | "terminal" | null>(null);
  const dir = workspaceDirOf(nodes);

  function run(kind: "folder" | "terminal") {
    if (!dir) return;
    const call = kind === "folder" ? api.workspace.open(dir) : api.workspace.openTerminal(dir);
    call.then(() => setErr(null)).catch(() => { setErr(kind); setTimeout(() => setErr(null), 3000); });
  }

  return (
    <div>
      <div style={{
        display: "flex", justifyContent: "flex-end", alignItems: "center",
        padding: "6px 10px 4px", borderBottom: "1px solid var(--card-border)",
      }}>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onRefresh(); }}
          disabled={refreshing}
          title="Refresh workspace file list (includes team_files/)"
          style={{
            fontSize: 11, fontWeight: 600, padding: "4px 10px", borderRadius: 6,
            background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)",
            color: refreshing ? "var(--text-dim)" : "var(--accent2)",
            cursor: refreshing ? "default" : "pointer",
            opacity: refreshing ? 0.7 : 1,
          }}
        >
          {refreshing ? "Refreshing…" : "↻ Update"}
        </button>
      </div>
      <FileExplorer nodes={nodes} onPreview={onPreview} />
      {dir && (
        <div style={{
          display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
          padding: "12px", marginTop: 2, borderTop: "1px solid var(--card-border)",
        }}>
          <ActionButton
            icon="📂" label="Open folder" color="var(--accent2)"
            hint={`Reveal ${dir} in Finder`} onClick={() => run("folder")}
          />
          <ActionButton
            icon={<span style={{ fontFamily: "monospace", fontWeight: 700 }}>{">_"}</span>}
            label="Open in terminal" color="var(--green)"
            hint={`Open a terminal at ${dir}`} onClick={() => run("terminal")}
          />
          {err && (
            <span style={{ fontSize: 10, color: "var(--red)" }}>
              Couldn’t open {err}.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// Desk history log — every session row this desk ran (root + each resume /
// model-switch), with its start time, agent profile, and model. Every resume is
// its own entry, even when the profile + model are unchanged from the previous.
function DeskHistoryView({ history }: { history: DeskHistory | null }) {
  if (!history) {
    return <div style={{ padding: "12px", fontSize: 11, color: "var(--text-dim)" }}>Loading history…</div>;
  }
  const rows = history.sessions;
  const fmt = (iso: string) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
  };
  return (
    <div style={{ padding: "10px 12px" }}>
      <div style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
        Desk session history · {rows.length} run{rows.length === 1 ? "" : "s"}
      </div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-dim)" }}>No sessions recorded for this desk yet.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {rows.map((s, i) => {
            const prev = i > 0 ? rows[i - 1] : null;
            const changed = !!prev && (prev.profile !== s.profile || prev.model !== s.model);
            return (
            <div key={s.id} style={{
              display: "flex", flexDirection: "column", gap: 3,
              padding: "7px 9px", borderRadius: 6,
              background: "rgba(255,255,255,0.03)",
              border: `1px solid ${changed ? "var(--accent2)" : "var(--card-border)"}`,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 7,
                  background: s.is_root ? "var(--accent2)" : "rgba(255,255,255,0.08)",
                  color: s.is_root ? "#fff" : "var(--text-dim)",
                }}>
                  {s.is_root ? "root" : `resume ${i}`}
                </span>
                <span style={{ fontSize: 11, color: "var(--text)" }}>{fmt(s.started_at)}</span>
                {s.message_count > 0 && (
                  <span style={{ fontSize: 10, color: "var(--text-dim)" }}>· {s.message_count} msgs</span>
                )}
                {changed && (
                  <span style={{ fontSize: 9, color: "var(--accent2)", fontWeight: 700 }}>· config changed</span>
                )}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontSize: 10, color: "var(--text)", fontWeight: 600 }}>
                  {s.profile || "Default"}
                </span>
                {s.model && (
                  <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 10, color: "var(--accent2)" }}>
                    {s.model}
                  </span>
                )}
              </div>
              <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 9.5, color: "var(--text-dim)", wordBreak: "break-all" }}>
                {s.id}
              </div>
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function TaskDesk({ session, scene, isActive, searchMatch, index, autoExpand, openAnchor, workspacePath, taskContent, taskImages, verbose = true, reasoningEffort, apiMode, onPreview, panelZIndex, onPanelActivate, onSelect, onFocus, onOpen, deskFocused, onClose, onAutoExpanded, onActivity, onAskManager, onInterrupt, profileLabel, profileColor, profileModel }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<DeskTab>("activity");
  const [consoleView, setConsoleView] = useState<ConsoleView>("debug");
  const [activityView, setActivityView] = useState<"feed" | "overview" | "history">("feed");
  const [deskHistory, setDeskHistory] = useState<DeskHistory | null>(null);
  const [exporting, setExporting] = useState(false);
  const [autoContinue, setAutoContinue] = useState(!!session.auto_continue);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [overviewDesk, setOverviewDesk] = useState<{
    sessionId: string;
    events: ActivityEvent[];
    started_at: string | null;
    last_at: string | null;
  } | null>(null);
  const overviewReady = overviewDesk?.sessionId === session.id;
  const [files, setFiles] = useState<FileNode[]>([]);
  const [filesRefreshing, setFilesRefreshing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatImages, setChatImages] = useState<{ name: string; data: string; url: string }[]>([]);
  const [sending, setSending] = useState(false);
  const [termLines, setTermLines] = useState<string[]>([]);
  const [consoleLines, setConsoleLines] = useState<string[]>([]);
  // In-desk find (Ctrl/⌘-F): highlight matches + Enter/Shift-Enter click-through.
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [findIndex, setFindIndex] = useState(0);   // current hit within the active surface
  const [findCount, setFindCount] = useState(0);   // hits in the active surface (from the DOM)
  const findInputRef = useRef<HTMLInputElement>(null);
  const consoleBottomRef = useRef<HTMLDivElement>(null);
  const [liveState, setLiveState] = useState<LiveState>({ streamText: "" });
  useEffect(() => { liveStreamRef.current = liveState.streamText; }, [liveState.streamText]);
  const [liveEvents, setLiveEvents] = useState<ActivityEvent[]>([]);
  // delegate_task subagents, keyed by subagent_id. Append-only within a desk:
  // seeded from the server's durable {"subagents":[…]} replay on (re)connect and
  // updated incrementally by live {type:"subagent"} events. NOT cleared on turn
  // boundaries / WS close, so the tabs and their I/O persist (only reset when the
  // panel switches to a different session).
  const [subagents, setSubagents] = useState<Record<string, SubagentRecord>>({});
  // Which delegation rounds are expanded to reveal their subagent bubbles.
  // Collapsed by default so a long task stays compact; click a round to expand.
  const [expandedRounds, setExpandedRounds] = useState<Set<number>>(() => new Set());
  // User messages sent from this panel (follow-ups / barge-ins). Kept client-side
  // so they stay visible in the feed even before Hermes persists them to the DB.
  const [sentMsgs, setSentMsgs] = useState<{ text: string; ts: string }[]>([]);
  // Partial agent replies cut off by a barge-in — kept client-side so the
  // incomplete response stays visible (the interrupted turn never reaches the DB).
  const [interruptedReplies, setInterruptedReplies] = useState<{ text: string; ts: string }[]>([]);
  const liveStreamRef = useRef("");
  const [panelDeskOffset, setPanelDeskOffset] = useState<DeskOffset | null>(null);
  const onDragCommitRef = useRef<(vp: { top: number; left: number }) => void>(() => {});
  const { pos: panelDragPos, resetPos: resetPanelUserPos, dragging: panelDragging, bindHandle: bindPanelDrag } = usePanelDrag(12, (vp) => onDragCommitRef.current(vp));
  const { size: panelUserSize, resetSize: resetPanelUserSize, resizing: panelResizing, bindResize: bindPanelResize } = usePanelResize({
    width: PANEL_MIN_WIDTH,
    height: PANEL_MIN_HEIGHT,
  });
  const [viewportH, setViewportH] = useState(() =>
    typeof window !== "undefined" ? window.innerHeight : 800,
  );
  const [viewportW, setViewportW] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth : 1200,
  );
  const [isMaximized, setIsMaximized] = useState(false);
  const [chatDragOver, setChatDragOver] = useState(false);
  // Bumped on every in-panel resume so the activity WS reconnects immediately,
  // even if the run is too short for the is_running poll to ever observe it.
  const [wsEpoch, setWsEpoch] = useState(0);
  const [resuming, setResuming] = useState(false);
  const deskRef = useRef<HTMLDivElement>(null);
  const deskClickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { root: panelRoot, height: teamRowHeight } = useTeamRowPanel();
  const rowRef = useRef<HTMLElement | null>(null);
  rowRef.current = panelRoot;
  onDragCommitRef.current = (vp) => {
    if (deskRef.current) setPanelDeskOffset(deskOffsetFromViewport(deskRef.current, vp));
    resetPanelUserPos();
  };
  const panelRowPos = useDeskAnchoredRowPosition(deskRef, rowRef, panelDeskOffset, expanded && !panelDragging && !!panelRoot);
  const panelDisplayPos = (() => {
    if (panelDragging && panelDragPos && panelRoot) return viewportToRow(panelRoot, panelDragPos);
    return panelRowPos;
  })();
  const wsRef = useRef<WebSocket | null>(null);
  const termWsRef = useRef<WebSocket | null>(null);
  const termBottomRef = useRef<HTMLDivElement>(null);
  const panelContentRef = useRef<HTMLDivElement>(null);

  useEffect(() => () => {
    if (deskClickTimerRef.current) clearTimeout(deskClickTimerRef.current);
  }, []);

  const refreshFiles = useCallback(async () => {
    setFilesRefreshing(true);
    try {
      setFiles(await loadWorkspaceFiles(session.id));
    } catch { /* ignore */ }
    finally { setFilesRefreshing(false); }
  }, [session.id]);

  // Debug terminal lives inside the Console tab now; force the clean Agent view
  // when verbose (detailed mode) is off so simple mode stays uncluttered.
  useEffect(() => {
    if (!verbose) setConsoleView("agent");
  }, [verbose]);

  // Live activity feed via WebSocket (always open)
  const prevEventCountRef = useRef(0);
  const onActivityRef = useRef(onActivity);
  useEffect(() => { onActivityRef.current = onActivity; }, [onActivity]);
  // Fires onActivity once on the first live event of each turn so the avatar
  // walks over immediately when streaming starts, not only after a DB commit.
  const liveNotifiedRef = useRef(false);

  // If this panel slot is ever reused for a different desk, drop the previous
  // desk's subagent bubbles so they don't carry over to an unrelated session.
  useEffect(() => {
    setSubagents({});
    setExpandedRounds(new Set());
  }, [session.id]);

  useEffect(() => {
    const now = () => new Date().toISOString();

    function flushStreamed(prev: LiveState): void {
      if (prev.streamText) {
        setLiveEvents((le) => [...le, {
          timestamp: now(), event_type: "message", icon: "🤖", title: "Agent",
          detail: prev.streamText, tool_name: "", is_error: false, files_touched: [],
        }]);
      }
    }

    // When a reasoning phase ends (a token or tool call follows), keep the
    // streamed trace as a collapsible "Reasoning" step instead of dropping it.
    // The DB-backed step (from reasoning_content) replaces this on the next
    // activity refresh; both render identically so the swap is seamless.
    // Trim-gate to mirror the DB path (activity_parser strips reasoning_content):
    // qwen3-style models emit an empty `<think>\n\n</think>` on most tool-calling
    // turns, which the parser drops — without the same .trim() here, every such
    // turn left a clickable "Reasoning" step whose trace was blank when expanded.
    function flushThinking(prev: LiveState): void {
      const trace = prev.thinkingText?.trim();
      if (trace) {
        setLiveEvents((le) => [...le, {
          timestamp: now(), event_type: "thinking_start", icon: "💭", title: "Reasoning",
          detail: trace, tool_name: "", is_error: false, files_touched: [],
        }]);
      }
    }

    function notifyActivityOnce() {
      if (!liveNotifiedRef.current) {
        liveNotifiedRef.current = true;
        onActivityRef.current?.();
      }
    }

    function onLive(evt: WorkerEvent) {
      if (evt.type === "token") {
        notifyActivityOnce();
        setLiveState((prev) => {
          flushThinking(prev);     // preserve the just-finished reasoning trace
          return {
            ...prev,
            streamText: prev.streamText + (evt.text ?? ""),
            thinkingText: undefined,
            logLine: undefined,
            statusLine: undefined,   // the response itself is now the status
          };
        });
      } else if (evt.type === "thinking") {
        notifyActivityOnce();
        setLiveState((prev) => ({
          ...prev,
          thinkingText: (prev.thinkingText ?? "") + (evt.text ?? ""),
          statusLine: undefined,
        }));
      } else if (evt.type === "tool_start") {
        notifyActivityOnce();
        setLiveState((prev) => {
          flushThinking(prev);     // preserve reasoning that preceded this tool call
          flushStreamed(prev);
          // Commit a "calling <tool>" row so the in-progress call has a persistent
          // feed entry (mirrors the DB tool_call event + per-tool icon). The live
          // overlay below is transient and clears on tool_done; this row survives
          // and is replaced 1:1 by the parsed row when the DB snapshot catches up.
          setLiveEvents((le) => [...le, {
            timestamp: now(), event_type: "tool_call", icon: toolIcon(evt.name),
            title: `calling ${evt.name ?? "tool"}`, detail: "",
            tool_name: evt.name ?? "", is_error: false, files_touched: [],
          }]);
          return { streamText: "", toolName: evt.name, logLine: undefined, thinkingText: undefined,
                   statusLine: `Invoking ${evt.name ?? "tool"}` };
        });
      } else if (evt.type === "tool_done") {
        setLiveEvents((le) => [...le, {
          timestamp: now(), event_type: "tool_result", icon: toolIcon(evt.name),
          title: `${evt.name ?? "tool"} done`,
          detail: (evt.result ?? "").slice(0, 200),
          tool_name: evt.name ?? "", is_error: false, files_touched: [],
        }]);
        setLiveState((prev) => ({ ...prev, toolName: undefined, statusLine: undefined }));
      } else if (evt.type === "log") {
        notifyActivityOnce();
        const phase = phaseFromLog(evt.msg ?? "");
        setLiveState((prev) => prev.streamText
          ? prev
          : { ...prev, logLine: evt.msg, statusLine: phase ?? prev.statusLine });
      } else if (evt.type === "status") {
        notifyActivityOnce();
        const phase = evt.msg || evt.event;
        if (phase) setLiveState((prev) => prev.streamText ? prev : { ...prev, statusLine: phase });
      } else if (evt.type === "error") {
        // Show the failure in the feed right away; the server also preserves it
        // (with any partial output) so later DB snapshots keep it visible.
        setLiveEvents((le) => [...le, {
          timestamp: now(), event_type: "error", icon: "❌", title: "Error",
          detail: evt.msg ?? "", tool_name: "", is_error: true, files_touched: [],
        }]);
        setLiveState({ streamText: "" });
      } else if (evt.type === "interrupted") {
        setLiveState({ streamText: "" });
        setLiveEvents((le) => [...le, {
          timestamp: now(), event_type: "message", icon: "⏸", title: "Interrupted",
          detail: "", tool_name: "", is_error: false, files_touched: [],
        }]);
      } else if (evt.type === "agent_arrived") {
        setLiveEvents((le) => [...le, {
          timestamp: now(), event_type: "message", icon: "🚶", title: "Agent arrived",
          detail: "", tool_name: "", is_error: false, files_touched: [],
        }]);
      } else if (evt.type === "subagent") {
        // A delegate_task child: route into its own persistent tab instead of the
        // parent's feed, so its trace survives the turn (see `subagents` state).
        notifyActivityOnce();
        setSubagents((prev) => applySubagentLive(prev, evt));
      }
    }

    const ws = api.sessions.activityWs(
      session.id,
      (events) => {
        setActivity(events);
        if (events.length > prevEventCountRef.current) {
          prevEventCountRef.current = events.length;
          liveNotifiedRef.current = false; // reset so next turn's first live event fires again
          onActivityRef.current?.();
          setLiveState((prev) => ({ ...prev, streamText: "", toolName: undefined, thinkingText: undefined }));
          setLiveEvents([]);
        }
      },
      onLive,
      () => {
        // WS closed (session ended, interrupted, or server restarted) — clear stale live overlay.
        // NOTE: subagent tabs are intentionally NOT cleared here; the server's
        // durable replay re-seeds them on reconnect and they must persist.
        setLiveState({ streamText: "" });
        setLiveEvents([]);
      },
      (records) => {
        // Durable subagent replay sent once on (re)connect — seed/merge the tab
        // state. Records are authoritative (full timeline), so they win per id.
        setSubagents((prev) => {
          const next = { ...prev };
          for (const r of records) if (r && r.subagent_id) next[r.subagent_id] = r;
          return next;
        });
      },
    );
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
      prevEventCountRef.current = 0;
      setLiveState({ streamText: "" });
      setLiveEvents([]);
    };
    // Reconnect when the session starts running again (resume / reassign / TASK.md
    // save / chat) — the server closes the stream on "done", so a single-run WS
    // would leave later runs invisible and the avatar unresponsive.
  }, [session.id, session.is_running, wsEpoch]);

  // Overview uses desk-wide history (merged across related sessions). Fetch it
  // eagerly once the desk is open — not only when the Overview tab is selected —
  // so `overviewReady` is true before the user switches in. Otherwise the chart
  // falls back to session-scoped `activity` (only the folder session id's
  // messages), which after a resume drops prior runs Hermes stored under a
  // different internal session id and looks like "only the latest call".
  // Do not refetch on every WS activity tick — that would briefly null the
  // desk-wide events mid-turn.
  useEffect(() => {
    if (!expanded && !loaded) return;
    let cancelled = false;
    const sid = session.id;
    api.sessions.overview(sid)
      .then((data) => {
        if (!cancelled) {
          setOverviewDesk({
            sessionId: sid,
            events: data.events,
            started_at: data.started_at,
            last_at: data.last_at,
          });
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [expanded, loaded, session.id, wsEpoch, session.is_running]);

  // Desk history (session lineage). Refetch when the desk gains a new run — a
  // resume/model-switch adds a session row — so the log stays current.
  useEffect(() => {
    if (!expanded && !loaded) return;
    let cancelled = false;
    const sid = session.id;
    api.sessions.history(sid)
      .then((h) => { if (!cancelled) setDeskHistory(h); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [expanded, loaded, session.id, wsEpoch, session.is_running]);

  // Seed the Console + Debug terminal from the DB once per session. The live WS
  // streams only show output from a *running* worker, so reopening a finished
  // session (e.g. after an app restart) would leave both panels empty even though
  // the shell I/O is persisted. Backfill from history, then let the WS append.
  const histSeededRef = useRef<string | null>(null);
  useEffect(() => {
    if (histSeededRef.current === session.id) return;
    histSeededRef.current = session.id;
    let cancelled = false;
    api.sessions.consoleHistory(session.id)
      .then((r) => { if (!cancelled && r.text) setConsoleLines((prev) => [r.text, ...prev]); })
      .catch(() => {});
    api.sessions.terminalHistory(session.id)
      .then((r) => { if (!cancelled && r.text) setTermLines((prev) => [r.text, ...prev]); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [session.id]);

  // Terminal WebSocket. Reconnects when a worker (re)starts — same triggers as the
  // activity stream (session.is_running / wsEpoch) — so the terminal isn't stuck on
  // "Waiting for output…" for a desk that wasn't running when the panel opened, or
  // after a resume / auto-continue spins up a fresh worker.
  useEffect(() => {
    const ws = api.sessions.terminalWs(session.id, (chunk) => {
      if (chunk.includes("terminal output only available for sessions started from this workbench")) return;
      setTermLines((prev) => [...prev, chunk]);
    });
    termWsRef.current = ws;
    return () => { ws.close(); termWsRef.current = null; };
  }, [session.id, session.is_running, wsEpoch]);

  // Console WebSocket — clean shell I/O only (no agent chatter).
  useEffect(() => {
    const ws = api.sessions.consoleWs(session.id, (chunk) => {
      if (chunk) setConsoleLines((prev) => [...prev, chunk]);
    });
    return () => ws.close();
  }, [session.id, session.is_running, wsEpoch]);

  useEffect(() => {
    const container = panelContentRef.current;
    if (!container || tab !== "console" || consoleView !== "agent") return;
    scrollContainerToBottom(container);
  }, [consoleLines.length, tab, consoleView]);

  useEffect(() => {
    const container = panelContentRef.current;
    if (!container || tab !== "console" || consoleView !== "debug") return;
    scrollContainerToBottom(container);
  }, [termLines.length, tab, consoleView]);

  // ── In-desk find ──────────────────────────────────────────────────────────
  // Per-surface hit counts (data-driven, so we can label tabs that aren't
  // currently rendered). Surfaces: Activity feed, Console (agent), Debug.
  const findCounts = useMemo(() => {
    const q = findQuery.trim();
    if (!q) return { activity: 0, console: 0, debug: 0 };
    const actText = [
      taskContent || "",
      ...activity.map((e) => `${e.title} ${e.detail}`),
      ...liveEvents.map((e) => `${e.title} ${e.detail}`),
      ...sentMsgs.map((m) => m.text),
      ...interruptedReplies.map((m) => m.text),
    ].join("\n");
    return {
      activity: countOccurrences(actText, q),
      console: countOccurrences(applyCarriageReturns(stripAnsi(consoleLines.join(""))), q),
      debug: verbose ? countOccurrences(applyCarriageReturns(stripAnsi(termLines.join(""))), q) : 0,
    };
  }, [findQuery, activity, liveEvents, sentMsgs, interruptedReplies, taskContent, consoleLines, termLines, verbose]);

  type FindSurface = { key: "activity" | "console" | "debug"; count: number; activate: () => void };
  const findSurfaces: FindSurface[] = [
    { key: "activity", count: findCounts.activity, activate: () => { setTab("activity"); setActivityView("feed"); } },
    { key: "console", count: findCounts.console, activate: () => { setTab("console"); setConsoleView("agent"); } },
    ...(verbose ? [{ key: "debug" as const, count: findCounts.debug, activate: () => { setTab("console"); setConsoleView("debug"); } }] : []),
  ];
  const activeSurfaceKey: FindSurface["key"] =
    tab === "console" ? (consoleView === "debug" ? "debug" : "console") : "activity";

  // Recompute DOM ranges for the active surface whenever the query, content, or
  // tab changes; register them as highlights and scroll the current hit in view.
  useEffect(() => {
    const root = panelContentRef.current;
    const q = findQuery.trim();
    if (!findOpen || !q || !root) { _clearFindHL(); setFindCount(0); return; }
    const ranges: Range[] = [];
    const ql = q.toLowerCase();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const val = (node.nodeValue || "").toLowerCase();
      let i = val.indexOf(ql);
      while (i !== -1) {
        try { const r = document.createRange(); r.setStart(node, i); r.setEnd(node, i + q.length); ranges.push(r); } catch { /* skip */ }
        i = val.indexOf(ql, i + ql.length);
      }
      node = walker.nextNode();
    }
    setFindCount(ranges.length);
    const HL = _cssHL();
    const W = window as unknown as { Highlight?: new (...r: Range[]) => unknown };
    if (HL && W.Highlight) {
      if (ranges.length) HL.set("deskfind", new W.Highlight(...ranges)); else HL.delete("deskfind");
    }
    const idx = ranges.length ? ((findIndex % ranges.length) + ranges.length) % ranges.length : 0;
    const cur = ranges[idx];
    if (cur) {
      if (HL && W.Highlight) HL.set("deskfind-cur", new W.Highlight(cur));
      const hitEl = cur.startContainer.parentElement;
      const container = panelContentRef.current;
      if (hitEl instanceof HTMLElement && container) {
        scrollIntoContainer(container, hitEl, "center");
      }
    } else if (HL) HL.delete("deskfind-cur");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [findOpen, findQuery, findIndex, tab, activityView, consoleView,
      activity.length, liveEvents.length, consoleLines.length, termLines.length,
      sentMsgs.length, interruptedReplies.length]);

  // New query: jump to the first surface that has hits so the first match shows
  // even if the current tab has none.
  useEffect(() => {
    if (!findOpen || !findQuery.trim()) return;
    setFindIndex(0);
    const withHits = findSurfaces.filter((s) => s.count > 0);
    if (withHits.length && !withHits.some((s) => s.key === activeSurfaceKey)) withHits[0].activate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [findQuery]);

  useEffect(() => () => _clearFindHL(), []);  // clear highlights on unmount

  const findGo = (dir: 1 | -1) => {
    const withHits = findSurfaces.filter((s) => s.count > 0);
    if (!withHits.length) return;
    const ni = findIndex + dir;
    if (findCount > 0 && ni >= 0 && ni < findCount) { setFindIndex(ni); return; }  // stay in surface
    const order = withHits.map((s) => s.key);                                      // cross to next surface
    const ci = order.indexOf(activeSurfaceKey);
    const nextKey = ci === -1
      ? (dir === 1 ? order[0] : order[order.length - 1])
      : order[((ci + dir) % order.length + order.length) % order.length];
    const surf = findSurfaces.find((s) => s.key === nextKey)!;
    surf.activate();
    setFindIndex(dir === 1 ? 0 : Math.max(0, surf.count - 1));
  };

  const openFind = () => { setFindOpen(true); setTimeout(() => findInputRef.current?.select(), 0); };

  // Keep the Files tab in sync with the workspace: refresh the directory tree as
  // the agent works (activity grows) so newly-created files appear without a manual
  // reopen. Also poll on a short interval while the panel is open, so files written
  // *outside* the activity stream (e.g. AUDIT.md / PROGRESS.md from the manager)
  // show up promptly instead of waiting for the agent's next turn.
  useEffect(() => {
    if (!expanded) return;
    loadWorkspaceFiles(session.id).then(setFiles).catch(() => {});
    const iv = setInterval(() => {
      loadWorkspaceFiles(session.id).then(setFiles).catch(() => {});
    }, 3000);
    return () => clearInterval(iv);
  }, [session.id, activity.length, expanded]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    if (!autoExpand) return;
    setExpanded(true);
    if (deskRef.current) {
      if (openAnchor) {
        setPanelDeskOffset(centeredAnchorToDeskOffset(deskRef.current, openAnchor.left, openAnchor.top, PANEL_WIDTH));
      } else {
        setPanelDeskOffset(defaultBelowDeskOffset(deskRef.current, PANEL_WIDTH));
      }
    }
    onPanelActivate?.();
    onOpen?.();
    onFocus?.();
    api.sessions.activity(session.id).then(setActivity).catch(() => {});
    loadWorkspaceFiles(session.id).then(setFiles).catch(() => {});
    setLoaded(true);
    onAutoExpanded?.();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!expanded) return;
    function onResize() {
      setViewportH(window.innerHeight);
      setViewportW(window.innerWidth);
    }
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [expanded]);

  const isRunning = !session.ended_at && session.is_running !== false;

  // Forget locally-tracked sent messages when switching to a different desk.
  useEffect(() => { setSentMsgs([]); setInterruptedReplies([]); }, [session.id]);

  // Clear the "Resuming…" state once the session is actually running again.
  useEffect(() => { if (isRunning) setResuming(false); }, [isRunning]);

  // Save EVERYTHING about this desk to a .tar.gz: its sandbox (private state.db =
  // session history + model calls), workspace snapshot, run/profile history, and
  // markers. Streamed straight to disk via the archive URL (load it back with the
  // header's "Load desk").
  function handleExportDesk() {
    if (exporting) return;
    setExporting(true);
    try {
      const a = document.createElement("a");
      a.href = api.sessions.archiveUrl(session.id);
      a.download = `desk-${session.id}.tar.gz`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } finally {
      // The browser handles the download out-of-band; reset the label shortly after.
      setTimeout(() => setExporting(false), 1200);
    }
  }

  // One-click resume of an idle/finished desk — no follow-up text needed.
  async function handleStop() {
    // Temporarily stop this desk's agent (the worker exits; Resume restarts it).
    try { await api.sessions.interrupt(session.id); } catch { /* already idle */ }
    onInterrupt?.(session.id);
  }

  async function handleResume() {
    if (resuming || isRunning) return;
    setResuming(true);
    onActivity?.();            // walk the avatar over to this desk
    try {
      await api.sessions.wake(session.id);   // clear sleeping flag if set
      await api.sessions.resume(session.id, "Continue.", undefined, undefined, reasoningEffort, apiMode);
      setWsEpoch((e) => e + 1);
    } catch { /* 409 = already running */ }
    // Safety net: drop the spinner even if the poll never reports is_running.
    setTimeout(() => setResuming(false), 6000);
  }

  // Keep the toggle in sync with the server-reported state (5s poll).
  useEffect(() => { setAutoContinue(!!session.auto_continue); }, [session.auto_continue]);

  async function toggleAutoContinue() {
    const next = !autoContinue;
    setAutoContinue(next);  // optimistic
    try { await api.sessions.autoContinue(session.id, next); }
    catch { setAutoContinue(!next); }
  }

  async function handleSend() {
    const msg = chatInput.trim();
    if ((!msg && chatImages.length === 0) || sending) return;
    const interrupting = isRunning;   // barge in if the agent is mid-turn
    onActivity?.();
    setSending(true);
    setChatInput("");
    const attachments = chatImages.map((img) => ({ name: img.name, data: img.data }));
    setChatImages([]);
    // Preserve any in-flight partial agent response before we clear live state, so a
    // barge-in doesn't erase what the agent had already started saying (that tail is
    // never persisted to the DB).
    const partial = liveStreamRef.current.trim();
    if (interrupting && partial) {
      setInterruptedReplies((prev) => [...prev, { text: liveStreamRef.current, ts: new Date().toISOString() }]);
    }
    // Optimistic status so the user gets immediate feedback — the new worker takes
    // a few seconds to spawn + reprocess the conversation, and is_running only flips
    // on the next 5s poll, so without this the panel looks frozen during the wait.
    setLiveState({ streamText: "", statusLine: "Waiting for model…" });
    setLiveEvents([]);
    // Track the message client-side so it stays in the feed until the DB has it.
    setSentMsgs((prev) => [...prev, { text: msg, ts: new Date().toISOString() }]);
    try {
      // While running, redirect() interrupts the in-flight turn then resumes with
      // the new message; when idle, resume() just continues. bump wsEpoch so the
      // activity WS reconnects to stream the fresh worker.
      if (interrupting) await api.sessions.redirect(session.id, msg || " ", attachments.length ? attachments : undefined, reasoningEffort, apiMode);
      else              await api.sessions.resume(session.id, msg || "Continue.", attachments.length ? attachments : undefined, undefined, reasoningEffort, apiMode);
      setWsEpoch((e) => e + 1);
    } catch { /* WS stream handles everything else */ }
    setSending(false);
  }

  async function openPanel() {
    onSelect();
    onFocus?.();
    if (!loaded) {
      setLoading(true);
      try {
        const [acts, fls] = await Promise.all([
          api.sessions.activity(session.id),
          loadWorkspaceFiles(session.id),
        ]);
        setActivity(acts);
        setFiles(fls);
        setLoaded(true);
      } finally {
        setLoading(false);
      }
    }
    setExpanded(true);
    resetPanelUserPos();
    resetPanelUserSize();
    if (deskRef.current) {
      setPanelDeskOffset(defaultBelowDeskOffset(deskRef.current, PANEL_WIDTH));
    }
    onPanelActivate?.();
    onOpen?.();
    requestAnimationFrame(scrollPanelIntoView);
  }

  // The panel is anchored below the desk in viewport space, so for a desk on the
  // bottom team row it opens below the fold. Nudge the floor down just enough to
  // reveal the panel's bottom (the floor reserves slack below the last row for this).
  function scrollPanelIntoView() {
    const desk = deskRef.current;
    if (!desk) return;
    const scroller = desk.closest("[data-floor-scroll]") as HTMLElement | null;
    if (!scroller) return;
    const panelH = Math.max(PANEL_MIN_HEIGHT, Math.min(PANEL_PREF_HEIGHT, teamRowHeight));
    const panelBottom = desk.getBoundingClientRect().bottom + 10 + panelH; // 10 = gap below desk
    const overflow = panelBottom - scroller.getBoundingClientRect().bottom;
    if (overflow > 0) scroller.scrollBy({ top: overflow + 16, behavior: "smooth" });
  }

  function handleClick() {
    if (deskClickTimerRef.current) clearTimeout(deskClickTimerRef.current);
    deskClickTimerRef.current = setTimeout(() => {
      deskClickTimerRef.current = null;
      if (expanded) {
        setExpanded(false);
        setIsMaximized(false);
      } else {
        void openPanel();
      }
    }, 220);
  }

  function handleDeskDoubleClick(e: React.MouseEvent) {
    e.stopPropagation();
    // Swallow the double-click: cancel the pending single-click so the desk's
    // open/closed state is left unchanged. Double-click never maximizes.
    if (deskClickTimerRef.current) {
      clearTimeout(deskClickTimerRef.current);
      deskClickTimerRef.current = null;
    }
  }

  function toggleMaximized() {
    setIsMaximized((m) => !m);
  }

  const deskColors = ["#6b4c2a", "#5a3e22", "#7a5530", "#4e3018", "#635028", "#724830"];
  const deskColor = deskColors[index % deskColors.length];
  const deskTitle = deskDisplayTitle(session.title, session.title_summary, taskContent);

  // Spawned subagents grouped into delegation rounds (each renders as its own
  // desk: bubble → expandable panel) shown beside the parent desk.
  const subagentRounds = groupSubagentsIntoRounds(Object.values(subagents));
  const subagentCount = subagentRounds.reduce((n, r) => n + r.length, 0);
  const tabItems: { id: DeskTab; label: string }[] = [
    { id: "activity", label: "⚡ Activity" },
    { id: "tasks",    label: "📋 Tasks" },
    ...(files.length > 0 ? [{ id: "files" as DeskTab, label: "📁 Files" }] : []),
    { id: "console" as DeskTab, label: "🖥 Console" },
  ];

  // Inspect needs more room; clamp to the viewport so a wide panel never
  // overflows the screen edge.
  const inspectActive = tab === "console" && consoleView === "inspect";
  const panelW = inspectActive
    ? Math.min(INSPECT_PANEL_WIDTH, Math.max(PANEL_WIDTH, viewportW - 24))
    : PANEL_WIDTH;

  const floatingLayout = useMemo(
    () => (panelDisplayPos ? computeFloatingPanelLayout(panelDisplayPos.top, teamRowHeight) : null),
    [panelDisplayPos, teamRowHeight],
  );

  const maximizedLayout = useMemo(
    () => computeMaximizedPanelLayout(viewportW, viewportH),
    [viewportW, viewportH],
  );

  const autoPanelHeight = floatingLayout?.height ?? PANEL_PREF_HEIGHT;
  const effectivePanelW = panelUserSize?.width ?? panelW;
  const effectivePanelH = panelUserSize?.height ?? autoPanelHeight;

  function getPanelSize(): PanelSize {
    return { width: effectivePanelW, height: effectivePanelH };
  }

  const panelResizeHandle = bindPanelResize(getPanelSize);

  function getPanelTopLeft(): { top: number; left: number } {
    if (panelDragging && panelDragPos) return panelDragPos;
    if (panelDisplayPos && panelRoot) {
      return rowToViewport(panelRoot, {
        top: floatingLayout?.top ?? panelDisplayPos.top,
        left: panelDisplayPos.left,
      });
    }
    return { top: 0, left: 0 };
  }

  const panelDragHandle = bindPanelDrag(getPanelTopLeft);

  const panelViewportPos = getPanelTopLeft();

  const panelStyle: React.CSSProperties = isMaximized ? {
    position: "fixed",
    top: maximizedLayout.top,
    left: maximizedLayout.left,
    width: maximizedLayout.width,
    height: maximizedLayout.height,
    maxHeight: "none",
    transform: "none",
  } : {
    position: "fixed",
    top: panelViewportPos.top,
    left: panelViewportPos.left,
    transform: "none",
    width: effectivePanelW,
    height: effectivePanelH,
    maxHeight: effectivePanelH,
  };

  const panel = expanded && panelDeskOffset && (isMaximized || (panelRoot && panelDisplayPos)) ? createPortal(
    <div
      tabIndex={0}
      style={{
        ...panelStyle,
        background: "var(--bg2)",
        border: "1px solid var(--card-border)",
        borderRadius: 8,
        overflow: "hidden",
        boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
        zIndex: panelZIndex ?? DESK_PANEL_Z_BASE,
        display: "flex",
        flexDirection: "column",
        transition: (panelDragging || panelResizing) ? "none" : "width 0.18s ease, height 0.18s ease, top 0.18s ease, left 0.18s ease",
        outline: "none",
      }}
      onMouseDown={(e) => { e.stopPropagation(); onPanelActivate?.(); }}
      onClick={(e) => e.stopPropagation()}
      // Don't maximize on double-click inside the panel body (e.g. selecting a word
      // in the activity feed). Maximize stays on the tab-bar/header and the ⊞ button.
      onDoubleClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "f") {
          e.preventDefault();
          openFind();
          return;
        }
        if (e.key === "Escape" && findOpen) { e.preventDefault(); setFindOpen(false); return; }
        if ((e.ctrlKey || e.metaKey) && e.key === "a") {
          const el = panelContentRef.current;
          if (!el) return;
          e.preventDefault();
          const range = document.createRange();
          range.selectNodeContents(el);
          window.getSelection()?.removeAllRanges();
          window.getSelection()?.addRange(range);
        }
      }}
    >
      {/* Tabs — drag the bar to reposition; scroll tab labels; keep controls pinned right */}
      <div
        {...(!isMaximized ? panelDragHandle : {})}
        onDoubleClick={(e) => { e.stopPropagation(); toggleMaximized(); }}
        style={{
          display: "flex", alignItems: "stretch",
          borderBottom: "1px solid var(--card-border)",
          padding: "0 0 0 8px", flexShrink: 0, minWidth: 0,
          cursor: !isMaximized ? (panelDragging ? "grabbing" : "grab") : undefined,
        }}
        title={!isMaximized ? "Drag to move · double-click to maximize" : "Double-click to restore"}
      >
        <div style={{ display: "flex", flex: 1, minWidth: 0, overflowX: "auto" }}>
        {tabItems.map(({ id, label }) => {
          const hits = !findOpen ? 0
            : id === "activity" ? findCounts.activity
            : id === "console" ? findCounts.console + findCounts.debug
            : 0;
          return (
          <button
            key={id}
            onClick={(e) => {
              e.stopPropagation();
              setTab(id);
              // Opening/clicking the Files tab pulls the latest workspace tree.
              if (id === "files") loadWorkspaceFiles(session.id).then(setFiles).catch(() => {});
            }}
            onDoubleClick={(e) => e.stopPropagation()}
            style={{
              padding: "8px 10px",
              fontSize: 12, fontWeight: tab === id ? 600 : 400,
              color: tab === id ? "var(--accent2)" : "var(--text-dim)",
              borderBottom: tab === id ? "2px solid var(--accent2)" : "2px solid transparent",
              marginBottom: -1, whiteSpace: "nowrap",
            }}
          >
            {label}
            {hits > 0 && (
              <span style={{
                marginLeft: 4, fontSize: 9, fontWeight: 700, padding: "0 4px",
                borderRadius: 7, background: "var(--accent2)", color: "#fff",
              }}>{hits}</span>
            )}
          </button>
          );
        })}
        </div>
        <div style={{ display: "flex", flexShrink: 0, alignItems: "center", paddingRight: 4 }}>
        <button
          onClick={(e) => { e.stopPropagation(); findOpen ? setFindOpen(false) : openFind(); }}
          onDoubleClick={(e) => e.stopPropagation()}
          title="Find in desk (⌘F)"
          style={{ fontSize: 13, color: findOpen ? "var(--accent2)" : "var(--text-dim)", padding: "8px 6px" }}
        >🔍</button>
        <button
          onClick={(e) => { e.stopPropagation(); toggleMaximized(); }}
          onDoubleClick={(e) => e.stopPropagation()}
          title={isMaximized ? "Restore" : "Maximize (full screen)"}
          style={{ fontSize: 14, color: "var(--text-dim)", padding: "8px 6px" }}
        >{isMaximized ? "⊡" : "⊞"}</button>
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(false); setIsMaximized(false); setPanelDeskOffset(null); resetPanelUserPos(); resetPanelUserSize(); }}
          onDoubleClick={(e) => e.stopPropagation()}
          style={{ fontSize: 16, color: "var(--text-dim)", padding: "8px 6px" }}
        >×</button>
        </div>
      </div>

      <style>{`
        ::highlight(deskfind){ background: rgba(255,213,79,0.40); }
        ::highlight(deskfind-cur){ background: #ff9800; color: #000; }
      `}</style>

      {/* Find bar */}
      {findOpen && (
        <div style={{
          display: "flex", alignItems: "center", gap: 6, flexShrink: 0,
          padding: "5px 8px", borderBottom: "1px solid var(--card-border)", background: "var(--bg)",
        }}>
          <input
            ref={findInputRef}
            value={findQuery}
            onChange={(e) => setFindQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); findGo(e.shiftKey ? -1 : 1); }
              else if (e.key === "Escape") { e.preventDefault(); setFindOpen(false); }
            }}
            placeholder="Find in desk…  (Enter ↓ / Shift+Enter ↑)"
            autoFocus
            style={{
              flex: 1, background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)",
              borderRadius: 5, padding: "4px 8px", color: "var(--text)", fontSize: 12, outline: "none",
            }}
          />
          <span style={{ fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap", minWidth: 56, textAlign: "right" }}>
            {findCount > 0 ? `${(((findIndex % findCount) + findCount) % findCount) + 1}/${findCount} here` : (findQuery.trim() ? "0 here" : "")}
          </span>
          {[["↑", -1, "Previous (Shift+Enter)"], ["↓", 1, "Next (Enter)"]].map(([sym, dir, t]) => (
            <button key={sym as string} title={t as string}
              onClick={() => findGo(dir as 1 | -1)}
              style={{ fontSize: 12, color: "var(--text-dim)", padding: "2px 6px", borderRadius: 4, border: "1px solid var(--card-border)", background: "transparent", cursor: "pointer" }}
            >{sym}</button>
          ))}
          <button title="Close (Esc)" onClick={() => setFindOpen(false)}
            style={{ fontSize: 14, color: "var(--text-dim)", padding: "2px 6px", background: "transparent", cursor: "pointer" }}
          >×</button>
        </div>
      )}

      {/* Content */}
      <div ref={panelContentRef} style={{ flex: 1, overflowY: "auto", minHeight: 180 }}>
        {tab === "activity" && (
          <>
            {/* Feed ↔ Overview view switch (sticky at the top of the feed) */}
            <div style={{
              position: "sticky", top: 0, zIndex: 2,
              display: "flex", gap: 6, alignItems: "center",
              padding: "6px 10px", background: "var(--bg2)",
              borderBottom: "1px solid var(--card-border)",
            }}>
              {(["feed", "overview", "history"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setActivityView(v)}
                  style={{
                    fontSize: 11, padding: "3px 10px", borderRadius: 6, cursor: "pointer",
                    background: activityView === v ? "var(--accent2)" : "transparent",
                    color: activityView === v ? "#fff" : "var(--text-dim)",
                    border: `1px solid ${activityView === v ? "var(--accent2)" : "var(--card-border)"}`,
                  }}
                >
                  {v === "feed" ? "💬 Feed" : v === "overview" ? "📊 Overview" : "📜 History"}
                </button>
              ))}
              <div style={{ flex: 1 }} />
              <button
                onClick={handleExportDesk}
                disabled={exporting}
                title="Save this desk to a JSON file (config, TASK.md, and session history)"
                style={{
                  fontSize: 11, padding: "3px 10px", borderRadius: 6,
                  cursor: exporting ? "default" : "pointer",
                  background: "transparent", color: "var(--text-dim)",
                  border: "1px solid var(--card-border)",
                }}
              >
                {exporting ? "Saving…" : "💾 Save desk"}
              </button>
            </div>
            {activityView === "overview" ? (
              <ActivityOverview
                events={overviewReady ? overviewDesk!.events : activity}
                liveEvents={liveEvents}
                taskContent={taskContent}
                startTime={overviewReady ? overviewDesk!.started_at ?? session.started_at : session.started_at}
                deskEndTime={overviewReady ? overviewDesk!.last_at ?? undefined : undefined}
              />
            ) : activityView === "history" ? (
              <DeskHistoryView history={deskHistory} />
            ) : (
              <ActivityFeed
                events={activity}
                liveEvents={liveEvents}
                loading={loading}
                isActive={!session.ended_at}
                liveState={liveState}
                verbose={verbose}
                immediateUserMessage={taskContent}
                immediateUserImages={taskImages}
                pendingUserMessages={sentMsgs}
                pendingAgentMessages={interruptedReplies}
                scrollContainerRef={panelContentRef}
              />
            )}
          </>
        )}
        {tab === "tasks" && <TasksView sessionId={session.id} onTaskSaved={() => {
          api.sessions.resume(session.id, "TASK.md has been updated. Read it and execute the tasks described there.", undefined, undefined, reasoningEffort, apiMode)
            .then(() => setWsEpoch((e) => e + 1))
            .catch(() => {});
        }} onAskManager={onAskManager} />}
        {tab === "files" && (
          <FilesView
            nodes={files}
            onRefresh={refreshFiles}
            refreshing={filesRefreshing}
            onPreview={(d) => {
              refreshFiles();
              onPreview(d);
            }}
          />
        )}
        {tab === "console" && (
          <>
            {/* Agent Console ↔ Debug terminal sub-view switch (sticky at top) */}
            <div style={{
              position: "sticky", top: 0, zIndex: 2,
              display: "flex", gap: 6, alignItems: "center",
              padding: "6px 10px", background: "var(--bg2)",
              borderBottom: "1px solid var(--card-border)",
            }}>
              {([
                ["debug", "🐞 Debug terminal", "Full worker stream: tool calls, args, results, reasoning, and log lines"],
                ["agent", "🤖 Agent Console", "What the agent's shell commands print — like watching a person run them in a terminal"],
                ["inspect", "🔍 Inspect", "Run an ad-hoc tool against this desk and view its command/output"],
              ] as const).map(([v, lbl, tip]) => (
                <button
                  key={v}
                  onClick={() => setConsoleView(v)}
                  title={tip}
                  style={{
                    fontSize: 11, padding: "3px 10px", borderRadius: 6, cursor: "pointer",
                    background: consoleView === v ? "var(--accent2)" : "transparent",
                    color: consoleView === v ? "#fff" : "var(--text-dim)",
                    border: `1px solid ${consoleView === v ? "var(--accent2)" : "var(--card-border)"}`,
                  }}
                >
                  {lbl}
                </button>
              ))}
            </div>
            {consoleView === "agent" ? (
              <div style={{
                fontFamily: "monospace", fontSize: 11, lineHeight: 1.6,
                padding: "8px 10px", whiteSpace: "pre-wrap", wordBreak: "break-all",
                color: "#d4d4d4", background: "#0d0d14", minHeight: 200,
              }}>
                {consoleLines.length === 0
                  ? <span style={{ color: "#555" }}>Waiting for commands…{"\n"}(output appears here when the agent runs terminal/execute_code commands)</span>
                  : <span dangerouslySetInnerHTML={{ __html: escapeHtml(applyCarriageReturns(stripAnsi(consoleLines.join(""))))
                      .replace(/\$ (.+)/g, '<span style="color:#4ec9b0">$ <span style="color:#9cdcfe">$1</span></span>') }} />
                }
                <div ref={consoleBottomRef} />
              </div>
            ) : consoleView === "debug" ? (
              <div style={{
                fontFamily: "monospace", fontSize: 11, lineHeight: 1.5,
                padding: "8px 10px", whiteSpace: "pre-wrap", wordBreak: "break-all",
                color: "#a0ffa0", background: "#080810", minHeight: 200,
              }}>
                {termLines.length === 0
                  ? <span style={{ color: "#555" }}>Waiting for output…</span>
                  : <span>{applyCarriageReturns(stripAnsi(termLines.join("")))}</span>
                }
                <div ref={termBottomRef} />
              </div>
            ) : (
              <InspectPanel sessionId={session.id} />
            )}
          </>
        )}
      </div>

      {/* Auto-continue (heartbeat) toggle — hidden until the heartbeat is reliable
          on open-ended/looping tasks (see AUTO_CONTINUE_UI_ENABLED above). */}
      {AUTO_CONTINUE_UI_ENABLED && tab === "activity" && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px 0" }}>
          <button
            onClick={toggleAutoContinue}
            title="Heartbeat: when on, the agent auto-resumes after each turn — checking TASK.md against its progress — until the goal is judged complete (capped). Use for long, multi-step tasks. Stop/interrupt turns it off."
            style={{
              fontSize: 10, padding: "2px 9px", borderRadius: 11, cursor: "pointer",
              display: "flex", alignItems: "center", gap: 5,
              background: autoContinue ? "rgba(78,220,163,0.15)" : "transparent",
              color: autoContinue ? "var(--green)" : "var(--text-dim)",
              border: `1px solid ${autoContinue ? "var(--green)" : "var(--card-border)"}`,
            }}
          >
            🔁 Auto-continue {autoContinue ? "on" : "off"}
          </button>
          {autoContinue && (
            <span style={{ fontSize: 9.5, color: "var(--text-dim)" }}>
              keeps working until TASK.md is done
            </span>
          )}
        </div>
      )}

      {/* Chat input */}
      {tab === "activity" && (
        <div
          style={{
            display: "flex", flexDirection: "column", gap: 6, padding: "8px",
            borderTop: `1px solid ${chatDragOver ? "var(--accent2)" : "var(--card-border)"}`,
            background: chatDragOver ? "rgba(100,160,255,0.06)" : "var(--bg2)", flexShrink: 0,
            transition: "background 0.15s, border-color 0.15s",
          }}
          onDoubleClick={(e) => e.stopPropagation()}
          onDragOver={(e) => { e.preventDefault(); setChatDragOver(true); }}
          onDragLeave={() => setChatDragOver(false)}
          onDrop={async (e) => {
            e.preventDefault();
            setChatDragOver(false);
            const { text, images } = await _processFiles(Array.from(e.dataTransfer.files));
            if (text) setChatInput((prev) => prev ? `${prev}\n${text}` : text);
            if (images.length) setChatImages((prev) => [...prev, ...images]);
          }}
        >
          {chatImages.length > 0 && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {chatImages.map((img, i) => (
                <div key={i} style={{ position: "relative" }}>
                  <img src={img.url} alt={img.name} title={img.name}
                    style={{ height: 52, maxWidth: 80, objectFit: "cover", borderRadius: 4,
                      border: "1px solid var(--card-border)", display: "block" }} />
                  <button onClick={() => setChatImages((prev) => prev.filter((_, j) => j !== i))}
                    style={{ position: "absolute", top: -4, right: -4, width: 16, height: 16,
                      borderRadius: "50%", fontSize: 9,
                      background: "var(--red)", color: "white", border: "none", cursor: "pointer",
                      display: "flex", alignItems: "center", justifyContent: "center" }}>✕</button>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
          <input
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onFocus={() => onFocus?.()}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            placeholder={chatDragOver ? "Drop image or file here…" : isRunning ? "Redirect the agent — interrupts the current turn…" : "Send a follow-up…  (drop files/images to attach)"}
            style={{
              flex: 1, background: "var(--bg)", border: "1px solid var(--card-border)",
              borderRadius: 6, padding: "6px 10px", fontSize: 12,
              color: "var(--text)", outline: "none",
            }}
          />
          <button
            onClick={handleSend}
            disabled={sending || (!chatInput.trim() && chatImages.length === 0)}
            style={{
              padding: "6px 12px", borderRadius: 6, fontSize: 12,
              background: sending || (!chatInput.trim() && chatImages.length === 0) ? "var(--bg)" : (isRunning ? "var(--yellow)" : "var(--accent2)"),
              color: sending || (!chatInput.trim() && chatImages.length === 0) ? "var(--text-dim)" : (isRunning ? "#1a1a2e" : "white"),
              border: "1px solid var(--card-border)",
              cursor: sending || (!chatInput.trim() && chatImages.length === 0) ? "default" : "pointer",
              flexShrink: 0,
            }}
          >
            {sending ? "…" : "Send"}
          </button>
          </div>
        </div>
      )}
      {!isMaximized && (
        <PanelResizeHandle active={panelResizing} bind={panelResizeHandle} />
      )}
    </div>,
    document.body,
  ) : null;

  return (
    <>
      <div
        ref={deskRef}
        style={{ display: "flex", flexDirection: "column", alignItems: "center", position: "relative" }}
      >
        {/* Close button */}
        <button
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          style={{
            position: "absolute", top: -8, right: -8,
            width: 18, height: 18, borderRadius: "50%",
            background: "var(--bg2)", border: "1px solid var(--card-border)",
            color: "var(--text-dim)", fontSize: 11, zIndex: 10,
            display: "flex", alignItems: "center", justifyContent: "center",
            cursor: "pointer",
          }}
          title="Delete desk (removes session data)"
        >×</button>

        {/* Spawned subagents — each its own desk (bubble → expandable panel),
            grouped by delegation round in the gap to the right of this desk. */}
        {subagentCount > 0 && (
          <div style={{
            position: "absolute", left: "100%", top: 0, marginLeft: 10, zIndex: 5,
            display: "flex", flexDirection: "column", gap: 10, alignItems: "flex-start",
            maxWidth: 168,
          }}>
            {subagentRounds.map((round, ri) => {
              const open = expandedRounds.has(ri);
              const anyRunning = round.some(({ rec }) => rec.status === "running");
              return (
              <div key={ri} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandedRounds((prev) => {
                      const next = new Set(prev);
                      next.has(ri) ? next.delete(ri) : next.add(ri);
                      return next;
                    });
                  }}
                  title={open ? "Collapse round" : "Expand round"}
                  style={{
                    display: "flex", alignItems: "center", gap: 4, cursor: "pointer",
                    fontSize: 9, textTransform: "uppercase", letterSpacing: 0.5,
                    color: "var(--text-dim)", fontWeight: 600, whiteSpace: "nowrap",
                  }}
                >
                  <span style={{ display: "inline-block", width: 7 }}>{open ? "▾" : "▸"}</span>
                  {subagentRounds.length > 1 ? `Round ${ri + 1}` : "Subagents"}
                  <span style={{ opacity: 0.8 }}>· {round.length}</span>
                  {anyRunning && (
                    <span style={{
                      width: 6, height: 6, borderRadius: "50%",
                      background: "var(--red)", marginLeft: 2,
                    }} />
                  )}
                </button>
                {open && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "flex-start" }}>
                    {round.map(({ rec, index }) => (
                      <SubagentDesk key={rec.subagent_id} rec={rec} index={index} />
                    ))}
                  </div>
                )}
              </div>
              );
            })}
          </div>
        )}

        {/* Clickable desk body */}
        <div
          style={{
            width: 200, cursor: "pointer", userSelect: "none", borderRadius: 8,
            outline: deskFocused
              ? "2px solid var(--accent2)"
              : isActive
              ? "2px solid var(--accent2)"
              : searchMatch
                ? "2px solid var(--yellow)"
                : "2px solid transparent",
            outlineOffset: 4,
            boxShadow: searchMatch && !isActive ? "0 0 12px rgba(255,213,79,0.45)" : "none",
            transition: "outline-color 0.3s ease, box-shadow 0.3s ease",
          }}
          onClick={handleClick}
          onDoubleClick={handleDeskDoubleClick}
          title={expanded ? "Click to close" : "Click to open"}
        >
          {/* Monitor */}
          <div style={{
            width: 120, height: 80, margin: "0 auto",
            background: "#1a1a2e", border: "3px solid #333",
            borderRadius: "6px 6px 2px 2px",
            position: "relative", display: "flex", alignItems: "center", justifyContent: "center",
            overflow: "hidden",
          }}>
            <div style={{ padding: 6, width: "100%", height: "100%", overflow: "hidden" }}>
              {[...Array(5)].map((_, i) => (
                <div key={i} style={{
                  height: 4, margin: "3px 2px",
                  background: i === 0 ? "var(--accent2)" : "rgba(255,255,255,0.15)",
                  borderRadius: 2,
                  width: i === 0 ? "70%" : i === 2 ? "55%" : i === 4 ? "40%" : "85%",
                  animation: isActive && !session.ended_at && session.is_running !== false
                    ? `pulse-line 2s ${i * 0.3}s ease-in-out infinite`
                    : "none",
                }} />
              ))}
            </div>
            <div style={{
              position: "absolute", top: 5, right: 5,
              width: 6, height: 6, borderRadius: "50%",
              background: statusColor(session),
              boxShadow: session.is_running ? `0 0 6px ${statusColor(session)}` : "none",
            }} />
          </div>
          <div style={{ width: 8, height: 10, margin: "0 auto", background: "#333" }} />
          <div style={{ width: 40, height: 4, margin: "0 auto", background: "#333", borderRadius: 2 }} />
          <div style={{
            background: deskColor, height: 18, borderRadius: "4px 4px 2px 2px", marginTop: 4,
            boxShadow: "inset 0 -3px 0 rgba(0,0,0,0.3), inset 0 2px 0 rgba(255,255,255,0.1)",
            display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 8px",
          }}>
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <div style={{ width: 12, height: 10, background: "#4a3a2a", borderRadius: 1, opacity: 0.7 }} />
              <div style={{ width: 4, height: 8, background: "#e94560", borderRadius: 1, opacity: 0.8 }} />
            </div>
            <div style={{ fontSize: 10, color: "rgba(255,255,255,0.5)" }}>{session.message_count} msgs</div>
          </div>
          <div style={{
            background: `color-mix(in srgb, ${deskColor} 70%, black)`,
            height: 14, borderRadius: "2px 2px 6px 6px", boxShadow: "0 4px 8px rgba(0,0,0,0.4)",
          }} />
          <div style={{ marginTop: 6, padding: "4px 6px", textAlign: "center" }}>
            <div style={{
              fontSize: 11, fontWeight: 600, color: "var(--text)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 190,
            }} title={deskTitle}>{deskTitle}</div>
            <div style={{ display: "flex", justifyContent: "center", gap: 8, marginTop: 2 }}>
              <span style={{ fontSize: 10, color: statusColor(session) }}>● {statusLabel(session)}</span>
              {session.task_solved && (
                <span
                  title="Manager audit passed — all checks green"
                  style={{ fontSize: 10, color: "var(--green)", fontWeight: 700 }}
                >✓ solved</span>
              )}
              <span style={{ fontSize: 10, color: "var(--text-dim)" }}>{elapsedLabel(session)}</span>
            </div>
            {session.title_summary && session.title_summary.trim() !== deskTitle && (
              <div style={{
                marginTop: 3, fontSize: 9, color: "var(--accent2)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                maxWidth: 190, fontStyle: "italic",
              }} title={session.title_summary}>
                {session.title_summary}
              </div>
            )}
            {/* Profile · model line — under the status, above resume/stop. */}
            {(() => {
              const label = profileLabel || "Default";
              const model = profileModel || session.agent_model || session.model || "";
              return (
                <div
                  title={`Profile: ${label}${model ? ` · Model: ${model}` : ""}`}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 5,
                    marginTop: 4, maxWidth: 190, marginInline: "auto",
                  }}
                >
                  <span style={{
                    width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                    background: profileColor || "#6a7a9a",
                  }} />
                  <span style={{ fontSize: 9.5, fontWeight: 600, color: "var(--text)", whiteSpace: "nowrap" }}>
                    {label}
                  </span>
                  {model && (
                    <span style={{
                      fontSize: 9, color: "var(--text-dim)", fontFamily: "ui-monospace, monospace",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0,
                    }}>· {model}</span>
                  )}
                </div>
              );
            })()}
            {!isRunning ? (
              <button
                onClick={(e) => { e.stopPropagation(); handleResume(); }}
                disabled={resuming}
                title="Resume this desk's last task"
                style={{
                  marginTop: 8, padding: "3px 14px", borderRadius: 6, fontSize: 11,
                  background: resuming ? "var(--bg)" : "var(--accent2)",
                  color: resuming ? "var(--text-dim)" : "white",
                  border: "1px solid var(--card-border)",
                  cursor: resuming ? "default" : "pointer",
                }}
              >
                {resuming ? "Resuming…" : "▶ Resume"}
              </button>
            ) : (
              <button
                onClick={(e) => { e.stopPropagation(); handleStop(); }}
                title="Temporarily stop this agent (Resume to continue)"
                style={{
                  marginTop: 8, padding: "3px 14px", borderRadius: 6, fontSize: 11,
                  background: "rgba(74,142,255,0.15)", color: "#4a8eff",
                  border: "1px solid #4a8eff", cursor: "pointer",
                }}
              >
                ⏸ Stop
              </button>
            )}
          </div>
          <div style={{ marginTop: 4, display: "flex", justifyContent: "center" }}>
            <div style={{
              fontSize: 20,
              filter: expanded ? "drop-shadow(0 0 4px var(--accent2))" : "none",
              transition: "filter 0.2s",
            }}>
              {expanded ? "📂" : "📁"}
            </div>
          </div>
        </div>
      </div>

      {panel}

      <style>{`
        @keyframes pulse-line  { 0%,100% { opacity: 0.6; } 50% { opacity: 1; } }
        @keyframes think-pulse { 0%,100% { opacity: 0.2; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1.2); } }
      `}</style>
    </>
  );
}
