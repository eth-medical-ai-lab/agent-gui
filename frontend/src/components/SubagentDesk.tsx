import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { SubagentRecord, WorkerEvent } from "../types";
import { AgentFigure, type AgentArchetype } from "./AgentFigure";
import { useTeamRowPanel } from "../TeamRowPanelContext";
import { usePanelDrag } from "../usePanelDrag";
import { usePanelResize } from "../usePanelResize";
import { PanelResizeHandle } from "./PanelResizeHandle";
import {
  type DeskOffset,
  defaultBelowDeskOffset,
  deskOffsetFromViewport,
  rowToViewport,
  useDeskAnchoredRowPosition,
} from "../deskPanelAnchor";
import { DESK_PANEL_Z_BASE } from "../floatingPanelStack";

const SUBAGENT_MAX_TIMELINE = 600;  // cap client-side timeline growth per subagent
const SUB_PANEL_W = 420;
const SUB_PANEL_H = 460;
const SUB_PANEL_MIN = { width: 280, height: 240 };
// Subagent panels stack above desk panels (DESK_PANEL_Z_BASE) and above each
// other in click order — a shared module-level counter, bumped on open/activate.
const subZ = { current: DESK_PANEL_Z_BASE + 100 };

// Give each subagent a distinct-but-stable look (by spawn order).
const SUB_ARCHETYPES: AgentArchetype[] = ["researcher", "coder", "cloud", "local", "default"];
const SUB_COLORS = ["#c084fc", "#f472b6", "#60a5fa", "#34d399", "#fbbf24", "#fb7185"];

/** Reduce one live {type:"subagent"} worker event into the per-subagent map. */
export function applySubagentLive(
  prev: Record<string, SubagentRecord>,
  evt: WorkerEvent,
): Record<string, SubagentRecord> {
  const sid = evt.subagent_id;
  if (!sid) return prev;
  const ts = Date.now() / 1000;
  const existing = prev[sid] ?? {
    subagent_id: sid, goal: "", status: "running",
    started_at: ts, ended_at: null, output: "", events: [],
  };
  const rec: SubagentRecord = { ...existing, events: existing.events.slice() };
  if (evt.parent_id != null) rec.parent_id = evt.parent_id;
  if (evt.depth != null) rec.depth = evt.depth;
  if (evt.model != null) rec.model = evt.model;
  if (evt.task_index != null) rec.task_index = evt.task_index;
  if (evt.task_count != null) rec.task_count = evt.task_count;
  const ev = evt.event ?? "";
  if (ev === "start") {
    rec.goal = evt.goal || rec.goal;
    rec.status = "running";
  } else if (ev === "complete") {
    rec.status = evt.status || "ok";
    rec.output = evt.output || rec.output;
    rec.ended_at = ts;
    if (evt.duration_seconds != null) rec.duration_seconds = evt.duration_seconds;
  }
  if (rec.events.length < SUBAGENT_MAX_TIMELINE) {
    rec.events.push({
      event: ev, ts,
      text: evt.text, tool_name: evt.tool_name, preview: evt.preview,
      goal: evt.goal, status: evt.status, output: evt.output,
      duration_seconds: evt.duration_seconds,
    });
  }
  return { ...prev, [sid]: rec };
}

function subagentStatusColor(status: string): string {
  if (status === "running") return "var(--red)";    // actively working — draws the eye
  if (status === "ok") return "var(--green)";        // finished successfully
  return "var(--yellow)";  // error | timeout | failed — distinct from running red
}

// A new delegation round is assumed to begin sooner than this after the previous
// subagent started — within a batch, children spawn ~simultaneously; between
// rounds the parent must aggregate + re-delegate, which takes longer.
const ROUND_GAP_SECONDS = 4;     // gap that marks a new wave of delegation
const SAME_WAVE_SECONDS = 1.5;   // within this, spawns count as one parallel wave

/**
 * Cluster subagents (sorted by start time) into delegation rounds. Hermes emits
 * no round marker, so we infer a "wave" of spawns from start-time clustering:
 * subagents that start within ~SAME_WAVE_SECONDS of the previous one belong to
 * the same round (whether they came from one batch `delegate_task` call or from
 * several single-goal calls fired in parallel in the same turn). A larger gap —
 * the parent aggregating before delegating again — starts a new round. A
 * `task_index` reset (the per-call counter dropping) only starts a new round
 * when it ALSO coincides with a real gap, so genuinely parallel single-goal
 * calls (all task_index 0, near-simultaneous) stay in one round instead of
 * each becoming its own. Returns rounds of `{ rec, index }`, `index` being the
 * stable global spawn order.
 */
export function groupSubagentsIntoRounds(
  list: SubagentRecord[],
): { rec: SubagentRecord; index: number }[][] {
  const sorted = list
    .map((rec, index) => ({ rec, index }))
    .sort((a, b) => (a.rec.started_at ?? 0) - (b.rec.started_at ?? 0))
    // re-stamp the global index by start order so numbering stays 1..N stable
    .map((x, i) => ({ rec: x.rec, index: i }));
  const rounds: { rec: SubagentRecord; index: number }[][] = [];
  let prev: SubagentRecord | null = null;
  for (const item of sorted) {
    const s = item.rec;
    const gap = prev ? (s.started_at ?? 0) - (prev.started_at ?? 0) : Infinity;
    const indexReset =
      prev != null && s.task_index != null && prev.task_index != null
        ? s.task_index <= prev.task_index
        : false;
    const newRound =
      rounds.length === 0 ||
      gap > ROUND_GAP_SECONDS ||
      (indexReset && gap > SAME_WAVE_SECONDS);
    if (newRound) rounds.push([item]);
    else rounds[rounds.length - 1].push(item);
    prev = s;
  }
  return rounds;
}

/** Renders one subagent's input (goal), live tool/thinking timeline, and output. */
function SubagentView({ rec }: { rec: SubagentRecord }) {
  const dur = rec.duration_seconds != null ? ` · ${rec.duration_seconds}s` : "";
  const meta = [rec.model, rec.depth != null ? `depth ${rec.depth}` : null]
    .filter(Boolean).join(" · ");
  const steps = rec.events.filter((e) => e.event !== "start" && e.event !== "complete");
  return (
    <div style={{ padding: "10px 12px", fontSize: 12, lineHeight: 1.5 }}>
      {/* Input */}
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5,
                    color: "var(--text-dim)", marginBottom: 4 }}>Task (input)</div>
      <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word",
                    background: "var(--bg2)", border: "1px solid var(--card-border)",
                    borderRadius: 6, padding: "8px 10px", marginBottom: 4 }}>
        {rec.goal || <span style={{ color: "var(--text-dim)" }}>(no task text)</span>}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-dim)", marginBottom: 10 }}>
        <span style={{ color: subagentStatusColor(rec.status), fontWeight: 600 }}>
          {rec.status}
        </span>{dur}{meta ? ` · ${meta}` : ""}
      </div>

      {/* Timeline */}
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5,
                    color: "var(--text-dim)", marginBottom: 4 }}>Activity</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, marginBottom: 10 }}>
        {steps.length === 0 ? (
          <span style={{ color: "var(--text-dim)" }}>Waiting for the subagent to act…</span>
        ) : steps.map((e, i) => {
          const icon = e.event === "thinking" ? "💭" : e.event === "progress" ? "🔀" : "🔧";
          const head = e.event === "tool" ? (e.tool_name || "tool") : e.event;
          const body = e.text || e.preview || "";
          return (
            <div key={i} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
              <span>{icon}</span>
              <span style={{ minWidth: 0 }}>
                <span style={{ fontWeight: 600 }}>{head}</span>
                {body && (
                  <span style={{ color: "var(--text-dim)", marginLeft: 6,
                                 whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                    {body.length > 300 ? body.slice(0, 300) + "…" : body}
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </div>

      {/* Output */}
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5,
                    color: "var(--text-dim)", marginBottom: 4 }}>Output</div>
      <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word",
                    background: "var(--bg2)", border: "1px solid var(--card-border)",
                    borderRadius: 6, padding: "8px 10px",
                    color: rec.output ? "inherit" : "var(--text-dim)" }}>
        {rec.output || (rec.status === "running" ? "Running…" : "(no output)")}
      </div>
    </div>
  );
}

/**
 * A spawned subagent rendered as its own desk: a compact bubble (scaled avatar +
 * status) that sits to the right of the parent desk and expands on click into a
 * floating panel — anchored to the bubble and reusing the desk panel's
 * drag/resize/anchor machinery — showing the subagent's trace in an Activity tab.
 */
export function SubagentDesk({ rec, index }: { rec: SubagentRecord; index: number }) {
  const { root: panelRoot } = useTeamRowPanel();
  const rowRef = useRef<HTMLElement | null>(null);
  rowRef.current = panelRoot;
  const bubbleRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [panelDeskOffset, setPanelDeskOffset] = useState<DeskOffset | null>(null);
  const [z, setZ] = useState(DESK_PANEL_Z_BASE + 100);
  const onDragCommitRef = useRef<(vp: { top: number; left: number }) => void>(() => {});
  const { pos: dragPos, dragging, bindHandle } = usePanelDrag(12, (vp) => onDragCommitRef.current(vp));
  const { size: userSize, resizing, bindResize } = usePanelResize(SUB_PANEL_MIN);
  onDragCommitRef.current = (vp) => {
    const el = bubbleRef.current;
    if (el) setPanelDeskOffset(deskOffsetFromViewport(el, vp));
  };
  const rowPos = useDeskAnchoredRowPosition(
    bubbleRef, rowRef, panelDeskOffset, expanded && !dragging && !!panelRoot,
  );

  const archetype = SUB_ARCHETYPES[index % SUB_ARCHETYPES.length];
  const color = SUB_COLORS[index % SUB_COLORS.length];
  const goal = (rec.goal || "").trim().replace(/\s+/g, " ");
  const goalShort = goal.length > 44 ? goal.slice(0, 44) + "…" : goal;

  const w = userSize?.width ?? SUB_PANEL_W;
  const h = userSize?.height ?? SUB_PANEL_H;

  function bringForward() {
    subZ.current += 1;
    setZ(subZ.current);
  }
  function openPanel() {
    const el = bubbleRef.current;
    if (el) setPanelDeskOffset(defaultBelowDeskOffset(el, w));
    bringForward();
    setExpanded(true);
  }
  function getTopLeft(): { top: number; left: number } {
    if (dragging && dragPos) return dragPos;
    if (rowPos && panelRoot) return rowToViewport(panelRoot, rowPos);
    return { top: 0, left: 0 };
  }
  const dragHandle = bindHandle(getTopLeft);
  const resizeHandle = bindResize(() => ({ width: w, height: h }));
  const tl = getTopLeft();

  const panel = expanded && panelDeskOffset && panelRoot && rowPos ? createPortal(
    <div
      style={{
        position: "fixed", top: tl.top, left: tl.left, width: w, height: h, maxHeight: h,
        background: "var(--bg2)", border: "1px solid var(--card-border)", borderRadius: 8,
        overflow: "hidden", boxShadow: "0 8px 32px rgba(0,0,0,0.6)", zIndex: z,
        display: "flex", flexDirection: "column",
        transition: (dragging || resizing) ? "none" : "width 0.18s ease, height 0.18s ease, top 0.18s ease, left 0.18s ease",
      }}
      onMouseDown={(e) => { e.stopPropagation(); bringForward(); }}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header — drag handle + identity + close */}
      <div
        {...dragHandle}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          borderBottom: "1px solid var(--card-border)", padding: "6px 8px", flexShrink: 0,
          cursor: dragging ? "grabbing" : "grab",
        }}
        title="Drag to move"
      >
        <span style={{
          width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
          background: subagentStatusColor(rec.status),
        }} />
        <span style={{ fontWeight: 600, fontSize: 12, whiteSpace: "nowrap" }}>
          🔀 Subagent {index + 1}
        </span>
        <span style={{
          fontSize: 11, color: "var(--text-dim)", flex: 1, minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {goalShort}
        </span>
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(false); }}
          title="Close"
          style={{ fontSize: 15, lineHeight: 1, color: "var(--text-dim)", padding: "0 4px", flexShrink: 0 }}
        >
          ×
        </button>
      </div>

      {/* Activity tab (single tab for now; mirrors the desk panel's tab bar) */}
      <div style={{
        display: "flex", borderBottom: "1px solid var(--card-border)",
        padding: "0 0 0 8px", flexShrink: 0,
      }}>
        <span style={{
          padding: "8px 10px", fontSize: 12, fontWeight: 600, color: "var(--accent2)",
          borderBottom: "2px solid var(--accent2)", marginBottom: -1,
        }}>
          ⚡ Activity
        </span>
      </div>

      {/* Body — the subagent's trace */}
      <div style={{ flex: 1, overflowY: "auto", minHeight: 120 }}>
        <SubagentView rec={rec} />
      </div>

      <PanelResizeHandle active={resizing} bind={resizeHandle} />
    </div>,
    document.body,
  ) : null;

  return (
    <div ref={bubbleRef} style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <button
        onClick={(e) => { e.stopPropagation(); expanded ? setExpanded(false) : openPanel(); }}
        title={goal || `Subagent ${index + 1}`}
        style={{
          display: "flex", flexDirection: "column", alignItems: "center",
          padding: 4, borderRadius: 10, cursor: "pointer",
          background: expanded ? "rgba(255,255,255,0.06)" : "transparent",
          border: `1px solid ${expanded ? color : "transparent"}`,
        }}
      >
        <div style={{ position: "relative", lineHeight: 0 }}>
          <AgentFigure
            scale={0.42}
            archetype={archetype}
            color={color}
            state={rec.status === "running" ? "working" : "idle"}
          />
          <span style={{
            position: "absolute", bottom: 0, right: -2, width: 9, height: 9,
            borderRadius: "50%", background: subagentStatusColor(rec.status),
            border: "1px solid var(--bg2)",
          }} />
        </div>
        <span style={{
          fontSize: 9, color: expanded ? color : "var(--text-dim)", maxWidth: 64,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>
          Sub {index + 1}
        </span>
      </button>
      {panel}
    </div>
  );
}
