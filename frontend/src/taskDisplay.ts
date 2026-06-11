/** Strip the server-injected workspace context block from a stored user message. */
export function stripWorkspaceHeader(text: string | null | undefined): string {
  if (!text) return "";
  return text
    .replace(/^\s*\[Workspace(?: paths)?:[\s\S]*?\]\s*/i, "")
    .replace(/\[Attached (?:image|file): [^\]]*\]\s*/g, "")
    .trim();
}

/** Best label for a desk card: LLM title, then cleaned task text, then fallback. */
export function deskDisplayTitle(
  title: string | null | undefined,
  titleSummary?: string | null,
  taskContent?: string | null,
): string {
  if (titleSummary?.trim()) return titleSummary.trim();
  const cleaned = stripWorkspaceHeader(title);
  if (cleaned) return cleaned.length > 80 ? `${cleaned.slice(0, 77)}…` : cleaned;
  const task = taskContent?.trim();
  if (task) return task.length > 80 ? `${task.slice(0, 77)}…` : task;
  return "Untitled task";
}
