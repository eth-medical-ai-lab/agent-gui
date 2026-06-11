import { useEffect, useRef } from "react";
import type { AgentProfile } from "../types";
import type { RosterLayout } from "../rosterLayout";
import { AgentRoster } from "./AgentRoster";

interface Props {
  agents: AgentProfile[];
  defaultModel?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  dragActive?: boolean;
  dropHighlight?: boolean;
  rosterRef: React.RefObject<HTMLDivElement | null>;
  rosterLayout: RosterLayout;
  sectionDropHoverId?: string | null;
  onAgentDragStart?: (e: React.MouseEvent, agentId: string, color?: string) => void;
  onAgentEdit?: (agent: AgentProfile) => void;
  onDefaultEdit?: () => void;
  onCreateAgent?: () => void;
}

const HEADER_H = 57;

export function AgentRosterMenu({
  agents, defaultModel, open, onOpenChange,
  dragActive, dropHighlight, rosterRef,
  rosterLayout, sectionDropHoverId,
  onAgentDragStart, onAgentEdit, onDefaultEdit, onCreateAgent,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const highlighted = Boolean(dragActive && dropHighlight);

  useEffect(() => {
    if (dragActive && dropHighlight) onOpenChange(true);
  }, [dragActive, dropHighlight, onOpenChange]);

  useEffect(() => {
    if (!open || dragActive) return;
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (wrapRef.current?.contains(t)) return;
      if (rosterRef.current?.contains(t)) return;
      onOpenChange(false);
    }
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open, dragActive, onOpenChange, rosterRef]);

  return (
    <>
      <div ref={wrapRef} style={{ position: "relative", flexShrink: 0 }}>
        <button
          type="button"
          onClick={() => onOpenChange(!open)}
          title={open ? "Close Agent Profiles" : "Agent Profiles — view profiles & drag to desks"}
          style={{
            height: 28, padding: "0 10px",
            background: open || highlighted ? "#0f3048" : "#121828",
            border: highlighted ? "2px dashed var(--accent2)" : "1px solid #2a3558",
            borderRadius: 6,
            color: open || highlighted ? "var(--accent2)" : "var(--text-dim)",
            fontSize: 10, fontWeight: 600, cursor: "pointer",
            display: "flex", alignItems: "center", gap: 6,
          }}
        >
          👥 Agent Profiles
        </button>
      </div>

      {open && (
        <>
          <div
            data-roster-backdrop=""
            style={{
              position: "fixed", inset: 0, top: HEADER_H, zIndex: 480,
              background: "rgba(0,0,0,0.25)",
              pointerEvents: dragActive ? "none" : "auto",
            }}
            onMouseDown={() => { if (!dragActive) onOpenChange(false); }}
          />
          <div
            ref={rosterRef as React.RefObject<HTMLDivElement>}
            style={{
              position: "fixed",
              top: HEADER_H,
              right: 0,
              bottom: 0,
              zIndex: 500,
              width: "min(380px, 92vw)",
              borderLeft: highlighted ? "2px dashed var(--accent2)" : "1px solid #2a3558",
              background: "#16213e",
              boxShadow: "-8px 0 32px rgba(0,0,0,0.45)",
              display: "flex",
              flexDirection: "column",
              animation: "rosterSlideIn 0.22s ease-out",
            }}
          >
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "12px 16px 10px",
              borderBottom: "1px solid rgba(255,255,255,0.08)",
              flexShrink: 0,
            }}>
              <span style={{ fontSize: 20, fontWeight: 800, color: "var(--text)", letterSpacing: 0.2 }}>
                Agent Profiles
              </span>
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                style={{
                  background: "transparent", border: "none",
                  color: "var(--text-dim)", cursor: "pointer", fontSize: 16, lineHeight: 1,
                }}
              >
                ✕
              </button>
            </div>

            <div style={{
              flex: 1, overflow: "auto",
              padding: "14px 16px 16px",
            }}>
              <AgentRoster
                agents={agents}
                defaultModel={defaultModel}
                dragActive={dragActive}
                dropHighlight={dropHighlight}
                rosterLayout={rosterLayout}
                sectionDropHoverId={sectionDropHoverId}
                onAgentDragStart={onAgentDragStart}
                onAgentEdit={onAgentEdit}
                onDefaultEdit={onDefaultEdit}
                onCreateAgent={onCreateAgent}
              />
            </div>
          </div>
          <style>{`
            @keyframes rosterSlideIn {
              from { transform: translateX(100%); opacity: 0.6; }
              to { transform: translateX(0); opacity: 1; }
            }
          `}</style>
        </>
      )}
    </>
  );
}
