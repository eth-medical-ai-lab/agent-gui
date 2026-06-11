import { useCallback, useEffect, useState } from "react";
import type { AgentArchetype } from "./components/AgentFigure";

/**
 * Per-profile avatar look + color, chosen by the user and persisted to
 * localStorage (client-side, like the roster section layout). When a profile has
 * no preference, the figure falls back to its lineage-derived archetype + the
 * backend-assigned color.
 */
export interface AvatarPref {
  archetype?: AgentArchetype;
  color?: string;
}

/** The avatar "repository" — the built-in pixel-art looks to choose from. */
export const AVATAR_OPTIONS: { id: AgentArchetype; label: string }[] = [
  { id: "default", label: "Office" },
  { id: "coder", label: "Coder" },
  { id: "researcher", label: "Researcher" },
  { id: "cloud", label: "Sage" },
  { id: "local", label: "Friendly" },
];

/** Color swatches to choose from. */
export const AVATAR_COLORS: string[] = [
  "#4a8eff", "#58a6ff", "#a78bfa", "#e67e22",
  "#2ecc71", "#1abc9c", "#ff6b9d", "#f1c40f",
  "#e74c3c", "#9aa0b0",
];

const AVATARS_KEY = "hermes-roster-avatars";
const AVATAR_EVENT = "avatar:change";

function load(): Record<string, AvatarPref> {
  try {
    const raw = localStorage.getItem(AVATARS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") return parsed as Record<string, AvatarPref>;
    }
  } catch {
    /* ignore */
  }
  return {};
}

export interface AvatarPrefsApi {
  prefs: Record<string, AvatarPref>;
  get: (agentId: string) => AvatarPref | undefined;
  set: (agentId: string, pref: AvatarPref) => void;
}

/** Accent color for a profile — user override wins over the backend default. */
export function effectiveAgentColor(
  agent: { color: string } | undefined,
  pref?: AvatarPref,
  fallback = "#6a7a9a",
): string {
  return pref?.color || agent?.color || fallback;
}

export function useAvatarPrefs(): AvatarPrefsApi {
  const [prefs, setPrefs] = useState<Record<string, AvatarPref>>(load);

  useEffect(() => {
    try { localStorage.setItem(AVATARS_KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
  }, [prefs]);

  // Keep every hook instance (roster + modal) in sync.
  useEffect(() => {
    function onChange() { setPrefs(load()); }
    window.addEventListener(AVATAR_EVENT, onChange);
    return () => window.removeEventListener(AVATAR_EVENT, onChange);
  }, []);

  const set = useCallback((agentId: string, pref: AvatarPref) => {
    if (!agentId) return;
    setPrefs((prev) => {
      const merged = { ...prev[agentId], ...pref };
      // Drop empty keys so a cleared choice reverts to the lineage default.
      if (!merged.archetype) delete merged.archetype;
      if (!merged.color) delete merged.color;
      const next = { ...prev };
      if (Object.keys(merged).length === 0) delete next[agentId];
      else next[agentId] = merged;
      return next;
    });
    // Notify other hook instances after this tick.
    setTimeout(() => window.dispatchEvent(new CustomEvent(AVATAR_EVENT)), 0);
  }, []);

  const get = useCallback((agentId: string) => prefs[agentId], [prefs]);

  return { prefs, get, set };
}
