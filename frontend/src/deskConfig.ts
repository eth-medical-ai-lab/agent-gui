import type { AgentArchetype } from "./components/AgentFigure";
import type { AvatarPref } from "./avatarPrefs";
import type { AgentProfile, DeskItem, PendingAssignment, Session, ToolPresetId } from "./types";

export const DEFAULT_PROFILE_COLOR = "#6a7a9a";
export const DEFAULT_PROFILE_LABEL = "Default";

export interface DeskProfileVisual {
  isDefault: boolean;
  agentId: string;
  label: string;
  model: string;
  color: string;
  archetype?: AgentArchetype;
  isPrototype?: boolean;
  cloneFrom?: string | null;
}

/** Resolved profile label / avatar / model for a desk (including global default). */
export function resolveDeskProfileVisual(opts: {
  session?: Session | null;
  deskCfg?: DeskConfigView | null;
  pendingAssignment?: PendingAssignment | null;
  agents?: AgentProfile[];
  getAvatarPref?: (agentId: string) => AvatarPref | undefined;
}): DeskProfileVisual {
  const { session, deskCfg, pendingAssignment, agents, getAvatarPref } = opts;
  const agentId = session?.agent ?? pendingAssignment?.agentId ?? deskCfg?.agentId ?? "";
  const agentProfile = agentId ? agents?.find((a) => a.id === agentId) : undefined;
  const avatarPref = agentId ? getAvatarPref?.(agentId) : undefined;
  if (agentId) {
    const model = session?.agent_model || pendingAssignment?.modelOverride || deskCfg?.model
      || agentProfile?.model || "";
    return {
      isDefault: false,
      agentId,
      label: pendingAssignment?.agentName || agentProfile?.name || agentId,
      model,
      color: avatarPref?.color || pendingAssignment?.agentColor || agentProfile?.color || DEFAULT_PROFILE_COLOR,
      archetype: avatarPref?.archetype,
      isPrototype: agentProfile?.is_prototype,
      cloneFrom: agentProfile?.clone_from,
    };
  }
  const model = session?.agent_model || session?.model || pendingAssignment?.modelOverride
    || deskCfg?.model || deskCfg?.profileModel || "";
  return {
    isDefault: true,
    agentId: "",
    label: DEFAULT_PROFILE_LABEL,
    model,
    color: DEFAULT_PROFILE_COLOR,
  };
}

export interface GlobalHermesConfig {
  base_url: string;
  model: string;
}

/** Per-desk header bar state (defaults to Global until changed). */
export interface DeskBarConfig {
  agentId: string;
  model: string;
  toolPreset: ToolPresetId;
  toolsEnabled: string[];
  /** True when user applied overrides via desk Advanced (not profile defaults). */
  customized?: boolean;
}

export interface DeskConfigView {
  deskId: string;
  isPending: boolean;
  isGlobal: boolean;
  agentId: string;
  agentProfile: AgentProfile | null;
  model: string;
  profileModel: string;
  baseUrl: string;
  toolPreset: ToolPresetId;
  toolsEnabled: string[];
  /** Bench drag override — shown in UI when set. */
  benchAssigned: boolean;
  /** Desk overrides profile defaults (Advanced). */
  customized: boolean;
}

/** True when a live desk has an active worker (not pending, not solved, not ended). */
export function deskIsRunning(desk: DeskItem | null | undefined): boolean {
  if (!desk || "isPending" in desk) return false;
  const s = desk as Session;
  return s.is_running === true && !s.task_solved && !s.ended_at;
}

export function findDeskItem(teams: { desks: DeskItem[] }[], deskId: string | null): DeskItem | null {
  if (!deskId) return null;
  for (const team of teams) {
    const hit = team.desks.find((d) => d.id === deskId);
    if (hit) return hit;
  }
  return null;
}

export function defaultDeskBarConfig(
  global: GlobalHermesConfig,
  toolPresets: { chat: string[]; lean: string[]; full: string[] },
  toolDefault: string,
): DeskBarConfig {
  const preset: ToolPresetId =
    toolDefault === "chat" || toolDefault === "full" ? toolDefault : "lean";
  return {
    agentId: "",
    model: global.model,
    toolPreset: preset,
    toolsEnabled: toolPresets[preset] ?? toolPresets.lean,
  };
}

function presetFromEnabled(
  enabled: string[],
  presets: { chat: string[]; lean: string[]; full: string[] },
): ToolPresetId {
  const key = (arr: string[]) => [...arr].sort().join(",");
  const en = key(enabled);
  if (en === key(presets.chat)) return "chat";
  if (en === key(presets.lean)) return "lean";
  if (en === key(presets.full)) return "full";
  return "lean";
}

export function resolveDeskBarConfig(
  deskId: string,
  deskBarConfigs: Record<string, DeskBarConfig>,
  global: GlobalHermesConfig,
  toolPresets: { chat: string[]; lean: string[]; full: string[] },
  toolDefault: string,
): DeskBarConfig {
  return deskBarConfigs[deskId] ?? defaultDeskBarConfig(global, toolPresets, toolDefault);
}

export function buildDeskConfigView(
  deskId: string | null,
  teams: { desks: DeskItem[] }[],
  agents: AgentProfile[],
  pendingAssignments: Record<string, PendingAssignment>,
  deskBarConfigs: Record<string, DeskBarConfig>,
  global: GlobalHermesConfig,
  toolPresets: { chat: string[]; lean: string[]; full: string[] },
  toolDefault: string,
): DeskConfigView | null {
  const desk = findDeskItem(teams, deskId);
  if (!desk) return null;

  const bar = deskId
    ? resolveDeskBarConfig(deskId, deskBarConfigs, global, toolPresets, toolDefault)
    : defaultDeskBarConfig(global, toolPresets, toolDefault);

  if ("isPending" in desk) {
    const bench = pendingAssignments[desk.id];
    const agentId = bench?.agentId ?? bar.agentId;
    const agentProfile = agentId ? agents.find((a) => a.id === agentId) ?? null : null;
    const profileModel = agentProfile?.model ?? global.model;
    const baseUrl = agentProfile?.base_url ?? global.base_url;
    const toolsEnabled = bench?.toolsEnabled ?? bar.toolsEnabled;
    const toolPreset = bench?.toolPreset ?? bar.toolPreset;
    // When a profile is selected, its own default model is the meaningful default
    // — not the global/manager model. Only an explicit per-desk override (a bench
    // model or a user-customized bar) should win; otherwise the un-customized bar
    // (which defaults to global.model) would mask the profile's model.
    const explicitModel = bench?.modelOverride ?? (bar.customized ? bar.model : undefined);
    return {
      deskId: desk.id,
      isPending: true,
      isGlobal: !agentId,
      agentId,
      agentProfile,
      profileModel,
      model: explicitModel ?? profileModel,
      baseUrl,
      toolPreset,
      toolsEnabled,
      benchAssigned: Boolean(bench),
      customized: Boolean(bench?.customized ?? bar.customized),
    };
  }

  const session = desk as Session;
  const benchAgent = session.agent ?? "";
  const agentId = benchAgent || bar.agentId;
  const agentProfile = agentId ? agents.find((a) => a.id === agentId) ?? null : null;
  const profileModel = session.agent_model || agentProfile?.model || global.model;
  const baseUrl = session.agent_base_url || agentProfile?.base_url || global.base_url;
  const toolsEnabled = session.desk_tools != null
    ? session.desk_tools
    : bar.toolsEnabled;

  // session.agent_model (the desk's actual runtime model) is authoritative; after
  // that, prefer the selected profile's own model over the un-customized bar model
  // (which defaults to global.model) so a profile desk doesn't show the global default.
  const explicitModel = bar.customized ? bar.model : undefined;
  return {
    deskId: desk.id,
    isPending: false,
    isGlobal: !agentId,
    agentId,
    agentProfile,
    profileModel,
    model: session.agent_model || explicitModel || profileModel,
    baseUrl,
    toolPreset: presetFromEnabled(toolsEnabled, toolPresets),
    toolsEnabled,
    benchAssigned: Boolean(benchAgent),
    customized: Boolean(bar.customized),
  };
}

/** Effective runtime params for starting a pending desk. */
function sameToolList(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.every((v, i) => v === sb[i]);
}

export function pendingStartParams(
  deskId: string,
  pendingAssignments: Record<string, PendingAssignment>,
  deskBarConfigs: Record<string, DeskBarConfig>,
  global: GlobalHermesConfig,
  toolPresets: { chat: string[]; lean: string[]; full: string[] },
  toolDefault: string,
  toolDefaultLeanSkip: boolean,
): { agent?: string; model?: string; tools?: string[] } {
  const bar = resolveDeskBarConfig(deskId, deskBarConfigs, global, toolPresets, toolDefault);
  const bench = pendingAssignments[deskId];
  const agentId = bench?.agentId ?? bar.agentId;
  // For a profile desk, send a model only when explicitly overridden — the
  // profile's config.yaml owns the model/base_url. Sending the un-customized
  // bar model (which defaults to the global/manager model) would override the
  // profile and pin a mismatched model/base_url. The default desk still sends
  // its bar/global model.
  const explicitModel = (bench?.modelOverride ?? (bar.customized ? bar.model : undefined))?.trim();
  const model = agentId ? explicitModel : bar.model?.trim();
  const toolsEnabled = bench?.toolsEnabled ?? bar.toolsEnabled;
  const preset = bench?.toolPreset ?? bar.toolPreset;
  // Only omit tools for a plain default desk on global lean (pre-warm prefix sharing).
  // Agent-profile desks and any custom/filtered selection must send tools explicitly.
  const omitTools =
    toolDefaultLeanSkip &&
    !agentId &&
    preset === "lean" &&
    sameToolList(toolsEnabled, toolPresets.lean ?? []);

  return {
    ...(agentId ? { agent: agentId } : {}),
    ...(model ? { model } : {}),
    ...(omitTools ? {} : { tools: toolsEnabled }),
  };
}
