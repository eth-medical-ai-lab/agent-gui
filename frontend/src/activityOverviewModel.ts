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
  /** Real wall-clock time (sec) for each `seq` event (parallel array). Resolved
   *  from exact recorded emit-times where available and linearly interpolated
   *  across runs of approximate (batch-flushed) events otherwise; monotonic and
   *  clamped to [start, end]. Lets the overview place events at their TRUE time
   *  instead of a synthetic 1-second-per-event layout — without this, a single
   *  long agentic turn (one user message, hundreds of events over an hour)
   *  collapsed into a ~100-second sliver with the rest squashed into "idle". */
  times: number[];
  /** True when ≥1 event had an exact time AND the resolved times actually span
   *  real wall-clock — i.e. `times` are trustworthy enough to position events.
   *  When false the overview falls back to its synthetic even-spread layout. */
  timed: boolean;
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

/** Resolve a real wall-clock time for every event in a turn.
 *
 * Events with an *exact* recorded emit-time (`time_exact !== false`) are anchors;
 * runs of approximate (batch-flushed) events between them are spread by linear
 * interpolation, and leading/trailing approximate runs spread between the turn
 * boundary and the nearest anchor. The result is monotonic non-decreasing and
 * clamped to [start, end]. `timed` is true only when there is at least one anchor
 * AND the resolved times actually span more than a second of wall-clock — so an
 * all-clustered turn (every timestamp equal, e.g. after a server restart drops
 * the in-memory time markers) reports `timed:false` and the overview keeps using
 * its synthetic layout instead of collapsing every event onto one instant.
 *
 * `trustDb` lets a recovered desk's DB times act as anchors too. The
 * recover-desk-timestamps skill rewrites genuine per-event times back into
 * state.db but can't restore the in-memory `time_exact` flag, so its events
 * arrive `exact:false` even though their timestamps are real and spread across
 * the run. The caller sets `trustDb` only when the whole desk's DB times are
 * well-spread (not the clustered batch-flush case), so honouring them here
 * restores the true distribution instead of the synthetic 1s/event sliver. */
function resolveTurnTimes(
  start: number,
  end: number,
  raw: { ts: number; exact: boolean }[],
  trustDb = false,
): { times: number[]; timed: boolean } {
  const n = raw.length;
  if (!n) return { times: [], timed: false };
  const clamp = (t: number) => Math.min(end, Math.max(start, t));
  const times = raw.map((r) => ((r.exact || trustDb) && Number.isFinite(r.ts) ? clamp(r.ts) : NaN));
  if (!times.some((t) => Number.isFinite(t))) return { times: [], timed: false };

  // Sentinels at both ends so every approximate run is bracketed by a time.
  const anchors: { i: number; t: number }[] = [{ i: -1, t: start }];
  times.forEach((t, i) => { if (Number.isFinite(t)) anchors.push({ i, t }); });
  anchors.push({ i: n, t: end });
  for (let a = 0; a < anchors.length - 1; a++) {
    const lo = anchors[a];
    const hi = anchors[a + 1];
    const gap = hi.i - lo.i;
    if (gap <= 1) continue; // adjacent anchors — nothing to interpolate
    for (let i = lo.i + 1; i < hi.i; i++) {
      times[i] = lo.t + (hi.t - lo.t) * ((i - lo.i) / gap);
    }
  }

  // Enforce monotonic non-decreasing (exact times can arrive out of feed order —
  // parse_activity emits tool_call before message, but they stream the other way)
  // and re-clamp.
  let last = start;
  for (let i = 0; i < n; i++) {
    const t = Number.isFinite(times[i]) ? times[i] : last;
    times[i] = Math.min(end, Math.max(last, t));
    last = times[i];
  }
  return { times, timed: times[n - 1] - times[0] > MIN_TURN_SEC };
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

  // Raw per-event {time, exact} captured per turn (parallel to `out`), resolved
  // into `turn.times` once turn boundaries are final (after the spread fix-ups).
  const out: OverviewTurn[] = [];
  const rawByTurn: { ts: number; exact: boolean }[][] = [];
  let cur: OverviewTurn | null = null;
  let curRaw: { ts: number; exact: boolean }[] | null = null;
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
        times: [],
        timed: false,
        task: e.detail.trim().slice(0, 80) || "(task)",
      };
      out.push(cur);
      curRaw = [];
      rawByTurn.push(curRaw);
    } else if (cur && curRaw) {
      const c = eventCat(e);
      cur.counts[c] += 1;
      cur.seq.push(c);
      curRaw.push({ ts: tsSec(e.timestamp), exact: e.time_exact !== false });
    }
  }

  // Agent activity with no user anchor — one synthetic turn from session start.
  if (!out.length) {
    const agentish = feed.filter((e) => e.event_type !== "user_message");
    if (agentish.length > 0 || taskContent?.trim()) {
      const counts = emptyCounts();
      const seq: Exclude<Cat, "idle">[] = [];
      const raw: { ts: number; exact: boolean }[] = [];
      for (const e of agentish) {
        const c = eventCat(e);
        counts[c] += 1;
        seq.push(c);
        raw.push({ ts: tsSec(e.timestamp), exact: e.time_exact !== false });
      }
      out.push({
        start: fallbackStart,
        end: fallbackStart,
        counts,
        seq,
        times: [],
        timed: false,
        task: taskContent?.trim().slice(0, 80) || "(task)",
      });
      rawByTurn.push(raw);
    }
  }

  const now = Date.now() / 1000;
  // A numeric `endTime` is authoritative: the caller says the desk is FINISHED,
  // so the chart ends at the run's real end. Without it (live desk) the axis
  // keeps growing to `now` so ongoing work charts. Snapping finished desks to
  // `now` turned a 40-min run viewed a day later into a "1.1 d span" ending at
  // today's clock time, its whole tail one collapsed gap.
  const liveEndSec = tsSec(deskEndTime ?? "");
  const end = typeof endTime === "number" && Number.isFinite(endTime)
    ? (Number.isFinite(bounds.max) ? Math.max(endTime, bounds.max) : endTime)
    : Number.isFinite(liveEndSec)
      ? Math.max(liveEndSec, now)
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
  // A *recovered* desk (recover-desk-timestamps wrote real per-event times back
  // into state.db but couldn't restore the in-memory time_exact flag) arrives with
  // exact:false events whose DB timestamps are nonetheless genuine and spread
  // across the run. Trust those as real anchors — but only when the recovery was
  // wholesale: recovery follows a server restart, which drops EVERY marker, so a
  // recovered desk has zero exact events. (A live desk with only *partial* markers
  // keeps some exact events and its remaining approximate events still sit at the
  // clustered batch-flush time, which must be interpolated, not trusted.) Require
  // the DB times to actually span wall-clock with many distinct values too, so the
  // plain clustered batch-flush case (≤1 distinct timestamp, ~0 span) stays
  // synthetic.
  const noneExact = merged.length > 0 && merged.every((e) => e.time_exact === false);
  const distinctTs = new Set(
    merged.map((e) => tsSec(e.timestamp)).filter((t) => Number.isFinite(t)).map((t) => Math.round(t)),
  ).size;
  const trustDbTimes = noneExact && eventSpan > MIN_TURN_SEC && distinctTs > 2;
  // Any event carrying a genuine recorded emit-time (time_exact === true) means the
  // recording path has real per-event timing — now durable across restarts via the
  // persisted marker store (server `_persist_event_times`). Those events must be
  // PLACED at their true time, never thrown onto a synthetic axis. `resolveTurnTimes`
  // already anchors on them and interpolates the gaps; the redistribution below
  // exists only for the no-real-times case, so suppress it whenever real times exist.
  // Without this gate a finished multi-turn desk viewed long after it ran still
  // collapsed: `end` snaps to now(), so deskSpan dwarfs the real eventSpan and
  // `timestampsClustered` mis-fired, discarding the (now reliable) recorded times.
  const hasExactTimes = merged.some((e) => e.time_exact === true);
  const deskSpan = Number.isFinite(spanStart) ? end - spanStart : 0;
  // DB timestamps often cluster at the latest flush/resume while the desk ran for
  // hours — redistribute those onto a synthetic axis. But NOT when `trustDbTimes`
  // holds: a recovered desk has genuine, well-spread per-event times, and here
  // `eventSpan` looks small only because `end` is pinned to `now` (the desk ran on
  // a prior day, so `deskSpan` swallows every idle hour since). Redistributing then
  // would discard the real times and collapse the run back to the 1s/event sliver —
  // the exact bug recover-overview exists to fix. The trailing idle is the
  // renderer's job to compress into a "paused" break (GAP_THRESHOLD), not ours to
  // redistribute. Without this guard the fix silently fails for any multi-turn desk
  // viewed long after it ran (single-turn desks dodge it: out.length > 1 below).
  const timestampsClustered =
    !trustDbTimes && !hasExactTimes &&
    out.length > 1 && Number.isFinite(spanStart) && deskSpan > 600 && eventSpan < deskSpan * 0.15;

  // `timestampsClustered` already excludes the real-times cases; guard the
  // degenerate `needsEvenSpread` fallback the same way. Otherwise a desk with
  // genuine per-event times — exact markers (now durable across restarts) or a
  // recovered desk's spread DB times — would still be thrown onto the synthetic
  // axis (and its `resolveTurnTimes` skipped) whenever its turn *boundaries*
  // happen to be degenerate, e.g. several user messages sharing one flush
  // timestamp. Redistribution stays only for the truly timeless case.
  const redistributed =
    (needsEvenSpread && !hasExactTimes && !trustDbTimes) || timestampsClustered;
  if (redistributed) {
    const t0 = Number.isFinite(spanStart) ? spanStart : out[0].start;
    const t1 = Math.max(end, t0 + out.length * MIN_TURN_SEC);
    const slice = (t1 - t0) / out.length;
    out.forEach((t, i) => {
      t.start = t0 + i * slice;
      t.end = t0 + (i + 1) * slice;
    });
    out[out.length - 1].end = t1;
  }

  // Resolve real per-event times now that turn [start, end] are final. When we
  // had to redistribute turns onto a synthetic axis (timestamps clustered/unset),
  // per-event times are meaningless — leave `timed:false` so the overview uses
  // its synthetic even-spread layout instead.
  if (!redistributed) {
    out.forEach((turn, i) => {
      const { times, timed } = resolveTurnTimes(turn.start, turn.end, rawByTurn[i] ?? [], trustDbTimes);
      turn.times = times;
      turn.timed = timed;
    });
  }

  return out.filter((t) => t.end > t.start);
}
