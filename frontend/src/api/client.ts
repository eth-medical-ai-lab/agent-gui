import type { ActivityEvent, AgentCapabilities, AgentPersona, AgentProfile, AgentPrototype, AuditResult, DeskExport, DeskHistory, FileNode, FilePreviewData, LlmProvider, Session, SubagentRecord, TodoData, WorkerEvent } from "../types";

const BASE = "/api";
const WS_BASE = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;

async function errorDetail(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j.detail === "string") return j.detail;
    if (j.detail) return JSON.stringify(j.detail);
  } catch { /* ignore */ }
  return `${r.status} ${r.statusText}`;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errorDetail(r));
  return r.json();
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errorDetail(r));
  return r.json();
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errorDetail(r));
  return r.json();
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path, { method: "DELETE" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  sessions: {
    list: (limit = 50, offset = 0) =>
      get<Session[]>(`/sessions?limit=${limit}&offset=${offset}`),
    get: (id: string) => get<Session>(`/sessions/${id}`),
    patchDeskConfig: (id: string, body: { agent?: string; model?: string; tools?: string[] }) =>
      patch<Session>(`/sessions/${encodeURIComponent(id)}/desk-config`, body),
    // Default to the full desk conversation (server caps far above any realistic
    // desk size). Callers that only need a peek pass an explicit small limit
    // (e.g. the manager's quick glance).
    activity: (id: string, limit = 1_000_000, tail = false) =>
      get<ActivityEvent[]>(
        `/sessions/${id}/activity?limit=${limit}${tail ? "&tail=1" : ""}`,
      ),
    overview: (id: string, limit = 10000) =>
      get<{
        events: ActivityEvent[];
        started_at: string | null;
        last_at: string | null;
        message_count: number;
        session_ids?: string[];
        truncated?: boolean;
      }>(`/sessions/${id}/overview?limit=${limit}`),
    consoleHistory: (id: string, limit = 2000) =>
      get<{ text: string }>(`/sessions/${id}/console?limit=${limit}`),
    terminalHistory: (id: string, limit = 2000) =>
      get<{ text: string }>(`/sessions/${id}/terminal?limit=${limit}`),
    files: (id: string) => get<FileNode[]>(`/sessions/${id}/files`),
    workspaceTree: (id: string) => get<FileNode[]>(`/sessions/${id}/workspace_tree`),
    todos: (id: string) => get<TodoData>(`/sessions/${id}/todos`),
    delete: (id: string) =>
      del<{ ok: boolean; deleted: boolean; sandbox: boolean; workspace: boolean; transcripts: boolean; container: boolean }>(
        `/sessions/${id}`,
      ),
    interrupt: (id: string) => post<{ ok: boolean }>(`/sessions/${id}/interrupt`, {}),
    inspect: (id: string, tool: string, args: Record<string, unknown>) =>
      post<{ ok: boolean; tool: string; result?: string; error?: string }>(
        `/sessions/${id}/inspect`, { tool, args }),
    inspectStop: (id: string) =>
      post<{ ok: boolean; error?: string }>(`/sessions/${id}/inspect/stop`, {}),
    arrive: (id: string) => post<{ ok: boolean }>(`/sessions/${id}/arrive`, {}),
    sleep: (id: string) => post<{ ok: boolean }>(`/sessions/${id}/sleep`, {}),
    wake: (id: string) => post<{ ok: boolean }>(`/sessions/${id}/wake`, {}),
    resume: (id: string, content: string, attachments?: { name: string; data: string }[], agent?: string, reasoning_effort?: string, api_mode?: string) =>
      post<{ ok: boolean }>(`/sessions/${id}/resume`, {
        content,
        ...(attachments?.length ? { attachments } : {}),
        ...(agent !== undefined ? { agent } : {}),
        ...(reasoning_effort !== undefined ? { reasoning_effort } : {}),
        ...(api_mode !== undefined ? { api_mode } : {}),
      }),
    redirect: (id: string, content: string, attachments?: { name: string; data: string }[], reasoning_effort?: string, api_mode?: string) =>
      post<{ ok: boolean }>(`/sessions/${id}/redirect`, {
        content,
        ...(attachments?.length ? { attachments } : {}),
        ...(reasoning_effort !== undefined ? { reasoning_effort } : {}),
        ...(api_mode !== undefined ? { api_mode } : {}),
      }),
    autoContinue: (id: string, enabled: boolean) =>
      post<{ ok: boolean; enabled: boolean; max: number }>(`/sessions/${id}/autocontinue`, { enabled }),
    reassign: (fromId: string, toId: string, message = "Continue.") =>
      post<{ ok: boolean; session_id: string }>("/sessions/reassign", {
        from_id: fromId,
        to_id: toId,
        message,
      }),
    taskFile: {
      get: (id: string) => get<{ content: string; path: string; workspace: string }>(`/sessions/${id}/taskfile`),
      save: (id: string, content: string) => put<{ ok: boolean }>(`/sessions/${id}/taskfile`, { content }),
    },
    // Desk session-lineage history: root session + each resume/model-switch.
    history: (id: string) =>
      get<DeskHistory>(`/sessions/${id}/history`),
    // Full desk export (config + TASK.md + session history) as a JSON document.
    export: (id: string) =>
      get<DeskExport>(`/sessions/${id}/export`),
    // Full-desk archive (.tar.gz of the whole sandbox) — direct download URL so
    // the browser streams it to disk with the server's Content-Disposition name.
    archiveUrl: (id: string) => `${BASE}/sessions/${id}/archive`,
    // Load a desk previously saved via archiveUrl back into the workbench.
    importDesk: async (fileBlob: File) => {
      const fd = new FormData();
      fd.append("file", fileBlob);
      const r = await fetch(`${BASE}/sessions/import`, { method: "POST", body: fd });
      if (!r.ok) throw new Error(await errorDetail(r));
      return r.json() as Promise<{ ok: boolean; session_id: string; workspace_path: string | null; team_id: string | null }>;
    },
    listSavedDesks: () =>
      get<{ dir: string; archives: { filename: string; size: number; modified_at: string }[] }>("/sessions/saved"),
    importSavedDesk: (filename: string) =>
      post<{ ok: boolean; session_id: string; workspace_path: string | null; team_id: string | null }>(
        "/sessions/import-saved", { filename },
      ),
    // Orchestrated evidence-based manager audit (~60s; runs the agent's model).
    // Returns the cached audit instantly when state is unchanged unless force=true.
    audit: (id: string, force = false) =>
      post<AuditResult>(`/sessions/${id}/audit${force ? "?force=true" : ""}`, {}),
    auditCached: (id: string) => get<AuditResult>(`/sessions/${id}/audit`),
    // Agent progress report (PROGRESS.md). get = read cached; generate = (re)build.
    progress: {
      get: (id: string) => get<{ content: string; exists: boolean }>(`/sessions/${id}/progress`),
      generate: (id: string) => post<{ content: string; exists: boolean }>(`/sessions/${id}/progress`, {}),
    },
    // Cheap, no-LLM: has the current state already been audited?
    auditStatus: (id: string) =>
      get<{ current_hash: string; auditable: boolean; audited: boolean;
            summary: { passed: number; failed: number; unsure: number; total: number } | null }>(
        `/sessions/${id}/audit/status`),
    new: (
      content: string,
      reasoning_effort?: string,
      api_mode?: string,
      model?: string,
      attachments?: { name: string; data: string }[],
      tools?: string[],
      agent?: string,
      team_id?: string,
    ) =>
      post<{ session_id: string; workspace_path?: string; response: string; session?: Session; agent?: string | null }>(
        "/sessions/new",
        {
          content,
          ...(reasoning_effort !== undefined ? { reasoning_effort }         : {}),
          ...(api_mode                  ? { api_mode }                          : {}),
          ...(model                     ? { model }                             : {}),
          ...(attachments?.length       ? { attachments }                       : {}),
          ...(tools !== undefined        ? { tools }                            : {}),
          ...(agent                     ? { agent }                             : {}),
          ...(team_id                   ? { team_id }                           : {}),
        },
      ),
    sendMessage: async (
      id: string,
      content: string,
      onChunk?: (delta: string) => void,
    ): Promise<void> => {
      const res = await fetch(`${BASE}/sessions/${id}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      if (!res.body) return;
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (payload === "[DONE]") return;
          try {
            const json = JSON.parse(payload);
            const delta: string | undefined = json.choices?.[0]?.delta?.content;
            if (delta && onChunk) onChunk(delta);
          } catch { /* partial chunk */ }
        }
      }
    },
    activityWs: (
      id: string,
      onEvents: (events: ActivityEvent[]) => void,
      onLive?: (evt: WorkerEvent) => void,
      onClose?: () => void,
      onSubagents?: (records: SubagentRecord[]) => void,
    ): WebSocket => {
      const ws = new WebSocket(`${WS_BASE}/ws/activity/${id}`);
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (Array.isArray(data)) {
            onEvents(data);
          } else if (data.subagents && onSubagents) {
            onSubagents(data.subagents as SubagentRecord[]);
          } else if (data.live && onLive) {
            onLive(data.live as WorkerEvent);
          }
        } catch { /* ignore */ }
      };
      ws.onclose = () => onClose?.();
      return ws;
    },
    terminalWs: (id: string, onLine: (line: string) => void): WebSocket => {
      const ws = new WebSocket(`${WS_BASE}/ws/terminal/${id}`);
      ws.onmessage = (e) => { onLine(String(e.data)); };
      return ws;
    },
    consoleWs: (id: string, onLine: (line: string) => void): WebSocket => {
      const ws = new WebSocket(`${WS_BASE}/ws/console/${id}`);
      ws.onmessage = (e) => { onLine(String(e.data)); };
      return ws;
    },
    tailWs: (id: string, file: string, onLine: (line: string) => void): WebSocket => {
      const ws = new WebSocket(`${WS_BASE}/ws/tail/${id}?file=${encodeURIComponent(file)}`);
      ws.onmessage = (e) => { onLine(String(e.data)); };
      return ws;
    },
  },
  search: (q: string) => get<Session[]>(`/search?q=${encodeURIComponent(q)}`),
  file: {
    preview: (path: string) =>
      get<FilePreviewData>(`/file/preview?path=${encodeURIComponent(path)}`),
    tree: (root: string) =>
      get<FileNode[]>(`/file/tree?root=${encodeURIComponent(root)}`),
  },
  hermes: {
    status: () => get<{ available: boolean; [k: string]: unknown }>("/hermes/status"),
    warmup: () => post<{ ok: boolean }>("/warmup", {}),
  },
  llm: {
    models: (opts?: { baseUrl?: string; agentId?: string }) => {
      const q = new URLSearchParams();
      if (opts?.baseUrl) q.set("base_url", opts.baseUrl);
      if (opts?.agentId) q.set("agent_id", opts.agentId);
      const qs = q.toString();
      return get<{ models: string[]; current: string; base_url?: string }>(
        `/llm/models${qs ? `?${qs}` : ""}`,
      );
    },
    providers: (opts?: { agentId?: string }) => {
      const q = new URLSearchParams();
      if (opts?.agentId) q.set("agent_id", opts.agentId);
      const qs = q.toString();
      return get<{ providers: LlmProvider[]; active: string }>(
        `/llm/providers${qs ? `?${qs}` : ""}`,
      );
    },
  },
  manager: {
    getProfile: () =>
      get<{ profile: string; model: string; base_url: string }>("/manager/profile"),
    setProfile: (profile: string) =>
      post<{ profile: string; model: string }>("/manager/profile", { profile }),
  },
  /** @deprecated use api.llm.models */
  ollama: {
    models: (baseUrl?: string, agentId?: string) =>
      get<{ models: string[]; current: string }>(
        `/llm/models${(() => {
          const q = new URLSearchParams();
          if (baseUrl) q.set("base_url", baseUrl);
          if (agentId) q.set("agent_id", agentId);
          const qs = q.toString();
          return qs ? `?${qs}` : "";
        })()}`,
      ),
  },
  agents: {
    list: () => get<{ agents: AgentProfile[] }>("/agents"),
    prototypes: () => get<{ prototypes: AgentPrototype[] }>("/agents/prototypes"),
    capabilities: (id: string) =>
      get<AgentCapabilities>(`/agents/${encodeURIComponent(id)}/capabilities`),
    persona: (id: string) => get<AgentPersona>(`/agents/${encodeURIComponent(id)}/persona`),
    savePersona: (
      id: string,
      body: { soul: string; memory: string; model_default?: string; base_url?: string; provider?: string },
    ) =>
      put<{ ok: boolean; id: string }>(`/agents/${encodeURIComponent(id)}/persona`, body),
    create: (body: {
      id: string;
      clone_from: string;
      name?: string;
      tagline?: string;
      soul?: string;
      memory?: string;
      model_default?: string;
      base_url?: string;
      provider?: string;
    }) => post<{ ok: boolean; agent: AgentProfile }>("/agents", body),
    delete: (id: string) =>
      del<{ ok: boolean; id: string }>(`/agents/${encodeURIComponent(id)}`),
  },
  teams: {
    files: (teamId: string) =>
      get<{ files: FileNode[]; root: string }>(`/teams/${encodeURIComponent(teamId)}/files`),
    upload: (teamId: string, path: string, data: string) =>
      post<{ ok: boolean; path: string; synced_desks: number }>(
        `/teams/${encodeURIComponent(teamId)}/files`, { path, data }),
    delete: (teamId: string, path: string) =>
      del<{ ok: boolean }>(`/teams/${encodeURIComponent(teamId)}/files?path=${encodeURIComponent(path)}`),
    sync: (teamId: string) =>
      post<{ ok: boolean; synced_desks: number }>(`/teams/${encodeURIComponent(teamId)}/sync`, {}),
    register: (teamId: string, sessionIds: string[]) =>
      post<{ ok: boolean; registered: number }>(
        `/teams/${encodeURIComponent(teamId)}/register`, { session_ids: sessionIds }),
  },
  guiConfig: () =>
    get<{
      agent_profiles_dir: string;
      desk_default_model: string | null;
      agents: AgentProfile[];
      prototypes: AgentPrototype[];
      global?: { base_url: string; model: string };
      manager: { base_url: string; model: string; uses_effective_agent_model?: boolean };
    }>("/gui-config"),
  globalPersona: {
    get: () =>
      get<{ id: string; profile_path: string; model: string; base_url: string; soul: string; memory: string }>(
        "/global/persona",
      ),
    save: (body: { soul?: string; memory?: string; model_default?: string; base_url?: string; provider?: string }) =>
      put<{ ok: boolean }>("/global/persona", body),
  },
  models: {
    // Reasoning-effort options the given model + backend supports (empty = gray out).
    reasoning: (model?: string, opts?: { baseUrl?: string; agentId?: string }) => {
      const q = new URLSearchParams();
      if (model) q.set("model", model);
      if (opts?.baseUrl) q.set("base_url", opts.baseUrl);
      if (opts?.agentId) q.set("agent_id", opts.agentId);
      const qs = q.toString();
      return get<{ options: { value: string; label: string }[] }>(
        `/models/reasoning${qs ? `?${qs}` : ""}`);
    },
  },
  toolsets: () =>
    get<{
      toolsets: { name: string; label: string; lean: boolean }[];
      presets: { chat: string[]; lean: string[]; full: string[] };
      default: string;
    }>("/toolsets"),
  workspace: {
    open: (path: string) => post<{ ok: boolean }>("/workspace/open", { path }),
    openTerminal: (path: string) => post<{ ok: boolean }>("/workspace/open-terminal", { path }),
  },
  docker: {
    cleanup: () =>
      post<{ removed: number; kept: number; skipped: boolean; reason?: string }>(
        "/docker/cleanup",
        {},
      ),
    getConfig: () => get<{ persist: boolean }>("/docker/config"),
    setConfig: (persist: boolean) =>
      post<{ persist: boolean }>("/docker/config", { persist }),
  },
};
