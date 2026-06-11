import { createPortal } from "react-dom";
import type React from "react";
import type { AgentProfile } from "../types";
import { useAvatarPrefs } from "../avatarPrefs";
import { AgentFigure, type AgentArchetype } from "./AgentFigure";
import { modalPanelStyle } from "./modalStyles";

interface Props {
  agents: AgentProfile[];
  selectedAgentId: string;
  onSelect: (agentId: string) => void;
  onClose: () => void;
}

const SLOT_W = 72;

function ProfileTile({
  label,
  selected,
  onClick,
  agentId,
  color,
  isPrototype,
  cloneFrom,
  archetype,
  disabled,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
  agentId?: string;
  color?: string;
  archetype?: AgentArchetype;
  isPrototype?: boolean;
  cloneFrom?: string | null;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      title={disabled ? `${label} — in use on another desk` : label}
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
        width: SLOT_W, padding: "6px 4px 8px", borderRadius: 8,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.35 : 1,
        background: selected ? "#0f3048" : "#121828",
        border: `2px solid ${selected ? "var(--accent2)" : "#2a3558"}`,
        color: "var(--text)",
      }}
    >
      <AgentFigure
        agentId={agentId}
        color={color ?? "#6a7a9a"}
        archetype={archetype}
        isPrototype={isPrototype}
        cloneFrom={cloneFrom}
        scale={0.55}
        state="idle"
      />
      <span style={{
        fontSize: 9, fontWeight: 600, maxWidth: "100%",
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {label}
      </span>
    </button>
  );
}

export function DeskAgentPicker({
  agents, selectedAgentId, onSelect, onClose,
}: Props) {
  const avatars = useAvatarPrefs();
  return createPortal(
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 350,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "rgba(8,8,16,0.72)",
      }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        ...modalPanelStyle,
        width: "min(420px, calc(100vw - 32px))",
        padding: "14px 16px 12px",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <span style={{ fontSize: 12, fontWeight: 700 }}>Choose profile</span>
          <button type="button" onClick={onClose} style={ghostBtn}>✕</button>
        </div>

        <div style={{
          display: "flex", flexWrap: "wrap", gap: 8,
          justifyContent: "flex-start", maxHeight: 280, overflowY: "auto",
        }}>
          <ProfileTile
            label="Default"
            selected={!selectedAgentId}
            onClick={() => onSelect("")}
            color="#6a7a9a"
          />
          {agents.filter((a) => a.available !== false).map((a) => {
            const pref = avatars.get(a.id);
            return (
            <ProfileTile
              key={a.id}
              label={a.name || a.id}
              selected={selectedAgentId === a.id}
              onClick={() => onSelect(a.id)}
              agentId={a.id}
              color={pref?.color || a.color}
              archetype={pref?.archetype}
              isPrototype={a.is_prototype}
              cloneFrom={a.clone_from}
            />
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}

const ghostBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "var(--text-dim)", cursor: "pointer", fontSize: 14,
};
