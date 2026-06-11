import React, { useEffect, useMemo, useState } from "react";
import type { AgentCapabilities, AgentProfile, ToolsetMeta } from "../types";
import type { PendingAssignment, ToolPresetId } from "../types";
import { api } from "../api/client";
import { AgentFigure } from "./AgentFigure";
import {
  modalPanelStyle,
  modalOverlayStyle,
  personaColumnStyle,
  personaGridStyle,
  presetTabStyle,
  sectionBoxStyle,
  sectionTitleStyle,
  skillChipStyle,
  toolChipStyle,
  wideModalWidthStyle,
} from "./modalStyles";
import { ModelSelectField } from "./ModelSelectField";

const PRESET_TABS: ToolPresetId[] = ["chat", "lean", "full"];

interface Props {
  deskId: string;
  agent: AgentProfile;
  toolsets: ToolsetMeta[];
  onAssign: (deskId: string, assignment: PendingAssignment) => void;
  onClose: () => void;
}

function PersonaColumn({ label, text }: { label: string; text: string }) {
  const empty = !text.trim();
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
      <div style={{ ...sectionTitleStyle, marginBottom: 5 }}>{label}</div>
      <div style={personaColumnStyle}>
        {empty ? <span style={{ color: "var(--text-dim)", fontStyle: "italic" }}>(empty)</span> : text}
      </div>
    </div>
  );
}

export function AgentAssignModal({ deskId, agent, toolsets, onAssign, onClose }: Props) {
  const [caps, setCaps] = useState<AgentCapabilities | null>(null);
  const [soul, setSoul] = useState("");
  const [memory, setMemory] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<ToolPresetId>("lean");
  const backendUrl = (agent.base_url || "").trim();
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelOverride, setModelOverride] = useState(agent.model || "");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      api.agents.capabilities(agent.id),
      api.agents.persona(agent.id),
      ...(backendUrl ? [api.llm.models({ baseUrl: backendUrl, agentId: agent.id })] : []),
    ])
      .then((results) => {
        if (cancelled) return;
        const capData = results[0] as AgentCapabilities;
        const persona = results[1] as { soul?: string; memory?: string };
        setCaps(capData);
        setSoul(persona.soul ?? "");
        setMemory(persona.memory ?? "");
        const def = capData.default_preset;
        if (def === "chat" || def === "lean" || def === "full") setTab(def);
        if (backendUrl && results[2]) {
          const listed = results[2] as { models: string[]; current: string };
          setAvailableModels(listed.models ?? []);
          setModelOverride((prev) => prev || agent.model || listed.current || "");
        }
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [agent.id, agent.model, backendUrl]);

  const labels = useMemo(
    () => Object.fromEntries(toolsets.map((t) => [t.name, t.label])),
    [toolsets],
  );

  const enabledSet = new Set(caps?.presets[tab] ?? []);

  const skillBundles = useMemo(() => {
    if (!caps) return [];
    return [...caps.skill_bundles].sort((a, b) => {
      if (a.count > 0 && b.count === 0) return -1;
      if (a.count === 0 && b.count > 0) return 1;
      return a.bundle.localeCompare(b.bundle);
    });
  }, [caps]);

  function handleAssign() {
    if (!caps) return;
    const pickedModel = modelOverride.trim();
    onAssign(deskId, {
      agentId: agent.id,
      agentName: agent.name,
      agentColor: agent.color,
      toolPreset: tab,
      toolsEnabled: caps.presets[tab] ?? [],
      ...(pickedModel ? { modelOverride: pickedModel } : {}),
    });
    onClose();
  }

  return (
    <div
      style={{ ...modalOverlayStyle, zIndex: 450 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        ...wideModalWidthStyle,
        ...modalPanelStyle, padding: "20px 24px",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <AgentFigure
              agentId={agent.id}
              color={agent.color}
              isPrototype={agent.is_prototype}
              cloneFrom={agent.clone_from}
              scale={0.65}
            />
            <div>
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{agent.name}</h2>
              <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 3 }}>{agent.tagline}</div>
            </div>
          </div>
          <button type="button" onClick={onClose} style={ghostBtn}>✕</button>
        </div>

        {loading ? (
          <div style={{ padding: 24, textAlign: "center", color: "var(--text-dim)", fontSize: 12 }}>
            Loading profile…
          </div>
        ) : error ? (
          <div style={{ padding: 12, color: "#ff8080", fontSize: 11 }}>{error}</div>
        ) : caps && (
          <>
            {/* Persona — SOUL | MEMORY side by side */}
            <div style={sectionBoxStyle}>
              <div style={sectionTitleStyle}>Persona</div>
              <div style={personaGridStyle}>
                <PersonaColumn label="SOUL.md — personality & instructions" text={soul} />
                <PersonaColumn label="MEMORY.md — persistent notes" text={memory} />
              </div>
            </div>

            {backendUrl && (
              <ModelSelectField
                label="Model (this desk)"
                baseUrl={backendUrl}
                profileModel={agent.model || ""}
                value={modelOverride}
                onChange={setModelOverride}
                models={availableModels}
                hint={
                  agent.id === "cloud" || backendUrl.includes("generativelanguage.googleapis.com")
                    ? "Pick any Gemini model from the profile catalog, or keep the profile default."
                    : "Per-desk override — leave as profile default or pick another model from the backend."
                }
              />
            )}

            {/* Tools + skills — full width below */}
            <div style={{ display: "grid", gridTemplateColumns: skillBundles.length ? "1fr 1fr" : "1fr", gap: 12 }}>
              <div style={{ ...sectionBoxStyle, marginBottom: 0 }}>
                <div style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  marginBottom: 8, gap: 8, flexWrap: "wrap",
                }}>
                  <div style={sectionTitleStyle}>Tools</div>
                  <div style={{ fontSize: 10, color: "var(--text-dim)" }}>
                    {caps.source === "profile" ? "Profile presets" : "Global presets"}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 4, marginBottom: 10 }}>
                  {PRESET_TABS.map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setTab(id)}
                      style={presetTabStyle(tab === id)}
                    >
                      {id}
                      {caps.default_preset === id && (
                        <span style={{ marginLeft: 4, fontSize: 9, opacity: 0.75 }}>default</span>
                      )}
                    </button>
                  ))}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                  {toolsets.map((t) => (
                    <span
                      key={t.name}
                      title={(t.tools ?? [t.name]).join(", ")}
                      style={toolChipStyle(enabledSet.has(t.name))}
                    >
                      {labels[t.name] ?? t.label}
                    </span>
                  ))}
                </div>
              </div>

              {skillBundles.length > 0 && (
                <div style={{ ...sectionBoxStyle, marginBottom: 0 }}>
                  <div style={sectionTitleStyle}>
                    Skill categories
                    <span style={{ fontWeight: 400, marginLeft: 6, textTransform: "none", letterSpacing: 0 }}>
                      ({caps.skill_count} installed)
                    </span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {skillBundles.map((b) => (
                      <span key={b.bundle} style={skillChipStyle(b.count > 0)} title={
                        b.count > 0 ? b.skills.slice(0, 8).join(", ") : "No skills in this category"
                      }>
                        {b.bundle}
                        <span style={{ marginLeft: 4, opacity: b.count > 0 ? 0.85 : 0.55 }}>
                          ({b.count})
                        </span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
              <button type="button" onClick={onClose} style={ghostBtn}>Cancel</button>
              <button type="button" onClick={handleAssign} style={primaryBtn}>Assign to desk</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const ghostBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "var(--text-dim)", cursor: "pointer", fontSize: 14,
};

const primaryBtn: React.CSSProperties = {
  padding: "8px 14px", borderRadius: 6, border: "none", cursor: "pointer",
  background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600,
};
