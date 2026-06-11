import type React from "react";

/** Solid backdrop for modal overlays (no see-through). */
export const modalOverlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 16,
  background: "#080810",
};

/** Opaque modal shell — avoids background bleed-through. */
export const modalPanelStyle: React.CSSProperties = {
  background: "#16213e",
  borderRadius: 12,
  border: "1px solid #2a3558",
  boxShadow: "0 12px 40px rgba(0, 0, 0, 0.65)",
};

export const sectionBoxStyle: React.CSSProperties = {
  marginBottom: 12,
  padding: "12px 14px",
  borderRadius: 8,
  background: "#121828",
  border: "1px solid #2a3558",
};

export const sectionTitleStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  color: "var(--text-dim)",
  marginBottom: 8,
};

export const personaTextStyle: React.CSSProperties = {
  fontSize: 12,
  lineHeight: 1.55,
  fontFamily: "ui-monospace, monospace",
  color: "var(--text)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  maxHeight: 160,
  overflowY: "auto",
  padding: "10px 12px",
  borderRadius: 6,
  background: "#0d1220",
  border: "1px solid #243050",
};

/** Taller persona column for wide modals (SOUL | MEMORY side-by-side). */
export const personaColumnStyle: React.CSSProperties = {
  ...personaTextStyle,
  minHeight: 260,
  maxHeight: 360,
  flex: 1,
};

export const wideModalWidthStyle: React.CSSProperties = {
  width: "min(1120px, 96vw)",
  maxHeight: "92vh",
  overflow: "auto",
};

export const personaGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 16,
  alignItems: "start",
};

export const inputSolidStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  borderRadius: 6,
  border: "1px solid #2a3558",
  background: "#0d1220",
  color: "var(--text)",
  fontSize: 12,
};

export function toolChipStyle(on: boolean): React.CSSProperties {
  return {
    fontSize: 10,
    padding: "3px 8px",
    borderRadius: 5,
    background: on ? "#1a3d2a" : "#1a2030",
    color: on ? "#a8e6b0" : "#6a7090",
    border: `1px solid ${on ? "#3d8f55" : "#2a3558"}`,
    fontWeight: on ? 600 : 400,
  };
}

export function skillChipStyle(hasSkills: boolean): React.CSSProperties {
  return {
    fontSize: 10,
    padding: "4px 9px",
    borderRadius: 5,
    background: hasSkills ? "#1a3d2a" : "#1a2030",
    color: hasSkills ? "#a8e6b0" : "#5a6080",
    border: `1px solid ${hasSkills ? "#3d8f55" : "#2a3558"}`,
    fontWeight: hasSkills ? 600 : 400,
  };
}

export function presetTabStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 10px",
    borderRadius: 6,
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    textTransform: "capitalize",
    border: active ? "1px solid var(--accent2)" : "1px solid #2a3558",
    background: active ? "#0f3048" : "#1a2030",
    color: active ? "var(--accent2)" : "var(--text-dim)",
  };
}
