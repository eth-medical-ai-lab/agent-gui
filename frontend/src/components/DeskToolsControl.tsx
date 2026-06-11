import { useState } from "react";
import type { ToolPresetId, ToolsetMeta } from "../types";
import type React from "react";
import { inputSolidStyle } from "./modalStyles";

const PRESETS: ToolPresetId[] = ["chat", "lean", "full"];

function toolsDisplayName(toolsEnabled: string[], toolsets: ToolsetMeta[]): string {
  const key = (arr: string[]) => [...arr].sort().join(",");
  const enabled = key(toolsEnabled);
  const chat: string[] = [];
  const lean = toolsets.filter((t) => t.lean).map((t) => t.name);
  const full = toolsets.map((t) => t.name);
  if (enabled === key(chat)) return "Chat";
  if (enabled === key(lean)) return "Lean";
  if (enabled === key(full)) return "Full";
  return "Custom";
}

interface Props {
  toolPreset: ToolPresetId;
  toolsEnabled: string[];
  toolsets: ToolsetMeta[];
  compact?: boolean;
  header?: boolean;
  vertical?: boolean;
  readOnly?: boolean;
  onChange: (preset: ToolPresetId, enabled: string[]) => void;
}

export function DeskToolsControl({ toolPreset, toolsEnabled, toolsets, compact = false, header = false, vertical = false, readOnly = false, onChange }: Props) {
  const [expanded, setExpanded] = useState(false);
  const enabledSet = new Set(toolsEnabled);

  function selectPreset(id: ToolPresetId) {
    const map: Record<ToolPresetId, string[]> = {
      chat: [],
      lean: toolsets.filter((t) => t.lean).map((t) => t.name),
      full: toolsets.map((t) => t.name),
    };
    onChange(id, map[id]);
    setExpanded(false);
  }

  function toggleTool(name: string) {
    const next = new Set(enabledSet);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange("lean", toolsets.map((t) => t.name).filter((n) => next.has(n)));
  }

  const btnPad = header ? "6px 12px" : compact ? "3px 5px" : "4px 8px";
  const btnFont = header ? 12 : compact ? 9 : 10;
  const labelWidth = vertical ? 52 : undefined;
  const labelFont = header ? 12 : btnFont;
  const displayName = toolsDisplayName(toolsEnabled, toolsets);

  if (readOnly) {
    return (
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: vertical ? 8 : header ? 8 : compact ? 4 : 6,
        width: vertical ? "100%" : undefined,
        minWidth: 0,
      }}>
        <span style={{
          fontSize: labelFont,
          color: "var(--text-dim)",
          userSelect: "none",
          flexShrink: 0,
          width: labelWidth,
        }}>Tools</span>
        <span
          title="Tools — change them from the desk's ⚙ settings"
          style={{
            fontSize: header ? 13 : compact ? 10 : 12,
            fontWeight: 600,
            color: "var(--text)",
            textTransform: "capitalize",
          }}
        >
          {displayName}
        </span>
      </div>
    );
  }

  return (
    <div style={{
      position: "relative",
      display: "flex",
      alignItems: "center",
      gap: vertical ? 8 : header ? 8 : compact ? 4 : 6,
      width: vertical ? "100%" : undefined,
      minWidth: 0,
    }}>
      <span style={{
        fontSize: labelFont,
        color: "var(--text-dim)",
        userSelect: "none",
        flexShrink: 0,
        width: labelWidth,
      }}>Tools</span>
      <div style={{
        display: "flex",
        alignItems: "center",
        flex: vertical ? 1 : undefined,
        minWidth: 0,
        background: "#121828", border: "1px solid #2a3558", borderRadius: 6, overflow: "hidden",
      }}>
        {PRESETS.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => selectPreset(id)}
            style={{
              padding: btnPad, fontSize: btnFont, fontWeight: 600, cursor: "pointer",
              textTransform: "capitalize", border: "none",
              background: toolPreset === id ? "#0f3048" : "transparent",
              color: toolPreset === id ? "var(--accent2)" : "var(--text-dim)",
            }}
          >
            {id}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          title="Customize toolsets"
          style={{
            padding: compact ? "3px 5px" : "4px 7px", fontSize: btnFont, cursor: "pointer", border: "none",
            borderLeft: "1px solid #2a3558",
            background: expanded ? "#0f3048" : "transparent",
            color: expanded ? "var(--accent2)" : "var(--text-dim)",
          }}
        >
          {expanded ? "▴" : "▾"}
        </button>
      </div>
      {expanded && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 300,
          minWidth: 280, maxWidth: 360, padding: 10,
          background: "#16213e", border: "1px solid #2a3558", borderRadius: 8,
          boxShadow: "0 8px 28px rgba(0,0,0,0.55)",
          display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 10px",
        }}>
          {toolsets.map((t) => (
            <label
              key={t.name}
              title={(t.tools ?? [t.name]).join(", ")}
              style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}
            >
              <input
                type="checkbox"
                checked={enabledSet.has(t.name)}
                onChange={() => toggleTool(t.name)}
              />
              <span>{t.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export const deskSelectStyle: React.CSSProperties = {
  ...inputSolidStyle,
  fontSize: 11,
  padding: "4px 8px",
  maxWidth: 150,
  fontFamily: "inherit",
};
