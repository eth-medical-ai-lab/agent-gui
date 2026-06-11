import { useState } from "react";
import type { ToolsetMeta, ToolProfile } from "../types";
import { modalOverlayStyle, modalPanelStyle } from "./modalStyles";

interface Props {
  toolsets: ToolsetMeta[];
  profiles: ToolProfile[];        // built-ins first, then custom
  selectedId: string;
  onSelect: (id: string) => void;
  onSaveProfile: (p: ToolProfile) => void;
  onDeleteProfile: (id: string) => void;
}

const selStyle: React.CSSProperties = {
  background: "rgba(255,255,255,0.04)",
  border: "1px solid var(--card-border)",
  borderRadius: 6,
  color: "var(--text)",
  fontSize: 11,
  padding: "3px 6px",
  cursor: "pointer",
  outline: "none",
  maxWidth: 130,
};

export function ToolsMenu({ toolsets, profiles, selectedId, onSelect, onSaveProfile, onDeleteProfile }: Props) {
  const [manageOpen, setManageOpen] = useState(false);
  const builtins = profiles.filter((p) => p.builtin);
  const custom = profiles.filter((p) => !p.builtin);

  // Modal draft state — seeded from the currently selected profile when opened.
  const selected = profiles.find((p) => p.id === selectedId);
  const [draftName, setDraftName] = useState("");
  const [draftEnabled, setDraftEnabled] = useState<Set<string>>(new Set());

  function openManage() {
    setDraftName("");
    setDraftEnabled(new Set(selected?.enabled ?? []));
    setManageOpen(true);
  }
  function toggle(name: string) {
    setDraftEnabled((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  }
  function save() {
    const name = draftName.trim();
    if (!name) return;
    const id = `custom-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${Date.now().toString(36)}`;
    const profile: ToolProfile = { id, name, enabled: toolsets.map((t) => t.name).filter((n) => draftEnabled.has(n)) };
    onSaveProfile(profile);
    onSelect(id);
    setManageOpen(false);
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span style={{ fontSize: 10, color: "var(--text-dim)", userSelect: "none", flexShrink: 0 }}>Tools</span>
      <select
        value={selectedId}
        onChange={(e) => onSelect(e.target.value)}
        title="Toolset profile for new desks — fewer tools = faster first response"
        style={selStyle}
      >
        <optgroup label="Presets">
          {builtins.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </optgroup>
        {custom.length > 0 && (
          <optgroup label="Custom">
            {custom.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </optgroup>
        )}
      </select>
      <button
        onClick={openManage}
        title="Create / manage custom tool profiles"
        style={{
          background: "rgba(255,255,255,0.04)", border: "1px solid var(--card-border)",
          borderRadius: 6, color: "var(--text-dim)", fontSize: 12, lineHeight: 1,
          padding: "4px 7px", cursor: "pointer",
        }}
      >
        ✎
      </button>

      {manageOpen && (
        <div
          onClick={() => setManageOpen(false)}
          style={{ ...modalOverlayStyle, zIndex: 1000 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              ...modalPanelStyle,
              padding: 20, width: 420, maxHeight: "80vh", overflowY: "auto",
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>Tool profiles</div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 14 }}>
              Pick the toolsets a new desk should load. Fewer tools → faster first response.
            </div>

            {/* Toolset checklist. Hermes filters by toolset, not individual tool, so
                each row is a group; the count + tooltip show what it contains. */}
            <div style={{ fontSize: 10, color: "var(--text-dim)", marginBottom: 8 }}>
              {toolsets.reduce((n, t) => n + (t.tools?.length ?? 1), 0)} tools across {toolsets.length} toolsets — toggle whole toolsets:
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 12px", marginBottom: 16 }}>
              {toolsets.map((t) => (
                <label
                  key={t.name}
                  title={(t.tools ?? [t.name]).join(", ")}
                  style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, cursor: "pointer" }}
                >
                  <input type="checkbox" checked={draftEnabled.has(t.name)} onChange={() => toggle(t.name)} />
                  <span>{t.label}</span>
                  <span style={{ fontSize: 9, color: "var(--text-dim)" }}>
                    {(t.tools?.length ?? 1) > 1 ? `${t.tools!.length} tools` : ""}{!t.lean ? " · heavy" : ""}
                  </span>
                </label>
              ))}
            </div>

            {/* Save as new profile */}
            <div style={{ display: "flex", gap: 8, marginBottom: 18 }}>
              <input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="New profile name…"
                style={{
                  flex: 1, background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)",
                  borderRadius: 6, padding: "6px 10px", color: "var(--text)", fontSize: 12, outline: "none",
                }}
              />
              <button
                onClick={save}
                disabled={!draftName.trim()}
                style={{
                  padding: "0 14px", background: draftName.trim() ? "var(--accent2)" : "rgba(255,255,255,0.06)",
                  border: "none", borderRadius: 6, color: "white", fontSize: 12, fontWeight: 600,
                  cursor: draftName.trim() ? "pointer" : "default",
                }}
              >
                Save
              </button>
            </div>

            {/* Existing custom profiles */}
            {custom.length > 0 && (
              <div>
                <div style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>
                  Saved profiles
                </div>
                {custom.map((p) => (
                  <div key={p.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 0", borderTop: "1px solid var(--card-border)" }}>
                    <div>
                      <span style={{ fontSize: 12, fontWeight: 600 }}>{p.name}</span>
                      <span style={{ fontSize: 10, color: "var(--text-dim)", marginLeft: 8 }}>{p.enabled.length} tools</span>
                    </div>
                    <button
                      onClick={() => onDeleteProfile(p.id)}
                      title="Delete profile"
                      style={{ background: "none", border: "none", color: "var(--text-dim)", cursor: "pointer", fontSize: 14 }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div style={{ textAlign: "right", marginTop: 16 }}>
              <button
                onClick={() => setManageOpen(false)}
                style={{
                  padding: "6px 14px", background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)",
                  borderRadius: 6, color: "var(--text)", fontSize: 12, cursor: "pointer",
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
