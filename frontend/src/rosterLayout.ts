import { useCallback, useEffect, useState } from "react";
import type { AgentProfile } from "./types";
import { ROSTER_CATEGORIES, rosterCategoryForAgent } from "./agentRosterMeta";

/**
 * Configurable Agent Profiles layout: user-defined sections (editable name +
 * blurb) and an agent→section placement map, both persisted to localStorage.
 *
 * A profile that has no explicit placement falls back to its default category
 * (see agentRosterMeta) when that section still exists, otherwise it lands in
 * the always-present "Unsorted" bucket.
 */
export interface RosterSection {
  id: string;
  name: string;
  blurb: string;
  color: string;
}

export const UNSORTED_SECTION_ID = "__unsorted__";

/** Header text for the always-present default-agent column. */
export interface GlobalSectionMeta {
  name: string;
  blurb: string;
}
const DEFAULT_GLOBAL: GlobalSectionMeta = {
  name: "Global",
  blurb: "Default agent from ~/.hermes/config.yaml",
};

const SECTIONS_KEY = "hermes-roster-sections";
const PLACEMENT_KEY = "hermes-roster-placement";
const GLOBAL_KEY = "hermes-roster-global";

const SECTION_PALETTE = [
  "#4a8eff", "#e67e22", "#2ecc71", "#a78bfa",
  "#ff6b9d", "#f1c40f", "#1abc9c", "#e74c3c",
];

function defaultSections(): RosterSection[] {
  return ROSTER_CATEGORIES.map((c) => ({
    id: c.id,
    name: c.name,
    blurb: c.blurb,
    color: c.color,
  }));
}

function loadSections(): RosterSection[] {
  try {
    const raw = localStorage.getItem(SECTIONS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((s) => s && typeof s.id === "string")) {
        return parsed as RosterSection[];
      }
    }
  } catch {
    /* ignore */
  }
  return defaultSections();
}

function loadGlobalMeta(): GlobalSectionMeta {
  try {
    const raw = localStorage.getItem(GLOBAL_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed.name === "string") {
        return { name: parsed.name, blurb: typeof parsed.blurb === "string" ? parsed.blurb : "" };
      }
    }
  } catch {
    /* ignore */
  }
  return { ...DEFAULT_GLOBAL };
}

function loadPlacements(): Record<string, string> {
  try {
    const raw = localStorage.getItem(PLACEMENT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") return parsed as Record<string, string>;
    }
  } catch {
    /* ignore */
  }
  return {};
}

/** Window event used to move an agent into a section from the drag layer. */
export interface RosterPlaceDetail {
  agentId: string;
  sectionId: string;
}
export const ROSTER_PLACE_EVENT = "roster:place";

export function dispatchRosterPlace(agentId: string, sectionId: string) {
  window.dispatchEvent(
    new CustomEvent<RosterPlaceDetail>(ROSTER_PLACE_EVENT, { detail: { agentId, sectionId } }),
  );
}

export interface RosterLayout {
  sections: RosterSection[];
  /** Editable header for the default-agent column. */
  globalSection: GlobalSectionMeta;
  updateGlobalSection: (patch: Partial<GlobalSectionMeta>) => void;
  /** Resolve which section an agent belongs to (null → Unsorted). */
  resolveSectionId: (agent: AgentProfile) => string | null;
  placeAgent: (agentId: string, sectionId: string) => void;
  addSection: (name: string, blurb: string) => void;
  updateSection: (id: string, patch: Partial<Pick<RosterSection, "name" | "blurb">>) => void;
  removeSection: (id: string) => void;
}

export function useRosterLayout(): RosterLayout {
  const [sections, setSections] = useState<RosterSection[]>(loadSections);
  const [placements, setPlacements] = useState<Record<string, string>>(loadPlacements);
  const [globalSection, setGlobalSection] = useState<GlobalSectionMeta>(loadGlobalMeta);

  useEffect(() => {
    try { localStorage.setItem(SECTIONS_KEY, JSON.stringify(sections)); } catch { /* ignore */ }
  }, [sections]);
  useEffect(() => {
    try { localStorage.setItem(PLACEMENT_KEY, JSON.stringify(placements)); } catch { /* ignore */ }
  }, [placements]);
  useEffect(() => {
    try { localStorage.setItem(GLOBAL_KEY, JSON.stringify(globalSection)); } catch { /* ignore */ }
  }, [globalSection]);

  const updateGlobalSection = useCallback((patch: Partial<GlobalSectionMeta>) => {
    setGlobalSection((prev) => ({ ...prev, ...patch }));
  }, []);

  const placeAgent = useCallback((agentId: string, sectionId: string) => {
    setPlacements((prev) => {
      if (sectionId === UNSORTED_SECTION_ID) {
        if (!(agentId in prev)) return prev;
        const next = { ...prev };
        delete next[agentId];
        return next;
      }
      if (prev[agentId] === sectionId) return prev;
      return { ...prev, [agentId]: sectionId };
    });
  }, []);

  // Bridge drops from the global drag layer (useAgentDrag) into state.
  useEffect(() => {
    function onPlace(e: Event) {
      const detail = (e as CustomEvent<RosterPlaceDetail>).detail;
      if (detail?.agentId != null && detail?.sectionId) placeAgent(detail.agentId, detail.sectionId);
    }
    window.addEventListener(ROSTER_PLACE_EVENT, onPlace);
    return () => window.removeEventListener(ROSTER_PLACE_EVENT, onPlace);
  }, [placeAgent]);

  const resolveSectionId = useCallback(
    (agent: AgentProfile): string | null => {
      const explicit = placements[agent.id];
      if (explicit && sections.some((s) => s.id === explicit)) return explicit;
      const cat = rosterCategoryForAgent(agent);
      if (cat && sections.some((s) => s.id === cat)) return cat;
      return null;
    },
    [placements, sections],
  );

  const addSection = useCallback((name: string, blurb: string) => {
    const id = `sec-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    setSections((prev) => [
      ...prev,
      {
        id,
        name: name.trim() || "New section",
        blurb: blurb.trim(),
        color: SECTION_PALETTE[prev.length % SECTION_PALETTE.length],
      },
    ]);
  }, []);

  const updateSection = useCallback(
    (id: string, patch: Partial<Pick<RosterSection, "name" | "blurb">>) => {
      setSections((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
    },
    [],
  );

  const removeSection = useCallback((id: string) => {
    setSections((prev) => prev.filter((s) => s.id !== id));
    // Drop placements that pointed at the removed section (they fall back to
    // their default category / Unsorted).
    setPlacements((prev) => {
      let changed = false;
      const next: Record<string, string> = {};
      for (const [agentId, sectionId] of Object.entries(prev)) {
        if (sectionId === id) { changed = true; continue; }
        next[agentId] = sectionId;
      }
      return changed ? next : prev;
    });
  }, []);

  return {
    sections, globalSection, updateGlobalSection,
    resolveSectionId, placeAgent, addSection, updateSection, removeSection,
  };
}
