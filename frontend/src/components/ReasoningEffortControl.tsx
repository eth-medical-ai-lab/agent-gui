import type React from "react";
import type { ReasoningEffort } from "../types";

interface Props {
  value: ReasoningEffort;
  options: { value: ReasoningEffort; label: string }[];
  disabled?: boolean;
  disabledHint?: string;
  compact?: boolean;
  header?: boolean;
  vertical?: boolean;
  readOnly?: boolean;
  onChange: (v: ReasoningEffort) => void;
}

export function ReasoningEffortControl({
  value, options, disabled = false, disabledHint, compact = false, header = false, vertical = false, readOnly = false, onChange,
}: Props) {
  const labelStyle: React.CSSProperties = {
    fontSize: header ? 12 : compact ? 9 : 10,
    color: disabled ? "#5a6478" : "var(--text-dim)",
    flexShrink: 0,
    userSelect: "none",
    ...(vertical ? { width: 52 } : {}),
  };
  const btnPad = header ? "6px 14px" : compact ? "3px 7px" : "4px 9px";
  const btnFont = header ? 12 : compact ? 9 : 10;
  const displayOptions = disabled ? [{ value: "none" as ReasoningEffort, label: "n/a" }] : options;
  const activeLabel = displayOptions.find((o) => o.value === (disabled ? "none" : value))?.label ?? value;

  if (readOnly) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: vertical ? 8 : header ? 8 : compact ? 4 : 6,
          minWidth: 0,
          width: vertical ? "100%" : undefined,
        }}
        title={disabled ? (disabledHint ?? "Reasoning not available for this backend") : "Reasoning — change it from the desk's ⚙ settings"}
      >
        <span style={labelStyle}>Reasoning</span>
        <span style={{
          fontSize: header ? 13 : compact ? 10 : 12,
          fontWeight: 600,
          color: disabled ? "#5a6478" : "var(--text)",
          textTransform: "capitalize",
        }}>
          {activeLabel}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: vertical ? 8 : header ? 8 : compact ? 4 : 6,
        minWidth: 0,
        width: vertical ? "100%" : undefined,
      }}
      title={disabled ? (disabledHint ?? "Reasoning not available for this backend") : undefined}
    >
      <span style={labelStyle}>Reasoning</span>
      <div style={{
        display: "flex",
        alignItems: "center",
        flex: vertical ? 1 : undefined,
        minWidth: 0,
        background: disabled ? "#0e121c" : "#121828",
        border: `1px solid ${disabled ? "#1e2436" : "#2a3558"}`,
        borderRadius: 6,
        overflow: "hidden",
        opacity: disabled ? 0.55 : 1,
      }}>
        {(disabled ? [{ value: "none" as ReasoningEffort, label: "n/a" }] : options).map(({ value: v, label }, i, arr) => {
          const effort = disabled ? "none" : v;
          const active = !disabled && value === effort;
          return (
            <button
              key={effort}
              type="button"
              disabled={disabled}
              onClick={() => { if (!disabled) onChange(effort); }}
              title={disabled ? disabledHint : `Reasoning: ${label}`}
              style={{
                padding: btnPad,
                fontSize: btnFont,
                fontWeight: 500,
                cursor: disabled ? "not-allowed" : "pointer",
                border: "none",
                borderRight: i < arr.length - 1 ? "1px solid #2a3558" : undefined,
                background: active ? "var(--accent2)" : "transparent",
                color: active ? "white" : disabled ? "#5a6478" : "var(--text-dim)",
                transition: "background 0.15s, color 0.15s",
                flex: vertical ? 1 : undefined,
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
