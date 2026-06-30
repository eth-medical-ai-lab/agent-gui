export interface Session {
  id: string;
  started_at: string;
  ended_at: string | null;
  source: string;
  model: string;
  parent_session_id: string | null;
  title: string;
  message_count: number;
  token_estimate: number;
  is_running?: boolean;
  /** First-command / last-activity timestamps across the desk's whole lineage
   *  (same span the Overview uses). Used for the desk-card execution timer so an
   *  idle desk freezes at its last activity instead of ticking wall-clock. */
  first_activity_at?: string | null;
  last_activity_at?: string | null;
  title_summary?: string | null;
  auto_continue?: boolean;
  /** Set by the backend when the most recent manager audit passed every check.
   *  Cleared as soon as the agent is resumed (does more work). */
  task_solved?: boolean;
  workspace_path?: string | null;
  is_sleeping?: boolean;
  /** Hermes agent profile id (coder, jedi, …) when desk uses a profile. */
  agent?: string | null;
  agent_model?: string | null;
  agent_base_url?: string | null;
  /** Enabled UI toolset names from `.hermes_tools` marker. */
  desk_tools?: string[];
  /** Team this desk belongs to (from its server-side `.hermes_team_id` marker).
   *  Lets the office reconstruct teams created outside the browser (API/script). */
  team_id?: string | null;
}

export interface ToolsetMeta {
  name: string;
  label: string;
  lean: boolean;
  tools?: string[];   // individual tool names this toolset provides (display only)
}

// A tool profile = a named set of enabled toolset names. Built-in presets
// (chat/lean/full) have builtin=true; user-created ones are saved to localStorage.
export interface ToolProfile {
  id: string;
  name: string;
  enabled: string[];
  builtin?: boolean;
}

/** Hermes agent profile from ~/.hermes/profiles (or --profiles-dir). */
export interface AgentProfile {
  id: string;
  name: string;
  tagline: string;
  color: string;
  available: boolean;
  model: string;
  base_url: string;
  profile_path: string;
  is_prototype?: boolean;
  clone_from?: string | null;
  /** Frontend-derived: profile is currently bound to an awake desk (one-per-desk). */
  inUse?: boolean;
}

export interface LlmProvider {
  id: string;
  name: string;
  base_url: string;
  default_model: string;
  models: string[];
}

export interface AgentPrototype {
  id: string;
  name: string;
  tagline: string;
  color: string;
  is_prototype?: boolean;
}

export interface AgentPersona {
  id: string;
  soul: string;
  memory: string;
  profile_path: string;
  is_prototype?: boolean;
  clone_from?: string | null;
  name?: string | null;
  tagline?: string | null;
  model?: string | null;
  base_url?: string | null;
}

export interface AgentSkillBundle {
  bundle: string;
  count: number;
  skills: string[];
}

export interface AgentCapabilities {
  id: string;
  presets: { chat: string[]; lean: string[]; full: string[] };
  source: "global" | "profile";
  default_preset: string;
  profile_disabled_toolsets: string[];
  skill_bundles: AgentSkillBundle[];
  skill_count: number;
}

export type ToolPresetId = "chat" | "lean" | "full";

/** Agent + tool preset chosen for a pending desk before Start. */
export interface PendingAssignment {
  agentId: string;
  agentName: string;
  agentColor: string;
  toolPreset: ToolPresetId;
  toolsEnabled: string[];
  /** Ollama-only: override the profile's default model for this desk. */
  modelOverride?: string;
  /** Set when user saved overrides via desk Advanced. */
  customized?: boolean;
}

export interface ActivityEvent {
  timestamp: string;
  event_type: "tool_call" | "tool_result" | "compression" | "message" | "user_message" | "error" | "thinking_start";
  icon: string;
  title: string;
  detail: string;
  tool_name: string;
  is_error: boolean;
  files_touched: string[];
  // True when `timestamp` is a real recorded emit-time; false when it's Hermes's
  // coarse batch-flush time (rendered as approximate). Absent on old payloads.
  time_exact?: boolean;
}

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
  children?: FileNode[];
  operation?: string;
  preview_type?: string | null;
}

export interface FilePreviewData {
  type: "code" | "markdown" | "pdf" | "image" | "text";
  content?: string;
  path: string;
  name: string;
}

export interface AuditCriterionResult {
  id: number | string;
  task?: string;
  criterion: string;
  verdict: "pass" | "fail" | "unsure";
  evidence: string;
  fix_hint: string;
}

export interface AuditResult {
  session_id: string;
  generated_at: string;
  state_hash?: string;
  goal?: string;
  sources_inspected?: { task_spec: boolean; conversation_messages: number; output_files: string[] };
  results: AuditCriterionResult[];
  summary: { passed: number; failed: number; unsure: number; total: number };
  cached?: boolean;
  /** Auto patrol: desk was mid-turn — audit skipped, do not nudge. */
  skipped_running?: boolean;
  /** True only when a fresh audit found issues AND the loop cap isn't reached. */
  should_intervene?: boolean;
  intervention_count?: number;
  max_interventions?: number;
}

/** One session row in a desk's lineage (root, or a resume/model-switch). */
export interface DeskSessionEntry {
  id: string;
  started_at: string;
  ended_at: string | null;
  model: string;
  /** Agent profile in effect when this session row started ("" = Default). */
  profile: string;
  parent_session_id: string | null;
  message_count: number;
  is_root: boolean;
}

/** Desk history log — every session id this desk has run, oldest first. */
export interface DeskHistory {
  desk_id: string;
  profile: string;
  sessions: DeskSessionEntry[];
}

/** A desk exported to a single downloadable JSON document. */
export interface DeskExport {
  format: string;
  exported_at: string;
  desk_id: string;
  title: string;
  profile: string;
  model: string;
  tools: string[] | null;
  task: string;
  sessions: DeskSessionEntry[];
}

/** Prefix that marks a conversation message as coming from the team manager (not
 *  the human user), so the activity feed can render it distinctly. */
export const MANAGER_MSG_PREFIX = "👩‍💼 [Team manager] ";

export interface PendingDesk {
  id: string;
  isPending: true;
}

export type DeskItem = Session | PendingDesk;

export type TeamColor = "blue" | "red" | "green" | "purple" | "orange";

export const TEAM_COLORS: TeamColor[] = ["blue", "red", "green", "purple", "orange"];

export interface Team {
  id: string;
  color: TeamColor;
  desks: DeskItem[];
  /** User-visible label; falls back to "Team N" when unset. */
  name?: string;
  /** Per-team background scene id; falls back to the global scene when unset. */
  scene?: string;
}

export function teamDisplayName(team: Team, index: number): string {
  const custom = team.name?.trim();
  return custom || `Team ${index + 1}`;
}

export interface TodoItem {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed";
  parent_id?: string | null;
}

export interface TodoData {
  tasks: TodoItem[];
  summary: string;
}

/** Live event emitted by hermes_worker.py over the activity WebSocket. */
export interface WorkerEvent {
  type: "token" | "tool_start" | "tool_done" | "thinking" | "status" | "log" | "done" | "error" | "interrupted" | "agent_arrived" | "subagent";
  text?: string;   // token / thinking / subagent(thinking,progress)
  name?: string;   // tool_start / tool_done
  result?: string; // tool_done
  event?: string;  // status / subagent lifecycle (start|thinking|tool|progress|complete)
  msg?: string;    // status / error
  // ── subagent (delegate_task child) fields ──
  subagent_id?: string;
  parent_id?: string;
  depth?: number;
  model?: string;
  task_index?: number;  // 0-based position within its delegate_task call
  task_count?: number;  // batch size of that call
  goal?: string;        // subagent.start → the task/input
  tool_name?: string;   // subagent.tool
  preview?: string;     // subagent.tool args preview
  args?: string;        // subagent.tool serialized args
  status?: string;      // subagent.complete → ok|error|timeout|failed
  output?: string;      // subagent.complete → the result/output
  duration_seconds?: number;
}

/** A single entry in a subagent's activity timeline. */
export interface SubagentTimelineEvent {
  event: string;        // start | thinking | tool | progress | complete
  ts: number;
  text?: string;
  tool_name?: string;
  preview?: string;
  goal?: string;
  status?: string;
  output?: string;
  duration_seconds?: number;
}

/** A delegate_task subagent's durable trace, one per desk tab. */
export interface SubagentRecord {
  subagent_id: string;
  parent_id?: string;
  depth?: number;
  model?: string;
  task_index?: number;                // 0-based position within its delegate_task call
  task_count?: number;                // batch size of that call
  goal: string;                       // the task/input
  status: string;                     // running | ok | error | timeout | failed
  started_at?: number;
  ended_at?: number | null;
  output: string;                     // final result/output
  duration_seconds?: number;
  events: SubagentTimelineEvent[];    // tool/thinking/progress timeline
}

/** Accumulated live state shown while a session is actively running. */
export interface LiveState {
  streamText: string;   // tokens accumulated for the current assistant turn
  toolName?: string;    // name of the tool currently executing
  logLine?: string;     // latest verbose/status line from the agent
  thinkingText?: string; // extended thinking / reasoning tokens (verbose only)
  statusLine?: string;   // honest short phase label (e.g. "Invoking bash", "Waiting for model")
}

export type ReasoningEffort = "none" | "low" | "medium" | "high";
export type ApiMode = "openai" | "ollama";
