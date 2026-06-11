/**
 * FloorManager — team manager patrol component (component name kept for stable imports).
 *
 * Path (starting/ending at her staging desk):
 *   1. Rise from staging desk
 *   2. Walk RIGHT at agent-row height, stopping at each desk to inspect
 *   3. After last desk: walk DOWN to the return corridor (below desk bodies)
 *   4. Walk LEFT (slowly) back to staging X at return-row height
 *   5. Walk UP back to patrol height
 *   6. Sit back down at staging desk
 *
 * Inspection logic (two paths):
 *   Auto patrol (timer):
 *     - Active / recent (< idleGraceSec): nod "✓ looking good", no audit
 *     - Server confirms is_running before audit/resume (guards stale poll)
 *     - Idle incomplete desks: audit → nudge via resume (bounded)
 *   "Ask manager for guidance" (priority visit, force audit):
 *     - Always runs a fresh audit, even on a running desk
 *     - Writes AUDIT.md / shows verdict — never resume/kill a running turn
 *
 * Auto-trigger: fires every `patrolIntervalSec` seconds when any non-running
 * session desk exists and manager is at her staging desk.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import type { DeskItem, Session } from "../types";
import { MANAGER_MSG_PREFIX } from "../types";
import { api } from "../api/client";
import { ManagerFigure } from "./ManagerFigure";
import type { ManagerState, ManagerDirection } from "./ManagerFigure";

// ── Types ─────────────────────────────────────────────────────────────────────

type Phase =
  | "at-staging"       // idle at her desk — fixed overlay hidden, staging renders figure
  | "leaving"          // walking right away from staging to first desk
  | "walking-to-desk"  // walking between desks
  | "at-desk"          // stopped, inspecting
  | "walking-down"     // descending to return corridor
  | "returning"        // walking left (slow) back towards staging
  | "walking-up";      // ascending back to patrol height at staging

type Verdict = "active" | "recent" | "done" | "incomplete" | "unclear";

interface InspectionResult {
  verdict: Verdict;
  unfinishedTasks?: string[];
  recentActivity?: string;
}

export interface Props {
  desks: DeskItem[];
  deskRefs: React.RefObject<Map<number, HTMLDivElement>>;
  scrollRef: React.RefObject<HTMLDivElement>;
  stagingRef: React.RefObject<HTMLDivElement>;
  enabled: boolean;
  patrolIntervalSec: number;
  idleGraceSec: number;
  reasoningEffort?: import("../types").ReasoningEffort;
  apiMode?: import("../types").ApiMode;
  askManagerDeskId?: string | null;
  onAskManagerDone?: () => void;
  onPatrolChange?: (onPatrol: boolean) => void;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const FIGURE_W = 40;
const FIGURE_H = 60;

// Desk strip layout constants (must match Office.tsx padding values)
const STRIP_PADDING_TOP = 72;   // paddingTop on the scroll container
const AGENT_AREA_H      = 80;   // height of the agent-figure area above each desk
const DESK_BODY_H       = 240;  // height of the desk body (TaskDesk)
const DESK_BODY_GAP     = 4;    // marginBottom under agent area before desk body

// ── Helpers ───────────────────────────────────────────────────────────────────

function isSession(d: DeskItem): d is Session {
  return !("isPending" in d);
}

/** Match TaskDesk: undefined is_running counts as running unless ended. */
function deskIsRunning(session: Session): boolean {
  return !session.ended_at && session.is_running !== false;
}

/** Authoritative is_running from the server (5s poll can lag mid-turn). */
async function deskIsRunningLive(session: Session): Promise<boolean> {
  if (deskIsRunning(session)) return true;
  try {
    const live = await api.sessions.get(session.id);
    return !live.ended_at && live.is_running === true;
  } catch {
    return deskIsRunning(session);
  }
}

async function inspectDesk(session: Session, idleThresholdMs: number): Promise<InspectionResult> {
  if (deskIsRunning(session)) return { verdict: "active" };
  if (await deskIsRunningLive(session)) return { verdict: "active" };

  let events: import("../types").ActivityEvent[] = [];
  try { events = await api.sessions.activity(session.id, 20); } catch { /* ok */ }

  if (events.length > 0) {
    const lastMs = new Date(events[events.length - 1].timestamp).getTime();
    if (Date.now() - lastMs < idleThresholdMs) return { verdict: "recent" };
  }

  let taskContent = "";
  try {
    const tf = await api.sessions.taskFile.get(session.id);
    taskContent = tf.content ?? "";
  } catch { /* no task file */ }

  const unchecked = taskContent.match(/^[-*] \[ \] .+/gm) ?? [];
  const checked   = (taskContent.match(/^[-*] \[x\] .+/gim) ?? []).length;

  if (unchecked.length > 0) {
    return {
      verdict: "incomplete",
      unfinishedTasks: unchecked.map(l => l.replace(/^[-*] \[ \] /, "").trim()).slice(0, 3),
    };
  }
  if (checked > 0 || events.length > 0) return { verdict: "done" };

  const recentActivity = events.slice(-3).map(e => e.title || e.tool_name).filter(Boolean).join(", ");
  return { verdict: "unclear", recentActivity };
}

// ── Component ─────────────────────────────────────────────────────────────────

export function FloorManager({
  desks, deskRefs, scrollRef, stagingRef, enabled, patrolIntervalSec, idleGraceSec,
  reasoningEffort, apiMode,
  askManagerDeskId, onAskManagerDone, onPatrolChange,
}: Props) {
  const [figX,     setFigX]     = useState(-200);
  const [figY,     setFigY]     = useState(-200);
  const [figState, setFigState] = useState<ManagerState>("idle");
  const [figDir,   setFigDir]   = useState<ManagerDirection>("right");
  const [bubble,   setBubble]   = useState<string | null>(null);
  const [visible,  setVisible]  = useState(false);
  const [walkDur,  setWalkDur]  = useState("0ms");  // CSS transition duration string
  // When true, the figure jumps without the walk transition — used so scroll keeps
  // her glued to the desks instead of gliding across the room.
  const [instant,  setInstant]  = useState(false);

  const phaseRef           = useRef<Phase>("at-staging");
  const runningRef         = useRef(true);
  const desksRef           = useRef(desks);
  desksRef.current         = desks;
  const patrolIntervalRef = useRef(patrolIntervalSec);
  patrolIntervalRef.current = patrolIntervalSec;
  const idleGraceRef = useRef(idleGraceSec);
  idleGraceRef.current = idleGraceSec;
  const patrolTimerRef     = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Coordinate helpers ─────────────────────────────────────────────────────

  /** Y for the outbound patrol row — same visual level as the agent figures */
  const getPatrolY = useCallback((): number => {
    const el = scrollRef.current;
    if (!el) return 60;
    // Agent figures bottom-align within the 80-px agent area (paddingTop: 72).
    // AgentFigure is 60 px tall → figure top = strip.top + 72 + (80-60) = strip.top + 92.
    return el.getBoundingClientRect().top + STRIP_PADDING_TOP + (AGENT_AREA_H - FIGURE_H);
  }, [scrollRef]);

  /** Y for the return corridor — just below the desk bodies, not at screen bottom */
  const getReturnY = useCallback((): number => {
    const el = scrollRef.current;
    if (!el) return window.innerHeight - 120;
    const stripTop = el.getBoundingClientRect().top;
    // Desk bodies end at stripTop + 72 + 80 + 4 + 240 = stripTop + 396.
    // Walk 30px above the desk bottom so she's visually "just below" the desk faces.
    const deskBottom = stripTop + STRIP_PADDING_TOP + AGENT_AREA_H + DESK_BODY_GAP + DESK_BODY_H;
    return deskBottom - 50;
  }, [scrollRef]);

  /** X of the manager staging area, centred on the figure */
  const getStagingX = useCallback((): number => {
    const el = stagingRef.current;
    if (!el) return 170;
    const r = el.getBoundingClientRect();
    return r.left + r.width / 2 - FIGURE_W / 2;
  }, [stagingRef]);

  const getDeskX = useCallback((idx: number): number | null => {
    const el = deskRefs.current?.get(idx);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    // Stop one avatar-width to the left of centre so the manager stands beside the agent
    return r.left + r.width / 2 - FIGURE_W / 2 - FIGURE_W;
  }, [deskRefs]);

  // ── Low-level walk helper ─────────────────────────────────────────────────

  const walkTo = useCallback((
    toX: number, toY: number,
    dir: ManagerDirection,
    dur: number,   // ms
  ): Promise<void> => new Promise(resolve => {
    if (!runningRef.current) { resolve(); return; }
    const durStr = `${dur}ms`;
    setInstant(false);   // a real walk → animate the move
    setFigDir(dir);
    setFigState("walking");
    setWalkDur(durStr);
    // Set target first so CSS transition can pick it up
    requestAnimationFrame(() => {
      setFigX(toX);
      setFigY(toY);
    });
    setTimeout(() => { if (runningRef.current) resolve(); }, dur);
  }), []);

  const pause = (ms: number): Promise<void> =>
    new Promise(resolve => setTimeout(resolve, ms));

  /** Walk horizontally at outbound speed (px → ms multiplier = 2.0) */
  const walkHOut = useCallback((toX: number, curX: number, y: number, dir: ManagerDirection) => {
    const ms = Math.max(400, Math.min(2000, Math.abs(toX - curX) * 2.0));
    return walkTo(toX, y, dir, ms);
  }, [walkTo]);

  /** Walk horizontally at return speed (px → ms multiplier = 3.5 — noticeably slower) */
  const walkHReturn = useCallback((toX: number, curX: number, y: number, dir: ManagerDirection) => {
    const ms = Math.max(600, Math.min(4000, Math.abs(toX - curX) * 3.5));
    return walkTo(toX, y, dir, ms);
  }, [walkTo]);

  /** Walk vertically */
  const walkV = useCallback((x: number, toY: number, curY: number, dir: ManagerDirection) => {
    const ms = Math.max(300, Math.min(1500, Math.abs(toY - curY) * 2.2));
    return walkTo(x, toY, dir, ms);
  }, [walkTo]);

  const scrollToDeskAndWait = useCallback(async (_idx: number) => {
    // Do not auto-scroll — let the user stay wherever they are.
  }, []);

  // ── Main patrol ───────────────────────────────────────────────────────────

  const runPatrol = useCallback(async (prioritySessionId?: string | null) => {
    if (!runningRef.current) return;
    if (phaseRef.current !== "at-staging") return;

    const sessionDesks = desksRef.current.filter(isSession);
    if (sessionDesks.length === 0) return;

    const manualPatrol = prioritySessionId === "__manual__";
    const deskPriorityId = manualPatrol ? null : prioritySessionId;

    onPatrolChange?.(true);
    try {
    const patrolY  = getPatrolY();
    const returnY  = getReturnY();
    const stagingX = getStagingX();

    // ── Rise from staging desk ─────────────────────────────────────────────
    setVisible(true);
    setWalkDur("0ms");
    setFigX(stagingX);
    setFigY(patrolY);
    setFigDir("right");
    setFigState("idle");
    phaseRef.current = "leaving";
    await pause(300);

    // ── Build visit order ─────────────────────────────────────────────────
    const allIdxs: number[] = desksRef.current
      .map((d, i) => (isSession(d) ? i : -1))
      .filter(i => i >= 0);

    let visitOrder = allIdxs;
    if (deskPriorityId) {
      const prioIdx = desksRef.current.findIndex(
        d => isSession(d) && (d as Session).id === deskPriorityId
      );
      if (prioIdx >= 0) visitOrder = [prioIdx, ...allIdxs.filter(i => i !== prioIdx)];
    }

    let curX = stagingX;

    // ── Walk desk-to-desk ─────────────────────────────────────────────────
    for (const deskIdx of visitOrder) {
      if (!runningRef.current) return;

      await scrollToDeskAndWait(deskIdx);
      const deskX = getDeskX(deskIdx);
      if (deskX === null) continue;

      phaseRef.current = "walking-to-desk";
      await walkHOut(deskX, curX, patrolY, deskX >= curX ? "right" : "left");
      curX = deskX;

      phaseRef.current = "at-desk";
      setFigState("inspecting");
      setFigDir("right");

      const desk = desksRef.current[deskIdx];
      if (!isSession(desk)) continue;

      // Already audited green and unchanged since → just a nod, never re-audit.
      // (Unless the user explicitly asked the manager to revisit this desk.)
      if (desk.task_solved && deskPriorityId !== desk.id) {
        setBubble("✓ solved");
        setFigState("idle");
        await pause(700);
        setBubble(null);
        continue;
      }

      await pause(1000);
      if (!runningRef.current) return;

      const isPriority = deskPriorityId === desk.id;
      const result = await inspectDesk(desk, idleGraceRef.current * 1000);

      // Still working (or just was) → a quick glance, don't tie up the model.
      // Explicit "Ask manager" bypasses this and always audits below.
      if (!isPriority && (result.verdict === "active" || result.verdict === "recent")) {
        setBubble("✓ looking good");
        setFigState("idle");
        await pause(700);
        setBubble(null);
        continue;
      }

      // Auto patrol: live server check before spending ~60s on an audit.
      if (!isPriority && await deskIsRunningLive(desk)) {
        setBubble("✓ looking good");
        setFigState("idle");
        await pause(700);
        setBubble(null);
        continue;
      }

      // ── Desk looks finished/idle (or user asked) → get an audit verdict ──

      // audit() is cheap when the work is unchanged (returns the cached verdict,
      // no LLM call) and runs a fresh ~60s audit otherwise. An explicit "Ask
      // manager" click forces a fresh audit.
      setBubble("📋 Auditing tasks & outputs…");
      setFigState("inspecting");
      let audit = await api.sessions.audit(desk.id, isPriority).catch(() => null);
      // If the live audit failed (e.g. the model returned unparseable JSON), fall
      // back to the last good verdict so known issues aren't lost.
      if (!audit) audit = await api.sessions.auditCached(desk.id).catch(() => null);
      if (!runningRef.current) return;

      // Auto patrol: server skipped audit because the turn is still in flight.
      if (!isPriority && audit?.skipped_running) {
        setBubble("✓ looking good");
        setFigState("idle");
        await pause(700);
        setBubble(null);
        continue;
      }

      // No verdict available at all (never audited / model unreachable). Crucially
      // we do NOT claim "all good" here — that would hide an incomplete task.
      if (!audit || audit.summary.total === 0) {
        setBubble("Couldn't audit just now");
        setFigState("idle");
        await pause(1000);
        setBubble(null);
        if (isPriority) onAskManagerDone?.();
        continue;
      }

      const failures = audit.results.filter(r => r.verdict === "fail" || r.verdict === "unsure");

      // All criteria pass → approve and move on. No message to the agent.
      if (failures.length === 0) {
        setBubble(`All ${audit.summary.total} checks passed ✓`);
        setFigState("idle");
        await pause(1300);
        setBubble(null);
        if (isPriority) onAskManagerDone?.();
        continue;
      }

      // ── Issues found (recorded in AUDIT.md server-side) → surface the top one ─
      const top = failures[0];
      const topMsg = (top.fix_hint || top.criterion || "needs work").trim();
      setFigState("writing");
      setBubble(`Needs work: ${topMsg.length > 70 ? topMsg.slice(0, 70) + "…" : topMsg}`);
      await pause(2400);

      // If the agent is still actively working this turn, don't resume/kill it.
      // Auto patrol should never reach here (guards above); "Ask manager" may
      // audit a running desk for guidance — AUDIT.md is written, agent keeps going.
      if (await deskIsRunningLive(desk)) {
        setBubble(isPriority
          ? `Working on it · ${audit.summary.passed}/${audit.summary.total}`
          : "✓ looking good");
        setFigState("idle");
        await pause(1400);
        setBubble(null);
        if (isPriority) onAskManagerDone?.();
        continue;
      }

      // Loop guard: keep nudging while the agent makes progress; once it's stuck
      // (no improvement across attempts) escalate to the human instead of looping.
      if (!audit.should_intervene) {
        setBubble(`Stuck at ${audit.summary.passed}/${audit.summary.total} — needs human review 🚩`);
        setFigState("idle");
        await pause(1600);
        setBubble(null);
        if (isPriority) onAskManagerDone?.();
        continue;
      }

      setBubble(`${failures.length} issue${failures.length > 1 ? "s" : ""} noted ✏️`);
      await pause(900);
      setBubble(null);

      setFigState("poking");
      setFigDir("right");
      await pause(350);
      setBubble("Let's fix these 👋");
      await pause(800);
      setBubble(null);
      await pause(350);
      setFigState("idle");

      if (isPriority) onAskManagerDone?.();
      // Manager-tagged message (prefixed) so the feed renders it distinctly — never
      // like a human query — and only ever sent when issues were actually found.
      // Feedback lives in AUDIT.md (manager-owned; the agent should read, not edit).
      // The manager is an explicit patrol, so it may revive a paused/slept desk —
      // wake first (a sleeping desk rejects resume with 423).
      if (desk.is_sleeping) await api.sessions.wake(desk.id).catch(() => {});
      api.sessions.resume(desk.id,
        MANAGER_MSG_PREFIX +
        "I audited your work and found some issues. Read AUDIT.md in your workspace " +
        "(my feedback — please don't edit that file) and fix the listed problems.",
        undefined, undefined, reasoningEffort, apiMode)
        .catch(() => {});
      await pause(250);
    }

    // ── Descend to return corridor ────────────────────────────────────────
    phaseRef.current = "walking-down";
    await walkV(curX, returnY, patrolY, "right");

    // ── Walk left slowly back to staging X ───────────────────────────────
    phaseRef.current = "returning";
    await walkHReturn(stagingX, curX, returnY, "left");

    // ── Rise back up to patrol row ────────────────────────────────────────
    phaseRef.current = "walking-up";
    await walkV(stagingX, patrolY, returnY, "left");

    // ── Sit back at staging ───────────────────────────────────────────────
    setFigState("idle");
    await pause(200);
    } finally {
      setVisible(false);
      setBubble(null);
      setFigState("idle");
      phaseRef.current = "at-staging";
      onPatrolChange?.(false);
      if (prioritySessionId) onAskManagerDone?.();
    }
  }, [
    getDeskX, getPatrolY, getReturnY, getStagingX,
    onAskManagerDone, onPatrolChange, scrollToDeskAndWait,
    walkHOut, walkHReturn, walkV,
  ]);

  // Keep a ref so the idle timer always calls the latest version
  const runPatrolRef = useRef(runPatrol);
  runPatrolRef.current = runPatrol;

  // ── On enable/disable ─────────────────────────────────────────────────────

  useEffect(() => {
    if (!enabled) {
      runningRef.current = false;
      setVisible(false);
      setBubble(null);
      setFigState("idle");
      phaseRef.current = "at-staging";
      onPatrolChange?.(false);
      return;
    }
    runningRef.current = true;
    return () => {
      runningRef.current = false;
      if (patrolTimerRef.current) clearTimeout(patrolTimerRef.current);
    };
  }, [enabled, onPatrolChange]);

  // ── Auto-trigger idle timer ────────────────────────────────────────────────

  useEffect(() => {
    if (!enabled) return;
    const iv = setInterval(() => {
      if (phaseRef.current !== "at-staging") return;
      // Only patrol when a desk actually needs attention: idle (not running) and
      // not already audited-clean. If every desk is solved (or busy), the manager
      // stays put — no pointless laps once the whole team is checked green.
      const needsAttention = desksRef.current.some(
        d => isSession(d) && !(d as Session).is_running && !(d as Session).task_solved
      );
      if (needsAttention) runPatrolRef.current();
    }, patrolIntervalSec * 1000);
    return () => clearInterval(iv);
  }, [enabled, patrolIntervalSec]);

  // ── Keep the figure glued to the desks while the page/desk-strip scrolls ──
  // Her coordinates are viewport-relative (position: fixed), so without this she
  // would stay put on screen while the desks scroll away — looking like she flies
  // across the room. We shift her by each scroll container's delta instead.
  useEffect(() => {
    if (!enabled) return;
    const last = new WeakMap<EventTarget, { top: number; left: number }>();
    const onScroll = (e: Event) => {
      // Only react to scrollers that actually move the desks: the desk strip itself
      // or an ancestor that contains it (the office vertical scroller / the page).
      // Ignore inner panels like the activity feed, file tree, terminal.
      const stripEl = scrollRef.current;
      const t = e.target as Node;
      const affectsDesks =
        t === document ||
        (!!stripEl && (t === stripEl || (t.nodeType === 1 && (t as Element).contains(stripEl))));
      if (!affectsDesks) return;
      const el = e.target as HTMLElement | Document;
      const top = el === document ? window.scrollY : (el as HTMLElement).scrollTop;
      const leftPos = el === document ? window.scrollX : (el as HTMLElement).scrollLeft;
      const prev = last.get(e.target as EventTarget) ?? { top, left: leftPos };
      const dTop = top - prev.top;
      const dLeft = leftPos - prev.left;
      last.set(e.target as EventTarget, { top, left: leftPos });
      if (dTop === 0 && dLeft === 0) return;
      setInstant(true);                       // move with the content, no glide
      setFigX(x => x - dLeft);
      setFigY(y => y - dTop);
    };
    window.addEventListener("scroll", onScroll, true);  // capture: catch inner scrollers
    return () => window.removeEventListener("scroll", onScroll, true);
  }, [enabled]);

  // ── Priority visit triggered by "Ask manager" button ─────────────────────

  useEffect(() => {
    if (!askManagerDeskId || !enabled) return;
    if (phaseRef.current !== "at-staging") return;
    if (patrolTimerRef.current) { clearTimeout(patrolTimerRef.current); patrolTimerRef.current = null; }
    runPatrol(askManagerDeskId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askManagerDeskId]);

  // ── Render ────────────────────────────────────────────────────────────────

  if (!enabled || !visible) return null;

  return (
    <>
      <style>{`
        @keyframes mgpop {
          from { opacity:0; transform:translateX(-50%) scale(0.8); }
          to   { opacity:1; transform:translateX(-50%) scale(1); }
        }
      `}</style>
      <div style={{
        position: "fixed",
        left: figX,
        top:  figY,
        transition: (figState === "walking" && !instant)
          ? `left ${walkDur} linear, top ${walkDur} ease-in-out`
          : "none",
        zIndex: 150,
        pointerEvents: "none",
        userSelect: "none",
      }}>
        {bubble && (
          <div style={{
            position: "absolute",
            bottom: "calc(100% + 5px)",
            left: "50%",
            transform: "translateX(-50%)",
            background: "#1a1a40",
            color: "#e8e8ff",
            border: "1px solid rgba(100,100,255,0.4)",
            borderRadius: 10,
            padding: "4px 9px",
            fontSize: 10, fontWeight: 600,
            whiteSpace: "nowrap",
            boxShadow: "0 2px 10px rgba(0,0,0,0.4)",
            zIndex: 151,
            animation: "mgpop 0.15s ease-out",
          }}>
            {bubble}
            <div style={{
              position: "absolute", bottom: -5, left: "50%",
              transform: "translateX(-50%)",
              borderLeft: "5px solid transparent",
              borderRight: "5px solid transparent",
              borderTop: "5px solid #1a1a40",
            }} />
          </div>
        )}
        <ManagerFigure state={figState} direction={figDir} scale={1} />
      </div>
    </>
  );
}
