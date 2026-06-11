import { useEffect, useMemo, useState } from "react";
import type React from "react";
import type { AgentProfile, ToolPresetId, ToolsetMeta } from "../types";
import type { DeskConfigView } from "../deskConfig";
import { DEFAULT_PROFILE_COLOR, DEFAULT_PROFILE_LABEL } from "../deskConfig";
import { api } from "../api/client";
import { DeskToolsControl, deskSelectStyle } from "./DeskToolsControl";

interface Props {
  config: DeskConfigView | null;
  agents: AgentProfile[];
  toolsets: ToolsetMeta[];
  compact?: boolean;
  vertical?: boolean;
  /** Header strip: no background box; spread profile/model/tools evenly. */
  spread?: boolean;
  bare?: boolean;
  showLabels?: boolean;
  showAdvanced?: boolean;
  /** Render the profile as a read-only chip (editing moved to the desk ⚙ panel). */
  profileReadOnly?: boolean;
  /** Render the model as plain text (editing moved to the desk ⚙ panel). */
  modelReadOnly?: boolean;
  /** Render tools as plain text (editing moved to the desk ⚙ panel). */
  toolsReadOnly?: boolean;
  highlighted?: boolean;
  onFocus?: () => void;
  onProfileChange: (agentId: string) => void;
  onModelChange: (model: string) => void;
  onToolsChange: (preset: ToolPresetId, enabled: string[]) => void;
  onAdvanced?: (agent: AgentProfile) => void;
}

export function DeskContextBar({
  config,
  agents,
  toolsets,
  compact = false,
  vertical = false,
  spread = false,
  bare = false,
  showLabels = true,
  showAdvanced = true,
  profileReadOnly = false,
  modelReadOnly = false,
  toolsReadOnly = false,
  highlighted = false,
  onFocus,
  onProfileChange,
  onModelChange,
  onToolsChange,
  onAdvanced,
}: Props) {
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  useEffect(() => {
    if (modelReadOnly || !config?.baseUrl.trim()) {
      setModels([]);
      return;
    }
    let cancelled = false;
    setModelsLoading(true);
    api.llm.models({
      baseUrl: config.baseUrl,
      agentId: config.agentId || undefined,
    })
      .then((r) => { if (!cancelled) setModels(r.models ?? []); })
      .catch(() => { if (!cancelled) setModels([]); })
      .finally(() => { if (!cancelled) setModelsLoading(false); });
  }, [config?.deskId, config?.agentId, config?.baseUrl, modelReadOnly]);

  const modelOptions = useMemo(() => {
    if (!config) return [];
    const names = new Set<string>();
    if (config.profileModel) names.add(config.profileModel);
    for (const m of models) names.add(m);
    if (config.model) names.add(config.model);
    return [...names];
  }, [config, models]);

  if (!config) {
    if (compact) return null;
    return (
      <span style={{ fontSize: 11, color: "var(--text-dim)", fontStyle: "italic" }}>
        Select a desk to configure its agent
      </span>
    );
  }

  const hasBackend = Boolean(config.baseUrl.trim());
  const isHeaderStrip = spread && bare && !vertical;
  const gap = vertical ? 8 : isHeaderStrip ? 10 : compact ? 6 : 14;
  const labelStyle: React.CSSProperties = {
    fontSize: isHeaderStrip ? 12 : compact ? 9 : 10,
    color: "var(--text-dim)",
    flexShrink: 0,
    userSelect: "none",
    ...(vertical ? { width: 52 } : {}),
  };
  const selectStyle: React.CSSProperties = {
    ...deskSelectStyle,
    fontSize: isHeaderStrip ? 13 : compact ? 10 : 11,
    padding: isHeaderStrip ? "6px 12px" : compact ? "3px 6px" : "4px 8px",
    maxWidth: vertical ? undefined : isHeaderStrip ? 200 : spread ? 160 : compact ? 110 : 150,
    ...(vertical ? { flex: 1, minWidth: 0, width: "100%" } : {}),
  };
  const modelSelectStyle: React.CSSProperties = {
    ...selectStyle,
    maxWidth: vertical ? undefined : isHeaderStrip ? 260 : spread ? 200 : compact ? 130 : 180,
    fontFamily: "ui-monospace, monospace",
  };
  const rowStyle: React.CSSProperties = vertical
    ? { display: "flex", alignItems: "center", gap: 8, width: "100%", minWidth: 0 }
    : { display: "flex", alignItems: "center", gap: isHeaderStrip ? 8 : compact ? 4 : 6, minWidth: 0, flexShrink: spread ? 0 : undefined };

  const useBox = compact && !vertical && !bare;
  const useContents = spread && bare && !vertical;

  function focusAnd<T>(fn: (v: T) => void, v: T) {
    onFocus?.();
    fn(v);
  }

  function focusAndTools(fn: (preset: ToolPresetId, enabled: string[]) => void, preset: ToolPresetId, enabled: string[]) {
    onFocus?.();
    fn(preset, enabled);
  }

  return (
    <div
      style={{
        display: useContents ? "contents" : "flex",
        flexDirection: vertical ? "column" : "row",
        alignItems: vertical ? "stretch" : "center",
        gap: spread ? 0 : gap,
        minWidth: 0,
        flex: spread && !useContents ? 1 : undefined,
        width: spread && !useContents ? "100%" : undefined,
        justifyContent: spread && !useContents ? "space-evenly" : undefined,
        flexWrap: vertical ? "nowrap" : spread ? "nowrap" : compact ? "wrap" : "wrap",
        padding: useBox ? "4px 6px" : undefined,
        borderRadius: useBox ? 6 : undefined,
        background: useBox
          ? highlighted ? "rgba(100,200,255,0.08)" : "rgba(18,24,40,0.85)"
          : undefined,
        border: useBox ? `1px solid ${highlighted ? "var(--accent2)" : "#2a3558"}` : undefined,
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div style={rowStyle}>
        {showLabels && <span style={labelStyle}>Profile</span>}
        {profileReadOnly ? (
          <span
            title="Profile — change it from the desk's ⚙ settings"
            style={{
              display: "inline-flex", alignItems: "center", gap: 7,
              fontSize: isHeaderStrip ? 14 : 12, fontWeight: 600,
              color: "var(--text)", letterSpacing: 0.2,
              maxWidth: isHeaderStrip ? 220 : 160,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}
          >
            <span style={{
              width: 9, height: 9, borderRadius: "50%", flexShrink: 0,
              background: config.agentProfile?.color ?? DEFAULT_PROFILE_COLOR,
              boxShadow: "0 0 5px rgba(0,0,0,0.4)",
            }} />
            {config.agentProfile?.name
              ?? agents.find((a) => a.id === config.agentId)?.name
              ?? DEFAULT_PROFILE_LABEL}
          </span>
        ) : (
          <select
            value={config.agentId}
            onChange={(e) => focusAnd(onProfileChange, e.target.value)}
            title="Hermes agent profile (Default = ~/.hermes/config.yaml)"
            style={selectStyle}
          >
            <option value="">Default</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        )}
      </div>

      {!vertical && !compact && !spread && <div style={{ width: 1, height: 22, background: "var(--card-border)", flexShrink: 0 }} />}

      <div style={rowStyle}>
        {showLabels && <span style={labelStyle}>Model</span>}
        {modelReadOnly ? (
          <span
            title="Model — change it from the desk's ⚙ settings"
            style={{
              fontSize: isHeaderStrip ? 13 : 12,
              fontFamily: "ui-monospace, monospace",
              color: config.model ? "var(--text)" : "var(--text-dim)",
              maxWidth: isHeaderStrip ? 260 : 180,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}
          >
            {config.model || config.profileModel || "—"}
          </span>
        ) : (
          <>
            <select
              value={config.model}
              onChange={(e) => focusAnd(onModelChange, e.target.value)}
              disabled={!hasBackend}
              title={config.baseUrl || "No backend URL in config"}
              style={modelSelectStyle}
            >
              {modelOptions.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            {modelsLoading && (
              <span style={{ fontSize: 9, color: "var(--text-dim)" }}>…</span>
            )}
          </>
        )}
      </div>

      {!vertical && !compact && !spread && <div style={{ width: 1, height: 22, background: "var(--card-border)", flexShrink: 0 }} />}

      <div style={spread ? rowStyle : vertical ? { ...rowStyle, flexWrap: "wrap" } : undefined}>
      <DeskToolsControl
        compact={compact}
        header={isHeaderStrip}
        vertical={vertical}
        readOnly={toolsReadOnly}
        toolPreset={config.toolPreset}
        toolsEnabled={config.toolsEnabled}
        toolsets={toolsets}
        onChange={(preset, enabled) => focusAndTools(onToolsChange, preset, enabled)}
      />
      </div>

      {showAdvanced && config.agentProfile && onAdvanced && (
        <>
          {!vertical && !compact && <div style={{ width: 1, height: 22, background: "var(--card-border)", flexShrink: 0 }} />}
          <button
            type="button"
            onClick={() => { onFocus?.(); onAdvanced(config.agentProfile!); }}
            style={{
              padding: compact ? "3px 7px" : "4px 10px",
              borderRadius: 6,
              fontSize: compact ? 9 : 10,
              fontWeight: 600,
              cursor: "pointer",
              border: "1px solid #2a3558",
              background: "#121828",
              color: "var(--text-dim)",
              flexShrink: 0,
            }}
          >
            Advanced…
          </button>
        </>
      )}
    </div>
  );
}
