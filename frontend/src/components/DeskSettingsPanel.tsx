import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AgentProfile, ReasoningEffort, ToolPresetId, ToolsetMeta } from "../types";
import type { DeskConfigView } from "../deskConfig";
import { DeskContextBar } from "./DeskContextBar";
import { ReasoningEffortControl } from "./ReasoningEffortControl";

interface Props {
  config: DeskConfigView;
  agents: AgentProfile[];
  toolsets: ToolsetMeta[];
  anchor: HTMLElement;
  panelRoot: HTMLElement | null;
  /** Running desks are immutable — show the config read-only. */
  locked: boolean;
  reasoningValue: ReasoningEffort;
  reasoningOptions: { value: ReasoningEffort; label: string }[];
  reasoningDisabled: boolean;
  onProfileChange: (agentId: string) => void;
  onModelChange: (model: string) => void;
  onToolsChange: (preset: ToolPresetId, enabled: string[]) => void;
  onReasoningChange: (v: ReasoningEffort) => void;
  onClose: () => void;
}

const PANEL_W = 280;

/**
 * Per-desk agent config subpage. Opens to the RIGHT of the desk (gear toggle).
 * Stacks, in order: profile → model (by profile) → tools → reasoning effort.
 */
export function DeskSettingsPanel({
  config, agents, toolsets, anchor, panelRoot, locked,
  reasoningValue, reasoningOptions, reasoningDisabled,
  onProfileChange, onModelChange, onToolsChange, onReasoningChange, onClose,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });

  // Position relative to the team row so the panel clips with it on floor scroll.
  useEffect(() => {
    if (!panelRoot) return;
    function update() {
      const r = anchor.getBoundingClientRect();
      const rr = panelRoot!.getBoundingClientRect();
      let left = r.right + 12 - rr.left;
      const rowW = rr.width;
      if (left + PANEL_W > rowW - 8) left = Math.max(8, r.left - PANEL_W - 12 - rr.left);
      setPos({ top: Math.max(8, r.top - rr.top), left });
    }
    update();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [anchor, panelRoot]);

  // Dismiss on outside click / Escape.
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node) && !anchor.contains(e.target as Node)) {
        onClose();
      }
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [anchor, onClose]);

  const showReasoning = !reasoningDisabled && reasoningOptions.length > 0;

  if (!panelRoot) return null;

  return createPortal(
    <div
      ref={ref}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: "absolute", top: pos.top, left: pos.left, width: PANEL_W,
        background: "var(--bg2)", border: "1px solid var(--accent2)",
        borderRadius: 10, boxShadow: "0 10px 36px rgba(0,0,0,0.55)",
        zIndex: 4000, padding: 12,
        display: "flex", flexDirection: "column", gap: 12,
        animation: "deskcfg-in 0.14s ease-out",
      }}
    >
      <style>{`@keyframes deskcfg-in { from{opacity:0;transform:translateX(-6px)} to{opacity:1;transform:translateX(0)} }`}</style>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: "var(--text)", letterSpacing: 0.3 }}>
          ⚙ Agent settings
        </span>
        <button
          onClick={onClose}
          style={{ fontSize: 15, color: "var(--text-dim)", lineHeight: 1, padding: "0 2px", cursor: "pointer" }}
          title="Close"
        >×</button>
      </div>

      {locked && (
        <div style={{ fontSize: 10, color: "var(--yellow)" }}>
          Desk is running — stop it to change its agent config.
        </div>
      )}

      <div style={{
        opacity: locked ? 0.5 : 1,
        pointerEvents: locked ? "none" : "auto",
        display: "flex", flexDirection: "column", gap: 12,
      }}>
        <DeskContextBar
          config={config}
          agents={agents}
          toolsets={toolsets}
          vertical
          showLabels
          showAdvanced={false}
          onProfileChange={onProfileChange}
          onModelChange={onModelChange}
          onToolsChange={onToolsChange}
        />
        {showReasoning && (
          <ReasoningEffortControl
            vertical
            value={reasoningValue}
            options={reasoningOptions}
            onChange={onReasoningChange}
          />
        )}
      </div>
    </div>,
    panelRoot,
  );
}
