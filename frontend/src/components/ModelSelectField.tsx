import React from "react";
import { inputSolidStyle, sectionBoxStyle, sectionTitleStyle } from "./modalStyles";

interface Props {
  label?: string;
  baseUrl?: string;
  profileModel: string;
  value: string;
  onChange: (model: string) => void;
  models: string[];
  loading?: boolean;
  hint?: string;
}

export function ModelSelectField({
  label = "Default model",
  baseUrl,
  profileModel,
  value,
  onChange,
  models,
  loading,
  hint,
}: Props) {
  if (!baseUrl?.trim()) return null;

  return (
    <div style={{ ...sectionBoxStyle, marginBottom: 12 }}>
      <div style={sectionTitleStyle}>{label}</div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading || (models.length === 0 && !profileModel)}
        title={baseUrl}
        style={{
          ...inputSolidStyle,
          width: "100%",
          fontFamily: "ui-monospace, monospace",
        }}
      >
        {profileModel && (
          <option value={profileModel}>
            {models.includes(profileModel) ? profileModel : `profile default (${profileModel})`}
          </option>
        )}
        {models.filter((m) => m !== profileModel).map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>
      {loading && (
        <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 6 }}>Loading models…</div>
      )}
      {hint && (
        <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 6, opacity: 0.85 }}>
          {hint}
        </div>
      )}
      {!loading && models.length > 1 && (
        <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 4, opacity: 0.7 }}>
          {models.length} models available
        </div>
      )}
    </div>
  );
}
