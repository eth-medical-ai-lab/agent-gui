import { useEffect, useState } from "react";
import type React from "react";
import type { LlmProvider } from "../types";
import { api } from "../api/client";
import { ModelSelectField } from "./ModelSelectField";
import { ProviderModelSelect } from "./ProviderModelSelect";
import {
  personaColumnStyle,
  personaGridStyle,
  sectionBoxStyle,
  sectionTitleStyle,
} from "./modalStyles";

interface Props {
  onClose: () => void;
  onSaved: () => void;
}

export function GlobalDefaultPersonaEditor({ onClose, onSaved }: Props) {
  const [soul, setSoul] = useState("");
  const [memory, setMemory] = useState("");
  const [profileModel, setProfileModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [providerId, setProviderId] = useState("");
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [providersLoading, setProvidersLoading] = useState(true);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.globalPersona.get()
      .then((p) => {
        if (cancelled) return;
        setSoul(p.soul ?? "");
        setMemory(p.memory ?? "");
        setProfileModel(p.model ?? "");
        setBaseUrl(p.base_url ?? "");
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setProvidersLoading(true);
    api.llm.providers()
      .then((r) => {
        if (cancelled) return;
        setProviders(r.providers ?? []);
        setProviderId(r.active ?? "");
      })
      .catch(() => { if (!cancelled) setProviders([]); })
      .finally(() => { if (!cancelled) setProvidersLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!baseUrl.trim()) {
      setAvailableModels([]);
      return;
    }
    let cancelled = false;
    setModelsLoading(true);
    api.llm.models({ baseUrl })
      .then((r) => { if (!cancelled) setAvailableModels(r.models ?? []); })
      .catch(() => { if (!cancelled) setAvailableModels([]); })
      .finally(() => { if (!cancelled) setModelsLoading(false); });
    return () => { cancelled = true; };
  }, [baseUrl]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const usingProviders = providers.length > 0 && !!providerId;
      const selected = usingProviders ? providers.find((p) => p.id === providerId) : undefined;
      await api.globalPersona.save({
        soul,
        memory,
        ...(profileModel.trim() ? { model_default: profileModel } : {}),
        ...(selected ? { base_url: selected.base_url, provider: selected.id } : {}),
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: "var(--text-dim)", fontSize: 12 }}>
        Loading default agent…
      </div>
    );
  }

  return (
    <>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 12 }}>
        Default agent uses <code style={{ opacity: 0.85 }}>~/.hermes/config.yaml</code> and applies to desks
        unless a named profile is selected.
      </div>

      {providers.length > 0 ? (
        <ProviderModelSelect
          providers={providers}
          providerId={providerId}
          model={profileModel}
          loading={providersLoading}
          hint="Saved to ~/.hermes/config.yaml — used when no profile is assigned."
          onChange={({ providerId: pid, model, baseUrl: url }) => {
            setProviderId(pid);
            setProfileModel(model);
            setBaseUrl(url);
          }}
        />
      ) : baseUrl.trim() && (
        <ModelSelectField
          label="Default model"
          baseUrl={baseUrl}
          profileModel={profileModel}
          value={profileModel}
          onChange={setProfileModel}
          models={availableModels}
          loading={modelsLoading}
          hint="Saved to ~/.hermes/config.yaml — used when no profile is assigned."
        />
      )}

      <div style={sectionBoxStyle}>
        <div style={sectionTitleStyle}>Persona</div>
        <div style={personaGridStyle}>
          <label style={personaColumnStyle}>
            <div style={fieldLabelStyle}>SOUL.md — personality & instructions</div>
            <textarea value={soul} onChange={(e) => setSoul(e.target.value)} style={personaTextareaStyle} />
          </label>
          <label style={personaColumnStyle}>
            <div style={fieldLabelStyle}>MEMORY.md — persistent notes</div>
            <textarea value={memory} onChange={(e) => setMemory(e.target.value)} style={personaTextareaStyle} />
          </label>
        </div>
      </div>

      {error && (
        <div style={{ color: "#ff8080", fontSize: 11, marginBottom: 10 }}>{error}</div>
      )}

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
        <button type="button" onClick={onClose} style={ghostBtn} disabled={saving}>Cancel</button>
        <button type="button" onClick={handleSave} style={primaryBtn} disabled={saving}>
          {saving ? "Saving…" : "Save default agent"}
        </button>
      </div>
    </>
  );
}

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
  color: "var(--text-dim)", marginBottom: 5,
};

const personaTextareaStyle: React.CSSProperties = {
  width: "100%", minHeight: 160, resize: "vertical",
  fontSize: 12, lineHeight: 1.45, padding: "8px 10px",
  background: "#121828", border: "1px solid #2a3558", borderRadius: 6,
  color: "var(--text)", fontFamily: "inherit",
};

const ghostBtn: React.CSSProperties = {
  background: "transparent", border: "1px solid #2a3558", borderRadius: 6,
  color: "var(--text-dim)", cursor: "pointer", fontSize: 12, padding: "6px 12px",
};

const primaryBtn: React.CSSProperties = {
  background: "var(--accent2)", border: "none", borderRadius: 6,
  color: "#fff", cursor: "pointer", fontSize: 12, fontWeight: 600, padding: "6px 14px",
};
