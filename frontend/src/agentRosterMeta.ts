/** Agent Roster category headers — prototype lineage groups. */
export const ROSTER_CATEGORIES = [
  { id: "coder", name: "Coder", blurb: "Ships features and squashes bugs", color: "#4a8eff" },
  { id: "researcher", name: "Researcher", blurb: "Searches through the internet and chases ideas", color: "#e67e22" },
  { id: "local", name: "Local", blurb: "Your friendly laptop-native chat companion", color: "#2ecc71" },
  { id: "api", name: "API", blurb: "The wise oracle for bigger questions", color: "#a78bfa" },
] as const;

export type RosterCategoryId = (typeof ROSTER_CATEGORIES)[number]["id"];

const CATEGORY_BY_ID = Object.fromEntries(
  ROSTER_CATEGORIES.map((c) => [c.id, c]),
) as Record<RosterCategoryId, (typeof ROSTER_CATEGORIES)[number]>;

function isLocalOllamaProfile(agent: { id: string; name?: string }): boolean {
  const id = agent.id.toLowerCase();
  const name = (agent.name ?? "").toLowerCase();
  if (name === "ollama" || name.includes("local ollama")) return true;
  if (id === "local-ollama" || id === "local_ollama" || id === "localollama") return true;
  if (id.includes("local") && id.includes("ollama")) return true;
  return false;
}

function isApiCloudProfile(agent: { id: string; name?: string }): boolean {
  const id = agent.id.toLowerCase();
  const name = (agent.name ?? "").toLowerCase();
  if (id === "cloud") return true;
  if (name === "google" || name === "cloud" || name.includes("profile cloud")) return true;
  return false;
}

export function rosterCategoryForAgent(agent: {
  id: string;
  name?: string;
  is_prototype?: boolean;
  clone_from?: string | null;
}): RosterCategoryId | null {
  if (isLocalOllamaProfile(agent)) return "local";
  if (isApiCloudProfile(agent)) return "api";
  // Built-in Claude Agent SDK agent (ids "claude-sdk"/"claude-agent-sdk", legacy
  // "claude-code") → group with the cloud/API agents so it shows in a visible
  // section instead of "Unsorted". A Hermes profile that talks to the Anthropic
  // API (e.g. the bare "claude" profile) is an API agent too.
  if (agent.id === "claude-sdk" || agent.id === "claude-agent-sdk" || agent.id === "claude-code") return "api";
  if (agent.id === "claude" || (agent.name ?? "").toLowerCase().includes("claude")) return "api";
  if (agent.is_prototype && agent.id in CATEGORY_BY_ID) {
    return agent.id as RosterCategoryId;
  }
  if (agent.clone_from && agent.clone_from in CATEGORY_BY_ID) {
    return agent.clone_from as RosterCategoryId;
  }
  return null;
}

export function rosterCategoryMeta(id: RosterCategoryId) {
  return CATEGORY_BY_ID[id];
}
