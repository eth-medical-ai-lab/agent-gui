import { useEffect, useRef, useState, type RefObject } from "react";
import type { ActivityEvent, LiveState } from "../types";
import { scrollContainerToBottom } from "../scrollContainer";
import { MANAGER_MSG_PREFIX } from "../types";

interface AttachedImage { name: string; url: string }

interface Props {
  events: ActivityEvent[];
  /** Accumulated live events from the current turn (tool calls, partial messages). */
  liveEvents?: ActivityEvent[];
  loading?: boolean;
  isActive?: boolean;
  liveState?: LiveState;
  verbose?: boolean;
  /** Task content to show as user bubble before the DB has the user message. */
  immediateUserMessage?: string;
  /** Attached images for the opening user bubble (base64 data URLs). */
  immediateUserImages?: AttachedImage[];
  /** Follow-up / barge-in messages sent from the panel, shown until the DB catches up. */
  pendingUserMessages?: { text: string; ts: string }[];
  /** Partial agent replies interrupted by a barge-in (never persisted to the DB). */
  pendingAgentMessages?: { text: string; ts: string }[];
  /** Panel body scroller — auto-follow stays inside this, not the floor scroll. */
  scrollContainerRef?: RefObject<HTMLElement | null>;
}

function formatTime(ts: string): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts.slice(11, 19) || "";
  }
}

// An event's time is "approximate" when it's Hermes's coarse batch-flush time
// rather than a real recorded emit-time (time_exact === false). We mark those
// with a leading "~" and a tooltip so the UI never presents a clustered/unknown
// time as if it were exact. (undefined = optimistic local event → treat as exact.)
const APPROX_TITLE =
  "Approximate time — Hermes flushed this whole turn's events to its DB at once, " +
  "so the exact per-event time wasn't captured.";

function timeText(ev: ActivityEvent): string {
  const t = formatTime(ev.timestamp);
  if (!t) return "";
  return ev.time_exact === false ? `~${t}` : t;
}

// The server prepends a workspace-paths header to messages before sending them to
// the agent — either the verbose first-task form ("[Workspace paths: …]") or the
// compact resume form ("[Workspace: terminal/bash uses …, file tools use …]"). It's
// agent context, not user content, so strip it from every bubble's display.
const WS_HEADER_RE = /^\s*\[Workspace(?: paths)?:[\s\S]*?\]\s*/;
function stripTaskPreamble(text: string): string {
  return text.replace(WS_HEADER_RE, "");
}

// Drop the "[Attached image: …]" / "[Attached file: …]" markers from any displayed
// message text (images are shown as thumbnails). Backstop for historical sessions
// whose stored messages still contain the marker.
function stripAttachmentMarkers(text: string): string {
  return text.replace(/\[Attached (?:image|file): [^\]]*\]\s*/g, "");
}


function fmtElapsed(s: number): string {
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

const TYPE_COLORS: Record<string, string> = {
  tool_call:     "var(--accent2)",
  tool_result:   "var(--green)",
  compression:   "var(--purple)",
  message:       "var(--text-dim)",
  user_message:  "var(--purple)",
  error:         "var(--red)",
  thinking_start:"var(--purple)",
};

const TOOL_VERBS: [string, string][] = [
  ["bash",        "Running code"],
  ["execute",     "Running code"],
  ["write_file",  "Writing file"],
  ["edit_file",   "Editing file"],
  ["read_file",   "Reading"],
  ["web_search",  "Searching web"],
  ["search",      "Searching"],
  ["list_files",  "Exploring"],
  ["computer",    "Working"],
  ["task",        "Managing tasks"],
];

function toolVerb(name: string): string {
  const lower = name.toLowerCase();
  for (const [k, v] of TOOL_VERBS) {
    if (lower.includes(k)) return v;
  }
  return "Working";
}

function UserBubble({ ev, images, verbose }: { ev: ActivityEvent; images?: AttachedImage[]; verbose?: boolean }) {
  // Always strip the workspace header (agent context, not user content); the verbose
  // flag no longer gates it. (verbose is still accepted for call-site compatibility.)
  void verbose;
  const text = stripAttachmentMarkers(stripTaskPreamble(ev.detail));
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", padding: "4px 12px" }}>
      <div style={{
        maxWidth: "82%", background: "var(--accent2)", color: "white",
        borderRadius: "12px 12px 2px 12px", padding: "7px 11px",
        fontSize: 12, lineHeight: 1.5, wordBreak: "break-word",
        whiteSpace: "pre-wrap", boxShadow: "0 1px 4px rgba(0,0,0,0.25)",
      }}>
        {images && images.length > 0 && (
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginBottom: 6 }}>
            {images.map((img) => (
              <img
                key={img.name}
                src={img.url}
                alt={img.name}
                title={img.name}
                style={{
                  height: 64, maxWidth: 120, objectFit: "cover",
                  borderRadius: 5, border: "1px solid rgba(255,255,255,0.25)",
                  display: "block",
                }}
              />
            ))}
          </div>
        )}
        {text}
        <div
          title={ev.time_exact === false ? APPROX_TITLE : undefined}
          style={{
            fontSize: 9, marginTop: 3, textAlign: "right",
            opacity: ev.time_exact === false ? 0.45 : 0.7,
            fontStyle: ev.time_exact === false ? "italic" : "normal",
          }}
        >
          {timeText(ev)}
        </div>
      </div>
    </div>
  );
}

function isManagerMsg(ev: ActivityEvent): boolean {
  // Strip any leading workspace header first so a manager message that was persisted
  // with one (before the server-side skip) is still recognized.
  return ev.event_type === "user_message" &&
    (ev.detail ?? "").replace(WS_HEADER_RE, "").trimStart().startsWith("👩‍💼");
}

/** A message injected by the team manager — visually distinct from a user query
 *  (centered, amber, manager label) so it never reads like the human spoke. */
function ManagerBubble({ ev }: { ev: ActivityEvent }) {
  // Strip the workspace header and the manager prefix (old "[Floor manager]" or new
  // "[Team manager]") so historical messages render cleanly after the rename too.
  const text = (ev.detail ?? "")
    .replace(WS_HEADER_RE, "")
    .replace(/👩‍💼\s*\[(?:Floor|Team) manager\]\s*/i, "")
    .trimStart();
  return (
    <div style={{ display: "flex", justifyContent: "center", padding: "6px 12px" }}>
      <div style={{
        maxWidth: "90%", background: "rgba(214,158,46,0.12)",
        border: "1px solid rgba(214,158,46,0.4)", color: "var(--text)",
        borderRadius: 10, padding: "7px 12px",
        fontSize: 12, lineHeight: 1.5, wordBreak: "break-word", whiteSpace: "pre-wrap",
      }}>
        <div style={{
          fontSize: 10, fontWeight: 700, letterSpacing: "0.04em",
          color: "#d69e2e", marginBottom: 3, textTransform: "uppercase",
        }}>
          👩‍💼 Team Manager
        </div>
        {text}
        <div title={ev.time_exact === false ? APPROX_TITLE : undefined} style={{
          fontSize: 9, marginTop: 3, textAlign: "right", opacity: 0.6,
          fontStyle: ev.time_exact === false ? "italic" : "normal",
        }}>
          {timeText(ev)}
        </div>
      </div>
    </div>
  );
}

/** A completed reasoning/thinking trace, collapsed by default. The live stream
 *  (VerboseLive) shows reasoning as it arrives; once the phase ends it lands here
 *  as a purple "Reasoning" step that can be expanded to re-read the full trace. */
function ReasoningStep({ ev }: { ev: ActivityEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginLeft: 8, borderLeft: "2px solid var(--purple)", borderRadius: "0 4px 4px 0" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex", alignItems: "center", gap: 6, width: "100%",
          background: "transparent", border: "none", cursor: "pointer",
          padding: "5px 16px", textAlign: "left", color: "var(--purple)",
        }}
      >
        <span style={{
          fontSize: 9, transition: "transform 0.12s",
          transform: open ? "rotate(90deg)" : "rotate(0deg)",
        }}>▶</span>
        <span style={{ fontSize: 13, lineHeight: 1.4 }}>💭</span>
        <span style={{ fontSize: 11, fontWeight: 600 }}>Reasoning</span>
        <span style={{ fontSize: 10, color: "var(--text-dim)", fontWeight: 400 }}>
          {open ? "hide" : "show"} trace
        </span>
        <span
          title={ev.time_exact === false ? APPROX_TITLE : undefined}
          style={{
            fontSize: 10, color: "var(--text-dim)", marginLeft: "auto",
            opacity: ev.time_exact === false ? 0.6 : 1,
            fontStyle: ev.time_exact === false ? "italic" : "normal",
          }}
        >
          {timeText(ev)}
        </span>
      </button>
      {open && (
        <div style={{
          fontSize: 10, color: "rgba(180,120,255,0.85)", lineHeight: 1.5,
          whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "monospace",
          padding: "0 16px 8px 30px", maxHeight: 320, overflowY: "auto",
        }}>
          {ev.detail}
        </div>
      )}
    </div>
  );
}

function EventRow({ ev }: { ev: ActivityEvent }) {
  return (
    <div style={{
      display: "flex", gap: 10, padding: "5px 16px",
      borderLeft: `2px solid ${ev.is_error ? "var(--red)" : TYPE_COLORS[ev.event_type] || "transparent"}`,
      marginLeft: 8,
      background: ev.is_error ? "rgba(239,71,111,0.04)" : "transparent",
      borderRadius: "0 4px 4px 0",
    }}>
      <span style={{ fontSize: 13, flexShrink: 0, lineHeight: 1.5 }}>{ev.icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: ev.is_error ? "var(--red)" : TYPE_COLORS[ev.event_type] }}>
            {ev.title}
          </span>
          <span
            title={ev.time_exact === false ? APPROX_TITLE : undefined}
            style={{
              fontSize: 10, color: "var(--text-dim)", flexShrink: 0,
              opacity: ev.time_exact === false ? 0.6 : 1,
              fontStyle: ev.time_exact === false ? "italic" : "normal",
            }}
          >
            {timeText(ev)}
          </span>
        </div>
        {ev.detail && (
          ev.event_type === "message" || ev.event_type === "error" ? (
            <div style={{
              fontSize: 11, color: "var(--text)", marginTop: 4,
              whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.55,
              borderLeft: "2px solid rgba(255,255,255,0.08)", paddingLeft: 8, marginLeft: -8,
            }}>
              {ev.detail}
              {ev.event_type === "error" && (
                <div style={{ color: "var(--text-dim)", marginTop: 6, fontStyle: "italic" }}>
                  Please go check Console → Debug terminal for the error message.
                </div>
              )}
            </div>
          ) : (
            <div style={{
              fontSize: 11, color: "var(--text-dim)", marginTop: 1,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              fontFamily: ev.event_type === "tool_call" ? "monospace" : "inherit",
            }}>
              {ev.detail}
            </div>
          )
        )}
        {ev.files_touched?.length > 0 && (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 3 }}>
            {ev.files_touched.map((f) => (
              <span key={f} style={{
                fontSize: 10, padding: "1px 5px", background: "rgba(78,204,163,0.12)",
                color: "var(--green)", borderRadius: 3, fontFamily: "monospace",
              }}>
                {f.split("/").pop()}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PulseDots({ delay = 0.2, color = "var(--accent2)" }: { delay?: number; color?: string }) {
  return (
    <span style={{ display: "inline-flex", gap: 3, alignItems: "center", marginLeft: 4 }}>
      {[0, delay, delay * 2].map((d) => (
        <span key={d} style={{
          width: 4, height: 4, borderRadius: "50%", background: color,
          display: "inline-block",
          animation: `act-pulse 1s ${d}s ease-in-out infinite`,
        }} />
      ))}
    </span>
  );
}

function ElapsedBadge({ elapsed }: { elapsed: number }) {
  if (elapsed < 3) return null;
  return (
    <span style={{
      fontSize: 9, color: "var(--text-dim)", marginLeft: 8,
      fontFamily: "monospace", opacity: 0.7,
      background: "rgba(255,255,255,0.06)", padding: "1px 5px", borderRadius: 3,
    }}>
      {fmtElapsed(elapsed)}
    </span>
  );
}

/** A partial agent reply that was cut off by a barge-in (kept client-side since
 *  the interrupted turn's tail never reaches the DB). */
function InterruptedAgentBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-start", padding: "4px 12px" }}>
      <div style={{
        maxWidth: "82%", background: "var(--bg)", color: "var(--text)",
        border: "1px dashed var(--card-border)", borderRadius: "12px 12px 12px 2px",
        padding: "7px 11px", fontSize: 12, lineHeight: 1.5,
        wordBreak: "break-word", whiteSpace: "pre-wrap",
      }}>
        <div style={{ fontSize: 9, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>
          Agent · interrupted
        </div>
        {text}
        <span style={{ color: "var(--text-dim)", fontStyle: "italic" }}> …⏸</span>
      </div>
    </div>
  );
}

/** Non-verbose: ChatGPT-style animated phrase + optional log line for debugging */
function SimpleLive({ liveState, elapsed }: { liveState: LiveState; elapsed: number }) {
  const { toolName, streamText, thinkingText, logLine, statusLine } = liveState;
  const phrase = streamText ? "Responding"
    : thinkingText ? "Reasoning"
    : toolName ? toolVerb(toolName)
    : statusLine || "Working";

  return (
    <div style={{ padding: "10px 16px" }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <span style={{ fontSize: 12, color: "var(--accent2)", fontWeight: 500 }}>{phrase}</span>
        <PulseDots />
        <ElapsedBadge elapsed={elapsed} />
      </div>
      {/* Show latest log line even in simple mode — helps spot stuck states */}
      {logLine && (
        <div style={{
          marginTop: 4, fontSize: 10, color: "var(--text-dim)", fontFamily: "monospace",
          opacity: 0.55, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {logLine}
        </div>
      )}
    </div>
  );
}

/** Verbose: full tool/stream/thinking + elapsed time */
function VerboseLive({ liveState, elapsed }: { liveState: LiveState; elapsed: number }) {
  const { streamText, toolName, logLine, thinkingText, statusLine } = liveState;

  // Keep the streaming reasoning box pinned to the bottom so the newest tokens
  // stay in view. (Rendering the full trace — not a trailing slice — means the
  // content only grows downward, so the text above no longer re-wraps/shuffles.)
  // But if the user scrolls up to read, pause auto-scroll so we don't yank them
  // back; resume once they return to the bottom or after 5 s of no scrolling.
  const thinkRef = useRef<HTMLDivElement>(null);
  const pausedUntilRef = useRef(0);
  const onThinkScroll = () => {
    const el = thinkRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    // >~1.5 lines from the bottom = exploring → hold for 5 s past the last
    // scroll; back at the bottom = re-pin to the live stream immediately.
    pausedUntilRef.current = distanceFromBottom > 24 ? Date.now() + 5000 : 0;
  };
  useEffect(() => {
    const el = thinkRef.current;
    if (el && Date.now() >= pausedUntilRef.current) el.scrollTop = el.scrollHeight;
  }, [thinkingText]);

  if (thinkingText && !toolName && !streamText) {
    return (
      <div style={{ padding: "5px 16px", marginLeft: 8, borderLeft: "2px solid var(--purple)" }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--purple)", marginBottom: 3, display: "flex", alignItems: "center", gap: 6 }}>
          <span>💭 Reasoning</span>
          <PulseDots delay={0.18} color="var(--purple)" />
          <ElapsedBadge elapsed={elapsed} />
        </div>
        <div ref={thinkRef} onScroll={onThinkScroll} style={{
          fontSize: 10, color: "rgba(180,120,255,0.8)", lineHeight: 1.5,
          whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "monospace",
          maxHeight: 120, overflowY: "auto", opacity: 0.8,
        }}>
          {thinkingText}
        </div>
      </div>
    );
  }

  if (toolName) {
    return (
      <div style={{ padding: "6px 16px 6px 26px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 13 }}>🔧</span>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent2)", fontFamily: "monospace" }}>{toolName}</span>
          <PulseDots delay={0.18} />
          <ElapsedBadge elapsed={elapsed} />
        </div>
        {logLine && (
          <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 3, fontFamily: "monospace", opacity: 0.65 }}>
            {logLine}
          </div>
        )}
      </div>
    );
  }

  if (streamText) {
    return (
      <div style={{ padding: "5px 16px", marginLeft: 8, borderLeft: "2px solid var(--text-dim)" }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)", marginBottom: 3, display: "flex", alignItems: "center" }}>
          Agent <ElapsedBadge elapsed={elapsed} />
        </div>
        <div style={{
          fontSize: 11, color: "var(--text)", lineHeight: 1.55,
          whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>
          {streamText}
          <span style={{
            display: "inline-block", width: 7, height: 13,
            background: "var(--accent2)", marginLeft: 2,
            animation: "cursor-blink 1s step-end infinite",
            verticalAlign: "text-bottom",
          }} />
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: "6px 28px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent2)" }}>{statusLine || "Working"}</span>
        <PulseDots delay={0.2} />
        <ElapsedBadge elapsed={elapsed} />
      </div>
      {logLine && logLine !== statusLine && (
        <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 5, fontFamily: "monospace", lineHeight: 1.4, opacity: 0.75 }}>
          {logLine}
        </div>
      )}
    </div>
  );
}

export function ActivityFeed({ events, liveEvents = [], loading, isActive, liveState, verbose = true, immediateUserMessage, immediateUserImages, pendingUserMessages = [], pendingAgentMessages = [], scrollContainerRef }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const liveStartRef = useRef<number>(0);
  const [elapsed, setElapsed] = useState(0);

  // Content-driven, NOT gated on isActive/is_running (those come from the 5s poll
  // and lag a just-sent follow-up by seconds) — so the live status shows the instant
  // there's anything to show, including an optimistic statusLine set on send.
  const hasLive = Boolean(liveState && (liveState.streamText || liveState.toolName || liveState.logLine || liveState.thinkingText || liveState.statusLine));

  // Start/reset elapsed timer when live activity changes
  useEffect(() => {
    if (hasLive) {
      if (!liveStartRef.current) liveStartRef.current = Date.now();
    } else {
      liveStartRef.current = 0;
      setElapsed(0);
    }
  }, [hasLive]);

  // Reset on new events (agent completed a turn)
  useEffect(() => {
    liveStartRef.current = 0;
    setElapsed(0);
  }, [events.length]);

  // Tick every second while there's live activity
  useEffect(() => {
    if (!hasLive) return;
    const iv = setInterval(() => {
      if (liveStartRef.current) {
        setElapsed(Math.floor((Date.now() - liveStartRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(iv);
  }, [hasLive]);

  useEffect(() => {
    const container = scrollContainerRef?.current;
    if (container) scrollContainerToBottom(container);
  }, [events.length, liveEvents.length, liveState?.streamText?.length, scrollContainerRef]);

  if (loading) {
    return <div style={{ padding: 24, color: "var(--text-dim)", fontSize: 13 }}>Loading activity…</div>;
  }

  const visibleEvents = verbose
    ? events
    : events.filter((ev) => ev.event_type === "user_message" || ev.event_type === "message" || ev.event_type === "error" || ev.event_type === "thinking_start");

  // Show the task content as a synthetic user bubble if the DB doesn't have one yet
  const hasDbUserMessage = events.some((ev) => ev.event_type === "user_message");
  const syntheticBubble = immediateUserMessage && !hasDbUserMessage ? immediateUserMessage : null;

  // Follow-up / barge-in messages the panel sent that the DB hasn't reflected yet,
  // plus partial agent replies interrupted by a barge-in (never reach the DB).
  // Interleave both by timestamp so an interrupted reply shows just before the
  // user message that cut it off.
  const dbUserTexts = new Set(events.filter((e) => e.event_type === "user_message").map((e) => e.detail));
  // The server now preserves interrupted turns' partial replies as feed events
  // (orphans merged into the snapshot) — drop a client-kept copy once it shows up
  // there, so a barge-in reply isn't displayed twice.
  const dbAgentTexts = new Set(events.filter((e) => e.event_type === "message").map((e) => (e.detail ?? "").trim()));
  const pendingBubbles: { role: "user" | "agent"; text: string; ts: string }[] = [
    ...pendingUserMessages.filter((m) => !dbUserTexts.has(m.text)).map((m) => ({ role: "user" as const, ...m })),
    ...pendingAgentMessages.filter((m) => !dbAgentTexts.has(m.text.trim())).map((m) => ({ role: "agent" as const, ...m })),
  ].sort((a, b) => a.ts.localeCompare(b.ts));

  if (!visibleEvents.length && !hasLive && !syntheticBubble && !pendingBubbles.length) {
    return <div style={{ padding: 24, color: "var(--text-dim)", fontSize: 13 }}>No activity recorded for this session.</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "8px 0" }}>
      <style>{`
        @keyframes act-pulse    { 0%,100%{opacity:0.25;transform:scale(0.75)} 50%{opacity:1;transform:scale(1)} }
        @keyframes cursor-blink { 0%,100%{opacity:1} 50%{opacity:0} }
      `}</style>
      {syntheticBubble && (
        <UserBubble
          verbose={verbose}
          images={immediateUserImages}
          ev={{
            timestamp: "", event_type: "user_message", icon: "👤", title: "User",
            detail: syntheticBubble, tool_name: "", is_error: false, files_touched: [],
          }}
        />
      )}
      {visibleEvents.map((ev, i) =>
        isManagerMsg(ev)
          ? <ManagerBubble key={i} ev={ev} />
          : ev.event_type === "user_message"
          ? <UserBubble key={i} verbose={verbose} ev={ev} images={i === 0 && !syntheticBubble ? immediateUserImages : undefined} />
          : ev.event_type === "thinking_start"
          ? <ReasoningStep key={i} ev={ev} />
          : <EventRow key={i} ev={ev} />
      )}
      {pendingBubbles.map((m, i) =>
        m.role === "user" ? (
          <UserBubble key={`pending-${i}`} verbose={verbose} ev={{
            timestamp: m.ts, event_type: "user_message", icon: "👤", title: "User",
            detail: m.text, tool_name: "", is_error: false, files_touched: [],
          }} />
        ) : (
          <InterruptedAgentBubble key={`pending-${i}`} text={m.text} />
        )
      )}
      {liveEvents.map((ev, i) =>
        isManagerMsg(ev)
          ? <ManagerBubble key={`live-${i}`} ev={ev} />
          : ev.event_type === "user_message"
          ? <UserBubble key={`live-${i}`} verbose={verbose} ev={ev} />
          : ev.event_type === "thinking_start"
          ? <ReasoningStep key={`live-${i}`} ev={ev} />
          : <EventRow key={`live-${i}`} ev={ev} />
      )}
      {hasLive && (verbose
        ? <VerboseLive liveState={liveState!} elapsed={elapsed} />
        : <SimpleLive liveState={liveState!} elapsed={elapsed} />
      )}
      <div ref={bottomRef} />
    </div>
  );
}
