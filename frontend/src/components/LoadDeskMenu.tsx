import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

interface SavedArchive {
  filename: string;
  size: number;
  modified_at: string;
}

interface Props {
  onLoadDesk: (file: File) => void;
  onLoadSavedDesk: (filename: string) => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function LoadDeskMenu({ onLoadDesk, onLoadSavedDesk }: Props) {
  const [open, setOpen] = useState(false);
  const [archives, setArchives] = useState<SavedArchive[]>([]);
  const [savedDir, setSavedDir] = useState("saved/");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    api.sessions.listSavedDesks()
      .then((res) => {
        setSavedDir(res.dir.replace(/^.*[/\\]/, "") + "/");
        setArchives(res.archives);
      })
      .catch((e) => setError((e as Error).message || "Couldn't list saved desks"))
      .finally(() => setLoading(false));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  async function pickSaved(filename: string) {
    setOpen(false);
    await onLoadSavedDesk(filename);
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <input
        ref={fileInputRef}
        type="file"
        accept=".gz,.tgz,.tar,application/gzip"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) {
            setOpen(false);
            onLoadDesk(f);
          }
          e.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Load a desk from saved/ or browse for a .tar.gz archive"
        style={{
          height: 28, padding: "0 8px",
          background: open ? "#1a2840" : "#121828",
          border: `1px solid ${open ? "var(--accent2)" : "#2a3558"}`,
          borderRadius: 6, color: "var(--text-dim)", fontSize: 10, cursor: "pointer",
        }}
      >
        📥 Load desk
      </button>
      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 5000,
          width: 320, maxHeight: 360, overflowY: "auto",
          background: "var(--bg2)", border: "1px solid var(--card-border)",
          borderRadius: 8, boxShadow: "0 8px 32px rgba(0,0,0,0.55)",
          padding: "8px 0",
        }}>
          <div style={{
            padding: "4px 12px 8px", fontSize: 10, color: "var(--text-dim)",
            borderBottom: "1px solid var(--card-border)",
          }}>
            <div style={{ fontWeight: 600, color: "var(--text)", marginBottom: 2 }}>
              {savedDir}
            </div>
            Desk archives saved in the repo (default folder)
          </div>
          {loading && (
            <div style={{ padding: "12px", fontSize: 11, color: "var(--text-dim)" }}>Loading…</div>
          )}
          {error && (
            <div style={{ padding: "12px", fontSize: 11, color: "var(--red)" }}>{error}</div>
          )}
          {!loading && !error && archives.length === 0 && (
            <div style={{ padding: "12px", fontSize: 11, color: "var(--text-dim)" }}>
              No .tar.gz files in saved/ yet. Use “Save desk” on a panel, then copy the download here.
            </div>
          )}
          {!loading && archives.map((a) => (
            <button
              key={a.filename}
              type="button"
              onClick={() => void pickSaved(a.filename)}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "8px 12px", background: "transparent", cursor: "pointer",
                border: "none", color: "var(--text)", fontSize: 11,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.06)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <div style={{ fontWeight: 500, wordBreak: "break-all" }}>{a.filename}</div>
              <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 2 }}>
                {formatSize(a.size)} · {formatWhen(a.modified_at)}
              </div>
            </button>
          ))}
          <div style={{ borderTop: "1px solid var(--card-border)", marginTop: 4, paddingTop: 4 }}>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "8px 12px", background: "transparent", cursor: "pointer",
                border: "none", color: "var(--accent2)", fontSize: 11,
              }}
            >
              Browse other file…
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
