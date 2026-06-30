import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AgentProfile } from "../types";
import type { SceneFloorChrome } from "../sceneFloorChrome";

const DEFAULT_OPT = "__default__";

/**
 * Settings wheel on the manager staging tile — points the team manager at an
 * installed profile's backend (model + base_url + API key). The manager only
 * makes plain LLM calls (audits / judge / titles), never tool calls.
 */
export function ManagerModelMenu({ chrome, agents }: { chrome: SceneFloorChrome; agents: AgentProfile[] }) {
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState("");   // "" = default ~/.hermes
  const [model, setModel] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api.manager.getProfile()
      .then((r) => { if (!cancelled) { setProfile(r.profile ?? ""); setModel(r.model ?? ""); } })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open]);

  async function choose(value: string) {
    const next = value === DEFAULT_OPT ? "" : value;
    setSaving(true);
    try {
      const r = await api.manager.setProfile(next);
      setProfile(r.profile ?? "");
      setModel(r.model ?? "");
    } catch { /* ignore */ }
    finally { setSaving(false); setOpen(false); }
  }

  const rect = open ? btnRef.current?.getBoundingClientRect() : undefined;
  // The Claude Agent SDK isn't an LLM endpoint (it resolves models itself — no
  // base_url to POST the manager's aux calls to), so it can't run the manager.
  const selectable = agents.filter(
    (a) => a.available !== false && a.base_url !== "claude-agent-sdk",
  );

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        title="Manager backend profile"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        onMouseDown={(e) => e.stopPropagation()}
        style={{
          position: "absolute", top: 3, right: 3, zIndex: 3,
          width: 18, height: 18, borderRadius: 4, lineHeight: 1, fontSize: 11,
          cursor: "pointer", padding: 0,
          background: open ? "rgba(100,160,255,0.18)" : "transparent",
          border: `1px solid ${open ? chrome.labelAccent : chrome.controlBorder}`,
          color: open ? chrome.labelAccent : chrome.labelDim,
        }}
      >
        ⚙
      </button>

      {open && rect && (
        <>
          <div
            style={{ position: "fixed", inset: 0, zIndex: 600 }}
            onMouseDown={(e) => { e.stopPropagation(); setOpen(false); }}
          />
          <div
            onMouseDown={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              left: Math.min(rect.left, window.innerWidth - 240),
              top: rect.bottom + 6,
              zIndex: 601, width: 230,
              background: "#16213e", border: "1px solid #2a3558", borderRadius: 8,
              boxShadow: "0 8px 28px rgba(0,0,0,0.5)", padding: 10,
            }}
          >
            <div style={{
              fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
              color: "var(--text-dim)", marginBottom: 6,
            }}>
              Manager profile
            </div>
            {loading ? (
              <div style={{ fontSize: 11, color: "var(--text-dim)", padding: "4px 0" }}>Loading…</div>
            ) : (
              <select
                value={profile || DEFAULT_OPT}
                disabled={saving}
                onChange={(e) => choose(e.target.value)}
                style={{
                  width: "100%", fontSize: 12, color: "var(--text)",
                  background: "#0f1626", border: "1px solid #2a3558", borderRadius: 5,
                  padding: "5px 7px",
                }}
              >
                <option value={DEFAULT_OPT}>Default (~/.hermes)</option>
                {selectable.map((a) => (
                  <option key={a.id} value={a.id}>{a.name || a.id}</option>
                ))}
              </select>
            )}
            <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 6, opacity: 0.85 }}>
              Runs the manager's audits, judging &amp; titles on this profile's backend
              {model ? ` · ${model}` : ""}. Applies to all teams.
            </div>
          </div>
        </>
      )}
    </>
  );
}
