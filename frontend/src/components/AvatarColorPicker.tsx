import React from "react";
import { AVATAR_COLORS, AVATAR_OPTIONS, type AvatarPref } from "../avatarPrefs";
import { AgentFigure, type AgentArchetype } from "./AgentFigure";

interface Props {
  /** Current selection (archetype/color override, if any). */
  value: AvatarPref;
  /** Fallback color when no color override is chosen (e.g. backend color). */
  fallbackColor?: string;
  /** Called with the changed field(s); the store/parent merges. */
  onChange: (patch: AvatarPref) => void;
  compact?: boolean;
}

/** Pick an avatar look + accent color from the built-in repository. */
export function AvatarColorPicker({ value, fallbackColor, onChange, compact }: Props) {
  const color = value.color || fallbackColor || "#6a7a9a";
  const hasOverride = !!value.archetype || !!value.color;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: compact ? 8 : 10 }}>
      <div>
        <Label>Avatar</Label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {AVATAR_OPTIONS.map((opt) => {
            const sel = value.archetype === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                title={opt.label}
                onClick={() => onChange({ archetype: sel ? undefined : opt.id })}
                style={{
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
                  padding: "4px 2px 2px", width: 52,
                  borderRadius: 8, cursor: "pointer",
                  border: sel ? "1px solid var(--accent2)" : "1px solid #2a3558",
                  background: sel ? "rgba(100,200,255,0.10)" : "#0f1626",
                }}
              >
                <div style={{ height: 34, display: "flex", alignItems: "flex-end" }}>
                  <AgentFigure archetype={opt.id as AgentArchetype} color={color} scale={0.55} state="idle" />
                </div>
                <span style={{ fontSize: 8, color: sel ? "var(--accent2)" : "var(--text-dim)" }}>{opt.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <Label>Color</Label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {AVATAR_COLORS.map((c) => {
            const sel = (value.color || "").toLowerCase() === c.toLowerCase();
            return (
              <button
                key={c}
                type="button"
                title={c}
                onClick={() => onChange({ color: sel ? undefined : c })}
                style={{
                  width: 20, height: 20, borderRadius: "50%", cursor: "pointer",
                  background: c,
                  border: sel ? "2px solid #fff" : "2px solid rgba(0,0,0,0.35)",
                  boxShadow: sel ? `0 0 0 2px ${c}` : "none",
                }}
              />
            );
          })}
        </div>
      </div>

      {hasOverride && (
        <button
          type="button"
          onClick={() => onChange({ archetype: undefined, color: undefined })}
          style={{
            alignSelf: "flex-start", padding: "3px 8px", fontSize: 10,
            borderRadius: 5, cursor: "pointer", color: "var(--text-dim)",
            background: "transparent", border: "1px solid #2a3558",
          }}
        >
          Reset to default
        </button>
      )}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
      color: "var(--text-dim)", marginBottom: 5,
    }}>
      {children}
    </div>
  );
}
