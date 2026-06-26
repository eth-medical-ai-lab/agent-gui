import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api/client";
import { Header } from "./components/Header";
import type { ApiMode, ReasoningEffort } from "./types";
import { Office } from "./components/Office";
import { AgentProfileModal } from "./components/AgentProfileModal";
import { AgentAssignModal } from "./components/AgentAssignModal";
import { DeskAgentPicker } from "./components/DeskAgentPicker";
import { GlobalDefaultPersonaEditor } from "./components/GlobalDefaultPersonaEditor";
import { FilePreview, DEFAULT_CODE_THEME } from "./components/FilePreview";
import type { CodeThemeId } from "./components/FilePreview";
import { DEFAULT_BELL } from "./sounds";
import { DEFAULT_SCENE } from "./components/SceneBackground";
import { buildDeskConfigView, defaultDeskBarConfig, deskIsRunning, findDeskItem, pendingStartParams, resolveDeskBarConfig, type DeskBarConfig, type GlobalHermesConfig } from "./deskConfig";
import { DESK_PANEL_Z_BASE, nextPanelZ } from "./floatingPanelStack";
import type { DeskItem, FilePreviewData, Session, Team, TeamColor, ToolsetMeta, AgentProfile, AgentPrototype, PendingAssignment, ToolPresetId, AgentCapabilities } from "./types";
import { TEAM_COLORS as TEAM_COLORS_ARRAY } from "./types";
import { useAgentDrag } from "./useAgentDrag";
import { useRosterLayout } from "./rosterLayout";
import { AgentFigure } from "./components/AgentFigure";
import { effectiveAgentColor, useAvatarPrefs } from "./avatarPrefs";
import "./styles/globals.css";

const POLL_INTERVAL = 5000;
// Shown until the backend reports the selected model's real capability.
const EMPTY_REASONING_OPTIONS: { value: ReasoningEffort; label: string }[] = [];

/** Profile-default model + tools for a desk (avatar pick — not Advanced overrides). */
async function fetchProfileDefaults(
  agentId: string,
  agents: AgentProfile[],
  globalConfig: GlobalHermesConfig,
  toolPresets: { chat: string[]; lean: string[]; full: string[] },
  toolDefault: string,
): Promise<{ toolPreset: ToolPresetId; toolsEnabled: string[]; model: string }> {
  if (!agentId) {
    const preset: ToolPresetId =
      toolDefault === "chat" || toolDefault === "full" ? toolDefault : "lean";
    return {
      toolPreset: preset,
      toolsEnabled: toolPresets[preset] ?? toolPresets.lean,
      model: globalConfig.model,
    };
  }
  const agent = agents.find((a) => a.id === agentId);
  let caps: AgentCapabilities;
  try {
    caps = await api.agents.capabilities(agentId);
  } catch {
    caps = {
      id: agentId,
      presets: toolPresets,
      source: "global",
      default_preset: "lean",
      profile_disabled_toolsets: [],
      skill_bundles: [],
      skill_count: 0,
    };
  }
  const def = caps.default_preset;
  const preset: ToolPresetId = def === "chat" || def === "lean" || def === "full" ? def : "lean";
  return {
    toolPreset: preset,
    toolsEnabled: caps.presets[preset] ?? toolPresets.lean,
    model: agent?.model ?? globalConfig.model,
  };
}

const WORKBENCH_KEY_V2 = "hermes-workbench-v2";
const WORKBENCH_KEY_V1 = "hermes-workbench-v1"; // read-only, backward compat

interface DeskSetupDraft {
  agentId: string;
  model: string;
  toolPreset: ToolPresetId;
  toolsEnabled: string[];
}

type WorkbenchEntry =
  | { type: "session"; id: string; taskContent?: string; taskImages?: { name: string; url: string }[] }
  | { type: "pending"; id: string; text: string };

interface WorkbenchV2 {
  version: 2;
  teams: Array<{ id: string; color: string; name?: string; scene?: string; items: WorkbenchEntry[] }>;
}

function readWorkbenchV2(): WorkbenchV2 | null {
  try {
    const raw = localStorage.getItem(WORKBENCH_KEY_V2);
    if (raw) return JSON.parse(raw) as WorkbenchV2;
    // Backward compat: V1 was a flat array → wrap as single blue team
    const v1raw = localStorage.getItem(WORKBENCH_KEY_V1);
    if (v1raw) {
      const items = JSON.parse(v1raw) as WorkbenchEntry[];
      if (items.length > 0) {
        return { version: 2, teams: [{ id: "team-default", color: "blue", items }] };
      }
    }
    return null;
  } catch { return null; }
}

function saveWorkbenchV2(
  teams: Team[],
  pendingTexts: Record<string, string>,
  taskContents: Record<string, string>,
  taskImages: Record<string, { name: string; url: string }[]>,
) {
  try {
    const v2: WorkbenchV2 = {
      version: 2,
      teams: teams.map((t) => ({
        id: t.id,
        color: t.color,
        name: t.name?.trim() || undefined,
        scene: t.scene,
        items: t.desks.map((d) => {
          if ("isPending" in d) return { type: "pending" as const, id: d.id, text: pendingTexts[d.id] ?? "" };
          const entry: WorkbenchEntry = { type: "session" as const, id: d.id };
          const tc = taskContents[d.id];
          if (tc) (entry as { type: "session"; id: string; taskContent?: string }).taskContent = tc;
          const ti = taskImages[d.id];
          if (ti && ti.length > 0) (entry as { type: "session"; id: string; taskImages?: { name: string; url: string }[] }).taskImages = ti;
          return entry;
        }),
      })),
    };
    localStorage.setItem(WORKBENCH_KEY_V2, JSON.stringify(v2));
  } catch {}
}

function makePending(): DeskItem {
  return { id: `pending-${Date.now()}-${Math.random().toString(36).slice(2)}`, isPending: true as const };
}

function makeTeam(color: TeamColor): Team {
  return { id: `team-${Date.now()}-${Math.random().toString(36).slice(2)}`, color, scene: DEFAULT_SCENE, desks: [makePending()] };
}

// Surface teams that exist server-side (e.g. desks created via the API or a script)
// but aren't in the browser workbench yet: for any session whose team_id is UNKNOWN
// to the frontend, reconstruct that team so the desk shows in the office. Only teams
// the frontend has never seen are created — known teams stay under the user's manual
// management, so removing a desk from one doesn't make it reappear on the next poll.
function mergeServerTeams(current: Team[], sessions: Session[]): Team[] {
  const knownTeamIds = new Set(current.map((t) => t.id));
  const placed = new Set(
    current.flatMap((t) => t.desks.filter((d) => !("isPending" in d)).map((d) => d.id)),
  );
  const byTeam = new Map<string, Session[]>();
  for (const s of sessions) {
    const tid = s.team_id;
    if (!tid || knownTeamIds.has(tid) || placed.has(s.id)) continue;
    let arr = byTeam.get(tid);
    if (!arr) { arr = []; byTeam.set(tid, arr); }
    arr.push(s);
  }
  if (byTeam.size === 0) return current;
  const colors: TeamColor[] = ["blue", "red", "green", "purple", "orange"];
  let i = current.length;
  const added: Team[] = [];
  for (const [tid, sess] of byTeam) {
    added.push({ id: tid, color: colors[i % colors.length], scene: DEFAULT_SCENE, desks: sess });
    i += 1;
  }
  return [...current, ...added];
}

export default function App() {
  const [teams, setTeams] = useState<Team[]>([makeTeam("blue")]);
  const [pendingTexts, setPendingTexts] = useState<Record<string, string>>({});
  const [justStartedId, setJustStartedId] = useState<string | null>(null);
  const [justStartedAnchor, setJustStartedAnchor] = useState<{ top: number; left: number } | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [preview, setPreview] = useState<FilePreviewData | null>(null);
  const panelZCounter = useRef(DESK_PANEL_Z_BASE);
  const [deskPanelZ, setDeskPanelZ] = useState<Record<string, number>>({});
  const [previewZ, setPreviewZ] = useState(DESK_PANEL_Z_BASE);

  const activateDeskPanel = useCallback((deskId: string) => {
    const z = nextPanelZ(panelZCounter);
    setDeskPanelZ((prev) => ({ ...prev, [deskId]: z }));
  }, []);

  const activateFilePreview = useCallback(() => {
    setPreviewZ(nextPanelZ(panelZCounter));
  }, []);

  function handleFilePreview(data: FilePreviewData) {
    setPreview((cur) => {
      const closing = cur?.path === data.path;
      if (!closing) setPreviewZ(nextPanelZ(panelZCounter));
      return closing ? null : data;
    });
  }
  const [codeTheme, setCodeTheme] = useState<CodeThemeId>(() => {
    try { return (localStorage.getItem("hermes-code-theme") as CodeThemeId) || DEFAULT_CODE_THEME; }
    catch { return DEFAULT_CODE_THEME; }
  });
  // Server-side Docker cleanup policy (⚙ → Docker). Loaded from the backend on
  // mount; toggling POSTs back. Default off = reap containers on delete/shutdown.
  const [dockerPersist, setDockerPersist] = useState(false);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [workspacePaths, setWorkspacePaths] = useState<Record<string, string>>({});
  const [taskContents, setTaskContents] = useState<Record<string, string>>({});
  const [taskImages, setTaskImages] = useState<Record<string, { name: string; url: string }[]>>({});
  // Pending desk → agent + tool preset chosen from the bench before Start.
  const [pendingAssignments, setPendingAssignments] = useState<Record<string, PendingAssignment>>({});
  const [activePendingDeskId, setActivePendingDeskId] = useState<string | null>(null);
  const [focusedDeskId, setFocusedDeskId] = useState<string | null>(null);
  const [assignModal, setAssignModal] = useState<{ deskId: string; agent: AgentProfile } | null>(null);
  const [verbose, setVerbose] = useState(() => {
    try {
      const stored = localStorage.getItem("hermes-verbose");
      return stored === null ? true : stored === "true";
    } catch { return true; }
  });
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(() => {
    try { return (localStorage.getItem("hermes-reasoning-effort") as ReasoningEffort) || "medium"; }
    catch { return "medium"; }
  });
  // Reasoning-effort options for the selected model (capability-driven, fetched
  // from the backend). qwen → Off/On; empty → gray out.
  const [reasoningOptions, setReasoningOptions] =
    useState<{ value: ReasoningEffort; label: string }[]>(EMPTY_REASONING_OPTIONS);
  const [apiMode, setApiMode] = useState<ApiMode>(() => {
    try { return (localStorage.getItem("hermes-api-mode") as ApiMode) || "openai"; }
    catch { return "openai"; }
  });
  const avatars = useAvatarPrefs();
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [prototypes, setPrototypes] = useState<AgentPrototype[]>([]);
  const [agentModal, setAgentModal] = useState<
    { mode: "create" | "edit"; agent?: AgentProfile | null } | null
  >(null);
  const [deskAgentPickerId, setDeskAgentPickerId] = useState<string | null>(null);
  const [defaultAgentEditorOpen, setDefaultAgentEditorOpen] = useState(false);
  const [rosterOpen, setRosterOpen] = useState(false);
  const rosterRef = useRef<HTMLDivElement>(null);
  const [deskDefaultModel, setDeskDefaultModel] = useState<string>("");
  const [globalConfig, setGlobalConfig] = useState<GlobalHermesConfig>({ base_url: "", model: "" });
  const [deskBarConfigs, setDeskBarConfigs] = useState<Record<string, DeskBarConfig>>({});
  const [toolsets, setToolsets] = useState<ToolsetMeta[]>([]);
  const [toolPresets, setToolPresets] = useState<{ chat: string[]; lean: string[]; full: string[] }>(
    { chat: [], lean: [], full: [] });
  const [toolDefault, setToolDefault] = useState<string>("lean");
  const [bellSound, setBellSound] = useState<string>(() => {
    try { return localStorage.getItem("hermes-bell-sound") || DEFAULT_BELL; }
    catch { return DEFAULT_BELL; }
  });
  const [scene, setScene] = useState<string>(() => {
    try { return localStorage.getItem("hermes-scene") || DEFAULT_SCENE; }
    catch { return DEFAULT_SCENE; }
  });
  const [showManager, setShowManager] = useState<boolean>(() => {
    try { return localStorage.getItem("hermes-show-manager") !== "false"; }
    catch { return true; }
  });
  const [managerPatrolIntervalSec, setManagerPatrolIntervalSec] = useState<number>(() => {
    try {
      const v = localStorage.getItem("hermes-manager-patrol-interval");
      if (v) return parseInt(v, 10) || 60;
      const legacy = localStorage.getItem("hermes-manager-idle-threshold");
      return legacy ? parseInt(legacy, 10) || 60 : 60;
    } catch { return 60; }
  });
  const [managerIdleGraceSec, setManagerIdleGraceSec] = useState<number>(() => {
    try {
      const v = localStorage.getItem("hermes-manager-idle-grace");
      if (v) return parseInt(v, 10) || 60;
      const legacy = localStorage.getItem("hermes-manager-idle-threshold");
      return legacy ? parseInt(legacy, 10) || 60 : 60;
    } catch { return 60; }
  });
  // Per-team ask-manager: maps team.id → session id to prioritise (null = full patrol)
  const [askManagerByTeamId, setAskManagerByTeamId] = useState<Record<string, string | null>>({});
  const [searchMatchIds, setSearchMatchIds] = useState<Set<string>>(new Set());
  const [searchStats, setSearchStats] = useState<{ onFloor: number; total: number } | null>(null);

  const sessionsRef = useRef<Session[]>([]);
  const workbenchRestoredRef = useRef(false);

  const loadSessions = useCallback(async () => {
    try {
      const data = await api.sessions.list(50);
      setSessions(data);
      sessionsRef.current = data;

      if (!workbenchRestoredRef.current) {
        workbenchRestoredRef.current = true;
        const saved = readWorkbenchV2();
        if (saved && saved.teams.length > 0) {
          const pendingTextsInit: Record<string, string> = {};
          const taskContentsInit: Record<string, string> = {};
          const taskImagesInit: Record<string, { name: string; url: string }[]> = {};
          const restoredTeams: Team[] = [];

          // The session list is capped (most-recent 50), but a snapshot/workbench
          // can reference older sessions. Look up referenced sessions in a map and
          // fetch any that the capped list missed — otherwise restoring a snapshot
          // silently drops every desk whose session isn't in the latest 50, which
          // looks like "the snapshot won't load". Truly-deleted sessions 404 and
          // are dropped (they really are gone).
          const known = new Map(data.map((s) => [s.id, s]));
          const missingIds = Array.from(new Set(
            saved.teams
              .flatMap((t) => t.items)
              .filter((it) => it.type === "session" && !known.has(it.id))
              .map((it) => it.id),
          ));
          if (missingIds.length > 0) {
            const fetched = await Promise.all(
              missingIds.map((id) => api.sessions.get(id).catch(() => null)),
            );
            for (const s of fetched) if (s) known.set(s.id, s);
          }

          for (const teamData of saved.teams) {
            const restoredDesks: DeskItem[] = [];
            for (const item of teamData.items) {
              if (item.type === "session") {
                const session = known.get(item.id);
                if (session) {
                  restoredDesks.push(session);
                  if (item.taskContent) taskContentsInit[item.id] = item.taskContent;
                  if (item.taskImages && item.taskImages.length > 0) taskImagesInit[item.id] = item.taskImages;
                }
              } else {
                const id = `pending-${Date.now()}-${Math.random().toString(36).slice(2)}`;
                restoredDesks.push({ id, isPending: true as const });
                if (item.text) pendingTextsInit[id] = item.text;
              }
            }
            if (restoredDesks.length === 0) restoredDesks.push(makePending());
            restoredTeams.push({
              id: teamData.id,
              color: (teamData.color as TeamColor) ?? "blue",
              name: teamData.name,
              scene: teamData.scene,
              desks: restoredDesks,
            });
          }

          if (restoredTeams.length > 0) {
            setTeams(mergeServerTeams(restoredTeams, data));
            if (Object.keys(pendingTextsInit).length > 0) setPendingTexts(pendingTextsInit);
            if (Object.keys(taskContentsInit).length > 0) setTaskContents(taskContentsInit);
            if (Object.keys(taskImagesInit).length > 0) setTaskImages(taskImagesInit);
            setBackendError(null);
            return;
          }
        }
        // No saved workbench (or nothing restored): still surface server-side teams
        // (e.g. script-created desks) so they appear in the office on a fresh load.
        setTeams((prev) => mergeServerTeams(prev, data));
      } else {
        // Normal poll: refresh session data across all teams + merge in any
        // newly-appeared server-side teams (script-created desks).
        setTeams((prev) =>
          mergeServerTeams(
            prev.map((t) => ({
              ...t,
              desks: t.desks.map((d) => {
                if ("isPending" in d) return d;
                const fresh = data.find((s) => s.id === d.id);
                return fresh ?? d;
              }),
            })),
            data,
          )
        );
      }
      setBackendError(null);
    } catch {
      setBackendError("Could not reach Hermes GUI backend. Is it running?");
    }
  }, []);

  const refreshAgents = useCallback(async () => {
    try {
      const r = await api.guiConfig();
      setAgents(r.agents ?? []);
      setPrototypes(r.prototypes ?? []);
      setDeskDefaultModel(r.desk_default_model ?? "");
      setGlobalConfig({
        base_url: r.global?.base_url ?? r.manager?.base_url ?? "",
        model: r.global?.model ?? r.desk_default_model ?? "",
      });
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadSessions();
    api.hermes.warmup().catch(() => {});
    api.guiConfig().then((r) => {
      setAgents(r.agents ?? []);
      setPrototypes(r.prototypes ?? []);
      setDeskDefaultModel(r.desk_default_model ?? "");
      setGlobalConfig({
        base_url: r.global?.base_url ?? r.manager?.base_url ?? "",
        model: r.global?.model ?? r.desk_default_model ?? "",
      });
    }).catch(() => {});
    api.toolsets().then((r) => { setToolsets(r.toolsets); setToolPresets(r.presets); setToolDefault(r.default); }).catch(() => {});
    // Also re-pull the roster: profiles installed/changed on disk while the GUI
    // is open (e.g. install_profiles.sh) otherwise never appear until a reload.
    const poll = setInterval(() => { loadSessions(); refreshAgents(); }, POLL_INTERVAL);
    return () => clearInterval(poll);
  }, [loadSessions, refreshAgents]);

  // Persist workbench to localStorage on every change
  useEffect(() => {
    if (workbenchRestoredRef.current) saveWorkbenchV2(teams, pendingTexts, taskContents, taskImages);
  }, [teams, pendingTexts, taskContents, taskImages]);

  // Load the server's Docker cleanup policy once so the ⚙ toggle reflects it.
  useEffect(() => {
    api.docker.getConfig().then((r) => setDockerPersist(r.persist)).catch(() => {});
  }, []);

  // Tab adds a desk to the first team. Shift+Tab is the global shortcut and works
  // even while a field is focused — the pending desk's task box auto-focuses on
  // load, so a guard that bailed on any focused input made Shift+Tab a no-op in
  // the app's default state. Plain Tab still only fires outside fields so normal
  // tabbing inside inputs/textareas is preserved.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const tag = (document.activeElement as Element | null)?.tagName ?? "";
      const inField = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if (inField && !e.shiftKey) return;
      e.preventDefault();
      const newDesk = makePending();
      setFocusedDeskId(newDesk.id);
      setTeams((prev) => {
        if (prev.length === 0) return prev;
        return prev.map((t, i) =>
          i === 0 ? { ...t, desks: [...t.desks, newDesk] } : t
        );
      });
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // ── Tool presets (for desk config defaults) ────────────────────────────────

  const selectedDeskId = focusedDeskId ?? activePendingDeskId;

  const deskConfigsById = useMemo(() => {
    const map: Record<string, NonNullable<ReturnType<typeof buildDeskConfigView>>> = {};
    for (const team of teams) {
      for (const desk of team.desks) {
        const view = buildDeskConfigView(
          desk.id, teams, agents, pendingAssignments, deskBarConfigs,
          globalConfig, toolPresets, toolDefault,
        );
        if (view) map[desk.id] = view;
      }
    }
    return map;
  }, [teams, agents, pendingAssignments, deskBarConfigs, globalConfig, toolPresets, toolDefault]);

  const deskConfig = selectedDeskId ? (deskConfigsById[selectedDeskId] ?? null) : null;
  const deskConfigLocked = !selectedDeskId || deskIsRunning(findDeskItem(teams, selectedDeskId));

  // Model + backend used to query /api/models/reasoning for the focused desk.
  const reasoningContext = useMemo(() => {
    if (selectedDeskId) {
      const cfg = deskConfigsById[selectedDeskId];
      if (cfg) {
        return {
          model: cfg.model || cfg.profileModel || globalConfig.model || deskDefaultModel || "",
          baseUrl: cfg.baseUrl || globalConfig.base_url,
          agentId: cfg.agentId || undefined,
        };
      }
    }
    return {
      model: globalConfig.model || deskDefaultModel || "",
      baseUrl: globalConfig.base_url,
      agentId: undefined as string | undefined,
    };
  }, [selectedDeskId, deskConfigsById, globalConfig.model, globalConfig.base_url, deskDefaultModel]);

  // Refresh the reasoning-effort menu when the focused desk's model/backend changes.
  useEffect(() => {
    let cancelled = false;
    const { model, baseUrl, agentId } = reasoningContext;
    api.models.reasoning(model || undefined, { baseUrl, agentId }).then((r) => {
      if (cancelled) return;
      const opts = r.options as { value: ReasoningEffort; label: string }[];
      setReasoningOptions(opts);
      if (opts.length && !opts.some((o) => o.value === reasoningEffort)) {
        const fallback = opts[opts.length - 1].value;
        setReasoningEffort(fallback);
        try { localStorage.setItem("hermes-reasoning-effort", fallback); } catch {}
      }
    }).catch(() => { if (!cancelled) setReasoningOptions(EMPTY_REASONING_OPTIONS); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reasoningContext.model, reasoningContext.baseUrl, reasoningContext.agentId]);

  // Only send reasoning_effort to the backend when the focused desk's model supports it.
  const apiReasoningEffort = reasoningOptions.length > 0 ? reasoningEffort : undefined;

  function upsertDeskBarConfig(deskId: string, patch: Partial<DeskBarConfig>) {
    setDeskBarConfigs((prev) => {
      const cur = resolveDeskBarConfig(deskId, prev, globalConfig, toolPresets, toolDefault);
      return { ...prev, [deskId]: { ...cur, ...patch } };
    });
  }

  useEffect(() => {
    if (focusedDeskId && !findDeskItem(teams, focusedDeskId)) {
      setFocusedDeskId(null);
    }
    if (activePendingDeskId && !findDeskItem(teams, activePendingDeskId)) {
      setActivePendingDeskId(null);
    }
  }, [teams, focusedDeskId, activePendingDeskId]);

  async function handleDeskProfileChange(deskId: string, agentId: string) {
    setFocusedDeskId(deskId);
    const desk = findDeskItem(teams, deskId);
    if (!desk) return;
    if (deskIsRunning(desk)) return;

    if ("isPending" in desk) {
      if (!agentId) {
        setPendingAssignments((prev) => {
          const next = { ...prev };
          delete next[deskId];
          return next;
        });
        upsertDeskBarConfig(deskId, defaultDeskBarConfig(globalConfig, toolPresets, toolDefault));
        return;
      }
      const defaults = await fetchProfileDefaults(
        agentId, agents, globalConfig, toolPresets, toolDefault,
      );
      const agent = agents.find((a) => a.id === agentId);
      setPendingAssignments((prev) => ({
        ...prev,
        [deskId]: {
          agentId,
          agentName: agent?.name ?? agentId,
          agentColor: effectiveAgentColor(agent, avatars.get(agentId)),
          toolPreset: defaults.toolPreset,
          toolsEnabled: defaults.toolsEnabled,
          customized: false,
        },
      }));
      upsertDeskBarConfig(deskId, {
        agentId,
        model: defaults.model,
        toolPreset: defaults.toolPreset,
        toolsEnabled: defaults.toolsEnabled,
        customized: false,
      });
      return;
    }

    try {
      if (!agentId) {
        const updated = await api.sessions.patchDeskConfig(deskId, { agent: "" });
        upsertDeskBarConfig(deskId, defaultDeskBarConfig(globalConfig, toolPresets, toolDefault));
        setSessions((prev) => prev.map((s) => (s.id === deskId ? { ...s, ...updated } : s)));
        setTeams((prev) => prev.map((t) => ({
          ...t,
          desks: t.desks.map((d) => (!("isPending" in d) && d.id === deskId ? { ...d, ...updated } : d)),
        })));
        return;
      }

      const defaults = await fetchProfileDefaults(
        agentId, agents, globalConfig, toolPresets, toolDefault,
      );
      const updated = await api.sessions.patchDeskConfig(deskId, {
        agent: agentId,
        tools: defaults.toolsEnabled,
        model: defaults.model,
      });
      upsertDeskBarConfig(deskId, {
        agentId,
        model: updated.agent_model || defaults.model,
        toolPreset: defaults.toolPreset,
        toolsEnabled: defaults.toolsEnabled,
        customized: false,
      });
      setSessions((prev) => prev.map((s) => (s.id === deskId ? { ...s, ...updated } : s)));
      setTeams((prev) => prev.map((t) => ({
        ...t,
        desks: t.desks.map((d) => (!("isPending" in d) && d.id === deskId ? { ...d, ...updated } : d)),
      })));
    } catch (e) {
      console.warn("desk profile change failed:", e);
    }
  }

  async function handleDeskSetupSave(deskId: string, draft: DeskSetupDraft) {
    setFocusedDeskId(deskId);
    const desk = findDeskItem(teams, deskId);
    if (!desk) return;
    if (deskIsRunning(desk)) return;

    const profileModel = draft.agentId
      ? agents.find((a) => a.id === draft.agentId)?.model ?? globalConfig.model
      : globalConfig.model;
    const modelOverride = draft.model && draft.model !== profileModel ? draft.model : undefined;

    if ("isPending" in desk) {
      if (draft.agentId) {
        const agent = agents.find((a) => a.id === draft.agentId);
        setPendingAssignments((prev) => ({
          ...prev,
          [deskId]: {
            agentId: draft.agentId,
            agentName: agent?.name ?? draft.agentId,
            agentColor: effectiveAgentColor(agent, avatars.get(draft.agentId)),
            toolPreset: draft.toolPreset,
            toolsEnabled: draft.toolsEnabled,
            ...(modelOverride ? { modelOverride } : {}),
            customized: true,
          },
        }));
      } else {
        setPendingAssignments((prev) => {
          const next = { ...prev };
          delete next[deskId];
          return next;
        });
      }
      upsertDeskBarConfig(deskId, {
        agentId: draft.agentId,
        model: draft.model,
        toolPreset: draft.toolPreset,
        toolsEnabled: draft.toolsEnabled,
        customized: true,
      });
      return;
    }

    try {
      const body: { agent?: string; model?: string; tools?: string[] } = {
        tools: draft.toolsEnabled,
        model: draft.model,
      };
      if (draft.agentId) body.agent = draft.agentId;
      else body.agent = "";
      const updated = await api.sessions.patchDeskConfig(deskId, body);
      upsertDeskBarConfig(deskId, {
        agentId: draft.agentId,
        model: draft.model,
        toolPreset: draft.toolPreset,
        toolsEnabled: draft.toolsEnabled,
        customized: true,
      });
      setSessions((prev) => prev.map((s) => (s.id === deskId ? { ...s, ...updated } : s)));
      setTeams((prev) => prev.map((t) => ({
        ...t,
        desks: t.desks.map((d) => (!("isPending" in d) && d.id === deskId ? { ...d, ...updated } : d)),
      })));
    } catch (e) {
      console.warn("desk setup save failed:", e);
      throw e;
    }
  }

  async function applyDeskCustomization(deskId: string, patch: Partial<DeskSetupDraft>) {
    const cfg = deskConfigsById[deskId];
    if (!cfg) return;
    await handleDeskSetupSave(deskId, {
      agentId: patch.agentId ?? cfg.agentId,
      model: patch.model ?? cfg.model,
      toolPreset: patch.toolPreset ?? cfg.toolPreset,
      toolsEnabled: patch.toolsEnabled ?? cfg.toolsEnabled,
    });
  }

  function handleDeskFocus(deskId: string) {
    setFocusedDeskId(deskId);
    const desk = findDeskItem(teams, deskId);
    if (desk && "isPending" in desk) {
      setActivePendingDeskId(deskId);
    } else {
      setActivePendingDeskId(null);
    }
  }

  /** Click the desk avatar → focus + glow it. The avatar's ⚙ gear (next to it)
   *  opens the agent-settings subpage; we no longer pop the picker modal here. */
  function handleAvatarClick(deskId: string) {
    handleDeskFocus(deskId);
  }

  function handleActivePendingDeskChange(deskId: string | null) {
    setActivePendingDeskId(deskId);
    if (deskId) setFocusedDeskId(deskId);
  }

  // ── Desk / team callbacks ──────────────────────────────────────────────────

  async function handleDeskStart(
    deskId: string,
    msg: string,
    _agentId: string,
    images?: { name: string; url: string }[],
    anchor?: { top: number; left: number },
  ) {
    const attachments = images?.map((img) => ({ name: img.name, data: img.url }));
    const start = pendingStartParams(
      deskId, pendingAssignments, deskBarConfigs, globalConfig, toolPresets, toolDefault, true,
    );

    const teamId = teams.find((t) => t.desks.some((d) => d.id === deskId))?.id;
    let started;
    try {
      started = await api.sessions.new(
        msg, apiReasoningEffort, apiMode,
        start.model,
        attachments, start.tools,
        start.agent,
        teamId,
      );
    } catch (e) {
      // Surface a failed start (e.g. backend unreachable, bad profile) instead of
      // leaving the desk silently stuck on "starting".
      window.alert((e as Error).message || "Couldn't start this desk.");
      return;
    }
    const { session_id, workspace_path, session: provisional } = started;
    const sessionRaw = provisional ?? await api.sessions.get(session_id);
    const session: Session = {
      ...sessionRaw,
      title: sessionRaw.title?.trim()
        || msg.trim().slice(0, 80).replace(/\n/g, " ")
        || "Untitled task",
    };
    setJustStartedId(session_id);
    setJustStartedAnchor(anchor ?? null);
    setPendingAssignments((prev) => {
      const next = { ...prev };
      delete next[deskId];
      return next;
    });
    setDeskBarConfigs((prev) => {
      const next = { ...prev };
      if (next[deskId]) {
        next[session_id] = next[deskId];
        delete next[deskId];
      } else {
        delete next[deskId];
      }
      return next;
    });
    setFocusedDeskId(session_id);
    setActivePendingDeskId(null);
    // Replace pending desk with session in whichever team contains it
    setTeams((prev) => prev.map((t) => ({
      ...t,
      desks: t.desks.map((d) => (d.id === deskId ? session : d)),
    })));
    setSessions((prev) => [...prev, session]);
    sessionsRef.current = [...sessionsRef.current, session];
    if (workspace_path) {
      setWorkspacePaths((prev) => ({ ...prev, [session_id]: workspace_path }));
    }
    setTaskContents((prev) => ({ ...prev, [session_id]: msg }));
    if (images && images.length > 0) {
      setTaskImages((prev) => ({ ...prev, [session_id]: images }));
    }
  }

  function handleSessionInterrupt(id: string) {
    setTeams((prev) => prev.map((t) => ({
      ...t,
      desks: t.desks.map((d) => {
        if ("isPending" in d || d.id !== id) return d;
        return { ...d, is_running: false };
      }),
    })));
  }

  // Drag an agent off the bench onto a desk. On a live/idle session it resumes the
  // workflow with that (possibly different) agent; on a pending desk it just
  // preselects the agent in the picker so the user can type a task and Start.
  async function handleAssignAgentToDesk(deskId: string, agentId: string) {
    if (agentId && !agents.some((a) => a.id === agentId)) return;

    const target = findDeskItem(teams, deskId);
    if (!target) return;
    if (deskIsRunning(target)) return;

    setFocusedDeskId(deskId);

    try {
      await handleDeskProfileChange(deskId, agentId);

      if ("isPending" in target) return;

      const s = target as Session;
      if (s.is_sleeping) await api.sessions.wake(s.id);
      await api.sessions.arrive(s.id);
      await api.sessions.resume(s.id, "Continue.", undefined, agentId, apiReasoningEffort, apiMode);

      setJustStartedId(s.id);
      const optimistic: Partial<Session> = {
        agent: agentId || null,
        is_running: true,
        is_sleeping: false,
        ended_at: null,
        task_solved: false,
      };
      setSessions((prev) => prev.map((x) => (x.id === s.id ? { ...x, ...optimistic } : x)));
      sessionsRef.current = sessionsRef.current.map((x) =>
        x.id === s.id ? { ...x, ...optimistic } : x,
      );
      setTeams((prev) =>
        prev.map((t) => ({
          ...t,
          desks: t.desks.map((d) =>
            !("isPending" in d) && d.id === s.id ? { ...(d as Session), ...optimistic } : d,
          ),
        })),
      );
      void loadSessions();
    } catch (e) {
      console.warn("assign agent to desk failed:", e);
    }
  }

  function confirmAssignment(deskId: string, assignment: PendingAssignment) {
    setPendingAssignments((prev) => ({
      ...prev,
      [deskId]: { ...assignment, customized: true },
    }));
    upsertDeskBarConfig(deskId, {
      agentId: assignment.agentId,
      model: assignment.modelOverride ?? agents.find((a) => a.id === assignment.agentId)?.model ?? globalConfig.model,
      toolPreset: assignment.toolPreset,
      toolsEnabled: assignment.toolsEnabled,
      customized: true,
    });
    setActivePendingDeskId(deskId);
    setFocusedDeskId(deskId);
  }

  function patchPendingAssignment(deskId: string, patch: Partial<PendingAssignment>) {
    setPendingAssignments((prev) => {
      const cur = prev[deskId];
      if (!cur) return prev;
      return { ...prev, [deskId]: { ...cur, ...patch } };
    });
  }

  const allFloorSessions = useMemo(
    () => teams.flatMap((t) => t.desks).filter((d) => !("isPending" in d)) as Session[],
    [teams],
  );

  // One profile ↔ one desk *at the same time*: a profile is "in use" only while a
  // desk is actively RUNNING it (plus pending desks about to start). Idle / ended /
  // stale desks do NOT reserve it — otherwise old desks left on disk would pin a
  // profile forever. The roster + picker grey out in-use profiles.
  const agentsForRoster = useMemo(() => {
    const inUse = new Set<string>();
    for (const s of allFloorSessions) {
      if (s.agent && s.is_running) inUse.add(s.agent);
    }
    for (const a of Object.values(pendingAssignments)) {
      if (a.agentId) inUse.add(a.agentId);
    }
    return agents.map((a) => ({ ...a, inUse: inUse.has(a.id) }));
  }, [agents, allFloorSessions, pendingAssignments]);

  const rosterLayout = useRosterLayout();

  const {
    agentDrag, rosterHover, deskDropHoverId, sectionDropHoverId,
    handleAgentDragStart, handleRosterAgentDragStart,
  } = useAgentDrag({
    rosterRef,
    agents,
    onSessionInterrupt: handleSessionInterrupt,
    onAssignAgentToDesk: (deskId, agentId) => { void handleAssignAgentToDesk(deskId, agentId); },
    onRosterAgentClick: (agentId) => {
      const deskId = focusedDeskId ?? activePendingDeskId;
      if (!deskId) return;
      const desk = findDeskItem(teams, deskId);
      if (deskIsRunning(desk)) return;
      void handleDeskProfileChange(deskId, agentId);
    },
    onRosterOpen: () => setRosterOpen(true),
  });

  async function closeDesk(deskId: string) {
    const isPending = teams.some((t) =>
      t.desks.some((d) => d.id === deskId && "isPending" in d),
    );
    if (!isPending) {
      if (!window.confirm(
        "Delete this desk and its session data (history, workspace, sandbox)? This cannot be undone.",
      )) return;
      try {
        await api.sessions.delete(deskId);
      } catch (e) {
        console.warn("session delete failed:", e);
      }
      setSessions((prev) => prev.filter((s) => s.id !== deskId));
      sessionsRef.current = sessionsRef.current.filter((s) => s.id !== deskId);
      setWorkspacePaths((prev) => { const n = { ...prev }; delete n[deskId]; return n; });
      setTaskContents((prev) => { const n = { ...prev }; delete n[deskId]; return n; });
      setTaskImages((prev) => { const n = { ...prev }; delete n[deskId]; return n; });
    }
    setPendingTexts((prev) => { const n = { ...prev }; delete n[deskId]; return n; });
    setPendingAssignments((prev) => { const n = { ...prev }; delete n[deskId]; return n; });
    if (focusedDeskId === deskId) setFocusedDeskId(null);
    setTeams((prev) => prev.map((t) => {
      if (!t.desks.some((d) => d.id === deskId)) return t;
      const next = t.desks.filter((d) => d.id !== deskId);
      return { ...t, desks: next.length === 0 ? [makePending()] : next };
    }));
  }

  function addDeskToTeam(teamId: string) {
    setTeams((prev) => prev.map((t) =>
      t.id === teamId ? { ...t, desks: [...t.desks, makePending()] } : t
    ));
  }

  function deleteTeam(teamId: string) {
    setTeams((prev) => {
      if (prev.length <= 1) return prev;
      return prev.filter((t) => t.id !== teamId);
    });
  }

  function setTeamScene(teamId: string, sceneId: string) {
    setTeams((prev) => prev.map((t) =>
      t.id === teamId ? { ...t, scene: sceneId } : t
    ));
  }

  function renameTeam(teamId: string, name: string) {
    const trimmed = name.trim();
    setTeams((prev) => prev.map((t) =>
      t.id === teamId ? { ...t, name: trimmed || undefined } : t
    ));
  }

  function addTeam() {
    const usedColors = teams.map((t) => t.color);
    const nextColor = TEAM_COLORS_ARRAY.find((c) => !usedColors.includes(c))
      ?? TEAM_COLORS_ARRAY[teams.length % TEAM_COLORS_ARRAY.length];
    setTeams((prev) => [...prev, makeTeam(nextColor)]);
  }

  /** Place an imported desk on the workbench (right of team strip; panel stays closed). */
  async function ingestImportedDesk(res: {
    session_id: string;
    workspace_path: string | null;
    team_id: string | null;
  }) {
    let session: Session;
    try {
      session = await api.sessions.get(res.session_id);
    } catch {
      window.alert("Desk imported but its session couldn't be read.");
      return;
    }
    setJustStartedId(null);
    setJustStartedAnchor(null);

    if (teams.some((t) => t.desks.some((d) => d.id === session.id))) {
      setFocusedDeskId(session.id);
      return;
    }
    setTeams((prev) => {
      const idx = res.team_id ? prev.findIndex((t) => t.id === res.team_id) : -1;
      const target = idx >= 0 ? idx : 0;
      return prev.map((t, i) => (i === target ? { ...t, desks: [...t.desks, session] } : t));
    });
    setSessions((prev) => (prev.some((s) => s.id === session.id) ? prev : [...prev, session]));
    sessionsRef.current = sessionsRef.current.some((s) => s.id === session.id)
      ? sessionsRef.current
      : [...sessionsRef.current, session];
    if (res.workspace_path) {
      setWorkspacePaths((prev) => ({ ...prev, [session.id]: res.workspace_path! }));
    }
    setFocusedDeskId(session.id);
  }

  /** Load a desk saved via "Save desk" (a full sandbox archive) back into the
   *  workbench — restores its session history + workspace and drops it on a team. */
  async function handleLoadDesk(file: File) {
    try {
      await ingestImportedDesk(await api.sessions.importDesk(file));
    } catch (e) {
      window.alert((e as Error).message || "Couldn't load this desk archive.");
    }
  }

  async function handleLoadSavedDesk(filename: string) {
    try {
      await ingestImportedDesk(await api.sessions.importSavedDesk(filename));
    } catch (e) {
      window.alert((e as Error).message || "Couldn't load this desk archive.");
    }
  }

  function handleLoadSnapshot() {
    workbenchRestoredRef.current = false;
    loadSessions();
  }

  const handleSearch = useCallback(async (q: string) => {
    if (!q) {
      setSearchMatchIds(new Set());
      setSearchStats(null);
      return;
    }
    try {
      const results = await api.search(q);
      const ids = new Set(results.map((s) => s.id));
      setSearchMatchIds(ids);
      const onFloorIds = teams.flatMap((t) =>
        t.desks.filter((d) => !("isPending" in d)).map((d) => d.id),
      );
      const onFloor = onFloorIds.filter((id) => ids.has(id)).length;
      setSearchStats({ onFloor, total: ids.size });
      const firstOnFloor = onFloorIds.find((id) => ids.has(id));
      if (firstOnFloor) {
        requestAnimationFrame(() => {
          document.querySelector(`[data-desk-id="${firstOnFloor}"]`)
            ?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
        });
      }
    } catch {
      setSearchMatchIds(new Set());
      setSearchStats(null);
    }
  }, [teams]);

  async function handleReset() {
    const running = teams
      .flatMap((t) => t.desks)
      .filter((d) => !("isPending" in d) && (d as Session).is_running === true);
    if (running.length > 0) {
      window.alert(
        `${running.length} task(s) still running — stop them before resetting the workbench.`,
      );
      return;
    }
    if (!window.confirm("Clear all desks and remove unused agent containers?")) return;
    try { localStorage.removeItem(WORKBENCH_KEY_V2); } catch {}
    try { localStorage.removeItem(WORKBENCH_KEY_V1); } catch {}
    setPendingTexts({});
    setTeams([makeTeam("blue")]);
    try {
      const r = await api.docker.cleanup();
      if (r.skipped) {
        window.alert(`Kept ${r.kept} container(s): ${r.reason}.`);
      } else if (r.removed > 0) {
        console.info(`Removed ${r.removed} unused agent container(s).`);
      }
    } catch {}
  }

  const allDesks = teams.flatMap((t) => t.desks);
  const realDesks = allDesks.filter((d) => !("isPending" in d)) as Session[];
  const activeCount = realDesks.filter((s) => s.is_running === true).length;
  const deskCount = allDesks.length;

  if (backendError) {
    return (
      <div style={{
        height: "100vh", display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        gap: 16, color: "var(--text-dim)", fontFamily: "system-ui, sans-serif",
        background: "var(--bg)",
      }}>
        <div style={{ fontSize: 48 }}>🏛️</div>
        <div style={{ fontSize: 18, color: "var(--text)" }}>Agent GUI</div>
        <div style={{ fontSize: 13, color: "var(--red)" }}>{backendError}</div>
        <button
          onClick={() => loadSessions()}
          style={{ marginTop: 8, padding: "8px 20px", background: "var(--accent2)", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 13 }}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <Header
        teams={teams}
        sessions={sessions}
        sessionCount={deskCount}
        activeCount={activeCount}
        bellSound={bellSound}
        scene={scene}
        showManager={showManager}
        managerPatrolIntervalSec={managerPatrolIntervalSec}
        managerIdleGraceSec={managerIdleGraceSec}
        onBellSoundChange={(id) => {
          setBellSound(id);
          try { localStorage.setItem("hermes-bell-sound", id); } catch {}
        }}
        onSceneChange={(id) => {
          setScene(id);
          try { localStorage.setItem("hermes-scene", id); } catch {}
        }}
        onShowManagerChange={(v) => {
          setShowManager(v);
          try { localStorage.setItem("hermes-show-manager", String(v)); } catch {}
        }}
        onManagerPatrolIntervalChange={(sec) => {
          setManagerPatrolIntervalSec(sec);
          try { localStorage.setItem("hermes-manager-patrol-interval", String(sec)); } catch {}
        }}
        onManagerIdleGraceChange={(sec) => {
          setManagerIdleGraceSec(sec);
          try { localStorage.setItem("hermes-manager-idle-grace", String(sec)); } catch {}
        }}
        onSearch={handleSearch}
        searchStats={searchStats}
        onReset={handleReset}
        onLoadSnapshot={handleLoadSnapshot}
        onLoadDesk={handleLoadDesk}
        onLoadSavedDesk={handleLoadSavedDesk}
        codeTheme={codeTheme}
        onCodeThemeChange={(id) => {
          setCodeTheme(id);
          try { localStorage.setItem("hermes-code-theme", id); } catch {}
        }}
        dockerPersist={dockerPersist}
        onDockerPersistChange={(v) => {
          setDockerPersist(v);
          api.docker.setConfig(v).then((r) => setDockerPersist(r.persist)).catch(() => {});
        }}
        verbose={verbose}
        onVerboseChange={(v) => {
          setVerbose(v);
          try { localStorage.setItem("hermes-verbose", String(v)); } catch {}
        }}
        agents={agents}
        rosterAgents={agentsForRoster}
        defaultModel={globalConfig.model || deskDefaultModel}
        rosterOpen={rosterOpen}
        onRosterOpenChange={setRosterOpen}
        rosterRef={rosterRef}
        rosterDragActive={agentDrag !== null}
        rosterDropHighlight={rosterHover}
        rosterLayout={rosterLayout}
        rosterSectionDropHoverId={sectionDropHoverId}
        onRosterAgentDragStart={handleRosterAgentDragStart}
        onAgentEdit={(agent) => setAgentModal({ mode: "edit", agent })}
        onDefaultEdit={() => setDefaultAgentEditorOpen(true)}
        onCreateAgent={() => setAgentModal({ mode: "create" })}
        selectedDeskId={selectedDeskId}
        deskConfig={deskConfig}
        deskConfigLocked={deskConfigLocked}
        toolsets={toolsets}
        reasoningEffort={reasoningEffort}
        reasoningOptions={reasoningOptions}
        onDeskProfileChange={(agentId) => {
          if (selectedDeskId) void applyDeskCustomization(selectedDeskId, { agentId });
        }}
        onDeskModelChange={(model) => {
          if (selectedDeskId) void applyDeskCustomization(selectedDeskId, { model });
        }}
        onDeskToolsChange={(toolPreset, toolsEnabled) => {
          if (selectedDeskId) void applyDeskCustomization(selectedDeskId, { toolPreset, toolsEnabled });
        }}
        onReasoningChange={(v) => {
          setReasoningEffort(v);
          try { localStorage.setItem("hermes-reasoning-effort", v); } catch {}
        }}
      />
      <Office
        teams={teams}
        searchMatchIds={searchMatchIds}
        justStartedId={justStartedId}
        justStartedAnchor={justStartedAnchor}
        onJustStartedConsumed={() => {
          setJustStartedId(null);
          setJustStartedAnchor(null);
        }}
        workspacePaths={workspacePaths}
        taskContents={taskContents}
        taskImages={taskImages}
        pendingTexts={pendingTexts}
        verbose={verbose}
        reasoningEffort={apiReasoningEffort}
        apiMode={apiMode}
        bellSound={bellSound}
        scene={scene}
        showManager={showManager}
        managerPatrolIntervalSec={managerPatrolIntervalSec}
        managerIdleGraceSec={managerIdleGraceSec}
        agents={agents}
        pendingAssignments={pendingAssignments}
        activePendingDeskId={activePendingDeskId}
        askManagerByTeamId={askManagerByTeamId}
        onAskManagerDone={(teamId) => setAskManagerByTeamId((prev) => ({ ...prev, [teamId]: null }))}
        onPreview={handleFilePreview}
        deskPanelZ={deskPanelZ}
        onDeskPanelActivate={activateDeskPanel}
        onDeskStart={handleDeskStart}
        onDeskClose={closeDesk}
        onAddDesk={addDeskToTeam}
        onAddTeam={addTeam}
        onDeleteTeam={deleteTeam}
        onTeamSceneChange={setTeamScene}
        onTeamRename={renameTeam}
        onSessionInterrupt={handleSessionInterrupt}
        onAssignAgentToDesk={(deskId, agentId) => handleAssignAgentToDesk(deskId, agentId)}
        deskDropHoverId={deskDropHoverId}
        onAgentDragStart={handleAgentDragStart}
        onPendingMsgChange={(id, msg) => setPendingTexts((prev) => ({ ...prev, [id]: msg }))}
        onPendingAssignmentPatch={patchPendingAssignment}
        onActivePendingDeskChange={handleActivePendingDeskChange}
        onDeskFocus={handleDeskFocus}
        focusedDeskId={focusedDeskId}
        selectedDeskId={selectedDeskId}
        deskConfigsById={deskConfigsById}
        onAvatarClick={handleAvatarClick}
        onDeskAskManager={(teamId, sid) => setAskManagerByTeamId((prev) => ({ ...prev, [teamId]: sid }))}
        toolsets={toolsets}
        reasoningValue={reasoningEffort}
        reasoningOptions={reasoningOptions}
        onDeskConfigProfileChange={(deskId, agentId) => { void handleDeskProfileChange(deskId, agentId); }}
        onDeskConfigModelChange={(deskId, model) => { void applyDeskCustomization(deskId, { model }); }}
        onDeskConfigToolsChange={(deskId, toolPreset, toolsEnabled) => { void applyDeskCustomization(deskId, { toolPreset, toolsEnabled }); }}
        onDeskConfigReasoningChange={(v) => {
          setReasoningEffort(v);
          try { localStorage.setItem("hermes-reasoning-effort", v); } catch {}
        }}
      />
      {agentDrag && (() => {
        const dragAgent = agentDrag.agentId
          ? agents.find((a) => a.id === agentDrag.agentId)
          : undefined;
        return (
          <div style={{
            position: "fixed", left: agentDrag.x - 20, top: agentDrag.y - 58,
            zIndex: 6500, pointerEvents: "none",
            filter: "drop-shadow(0 4px 12px rgba(0,0,0,0.45))",
          }}>
            <AgentFigure
              agentId={agentDrag.agentId || undefined}
              color={agentDrag.color}
              archetype={avatars.get(agentDrag.agentId)?.archetype}
              isPrototype={dragAgent?.is_prototype}
              cloneFrom={dragAgent?.clone_from}
              state={agentDrag.state}
              scale={1}
            />
          </div>
        );
      })()}
      {defaultAgentEditorOpen && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 400,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "rgba(8,8,16,0.72)",
        }}
          onMouseDown={(e) => { if (e.target === e.currentTarget) setDefaultAgentEditorOpen(false); }}
        >
          <div style={{
            background: "#16213e", border: "1px solid #2a3558", borderRadius: 10,
            padding: 16, width: "min(520px, 92vw)", maxHeight: "85vh", overflow: "auto",
          }}>
            <GlobalDefaultPersonaEditor
              onClose={() => setDefaultAgentEditorOpen(false)}
              onSaved={() => {
                void refreshAgents();
                void loadSessions();
                setDefaultAgentEditorOpen(false);
              }}
            />
          </div>
        </div>
      )}
      {deskAgentPickerId && (
        <DeskAgentPicker
          agents={agentsForRoster}
          selectedAgentId={deskConfigsById[deskAgentPickerId]?.agentId ?? ""}
          onSelect={(agentId) => {
            const deskId = deskAgentPickerId;
            if (!deskId) return;
            void handleDeskProfileChange(deskId, agentId);
            setDeskAgentPickerId(null);
          }}
          onClose={() => setDeskAgentPickerId(null)}
        />
      )}
      {assignModal && (
        <AgentAssignModal
          deskId={assignModal.deskId}
          agent={assignModal.agent}
          toolsets={toolsets}
          onAssign={confirmAssignment}
          onClose={() => setAssignModal(null)}
        />
      )}
      {agentModal && (
        <AgentProfileModal
          mode={agentModal.mode}
          agent={agentModal.agent}
          prototypes={prototypes}
          agents={agents}
          onClose={() => setAgentModal(null)}
          onSaved={refreshAgents}
          onDeleted={refreshAgents}
        />
      )}
      <FilePreview
        data={preview}
        zIndex={previewZ}
        onActivate={activateFilePreview}
        onClose={() => setPreview(null)}
        codeTheme={codeTheme}
      />
    </div>
  );
}
