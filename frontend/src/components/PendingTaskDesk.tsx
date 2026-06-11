import { createPortal } from "react-dom";
import { useEffect, useRef, useState } from "react";
import type { PendingAssignment } from "../types";
import { usePanelDrag } from "../usePanelDrag";
import { usePanelResize } from "../usePanelResize";
import { PanelResizeHandle } from "./PanelResizeHandle";
import {
  centeredAnchorToDeskOffset,
  deskOffsetFromViewport,
  rowToViewport,
  useDeskAnchoredRowPosition,
  viewportToRow,
  type DeskOffset,
} from "../deskPanelAnchor";
import { useTeamRowPanel } from "../TeamRowPanelContext";

export type PanelAnchor = { top: number; left: number };

const TEXT_EXTS = new Set([
  "txt","md","py","js","ts","jsx","tsx","json","csv","yaml","yml",
  "html","css","xml","sh","bash","sql","r","toml","ini","cfg","log",
  "rst","java","c","cpp","h","hpp","go","rs","rb","php","swift","kt",
]);
const IMAGE_EXTS = new Set(["jpg","jpeg","png","gif","webp","svg"]);

type AttachedImage = { name: string; url: string };

async function processFiles(files: File[]): Promise<{ textAppend: string; images: AttachedImage[] }> {
  const parts: string[] = [];
  const images: AttachedImage[] = [];
  for (const file of files) {
    const ext = (file.name.split(".").pop() ?? "").toLowerCase();
    if (IMAGE_EXTS.has(ext)) {
      const url = await new Promise<string>((res) => {
        const fr = new FileReader();
        fr.onload = (e) => res(e.target!.result as string);
        fr.readAsDataURL(file);
      });
      images.push({ name: file.name, url });
      // No text marker: the image is shown as a thumbnail and sent as an
      // attachment (the server tells the agent its saved path), so a
      // "[Attached image: …]" line would just be redundant UI noise.
    } else if (TEXT_EXTS.has(ext) || file.type.startsWith("text/")) {
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
  return { textAppend: parts.join("\n"), images };
}

interface Props {
  deskIndex: number;
  scene?: string;
  isActive: boolean;
  dropHighlight?: boolean;
  initialMsg?: string;
  assignment?: PendingAssignment | null;
  onStart: (msg: string, agentId: string, images?: AttachedImage[], anchor?: PanelAnchor) => Promise<void>;
  onSelect: () => void;
  onClose: () => void;
  onMsgChange?: (msg: string) => void;
}

const DESK_COLORS = ["#6b4c2a", "#5a3e22", "#7a5530", "#4e3018", "#635028", "#724830"];

function Dot({ delay, size = 5 }: { delay: number; size?: number }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: "50%",
      background: "var(--accent2)",
      animation: `pendot 0.9s ease-in-out ${delay}s infinite`,
    }} />
  );
}

const THINKING_PANEL_W = 380;
const THINKING_PANEL_H = 420;
const THINKING_PANEL_MIN = { width: 280, height: 240 };

export function PendingTaskDesk({ deskIndex, scene, isActive, dropHighlight, initialMsg, assignment, onStart, onSelect, onClose, onMsgChange }: Props) {
  const [msg, setMsg] = useState(initialMsg ?? "");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const agentId = assignment?.agentId ?? "";
  const [panelDeskOffset, setPanelDeskOffset] = useState<DeskOffset | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const onDragCommitRef = useRef<(vp: { top: number; left: number }) => void>(() => {});
  const { pos: panelDragPos, resetPos: resetPanelUserPos, dragging: panelDragging, bindHandle: bindPanelDrag } = usePanelDrag(12, (vp) => onDragCommitRef.current(vp));
  const { size: panelUserSize, resetSize: resetPanelUserSize, resizing: panelResizing, bindResize: bindPanelResize } = usePanelResize(THINKING_PANEL_MIN);
  const [dragOver, setDragOver] = useState(false);
  const [attachedImages, setAttachedImages] = useState<AttachedImage[]>([]);
  const promptBoxRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const deskColor = DESK_COLORS[deskIndex % DESK_COLORS.length];
  const { root: panelRoot } = useTeamRowPanel();
  const rowRef = useRef<HTMLElement | null>(null);
  rowRef.current = panelRoot;
  onDragCommitRef.current = (vp) => {
    if (containerRef.current) setPanelDeskOffset(deskOffsetFromViewport(containerRef.current, vp));
    resetPanelUserPos();
  };
  const panelRowPos = useDeskAnchoredRowPosition(containerRef, rowRef, panelDeskOffset, sending && !panelDragging && !!panelRoot);
  const panelDisplayPos = (() => {
    if (panelDragging && panelDragPos && panelRoot) return viewportToRow(panelRoot, panelDragPos);
    return panelRowPos;
  })();

  useEffect(() => {
    if (isActive && !sending) textRef.current?.focus();
  }, [isActive, sending]);

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (!files.length) return;
    const { textAppend, images } = await processFiles(files);
    if (textAppend) setMsg((prev) => prev ? `${prev}\n\n${textAppend}` : textAppend);
    if (images.length) setAttachedImages((prev) => [...prev, ...images]);
  }

  async function handleSend() {
    const text = msg.trim();
    if (!text || sending) return;
    let anchor: PanelAnchor | undefined;
    if (promptBoxRef.current && containerRef.current) {
      const r = promptBoxRef.current.getBoundingClientRect();
      anchor = { top: r.bottom + 10, left: r.left + r.width / 2 };
      setPanelDeskOffset(centeredAnchorToDeskOffset(containerRef.current, anchor.left, anchor.top, THINKING_PANEL_W));
    }
    resetPanelUserPos();
    resetPanelUserSize();
    setSending(true);
    setError(null);
    try {
      await onStart(text, agentId, attachedImages.length > 0 ? attachedImages : undefined, anchor);
    } catch (e) {
      // Surface the real server reason (e.g. 409 "agent already in use on another
      // desk") instead of always blaming Hermes.
      const detail = e instanceof Error ? e.message : "";
      setError(detail || "Failed to start — is Hermes running?");
      setSending(false);
    }
  }

  const panelW = panelUserSize?.width ?? THINKING_PANEL_W;
  const panelH = panelUserSize?.height ?? THINKING_PANEL_H;

  function getPanelTopLeft(): { top: number; left: number } {
    if (panelDragging && panelDragPos) return panelDragPos;
    if (panelDisplayPos && panelRoot) return rowToViewport(panelRoot, panelDisplayPos);
    return { top: 0, left: 0 };
  }

  const panelDragHandle = bindPanelDrag(getPanelTopLeft);
  const panelResizeHandle = bindPanelResize(() => ({ width: panelW, height: panelH }));

  // Panel that appears immediately when sending, before session_id is known
  const thinkingPanel = sending && panelDeskOffset && panelDisplayPos && panelRoot ? createPortal(
    <div style={{
      position: "absolute",
      top: panelDisplayPos.top,
      left: panelDisplayPos.left,
      transform: "none",
      width: panelW,
      height: panelH,
      background: "var(--bg2)", border: "1px solid var(--card-border)",
      borderRadius: 8, overflow: "hidden",
      boxShadow: "0 8px 32px rgba(0,0,0,0.6)", zIndex: 1000,
      display: "flex", flexDirection: "column",
      transition: (panelDragging || panelResizing) ? "none" : "width 0.18s ease, height 0.18s ease, top 0.18s ease, left 0.18s ease",
    }}>
      <style>{`
        @keyframes pendot    { 0%,100%{opacity:0.2;transform:scale(0.8)} 50%{opacity:1;transform:scale(1.2)} }
        @keyframes penline   { 0%,100%{opacity:0.35} 50%{opacity:1} }
        @keyframes thinkfade { 0%{opacity:0;transform:translateY(4px)} 100%{opacity:1;transform:translateY(0)} }
      `}</style>
      {/* Tab bar — drag to move */}
      <div
        {...panelDragHandle}
        style={{
          display: "flex", borderBottom: "1px solid var(--card-border)", padding: "0 8px", flexShrink: 0,
          cursor: panelDragging ? "grabbing" : "grab", userSelect: "none",
        }}
        title="Drag to move panel"
      >
        <div style={{
          padding: "8px 10px", fontSize: 12, fontWeight: 600,
          color: "var(--accent2)", borderBottom: "2px solid var(--accent2)",
          marginBottom: -1, whiteSpace: "nowrap",
        }}>
          ⚡ Activity
        </div>
      </div>
      {/* Thinking body */}
      <div style={{
        flex: 1, display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        gap: 14, padding: 40,
        animation: "thinkfade 0.25s ease-out",
      }}>
        <div style={{ display: "flex", gap: 8 }}>
          <Dot delay={0}    size={10} />
          <Dot delay={0.22} size={10} />
          <Dot delay={0.44} size={10} />
        </div>
        <div style={{ fontSize: 13, color: "var(--text-dim)", fontWeight: 500 }}>
          Starting agent…
        </div>
        <div style={{ fontSize: 11, color: "var(--text-dim)", opacity: 0.6, textAlign: "center" }}>
          First activity events will appear here shortly
        </div>
      </div>
      <PanelResizeHandle active={panelResizing} bind={panelResizeHandle} />
    </div>,
    panelRoot,
  ) : null;

  return (
    <div ref={containerRef} style={{ width: 200 }}>
      {sending ? (
        /* Animated mock-desk while launching */
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div style={{
            width: 120, height: 80, margin: "0 auto",
            background: "#1a1a2e", border: "3px solid #333",
            borderRadius: "6px 6px 2px 2px",
            position: "relative", overflow: "hidden",
            display: "flex", flexDirection: "column",
            justifyContent: "center", padding: "8px 6px", gap: 5,
          }}>
            {[0.7, 0.85, 0.55, 0.85, 0.4].map((w, i) => (
              <div key={i} style={{
                height: 4, borderRadius: 2,
                background: i === 0 ? "var(--accent2)" : "rgba(255,255,255,0.18)",
                width: `${w * 100}%`,
                animation: `penline 1.4s ${i * 0.22}s ease-in-out infinite`,
              }} />
            ))}
            <div style={{
              position: "absolute", top: 5, right: 5,
              width: 6, height: 6, borderRadius: "50%",
              background: "var(--green)", boxShadow: "0 0 8px var(--green)",
              animation: "pendot 1.1s ease-in-out infinite",
            }} />
          </div>
          <div style={{ width: 8, height: 10, background: "#333" }} />
          <div style={{ width: 40, height: 4, background: "#333", borderRadius: 2, marginTop: -2 }} />
          <div style={{
            background: deskColor, height: 18, width: "100%",
            borderRadius: "4px 4px 2px 2px", marginTop: 4,
            boxShadow: "inset 0 -3px 0 rgba(0,0,0,0.3), inset 0 2px 0 rgba(255,255,255,0.1)",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 5,
          }}>
            <Dot delay={0} /><Dot delay={0.22} /><Dot delay={0.44} />
          </div>
          <div style={{
            background: `color-mix(in srgb, ${deskColor} 70%, black)`,
            height: 14, width: "100%",
            borderRadius: "2px 2px 6px 6px", boxShadow: "0 4px 8px rgba(0,0,0,0.4)",
          }} />
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-dim)", textAlign: "center" }}>
            Launching agent…
          </div>
        </div>
      ) : (
        /* Input form */
        <div
          ref={promptBoxRef}
          onClick={onSelect}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          style={{ position: "relative", display: "flex", flexDirection: "column", gap: 8 }}
        >
          <button
            onClick={(e) => { e.stopPropagation(); onClose(); }}
            style={{
              position: "absolute", top: 6, right: 6,
              width: 18, height: 18, borderRadius: "50%",
              background: "rgba(255,255,255,0.07)", border: "1px solid var(--card-border)",
              color: "var(--text-dim)", fontSize: 11, zIndex: 10,
              display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer",
            }}
            title="Close"
          >×</button>

          {/* Attached image thumbnails */}
          {attachedImages.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {attachedImages.map((img) => (
                <div key={img.name} style={{ position: "relative" }}>
                  <img src={img.url} alt={img.name} title={img.name}
                    style={{ width: 52, height: 52, objectFit: "cover", borderRadius: 4,
                      border: "1px solid var(--card-border)" }} />
                  <button
                    onClick={(e) => { e.stopPropagation();
                      setAttachedImages((p) => p.filter((x) => x.name !== img.name)); }}
                    style={{
                      position: "absolute", top: -5, right: -5,
                      width: 14, height: 14, borderRadius: "50%",
                      background: "var(--bg2)", border: "1px solid var(--card-border)",
                      color: "var(--text-dim)", fontSize: 9, lineHeight: 1,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      cursor: "pointer", padding: 0,
                    }}
                  >×</button>
                </div>
              ))}
            </div>
          )}

          {/* Bench drag target only — profile/model/tools live in header */}
          {dropHighlight && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              padding: "8px 10px", borderRadius: 8, fontSize: 11,
              background: "#0f3048",
              border: "2px dashed var(--accent2)",
              color: "var(--accent2)",
              textAlign: "center", lineHeight: 1.45,
              fontWeight: 600,
            }}
          >
            Drop agent here to assign
          </div>
          )}

          <textarea
            ref={textRef}
            value={msg}
            onChange={(e) => { setMsg(e.target.value); onMsgChange?.(e.target.value); }}
            onFocus={() => onSelect()}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            placeholder={dragOver ? "Drop files here…" : ""}
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%", minHeight: 200,
              background: dragOver ? "rgba(100,160,255,0.08)" : "transparent",
              border: `1px solid ${dragOver ? "var(--accent2)" : error ? "var(--red)" : isActive ? "var(--accent2)" : "#2a3558"}`,
              borderRadius: 8, padding: "12px 14px",
              fontSize: 13, lineHeight: 1.6, color: "var(--text)",
              outline: "none", resize: "none", fontFamily: "inherit",
              cursor: "text", transition: "border-color 0.15s, background 0.15s",
              boxSizing: "border-box",
            }}
          />
          {error && (
            <div style={{ fontSize: 10, color: "var(--red)", textAlign: "center", marginTop: -4 }}>{error}</div>
          )}
          <div style={{ display: "flex" }} onClick={(e) => e.stopPropagation()}>
            <button
              onClick={handleSend}
              disabled={!msg.trim()}
              style={{
                flex: 1, padding: "7px 0", borderRadius: 6, fontSize: 12, fontWeight: 600,
                background: !msg.trim() ? "rgba(255,255,255,0.04)" : "var(--accent2)",
                color: !msg.trim() ? "var(--text-dim)" : "white",
                border: "1px solid var(--card-border)",
                cursor: !msg.trim() ? "default" : "pointer",
                transition: "background 0.15s",
              }}
            >
              Start →
            </button>
          </div>
        </div>
      )}
      {thinkingPanel}
    </div>
  );
}
