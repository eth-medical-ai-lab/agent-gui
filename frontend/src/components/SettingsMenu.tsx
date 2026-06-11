import { useEffect, useRef, useState } from "react";
import { BELL_SOUNDS, playBell } from "../sounds";
import { SCENES } from "./SceneBackground";
import { CODE_THEMES } from "./FilePreview";
import type { CodeThemeId } from "./FilePreview";

const MANAGER_SEC_MIN = 10;
const MANAGER_SEC_MAX = 3600;

function clampManagerSec(n: number, fallback: number): number {
  if (!Number.isFinite(n)) return fallback;
  return Math.min(MANAGER_SEC_MAX, Math.max(MANAGER_SEC_MIN, Math.round(n)));
}

function SecInput({
  value, onChange, min = MANAGER_SEC_MIN, max = MANAGER_SEC_MAX,
}: {
  value: number;
  onChange: (sec: number) => void;
  min?: number;
  max?: number;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => { setDraft(String(value)); }, [value]);

  function commit(raw: string) {
    const clamped = clampManagerSec(parseInt(raw, 10), value);
    onChange(clamped);
    setDraft(String(clamped));
  }

  return (
    <input
      type="number"
      min={min}
      max={max}
      step={1}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => commit(draft)}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
      style={{
        width: 52, padding: "3px 6px", borderRadius: 4, fontSize: 12,
        background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)",
        color: "var(--text)", textAlign: "right",
      }}
    />
  );
}

interface Props {
  bellSound: string;
  onBellSoundChange: (id: string) => void;
  scene: string;
  onSceneChange: (id: string) => void;
  showManager: boolean;
  onShowManagerChange: (v: boolean) => void;
  managerPatrolIntervalSec: number;
  onManagerPatrolIntervalChange: (sec: number) => void;
  managerIdleGraceSec: number;
  onManagerIdleGraceChange: (sec: number) => void;
  codeTheme: CodeThemeId;
  onCodeThemeChange: (id: CodeThemeId) => void;
  dockerPersist: boolean;
  onDockerPersistChange: (v: boolean) => void;
  verbose: boolean;
  onVerboseChange: (v: boolean) => void;
}

export function SettingsMenu({
  bellSound, onBellSoundChange, scene, onSceneChange, showManager, onShowManagerChange,
  managerPatrolIntervalSec, onManagerPatrolIntervalChange,
  managerIdleGraceSec, onManagerIdleGraceChange,
  codeTheme, onCodeThemeChange,
  dockerPersist, onDockerPersistChange, verbose, onVerboseChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title="Settings"
        style={{
          width: 32, height: 32,
          background: open ? "var(--accent2)" : "rgba(255,255,255,0.06)",
          border: "1px solid var(--card-border)",
          borderRadius: 6, color: open ? "white" : "var(--text)",
          fontSize: 15, display: "flex", alignItems: "center", justifyContent: "center",
          cursor: "pointer",
        }}
      >
        ⚙
      </button>

      {open && (
        <div style={{
          position: "absolute", top: 38, right: 0, zIndex: 200, width: 250,
          background: "var(--bg2)", border: "1px solid var(--card-border)",
          borderRadius: 8, boxShadow: "0 8px 32px rgba(0,0,0,0.6)", padding: 12,
        }}>
          <div style={{
            fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
            textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8,
          }}>
            Bell sound
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {BELL_SOUNDS.map((b) => {
              const active = bellSound === b.id;
              return (
                <button
                  key={b.id}
                  onClick={() => { onBellSoundChange(b.id); playBell(b.id); }}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    gap: 8, padding: "6px 9px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                    background: active ? "var(--accent2)" : "rgba(255,255,255,0.04)",
                    color: active ? "white" : "var(--text)",
                    border: "1px solid var(--card-border)", textAlign: "left",
                  }}
                >
                  <span>{b.name}</span>
                  <span style={{ fontSize: 11, opacity: 0.8 }}>{active ? "✓ ▶" : "▶"}</span>
                </button>
              );
            })}
          </div>
          <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 8, opacity: 0.7 }}>
            Click to preview &amp; select — saved automatically.
          </div>

          <div style={{
            fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
            textTransform: "uppercase", letterSpacing: "0.05em", margin: "14px 0 8px",
          }}>
            Office
          </div>
          <button
            onClick={() => onShowManagerChange(!showManager)}
            style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              gap: 8, padding: "6px 9px", borderRadius: 6, fontSize: 12, cursor: "pointer",
              background: showManager ? "var(--accent2)" : "rgba(255,255,255,0.04)",
              color: showManager ? "white" : "var(--text)",
              border: "1px solid var(--card-border)", width: "100%", textAlign: "left",
            }}
          >
            <span>👩‍💼 Team manager</span>
            <span style={{ fontSize: 11, opacity: 0.85 }}>{showManager ? "on" : "off"}</span>
          </button>

          {showManager && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.3 }}>
                  Patrol interval (sec)
                </span>
                <SecInput
                  value={managerPatrolIntervalSec}
                  onChange={onManagerPatrolIntervalChange}
                />
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.3 }}>
                  Idle grace (sec)
                </span>
                <SecInput
                  value={managerIdleGraceSec}
                  onChange={onManagerIdleGraceChange}
                />
              </div>
              <div style={{ fontSize: 9, color: "var(--text-dim)", opacity: 0.75, lineHeight: 1.35 }}>
                Patrol: how often she walks the floor when a desk looks unfinished.
                Grace: skip audit if activity was this recent.
              </div>
            </div>
          )}

          <div style={{
            fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
            textTransform: "uppercase", letterSpacing: "0.05em", margin: "14px 0 8px",
          }}>
            Activity feed
          </div>
          <button
            onClick={() => onVerboseChange(!verbose)}
            style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              gap: 8, padding: "6px 9px", borderRadius: 6, fontSize: 12, cursor: "pointer",
              background: verbose ? "var(--accent2)" : "rgba(255,255,255,0.04)",
              color: verbose ? "white" : "var(--text)",
              border: "1px solid var(--card-border)", width: "100%", textAlign: "left",
            }}
          >
            <span>🔬 Verbose streaming</span>
            <span style={{ fontSize: 11, opacity: 0.85 }}>{verbose ? "high" : "low"}</span>
          </button>
          <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 6, opacity: 0.7 }}>
            {verbose
              ? "Live tokens, reasoning and tool calls stream into the desk feed; Debug console tab shown."
              : "Desks show a compact status line while the agent works; Debug console tab hidden."}
          </div>

          <div style={{
            fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
            textTransform: "uppercase", letterSpacing: "0.05em", margin: "14px 0 8px",
          }}>
            Docker
          </div>
          <button
            onClick={() => onDockerPersistChange(!dockerPersist)}
            style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              gap: 8, padding: "6px 9px", borderRadius: 6, fontSize: 12, cursor: "pointer",
              background: dockerPersist ? "var(--accent2)" : "rgba(255,255,255,0.04)",
              color: dockerPersist ? "white" : "var(--text)",
              border: "1px solid var(--card-border)", width: "100%", textAlign: "left",
            }}
          >
            <span>🐳 Keep sandbox containers</span>
            <span style={{ fontSize: 11, opacity: 0.85 }}>{dockerPersist ? "on" : "off"}</span>
          </button>
          <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 6, opacity: 0.7 }}>
            {dockerPersist
              ? "Containers stay warm across desk deletion & restart — remove them with Reset."
              : "Containers are removed when a desk is deleted and when the server stops."}
          </div>

          <div style={{
            fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
            textTransform: "uppercase", letterSpacing: "0.05em", margin: "14px 0 8px",
          }}>
            Scene
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {SCENES.map((s) => {
              const active = scene === s.id;
              return (
                <button
                  key={s.id}
                  onClick={() => onSceneChange(s.id)}
                  style={{
                    flex: "1 1 45%", padding: "6px 9px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                    background: active ? "var(--accent2)" : "rgba(255,255,255,0.04)",
                    color: active ? "white" : "var(--text)",
                    border: "1px solid var(--card-border)", textAlign: "left",
                  }}
                >
                  {active ? "✓ " : ""}{s.name}
                </button>
              );
            })}
          </div>

          <div style={{
            fontSize: 10, fontWeight: 600, color: "var(--text-dim)",
            textTransform: "uppercase", letterSpacing: "0.05em", margin: "14px 0 8px",
          }}>
            Code theme
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {CODE_THEMES.map((t) => {
              const active = codeTheme === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => onCodeThemeChange(t.id as CodeThemeId)}
                  style={{
                    flex: "1 1 45%", padding: "6px 9px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                    background: active ? "var(--accent2)" : "rgba(255,255,255,0.04)",
                    color: active ? "white" : "var(--text)",
                    border: "1px solid var(--card-border)", textAlign: "left",
                  }}
                >
                  {active ? "✓ " : ""}{t.name}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
