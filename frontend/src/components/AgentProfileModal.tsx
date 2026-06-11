import React, { useEffect, useState } from "react";
import type { AgentPersona, AgentProfile, AgentPrototype, LlmProvider } from "../types";
import { api } from "../api/client";
import { useAvatarPrefs } from "../avatarPrefs";
import { AvatarColorPicker } from "./AvatarColorPicker";
import { ModelSelectField } from "./ModelSelectField";
import { ProviderModelSelect } from "./ProviderModelSelect";
import {
  inputSolidStyle,
  modalPanelStyle,
  modalOverlayStyle,
  personaColumnStyle,
  personaGridStyle,
  sectionBoxStyle,
  sectionTitleStyle,
  wideModalWidthStyle,
} from "./modalStyles";

interface Props {
  mode: "create" | "edit";
  agent?: AgentProfile | null;
  prototypes: AgentPrototype[];
  agents?: AgentProfile[];
  embedded?: boolean;
  onClose: () => void;
  onSaved: () => void;
  onDeleted?: () => void;
}

export function AgentProfileModal({ mode, agent, prototypes, agents = [], embedded = false, onClose, onSaved, onDeleted }: Props) {
  // Clone source can be any built-in prototype OR any installed profile. Show
  // prototypes first, then the rest of the installed profiles (deduped).
  const protoIds = new Set(prototypes.map((p) => p.id));
  const cloneSources = [
    ...prototypes.map((p) => ({ id: p.id, label: `${p.name} — ${p.tagline}`, group: "Templates" })),
    ...agents
      .filter((a) => !protoIds.has(a.id) && a.available !== false)
      .map((a) => ({ id: a.id, label: a.name ? `${a.name} (${a.id})` : a.id, group: "Existing profiles" })),
  ];

  const [profileId, setProfileId] = useState("");
  const [cloneFrom, setCloneFrom] = useState(cloneSources[0]?.id ?? prototypes[0]?.id ?? "coder");
  const [displayName, setDisplayName] = useState("");
  const [tagline, setTagline] = useState("");
  const [soul, setSoul] = useState("");
  const [memory, setMemory] = useState("");
  const [profileModel, setProfileModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [providerId, setProviderId] = useState("");
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [providersLoading, setProvidersLoading] = useState(false);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [loading, setLoading] = useState(mode === "edit");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canDelete = mode === "edit" && !!agent;
  const avatars = useAvatarPrefs();

  useEffect(() => {
    if (mode !== "edit" || !agent) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.agents.persona(agent.id)
      .then((p: AgentPersona) => {
        if (cancelled) return;
        setDisplayName(p.name ?? agent.name);
        setTagline(agent.tagline ?? "");
        setSoul(p.soul ?? "");
        setMemory(p.memory ?? "");
        const model = p.model ?? agent.model ?? "";
        const url = p.base_url ?? agent.base_url ?? "";
        setProfileModel(model);
        setBaseUrl(url);
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [mode, agent]);

  useEffect(() => {
    if (mode !== "edit" || !agent?.id || !baseUrl.trim()) {
      setAvailableModels([]);
      return;
    }
    let cancelled = false;
    setModelsLoading(true);
    api.llm.models({ agentId: agent.id, baseUrl })
      .then((r) => { if (!cancelled) setAvailableModels(r.models ?? []); })
      .catch(() => { if (!cancelled) setAvailableModels([]); })
      .finally(() => { if (!cancelled) setModelsLoading(false); });
    return () => { cancelled = true; };
  }, [mode, agent?.id, baseUrl]);

  useEffect(() => {
    // Edit → that profile's providers; create → global (~/.hermes) catalog, since
    // prototypes ship with an empty `providers:` block.
    let cancelled = false;
    setProvidersLoading(true);
    const req = mode === "edit"
      ? (agent?.id ? api.llm.providers({ agentId: agent.id }) : null)
      : api.llm.providers();
    if (!req) { setProviders([]); setProvidersLoading(false); return; }
    req
      .then((r) => {
        if (cancelled) return;
        setProviders(r.providers ?? []);
        // Create: default to the first provider's model so a backend is always chosen.
        if (mode === "create") {
          const p = (r.providers ?? [])[0];
          if (p) {
            setProviderId(p.id);
            setProfileModel(p.default_model || p.models[0] || "");
            setBaseUrl(p.base_url);
          }
        } else {
          setProviderId(r.active ?? "");
        }
      })
      .catch(() => { if (!cancelled) setProviders([]); })
      .finally(() => { if (!cancelled) setProvidersLoading(false); });
    return () => { cancelled = true; };
  }, [mode, agent?.id]);

  useEffect(() => {
    if (mode !== "create" || !cloneFrom) return;
    let cancelled = false;
    api.agents.persona(cloneFrom)
      .then((p: AgentPersona) => {
        if (cancelled) return;
        setSoul(p.soul ?? "");
        setMemory(p.memory ?? "");
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [mode, cloneFrom]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      if (mode === "create") {
        const id = profileId.trim().toLowerCase();
        if (!id) throw new Error("Profile id is required");
        const selected = providers.length > 0 && providerId
          ? providers.find((p) => p.id === providerId)
          : undefined;
        await api.agents.create({
          id,
          clone_from: cloneFrom,
          name: displayName.trim() || undefined,
          tagline: tagline.trim() || undefined,
          soul,
          memory,
          ...(selected && profileModel.trim()
            ? { model_default: profileModel, base_url: selected.base_url, provider: selected.id }
            : {}),
        });
      } else if (agent) {
        const usingProviders = providers.length > 0 && !!providerId;
        const selected = usingProviders ? providers.find((p) => p.id === providerId) : undefined;
        await api.agents.savePersona(agent.id, {
          soul,
          memory,
          ...(baseUrl.trim() || selected ? { model_default: profileModel } : {}),
          ...(selected ? { base_url: selected.base_url, provider: selected.id } : {}),
        });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!agent) return;
    const label = agent.name || agent.id;
    if (!window.confirm(`Delete agent profile "${label}"? This cannot be undone.`)) return;
    setDeleting(true);
    setError(null);
    try {
      await api.agents.delete(agent.id);
      onDeleted?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  const title = mode === "create" ? "New agent profile" : `Edit ${agent?.name ?? "agent"}`;

  const body = (
    <>
      {!embedded && (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{title}</h2>
          <button type="button" onClick={onClose} style={ghostBtn}>✕</button>
        </div>
      )}

      {loading ? (
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-dim)", fontSize: 12 }}>
          Loading…
        </div>
      ) : (
        <>
          {mode === "create" && (
            <div style={{ marginBottom: 12 }}>
              <Field label="Profile id">
                <input
                  value={profileId}
                  onChange={(e) => setProfileId(e.target.value)}
                  placeholder="my-coder"
                  style={inputStyle}
                  autoFocus
                />
                <Hint>Lowercase letters, numbers, hyphens. Used by Hermes as the profile name.</Hint>
              </Field>
              <Field label="Clone from">
                <select value={cloneFrom} onChange={(e) => setCloneFrom(e.target.value)} style={inputStyle}>
                  {["Templates", "Existing profiles"].map((group) => {
                    const opts = cloneSources.filter((s) => s.group === group);
                    if (opts.length === 0) return null;
                    return (
                      <optgroup key={group} label={group}>
                        {opts.map((s) => (
                          <option key={s.id} value={s.id}>{s.label}</option>
                        ))}
                      </optgroup>
                    );
                  })}
                </select>
                <Hint>Copies the source profile's config, SOUL &amp; MEMORY as a starting point.</Hint>
              </Field>
              <Field label="Display name (optional)">
                <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} style={inputStyle} />
              </Field>
              <Field label="Tagline (optional)">
                <input value={tagline} onChange={(e) => setTagline(e.target.value)} style={inputStyle} />
              </Field>
              {providers.length > 0 && (
                <ProviderModelSelect
                  providers={providers}
                  providerId={providerId}
                  model={profileModel}
                  loading={providersLoading}
                  hint="Backend for this profile — saved to the clone's config.yaml."
                  onChange={({ providerId: pid, model, baseUrl: url }) => {
                    setProviderId(pid);
                    setProfileModel(model);
                    setBaseUrl(url);
                  }}
                />
              )}
            </div>
          )}

          {mode === "edit" && agent && (
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 12 }}>
              Profile <code style={{ opacity: 0.85 }}>{agent.id}</code>
              {agent.is_prototype && " · prototype"}
              {agent.clone_from && !agent.is_prototype && (
                <> · clone of <code style={{ opacity: 0.85 }}>{agent.clone_from}</code></>
              )}
            </div>
          )}

          {mode === "edit" && providers.length > 0 ? (
            <ProviderModelSelect
              providers={providers}
              providerId={providerId}
              model={profileModel}
              loading={providersLoading}
              hint="Saved to config.yaml — used for every desk unless overridden at assign time."
              onChange={({ providerId: pid, model, baseUrl: url }) => {
                setProviderId(pid);
                setProfileModel(model);
                setBaseUrl(url);
              }}
            />
          ) : mode === "edit" && baseUrl.trim() && (
            <ModelSelectField
              label="Default model"
              baseUrl={baseUrl}
              profileModel={profileModel}
              value={profileModel}
              onChange={setProfileModel}
              models={availableModels}
              loading={modelsLoading}
              hint="Saved to config.yaml — used for every desk unless overridden at assign time."
            />
          )}

          {mode === "edit" && agent && (
            <div style={sectionBoxStyle}>
              <div style={sectionTitleStyle}>Avatar &amp; color</div>
              <AvatarColorPicker
                value={avatars.get(agent.id) ?? {}}
                fallbackColor={agent.color}
                onChange={(patch) => avatars.set(agent.id, patch)}
              />
            </div>
          )}

          <div style={sectionBoxStyle}>
            <div style={sectionTitleStyle}>Persona</div>
            <div style={personaGridStyle}>
              <PersonaField label="SOUL.md — personality & instructions">
                <textarea value={soul} onChange={(e) => setSoul(e.target.value)} style={personaTextareaStyle} />
              </PersonaField>
              <PersonaField label="MEMORY.md — persistent notes">
                <textarea value={memory} onChange={(e) => setMemory(e.target.value)} style={personaTextareaStyle} />
              </PersonaField>
            </div>
          </div>

          {error && (
            <div style={{ color: "#ff8080", fontSize: 11, marginBottom: 10 }}>{error}</div>
          )}

          <div style={{ display: "flex", gap: 8, justifyContent: "space-between", marginTop: 8, alignItems: "center" }}>
            <div>
              {canDelete && (
                <button
                  type="button"
                  onClick={handleDelete}
                  disabled={deleting || saving}
                  style={deleteBtn}
                >
                  {deleting ? "Deleting…" : "Delete profile"}
                </button>
              )}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" onClick={onClose} style={ghostBtn} disabled={saving || deleting}>Cancel</button>
              <button type="button" onClick={handleSave} style={primaryBtn} disabled={saving || deleting}>
                {saving ? "Saving…" : mode === "create" ? "Create profile" : "Save"}
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );

  if (embedded) return body;

  return (
    <div
      style={{ ...modalOverlayStyle, zIndex: 400 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        ...wideModalWidthStyle,
        ...modalPanelStyle, padding: "20px 24px",
      }}>
        {body}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", minWidth: 0, marginBottom: 12 }}>
      <div style={fieldLabelStyle}>{label}</div>
      {children}
    </label>
  );
}

function PersonaField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div style={fieldLabelStyle}>{label}</div>
      {children}
    </label>
  );
}

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
  color: "var(--text-dim)", marginBottom: 5,
};

function Hint({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 4, opacity: 0.8 }}>{children}</div>;
}

const inputStyle: React.CSSProperties = inputSolidStyle;

const personaTextareaStyle: React.CSSProperties = {
  ...inputSolidStyle,
  ...personaColumnStyle,
  width: "100%",
  boxSizing: "border-box",
  resize: "vertical",
  fontFamily: "ui-monospace, monospace",
  lineHeight: 1.45,
  flex: undefined,
};

const ghostBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "var(--text-dim)", cursor: "pointer", fontSize: 14,
};

const primaryBtn: React.CSSProperties = {
  padding: "8px 14px", borderRadius: 6, border: "none", cursor: "pointer",
  background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600,
};

const deleteBtn: React.CSSProperties = {
  padding: "8px 14px", borderRadius: 6, cursor: "pointer",
  background: "rgba(220,80,80,0.15)", color: "var(--red)",
  border: "1px solid var(--red)", fontSize: 12, fontWeight: 600,
};
