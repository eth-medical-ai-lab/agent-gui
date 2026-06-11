import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import type { FilePreviewData } from "../types";
import { modalPanelStyle } from "./modalStyles";
import { DESK_PANEL_Z_BASE } from "../floatingPanelStack";
import { usePanelDrag } from "../usePanelDrag";
import { usePanelResize } from "../usePanelResize";
import { PanelResizeHandle } from "./PanelResizeHandle";

const PREVIEW_PANEL_W = 760;
const PREVIEW_PANEL_H = 520;
const PREVIEW_PANEL_MIN = { width: 360, height: 240 };

// Register only the languages we actually use (keeps bundle small)
import langPython     from "react-syntax-highlighter/dist/esm/languages/hljs/python";
import langTypescript from "react-syntax-highlighter/dist/esm/languages/hljs/typescript";
import langJavascript from "react-syntax-highlighter/dist/esm/languages/hljs/javascript";
import langJSON       from "react-syntax-highlighter/dist/esm/languages/hljs/json";
import langYAML       from "react-syntax-highlighter/dist/esm/languages/hljs/yaml";
import langBash       from "react-syntax-highlighter/dist/esm/languages/hljs/bash";
import langRust       from "react-syntax-highlighter/dist/esm/languages/hljs/rust";
import langGo         from "react-syntax-highlighter/dist/esm/languages/hljs/go";
import langCpp        from "react-syntax-highlighter/dist/esm/languages/hljs/cpp";
import langC          from "react-syntax-highlighter/dist/esm/languages/hljs/c";
import langCSS        from "react-syntax-highlighter/dist/esm/languages/hljs/css";
import langHtml       from "react-syntax-highlighter/dist/esm/languages/hljs/xml";
import langSQL        from "react-syntax-highlighter/dist/esm/languages/hljs/sql";
import langMarkdown   from "react-syntax-highlighter/dist/esm/languages/hljs/markdown";

// Themes
import { atomOneDark }        from "react-syntax-highlighter/dist/esm/styles/hljs";
import { tomorrowNightBlue }  from "react-syntax-highlighter/dist/esm/styles/hljs";
import { monokai }            from "react-syntax-highlighter/dist/esm/styles/hljs";
import { vs2015 }             from "react-syntax-highlighter/dist/esm/styles/hljs";
import { nightOwl }           from "react-syntax-highlighter/dist/esm/styles/hljs";

SyntaxHighlighter.registerLanguage("python",     langPython);
SyntaxHighlighter.registerLanguage("typescript", langTypescript);
SyntaxHighlighter.registerLanguage("javascript", langJavascript);
SyntaxHighlighter.registerLanguage("json",       langJSON);
SyntaxHighlighter.registerLanguage("yaml",       langYAML);
SyntaxHighlighter.registerLanguage("bash",       langBash);
SyntaxHighlighter.registerLanguage("rust",       langRust);
SyntaxHighlighter.registerLanguage("go",         langGo);
SyntaxHighlighter.registerLanguage("cpp",        langCpp);
SyntaxHighlighter.registerLanguage("c",          langC);
SyntaxHighlighter.registerLanguage("css",        langCSS);
SyntaxHighlighter.registerLanguage("html",       langHtml);
SyntaxHighlighter.registerLanguage("xml",        langHtml);
SyntaxHighlighter.registerLanguage("sql",        langSQL);
SyntaxHighlighter.registerLanguage("markdown",   langMarkdown);

// ── Theme catalogue ────────────────────────────────────────────────────────

export const CODE_THEMES = [
  { id: "atomOneDark",       name: "Atom One Dark",    style: atomOneDark       },
  { id: "cobalt",            name: "Cobalt",           style: tomorrowNightBlue },
  { id: "monokai",           name: "Monokai",          style: monokai           },
  { id: "vs2015",            name: "VS Dark",          style: vs2015            },
  { id: "nightOwl",          name: "Night Owl",        style: nightOwl          },
] as const;

export type CodeThemeId = typeof CODE_THEMES[number]["id"];
export const DEFAULT_CODE_THEME: CodeThemeId = "cobalt";

// ── Helpers ────────────────────────────────────────────────────────────────

function langFromName(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    py: "python", ts: "typescript", tsx: "typescript", js: "javascript",
    jsx: "javascript", json: "json", yaml: "yaml", yml: "yaml",
    md: "markdown", sh: "bash", bash: "bash", zsh: "bash",
    rs: "rust", go: "go", cpp: "cpp", cc: "cpp", c: "c", h: "c",
    css: "css", scss: "css", html: "html", xml: "xml", sql: "sql",
  };
  return map[ext] || "";
}

// ── Props ──────────────────────────────────────────────────────────────────

interface Props {
  data: FilePreviewData | null;
  zIndex?: number;
  onActivate?: () => void;
  onClose: () => void;
  codeTheme?: CodeThemeId;
}

export function FilePreview({ data, zIndex, onActivate, onClose, codeTheme = DEFAULT_CODE_THEME }: Props) {
  const modalRef = useRef<HTMLDivElement>(null);
  const [mdRaw, setMdRaw] = useState(false);
  const { pos, resetPos, dragging, bindHandle } = usePanelDrag();
  const { size: panelUserSize, resetSize, resizing, bindResize } = usePanelResize(PREVIEW_PANEL_MIN);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    setMdRaw(false);
    resetPos();
    resetSize();
  }, [data?.path, resetPos, resetSize]);

  if (!data) return null;

  const panelW = panelUserSize?.width ?? Math.min(PREVIEW_PANEL_W, window.innerWidth - 48);
  const panelH = panelUserSize?.height ?? PREVIEW_PANEL_H;

  function defaultPos() {
    return {
      top: Math.max(24, Math.round(window.innerHeight * 0.1)),
      left: Math.max(16, Math.round((window.innerWidth - panelW) / 2)),
    };
  }

  const panelPos = pos ?? defaultPos();
  const panelDragHandle = bindHandle(defaultPos);
  const panelResizeHandle = bindResize(() => ({ width: panelW, height: panelH }));

  return createPortal(
    <div
      ref={modalRef}
      onMouseDown={(e) => { e.stopPropagation(); onActivate?.(); }}
      style={{
        position: "fixed",
        top: panelPos.top,
        left: panelPos.left,
        width: panelW,
        height: panelH,
        zIndex: zIndex ?? DESK_PANEL_Z_BASE + 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        ...modalPanelStyle,
        boxShadow: "0 12px 40px rgba(0, 0, 0, 0.75)",
        transition: (dragging || resizing) ? "none" : "width 0.18s ease, height 0.18s ease, top 0.18s ease, left 0.18s ease",
      }}
    >
      {/* Header — drag to move */}
      <div
        {...panelDragHandle}
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 16px", borderBottom: "1px solid var(--card-border)",
          flexShrink: 0,
          cursor: dragging ? "grabbing" : "grab",
          userSelect: "none",
        }}
        title="Drag to move"
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{data.name}</div>
          <div style={{
            fontSize: 11, color: "var(--text-dim)", marginTop: 2,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>{data.path}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {data.type === "markdown" && (
            <button
              onClick={(e) => { e.stopPropagation(); setMdRaw((r) => !r); }}
              onPointerDown={(e) => e.stopPropagation()}
              style={{
                fontSize: 11, padding: "3px 10px", borderRadius: 5,
                background: mdRaw ? "var(--accent2)" : "rgba(255,255,255,0.06)",
                color: mdRaw ? "white" : "var(--text-dim)",
                border: "1px solid var(--card-border)", cursor: "pointer",
              }}
            >
              {mdRaw ? "Rendered" : "Raw"}
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onClose(); }}
            onPointerDown={(e) => e.stopPropagation()}
            style={{
              width: 28, height: 28, borderRadius: 6,
              background: "rgba(255,255,255,0.08)",
              color: "var(--text)", fontSize: 16,
              display: "flex", alignItems: "center", justifyContent: "center",
              cursor: "pointer", border: "none",
            }}
          >×</button>
        </div>
      </div>

      {/* Content */}
      <div style={{ overflow: "auto", flex: 1, minHeight: 0 }}>
        {(data.type === "code" || data.type === "text") && (
          <CodeView content={data.content || ""} name={data.name} themeId={codeTheme} />
        )}
        {data.type === "markdown" && (
          mdRaw
            ? <CodeView content={data.content || ""} name={data.name} themeId={codeTheme} />
            : <MarkdownView content={data.content || ""} />
        )}
        {data.type === "image" && (
          <img
            src={`/api/file/preview?path=${encodeURIComponent(data.path)}`}
            alt={data.name}
            style={{ maxWidth: "100%", display: "block", margin: "auto", padding: 16 }}
          />
        )}
        {data.type === "pdf" && (
          <iframe
            src={`/api/file/preview?path=${encodeURIComponent(data.path)}`}
            style={{ width: "100%", height: "100%", border: "none" }}
            title={data.name}
          />
        )}
      </div>
      <PanelResizeHandle active={resizing} bind={panelResizeHandle} />
    </div>,
    document.body,
  );
}

// ── CodeView — syntax highlighted or plain fallback ────────────────────────

function CodeView({ content, name, themeId }: { content: string; name: string; themeId: CodeThemeId }) {
  const lang = langFromName(name);
  const theme = CODE_THEMES.find(t => t.id === themeId)?.style ?? tomorrowNightBlue;

  if (!lang) {
    // Plain text — no highlighting
    return (
      <pre style={{
        margin: 0, padding: 16, fontSize: 13, lineHeight: 1.6,
        color: "var(--text)", fontFamily: "'JetBrains Mono','Fira Code','Cascadia Code',monospace",
        overflowX: "auto", tabSize: 2,
      }}>
        <code>{content}</code>
      </pre>
    );
  }

  return (
    <SyntaxHighlighter
      language={lang}
      style={theme}
      showLineNumbers
      lineNumberStyle={{ opacity: 0.35, fontSize: 11, minWidth: "2.5em", userSelect: "none" }}
      customStyle={{
        margin: 0, padding: "16px 12px", fontSize: 13, lineHeight: 1.6,
        fontFamily: "'JetBrains Mono','Fira Code','Cascadia Code',monospace",
        background: "transparent",
        overflowX: "auto",
      }}
      codeTagProps={{ style: { fontFamily: "inherit" } }}
    >
      {content}
    </SyntaxHighlighter>
  );
}

// ── MarkdownView ───────────────────────────────────────────────────────────

export function MarkdownView({ content }: { content: string }) {
  return (
    <div style={{ padding: "24px 32px", lineHeight: 1.75, maxWidth: 760, margin: "0 auto" }}>
      <style>{`
        .md-body { color: var(--text); font-size: 14px; }
        .md-body h1,.md-body h2,.md-body h3,.md-body h4 {
          font-weight: 700; margin: 1.4em 0 0.5em; color: var(--text);
          border-bottom: 1px solid var(--card-border); padding-bottom: 0.25em;
        }
        .md-body h1 { font-size: 1.7em; }
        .md-body h2 { font-size: 1.35em; }
        .md-body h3 { font-size: 1.1em; border-bottom: none; }
        .md-body h4 { font-size: 1em; border-bottom: none; }
        .md-body p  { margin: 0.7em 0; }
        .md-body a  { color: var(--accent2); text-decoration: none; }
        .md-body a:hover { text-decoration: underline; }
        .md-body code {
          background: rgba(255,255,255,0.08); border-radius: 4px;
          padding: 1px 5px; font-size: 0.88em;
          font-family: 'JetBrains Mono','Fira Code',monospace;
        }
        .md-body pre {
          background: rgba(0,0,0,0.35); border: 1px solid var(--card-border);
          border-radius: 6px; padding: 14px 16px; overflow-x: auto; margin: 1em 0;
        }
        .md-body pre code { background: none; padding: 0; font-size: 0.85em; }
        .md-body blockquote {
          border-left: 3px solid var(--accent2); margin: 0.8em 0;
          padding: 4px 16px; color: var(--text-dim);
          background: rgba(255,255,255,0.03); border-radius: 0 4px 4px 0;
        }
        .md-body ul,.md-body ol { padding-left: 1.6em; margin: 0.6em 0; }
        .md-body li { margin: 0.25em 0; }
        .md-body li input[type=checkbox] { margin-right: 6px; accent-color: var(--accent2); }
        .md-body table {
          border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 13px;
        }
        .md-body th,.md-body td {
          border: 1px solid var(--card-border); padding: 6px 12px; text-align: left;
        }
        .md-body th { background: rgba(255,255,255,0.06); font-weight: 600; }
        .md-body tr:nth-child(even) { background: rgba(255,255,255,0.02); }
        .md-body hr {
          border: none; border-top: 1px solid var(--card-border); margin: 1.5em 0;
        }
        .md-body img { max-width: 100%; border-radius: 6px; }
      `}</style>
      <div className="md-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    </div>
  );
}
