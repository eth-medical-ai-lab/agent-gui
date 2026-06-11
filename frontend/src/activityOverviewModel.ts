import type { ActivityEvent } from "./types";

export type Cat = "reasoning" | "tool" | "generating" | "idle";

export interface OverviewTurn {
  start: number;
  end: number;
  counts: { reasoning: number; tool: number; generating: number };
  /** Agent-event categories in the order they occurred within the turn. The
   *  overview lays these out across the turn's active window so each time bucket
   *  reflects the events that fall in it (per-event timestamps are unreliable —
   *  Hermes batch-flushes — but event order is). */
  seq: Exclude<Cat, "idle">[];
  task: string;
}

export const MIN_TURN_SEC = 1;

export function tsSec(s: string): number {
  const t = Date.parse(s);
  return Number.isNaN(t) ? NaN : t / 1000;
}

function eventTimeBounds(events: ActivityEvent[]): { min: number; max: number } {
  let min = Infinity;
  let max = -Infinity;
  for (const e of events) {
    const t = tsSec(e.timestamp);
    if (Number.isFinite(t)) {
      min = Math.min(min, t);
      max = Math.max(max, t);
    }
  }
  return { min, max };
}

export function eventCat(ev: ActivityEvent): Exclude<Cat, "idle"> {
  if (ev.event_type === "tool_result") return "tool";
  if (ev.event_type === "thinking_start") return "reasoning";
  return "generating";
}

function emptyCounts() {
  return { reasoning: 0, tool: 0, generating: 0 };
}

function syntheticUserEvent(text: string, startSec: number): ActivityEvent {
  return {
    timestamp: new Date(startSec * 1000).toISOString(),
    event_type: "user_message",
    icon: "👤",
    title: "User",
    detail: text,
    tool_name: "",
    is_error: false,
    files_touched: [],
  };
}

/** Build ordered turns for the overview chart (see ActivityOverview.tsx). */
export function buildOverviewTurns(
  events: ActivityEvent[],
  opts: {
    endTime?: number;
    startTime?: string;
    /** Latest desk activity (ISO) from the overview API — not session.ended_at. */
    deskEndTime?: string;
    taskContent?: string | null;
    liveEvents?: ActivityEvent[];
  } = {},
): OverviewTurn[] {
  const { endTime, startTime, deskEndTime, taskContent, liveEvents = [] } = opts;
  const merged = [...events, ...liveEvents];
  const bounds = eventTimeBounds(merged);

  const hasUser = merged.some((e) => e.event_type === "user_message");
  const deskStartSec = tsSec(startTime ?? "");
  const eventMin = Number.isFinite(bounds.min) ? bounds.min : NaN;
  // Desk started_at is the floor when Hermes batch-flush timestamps are all identical.
  const spanStart = Number.isFinite(eventMin) && Number.isFinite(deskStartSec)
    ? Math.min(eventMin, deskStartSec)
    : Number.isFinite(eventMin)
      ? eventMin
      : Number.isFinite(deskStartSec)
        ? deskStartSec
        : NaN;
  const fallbackStart = Number.isFinite(spanStart) ? spanStart : Date.now() / 1000 - 60;

  const feed: ActivityEvent[] = [...merged];
  if (!hasUser && taskContent?.trim()) {
    feed.unshift(syntheticUserEvent(taskContent.trim(), fallbackStart));
  }

  const out: OverviewTurn[] = [];
  let cur: OverviewTurn | null = null;
  for (const e of feed) {
    if (e.event_type === "user_message") {
      let t = tsSec(e.timestamp);
      if (!Number.isFinite(t)) t = cur?.end ?? fallbackStart;
      if (cur) cur.end = Math.max(cur.end, t, cur.start + MIN_TURN_SEC);
      cur = {
        start: t,
        end: t,
        counts: emptyCounts(),
        seq: [],
        task: e.detail.trim().slice(0, 80) || "(task)",
      };
      out.push(cur);
    } else if (cur) {
      const c = eventCat(e);
      cur.counts[c] += 1;
      cur.seq.push(c);
    }
  }

  // Agent activity with no user anchor — one synthetic turn from session start.
  if (!out.length) {
    const agentish = feed.filter((e) => e.event_type !== "user_message");
    if (agentish.length > 0 || taskContent?.trim()) {
      const counts = emptyCounts();
      const seq: Exclude<Cat, "idle">[] = [];
      for (const e of agentish) {
        const c = eventCat(e);
        counts[c] += 1;
        seq.push(c);
      }
      out.push({
        start: fallbackStart,
        end: fallbackStart,
        counts,
        seq,
        task: taskContent?.trim().slice(0, 80) || "(task)",
      });
    }
  }

  const now = Date.now() / 1000;
  const deskEndSec = typeof endTime === "number" ? endTime : tsSec(deskEndTime ?? "");
  const end = Number.isFinite(deskEndSec)
    ? Math.max(deskEndSec, now)
    : Number.isFinite(bounds.max)
      ? Math.max(bounds.max, now)
      : now;
  if (out.length) {
    const last = out[out.length - 1];
    last.end = Math.max(last.end, end, last.start + MIN_TURN_SEC);
  }

  // Hermes batch-flushes often give every message the same timestamp — close gaps.
  for (let i = 0; i < out.length - 1; i++) {
    if (out[i].end <= out[i].start) {
      out[i].end = Math.max(out[i].start + MIN_TURN_SEC, out[i + 1].start);
    }
  }

  const needsEvenSpread =
    out.length > 0 &&
    (out.some((t) => t.end <= t.start) ||
      (out.length > 1 && out.every((t) => t.start === out[0].start)));

  const eventSpan =
    Number.isFinite(bounds.min) && Number.isFinite(bounds.max) ? bounds.max - bounds.min : 0;
  const deskSpan = Number.isFinite(spanStart) ? end - spanStart : 0;
  // DB timestamps often cluster at the latest flush/resume while the desk ran for hours.
  const timestampsClustered =
    out.length > 1 && Number.isFinite(spanStart) && deskSpan > 600 && eventSpan < deskSpan * 0.15;

  if (needsEvenSpread || timestampsClustered) {
    const t0 = Number.isFinite(spanStart) ? spanStart : out[0].start;
    const t1 = Math.max(end, t0 + out.length * MIN_TURN_SEC);
    const slice = (t1 - t0) / out.length;
    out.forEach((t, i) => {
      t.start = t0 + i * slice;
      t.end = t0 + (i + 1) * slice;
    });
    out[out.length - 1].end = t1;
  }

  return out.filter((t) => t.end > t.start);
}
