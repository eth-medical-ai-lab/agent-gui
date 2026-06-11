import React, { useEffect, useMemo, useState } from "react";
import type { AgentCapabilities, ToolsetMeta } from "../types";
import { api } from "../api/client";
import {
  personaColumnStyle,
  personaGridStyle,
  presetTabStyle,
  sectionBoxStyle,
  sectionTitleStyle,
  skillChipStyle,
  toolChipStyle,
} from "./modalStyles";

const PRESET_TABS = ["chat", "lean", "full"] as const;
type PresetTab = (typeof PRESET_TABS)[number];

interface Props {
  agentId: string;
  toolsets: ToolsetMeta[];
  soul?: string;
  memory?: string;
}

function PersonaBlock({ label, text }: { label: string; text: string }) {
  const empty = !text.trim();
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div style={{ ...sectionTitleStyle, marginBottom: 5 }}>{label}</div>
      <div style={personaColumnStyle}>
        {empty ? <span style={{ color: "var(--text-dim)", fontStyle: "italic" }}>(empty)</span> : text}
      </div>
    </div>
  );
}

export function AgentToolsPreview({ agentId, toolsets, soul, memory }: Props) {
  const [caps, setCaps] = useState<AgentCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<PresetTab>("lean");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.agents.capabilities(agentId)
      .then((data) => {
        if (cancelled) return;
        setCaps(data);
        const def = data.default_preset;
        if (def === "chat" || def === "lean" || def === "full") setTab(def);
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [agentId]);

  const labels = useMemo(
    () => Object.fromEntries(toolsets.map((t) => [t.name, t.label])),
    [toolsets],
  );

  const skillBundles = useMemo(() => {
    if (!caps) return [];
    return [...caps.skill_bundles].sort((a, b) => {
      if (a.count > 0 && b.count === 0) return -1;
      if (a.count === 0 && b.count > 0) return 1;
      return a.bundle.localeCompare(b.bundle);
    });
  }, [caps]);

  if (loading) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-dim)", padding: "8px 0" }}>
        Loading profile…
      </div>
    );
  }
  if (error || !caps) {
    return (
      <div style={{ fontSize: 11, color: "#ff8080", padding: "8px 0" }}>
        {error ?? "Could not load capabilities"}
      </div>
    );
  }

  const enabledSet = new Set(caps.presets[tab] ?? []);
  const showPersona = soul !== undefined || memory !== undefined;

  return (
    <>
      {showPersona && (
        <div style={sectionBoxStyle}>
          <div style={sectionTitleStyle}>Persona</div>
          <div style={personaGridStyle}>
            {soul !== undefined && (
              <PersonaBlock label="SOUL.md — personality & instructions" text={soul} />
            )}
            {memory !== undefined && (
              <PersonaBlock label="MEMORY.md — persistent notes" text={memory} />
            )}
          </div>
        </div>
      )}

      <div style={sectionBoxStyle}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 8, gap: 8, flexWrap: "wrap",
        }}>
          <div style={sectionTitleStyle}>Tools</div>
          <div style={{ fontSize: 10, color: "var(--text-dim)" }}>
            {caps.source === "profile" ? "Profile presets" : "Global presets"}
            {caps.profile_disabled_toolsets.length > 0
              ? ` · blocks ${caps.profile_disabled_toolsets.join(", ")}`
              : ""}
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

        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: skillBundles.length ? 0 : undefined }}>
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
        <div style={sectionBoxStyle}>
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
    </>
  );
}
