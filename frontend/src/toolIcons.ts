// Tool → emoji icon map for the activity feed. MIRRORS agent_gui/activity_parser.py
// TOOL_ICONS — keep the two in sync so a live (WS-streamed) tool event renders the
// same icon the DB-backed timeline will use once the turn is persisted, with no
// icon flip when the live row hands off to the parsed row. Unknown tools fall back
// to DEFAULT_TOOL_ICON.
const TOOL_ICONS: Record<string, string> = {
  bash: "⚡", execute_command: "⚡", terminal: "⚡", run_command: "⚡",
  write_file: "📝", create_file: "📝", file_write: "📝",
  str_replace_editor: "✏️", edit_file: "✏️",
  read_file: "📖", file_read: "📖",
  web_search: "🔍", search: "🔍",
  browser: "🌐", web_fetch: "🌐",
  memory: "🧠", remember: "🧠",
  compress: "🗜️", summarize: "🗜️",
  delegate: "👥", spawn_agent: "👥", subagent: "👥",
  skill_view: "📚", skill_run: "📚",
  python: "🐍",
  // Claude Code (Claude Agent SDK) tool names — PascalCase.
  Read: "📖", Write: "📝", Edit: "✏️", MultiEdit: "✏️", NotebookEdit: "✏️",
  Bash: "⚡", BashOutput: "⚡", KillShell: "⚡",
  Glob: "🔍", Grep: "🔍", WebSearch: "🔍", WebFetch: "🌐",
  Task: "👥", TodoWrite: "📋", ExitPlanMode: "📋",
};

export const DEFAULT_TOOL_ICON = "🔧";

/** Icon for a tool by name, mirroring the backend parser. Falls back to 🔧. */
export function toolIcon(name?: string | null): string {
  return (name && TOOL_ICONS[name]) || DEFAULT_TOOL_ICON;
}
