import { useEffect, useRef, useState } from "react";
import type { Session, Team } from "../types";

const SNAPSHOTS_KEY = "hermes-snapshots";
const SNAPSHOT_PREFIX = "hermes-snapshot-";
const WORKBENCH_KEY_V2 = "hermes-workbench-v2";

interface SessionSummary {
  id: string;
  title: string;
  workspacePath?: string | null;
}

interface SnapshotMeta {
  name: string;
  savedAt: string;
  note?: string;
  sessions?: SessionSummary[];
}

function loadIndex(): SnapshotMeta[] {
  try {
    const raw = localStorage.getItem(SNAPSHOTS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

function saveIndex(index: SnapshotMeta[]) {
  try { localStorage.setItem(SNAPSHOTS_KEY, JSON.stringify(index)); } catch {}
}

interface Props {
  teams: Team[];
  sessions: Session[];
  onLoadSnapshot: () => void;
}

export function SnapshotMenu({ teams, sessions, onLoadSnapshot }: Props) {
  const [open, setOpen] = useState(false);
  const [snapshots, setSnapshots] = useState<SnapshotMeta[]>([]);
  const [nameInput, setNameInput] = useState("");
  const [noteInput, setNoteInput] = useState("");
  const [expandedNote, setExpandedNote] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) setSnapshots(loadIndex());
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  function buildSessionSummaries(): SessionSummary[] {
    const sessionMap = new Map(sessions.map((s) => [s.id, s]));
    return teams
      .flatMap((t) => t.desks)
      .filter((d) => !("isPending" in d))
      .flatMap((d) => {
        const s = sessionMap.get((d as Session).id);
        if (!s) return [];
        const summary: SessionSummary = { id: s.id, title: s.title_summary || s.title || s.id };
        if (s.workspace_path) summary.workspacePath = s.workspace_path;
        return [summary];
      });
  }

  function handleSave() {
    const name = nameInput.trim();
    if (!name) return;
    try {
      const current = localStorage.getItem(WORKBENCH_KEY_V2);
      if (!current) return;
      localStorage.setItem(SNAPSHOT_PREFIX + name, current);
      const index = loadIndex();
      const existing = index.findIndex((s) => s.name === name);
      const meta: SnapshotMeta = {
        name,
        savedAt: new Date().toISOString(),
        note: noteInput.trim() || undefined,
        sessions: buildSessionSummaries(),
      };
      if (existing >= 0) index[existing] = meta;
      else index.unshift(meta);
      saveIndex(index);
      setSnapshots(index);
      setNameInput("");
      setNoteInput("");
    } catch {}
  }

  function handleLoad(name: string) {
    try {
      const raw = localStorage.getItem(SNAPSHOT_PREFIX + name);
      if (!raw) return;
      localStorage.setItem(WORKBENCH_KEY_V2, raw);
      setOpen(false);
      onLoadSnapshot();
    } catch {}
  }

  function handleDelete(name: string) {
    try {
      localStorage.removeItem(SNAPSHOT_PREFIX + name);
      const index = loadIndex().filter((s) => s.name !== name);
      saveIndex(index);
      setSnapshots(index);
    } catch {}
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title="Snapshots — save or restore workbench layout"
        style={{
          height: 32, padding: "0 10px",
          background: open ? "var(--accent2)" : "rgba(255,255,255,0.06)",
          border: "1px solid var(--card-border)",
          borderRadius: 6, color: open ? "white" : "var(--text-dim)",
          fontSize: 11, display: "flex", alignItems: "center", gap: 5,
          cursor: "pointer",
        }}
      >
        <span style={{ fontSize: 13 }}>💾</span> Snapshots
      </button>

      {open && (
        <div style={{
          position: "absolute", top: 38, right: 0, zIndex: 200, width: 320,
          background: "var(--bg2)", border: "1px solid var(--card-border)",
          borderRadius: 8, boxShadow: "0 8px 32px rgba(0,0,0,0.6)", padding: 12,
        }}>
          {/* ── Save form ── */}
          <div style={{
            fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
            textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8,
          }}>
            Save current workbench
          </div>
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            <input
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSave(); }}
              placeholder="Snapshot name…"
              style={{
                flex: 1,
                background: "rgba(255,255,255,0.06)",
                border: "1px solid var(--card-border)",
                borderRadius: 6, padding: "5px 9px",
                color: "var(--text)", fontSize: 12, outline: "none",
              }}
              onFocus={(e) => (e.target.style.borderColor = "var(--accent2)")}
              onBlur={(e) => (e.target.style.borderColor = "var(--card-border)")}
            />
            <button
              onClick={handleSave}
              disabled={!nameInput.trim()}
              style={{
                padding: "5px 11px", borderRadius: 6, fontSize: 12,
                cursor: nameInput.trim() ? "pointer" : "default",
                background: nameInput.trim() ? "var(--accent2)" : "rgba(255,255,255,0.04)",
                color: nameInput.trim() ? "white" : "var(--text-dim)",
                border: "1px solid var(--card-border)",
                flexShrink: 0,
              }}
            >
              Save
            </button>
          </div>
          <textarea
            value={noteInput}
            onChange={(e) => setNoteInput(e.target.value)}
            placeholder="Optional note…"
            rows={2}
            style={{
              width: "100%", boxSizing: "border-box",
              background: "rgba(255,255,255,0.04)",
              border: "1px solid var(--card-border)",
              borderRadius: 6, padding: "5px 9px",
              color: "var(--text)", fontSize: 11,
              resize: "none", outline: "none", fontFamily: "inherit",
            }}
            onFocus={(e) => (e.target.style.borderColor = "var(--accent2)")}
            onBlur={(e) => (e.target.style.borderColor = "var(--card-border)")}
          />

          {/* ── Snapshot list ── */}
          {snapshots.length > 0 ? (
            <>
              <div style={{
                fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
                textTransform: "uppercase", letterSpacing: "0.05em", margin: "14px 0 8px",
              }}>
                Saved snapshots
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {snapshots.map((s) => {
                  const expanded = expandedNote === s.name;
                  return (
                    <div
                      key={s.name}
                      style={{
                        borderRadius: 6,
                        background: "rgba(255,255,255,0.04)",
                        border: "1px solid var(--card-border)",
                        overflow: "hidden",
                      }}
                    >
                      {/* Header row */}
                      <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 9px" }}>
                        <span style={{
                          flex: 1, fontSize: 12, fontWeight: 600, color: "var(--text)",
                          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        }}>
                          {s.name}
                        </span>
                        <span style={{ fontSize: 10, color: "var(--text-dim)", flexShrink: 0 }}>
                          {new Date(s.savedAt).toLocaleDateString()}
                        </span>
                        {(s.note || (s.sessions && s.sessions.length > 0)) && (
                          <button
                            onClick={() => setExpandedNote(expanded ? null : s.name)}
                            title={expanded ? "Collapse" : "Show details"}
                            style={{
                              padding: "2px 5px", borderRadius: 4, fontSize: 10, cursor: "pointer",
                              background: expanded ? "var(--accent2)" : "rgba(255,255,255,0.08)",
                              color: expanded ? "white" : "var(--text-dim)",
                              border: "1px solid var(--card-border)", flexShrink: 0,
                            }}
                          >
                            {expanded ? "▲" : "▼"}
                          </button>
                        )}
                        <button
                          onClick={() => handleLoad(s.name)}
                          title={`Load "${s.name}"`}
                          style={{
                            padding: "3px 8px", borderRadius: 4, fontSize: 10, cursor: "pointer",
                            background: "var(--accent2)", color: "white",
                            border: "1px solid transparent", flexShrink: 0,
                          }}
                        >
                          Load
                        </button>
                        <button
                          onClick={() => handleDelete(s.name)}
                          title={`Delete "${s.name}"`}
                          style={{
                            padding: "3px 6px", borderRadius: 4, fontSize: 10, cursor: "pointer",
                            background: "rgba(255,255,255,0.06)", color: "var(--text-dim)",
                            border: "1px solid var(--card-border)", flexShrink: 0,
                          }}
                        >
                          ✕
                        </button>
                      </div>

                      {/* Inline note preview (collapsed) */}
                      {!expanded && s.note && (
                        <div style={{
                          padding: "0 9px 6px",
                          fontSize: 11, color: "var(--text-dim)",
                          fontStyle: "italic",
                          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        }}>
                          {s.note}
                        </div>
                      )}

                      {/* Expanded detail panel */}
                      {expanded && (
                        <div style={{
                          borderTop: "1px solid var(--card-border)",
                          padding: "8px 9px",
                          display: "flex", flexDirection: "column", gap: 6,
                        }}>
                          {s.note && (
                            <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.4 }}>
                              {s.note}
                            </div>
                          )}
                          {s.sessions && s.sessions.length > 0 && (
                            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                              {s.sessions.map((sess) => (
                                <div key={sess.id} style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                                  <span style={{ fontSize: 11, color: "var(--text)", fontWeight: 500,
                                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {sess.title}
                                  </span>
                                  {sess.workspacePath && (
                                    <span
                                      title={sess.workspacePath}
                                      style={{
                                        fontSize: 10, color: "var(--text-dim)", fontFamily: "monospace",
                                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                                        direction: "rtl", textAlign: "left",
                                      }}
                                    >
                                      {sess.workspacePath}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 10, opacity: 0.7 }}>
              No snapshots yet.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
