import { useState } from "react";
import { api } from "../api/client";

/**
 * Operator "inspect" REPL: run a read-only tool against the desk's live Docker
 * sandbox. Calls POST /sessions/:id/inspect, which routes through the desk's
 * persistent worker so it executes with the SAME container, path translation,
 * and read guards the agent uses. Tools are whitelisted server-side
 * (read_file, search_files, list_files, terminal — no writes).
 */

type Tool = "terminal" | "search_files" | "read_file" | "list_files";

const TOOLS: { id: Tool; label: string; hint: string }[] = [
  { id: "terminal", label: "terminal", hint: "Run a shell command in the sandbox (e.g. ls -la /workspace)" },
  { id: "search_files", label: "search_files", hint: "Grep file contents (or names) under a path" },
  { id: "read_file", label: "read_file", hint: "Read a file with line numbers" },
  { id: "list_files", label: "list_files", hint: "List a directory" },
];

const INPUT: React.CSSProperties = {
  fontSize: 12, fontFamily: "monospace", padding: "6px 8px", borderRadius: 6,
  background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)",
  color: "var(--text)", outline: "none",
};
const LABEL: React.CSSProperties = { fontSize: 10, color: "var(--text-dim)", marginBottom: 2 };

export function InspectPanel({ sessionId }: { sessionId: string }) {
  const [tool, setTool] = useState<Tool>("terminal");
  const [command, setCommand] = useState("ls -la /workspace");
  const [pattern, setPattern] = useState("");
  const [target, setTarget] = useState<"content" | "files">("content");
  const [path, setPath] = useState("/workspace");
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [output, setOutput] = useState("");
  const [isError, setIsError] = useState(false);

  function buildArgs(): Record<string, unknown> {
    switch (tool) {
      case "terminal": return { command };
      case "search_files": return { pattern, target, path: path || "/workspace" };
      case "read_file": return { path };
      case "list_files": return { path: path || "/workspace" };
    }
  }

  async function run() {
    setBusy(true); setIsError(false); setStopping(false);
    try {
      const r = await api.sessions.inspect(sessionId, tool, buildArgs());
      if (r.ok) { setOutput(formatResult(r.result)); setIsError(false); }
      else { setOutput(r.error || "inspect failed"); setIsError(true); }
    } catch (e) {
      setOutput(e instanceof Error ? e.message : String(e)); setIsError(true);
    } finally {
      setBusy(false); setStopping(false);
    }
  }

  async function stop() {
    setStopping(true);
    try {
      await api.sessions.inspectStop(sessionId);
    } catch {
      /* the in-flight run() will surface any error when it returns */
    }
  }

  const hint = TOOLS.find((t) => t.id === tool)?.hint || "";

  return (
    <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.4 }}>
        Run read-only tools against this desk's sandbox — same container & paths the agent sees.
        First call may take a few seconds while the worker warms up.
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <span style={LABEL}>tool</span>
          <select value={tool} onChange={(e) => setTool(e.target.value as Tool)} style={INPUT}>
            {TOOLS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </div>

        {tool === "terminal" && (
          <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 200 }}>
            <span style={LABEL}>command</span>
            <input value={command} onChange={(e) => setCommand(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && run()} style={INPUT} spellCheck={false} />
          </div>
        )}
        {tool === "search_files" && (
          <>
            <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 140 }}>
              <span style={LABEL}>pattern</span>
              <input value={pattern} onChange={(e) => setPattern(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && run()} style={INPUT} spellCheck={false} />
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span style={LABEL}>target</span>
              <select value={target} onChange={(e) => setTarget(e.target.value as "content" | "files")} style={INPUT}>
                <option value="content">content</option>
                <option value="files">files</option>
              </select>
            </div>
            <div style={{ display: "flex", flexDirection: "column", minWidth: 160 }}>
              <span style={LABEL}>path</span>
              <input value={path} onChange={(e) => setPath(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && run()} style={INPUT} spellCheck={false} />
            </div>
          </>
        )}
        {(tool === "read_file" || tool === "list_files") && (
          <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 200 }}>
            <span style={LABEL}>path</span>
            <input value={path} onChange={(e) => setPath(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && run()} style={INPUT} spellCheck={false}
                   placeholder={tool === "read_file" ? "/workspace/team_files/notes.md" : "/workspace"} />
          </div>
        )}

        <button type="button" onClick={run} disabled={busy} style={{
          fontSize: 12, fontWeight: 600, padding: "7px 14px", borderRadius: 6,
          background: busy ? "rgba(255,255,255,0.06)" : "var(--accent2)",
          border: "1px solid var(--card-border)",
          color: busy ? "var(--text-dim)" : "#0b0b0f",
          cursor: busy ? "default" : "pointer",
        }}>
          {busy ? "Running…" : "▶ Run"}
        </button>
        {busy && (
          <button type="button" onClick={stop} disabled={stopping} style={{
            fontSize: 12, fontWeight: 600, padding: "7px 14px", borderRadius: 6,
            background: stopping ? "rgba(255,255,255,0.06)" : "var(--red, #ff6b6b)",
            border: "1px solid var(--card-border)",
            color: stopping ? "var(--text-dim)" : "#0b0b0f",
            cursor: stopping ? "default" : "pointer",
          }}>
            {stopping ? "Stopping…" : "■ Stop"}
          </button>
        )}
      </div>

      <div style={{ fontSize: 10, color: "var(--text-dim)" }}>{hint}</div>

      <pre style={{
        margin: 0, padding: "10px 12px", borderRadius: 6, minHeight: 260, maxHeight: "55vh",
        overflow: "auto", fontSize: 12, fontFamily: "monospace", whiteSpace: "pre-wrap",
        wordBreak: "break-word", background: "rgba(0,0,0,0.28)",
        border: "1px solid var(--card-border)",
        color: isError ? "var(--red, #ff6b6b)" : "var(--text)",
      }}>
        {output || "— output will appear here —"}
      </pre>
    </div>
  );
}

/** Tool results arrive as JSON strings; pull out the human-readable field, else
 *  pretty-print. Falls back to the raw string for plain output. */
function formatResult(raw: string | undefined): string {
  if (!raw) return "(empty)";
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object") {
      const o = obj as Record<string, unknown>;
      for (const k of ["output", "content", "results", "matches", "error", "stdout"]) {
        if (typeof o[k] === "string" && o[k]) return o[k] as string;
      }
      return JSON.stringify(obj, null, 2);
    }
    return String(obj);
  } catch {
    return raw;
  }
}
