import { useState } from "react";
import type React from "react";
import type { AgentProfile, AgentPrototype, ReasoningEffort } from "../types";
import { AgentProfileModal } from "./AgentProfileModal";
import { GlobalDefaultPersonaEditor } from "./GlobalDefaultPersonaEditor";
import { modalOverlayStyle, modalPanelStyle } from "./modalStyles";
import { ENABLE_PROFILE_CREATE } from "../featureFlags";

const DEFAULT_PROFILE_KEY = "__default__";

const REASONING_OPTIONS: { value: ReasoningEffort; label: string }[] = [
  { value: "none", label: "off" },
  { value: "low", label: "low" },
  { value: "medium", label: "med" },
  { value: "high", label: "high" },
];

type Tab = "modify" | "create";

interface Props {
  agents: AgentProfile[];
  prototypes: AgentPrototype[];
  defaultAgentId?: string;
  verbose: boolean;
  reasoningEffort: ReasoningEffort;
  onVerboseToggle: () => void;
  onReasoningChange: (v: ReasoningEffort) => void;
  onClose: () => void;
  onSaved: () => void;
}

export function DeskAgentCustomizeModal({
  agents, prototypes, defaultAgentId,
  verbose, reasoningEffort,
  onVerboseToggle, onReasoningChange,
  onClose, onSaved,
}: Props) {
  const [tab, setTab] = useState<Tab>("modify");
  const initialModifyId = !defaultAgentId
    ? DEFAULT_PROFILE_KEY
    : agents.some((a) => a.id === defaultAgentId)
      ? defaultAgentId
      : DEFAULT_PROFILE_KEY;
  const [modifyAgentId, setModifyAgentId] = useState(initialModifyId);
  const modifyAgent = modifyAgentId === DEFAULT_PROFILE_KEY
    ? null
    : agents.find((a) => a.id === modifyAgentId) ?? null;

  const btnStyle: React.CSSProperties = {
    padding: "3px 7px", fontSize: 9, fontWeight: 500, cursor: "pointer",
    borderRadius: 5, border: "1px solid #2a3558",
  };

  return (
    <div
      style={{ ...modalOverlayStyle, zIndex: 450 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        ...modalPanelStyle,
        width: "min(920px, calc(100vw - 32px))",
        maxHeight: "calc(100vh - 32px)",
        overflow: "auto",
        padding: "16px 20px 12px",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 4 }}>
            {(ENABLE_PROFILE_CREATE ? (["modify", "create"] as Tab[]) : (["modify"] as Tab[])).map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                style={{
                  padding: "6px 12px", borderRadius: 6, fontSize: 11, fontWeight: 600,
                  cursor: "pointer", border: "1px solid #2a3558",
                  background: tab === id ? "#0f3048" : "#121828",
                  color: tab === id ? "var(--accent2)" : "var(--text-dim)",
                  textTransform: "capitalize",
                }}
              >
                {id === "modify" ? "Modify existing" : "Create new"}
              </button>
            ))}
          </div>
          <button type="button" onClick={onClose} style={ghostBtn}>✕</button>
        </div>

        <section style={sectionStyle}>
          <div style={sectionTitleStyle}>Worker preferences</div>
          <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
            <div
              onClick={onVerboseToggle}
              title={verbose ? "High verbosity" : "Low verbosity"}
              style={{
                display: "flex", alignItems: "center", gap: 5, cursor: "pointer", userSelect: "none",
                padding: "4px 10px", background: "#121828", borderRadius: 6, border: "1px solid #2a3558",
              }}
            >
              <span style={{ fontSize: 10, color: "var(--text-dim)" }}>Verbose</span>
              <span style={{ fontSize: 10, fontWeight: verbose ? 600 : 400, color: verbose ? "var(--text)" : "var(--text-dim)" }}>
                {verbose ? "high" : "low"}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ fontSize: 10, color: "var(--text-dim)", marginRight: 2 }}>Reasoning</span>
              {REASONING_OPTIONS.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => onReasoningChange(value)}
                  style={{
                    ...btnStyle,
                    background: reasoningEffort === value ? "#0f3048" : "#121828",
                    color: reasoningEffort === value ? "var(--accent2)" : "var(--text-dim)",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </section>

        {tab === "modify" ? (
          <>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text-dim)", textTransform: "uppercase" }}>
                Profile to edit
              </span>
              <select
                value={modifyAgentId}
                onChange={(e) => setModifyAgentId(e.target.value)}
                style={selectStyle}
              >
                <option value={DEFAULT_PROFILE_KEY}>Default (~/.hermes)</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.name || a.id}</option>
                ))}
              </select>
            </label>
            {modifyAgentId === DEFAULT_PROFILE_KEY ? (
              <GlobalDefaultPersonaEditor onClose={onClose} onSaved={onSaved} />
            ) : modifyAgent ? (
              <AgentProfileModal
                embedded
                mode="edit"
                agent={modifyAgent}
                prototypes={prototypes}
                onClose={onClose}
                onSaved={onSaved}
                onDeleted={onSaved}
              />
            ) : (
              <div style={{ padding: 24, textAlign: "center", color: "var(--text-dim)", fontSize: 12 }}>
                No agent profiles available. Switch to Create new.
              </div>
            )}
          </>
        ) : (
          <AgentProfileModal
            embedded
            mode="create"
            prototypes={prototypes}
            agents={agents}
            onClose={onClose}
            onSaved={onSaved}
          />
        )}
      </div>
    </div>
  );
}

const sectionStyle: React.CSSProperties = {
  marginBottom: 16, padding: "12px 14px", borderRadius: 8,
  background: "#0e1424", border: "1px solid #2a3558",
};

const sectionTitleStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
  color: "var(--text-dim)", marginBottom: 8,
};

const selectStyle: React.CSSProperties = {
  fontSize: 12, padding: "6px 10px", borderRadius: 6,
  background: "#121828", border: "1px solid #2a3558", color: "var(--text)",
};

const ghostBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "var(--text-dim)", cursor: "pointer", fontSize: 14,
};
