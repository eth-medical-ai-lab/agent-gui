import type React from "react";
import type { LlmProvider } from "../types";
import { inputSolidStyle, sectionBoxStyle, sectionTitleStyle } from "./modalStyles";

interface Props {
  providers: LlmProvider[];
  providerId: string;
  model: string;
  loading?: boolean;
  hint?: string;
  onChange: (next: { providerId: string; model: string; baseUrl: string }) => void;
}

/** Cascading backend → model picker sourced from a profile's `providers:` block. */
export function ProviderModelSelect({
  providers, providerId, model, loading, hint, onChange,
}: Props) {
  if (loading) {
    return (
      <div style={{ ...sectionBoxStyle, marginBottom: 12 }}>
        <div style={sectionTitleStyle}>Backend & model</div>
        <div style={{ fontSize: 10, color: "var(--text-dim)" }}>Loading providers…</div>
      </div>
    );
  }
  if (providers.length === 0) return null;

  const selected = providers.find((p) => p.id === providerId) ?? providers[0];
  const modelOptions = selected.models.length ? selected.models : [model].filter(Boolean);

  function pickProvider(id: string) {
    const p = providers.find((x) => x.id === id) ?? providers[0];
    const nextModel = p.default_model || p.models[0] || "";
    onChange({ providerId: p.id, model: nextModel, baseUrl: p.base_url });
  }

  function pickModel(m: string) {
    onChange({ providerId: selected.id, model: m, baseUrl: selected.base_url });
  }

  return (
    <div style={{ ...sectionBoxStyle, marginBottom: 12 }}>
      <div style={sectionTitleStyle}>Backend & model</div>
      <div style={{ display: "flex", gap: 8 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <span style={labelStyle}>Provider</span>
          <select
            value={selected.id}
            onChange={(e) => pickProvider(e.target.value)}
            style={selectStyle}
          >
            {providers.map((p) => (
              <option key={p.id} value={p.id}>{p.name || p.id}</option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <span style={labelStyle}>Model</span>
          <select
            value={model}
            onChange={(e) => pickModel(e.target.value)}
            disabled={modelOptions.length === 0}
            title={selected.base_url}
            style={selectStyle}
          >
            {model && !modelOptions.includes(model) && (
              <option value={model}>{model}</option>
            )}
            {modelOptions.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 6, opacity: 0.85 }}>
        {selected.base_url}
      </div>
      {hint && (
        <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 4, opacity: 0.85 }}>
          {hint}
        </div>
      )}
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
  color: "var(--text-dim)",
};

const selectStyle: React.CSSProperties = {
  ...inputSolidStyle,
  width: "100%",
  fontFamily: "ui-monospace, monospace",
};
