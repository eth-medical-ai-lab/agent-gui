import { useMemo, useState } from "react";
import type { ActivityEvent } from "../types";
import {
  buildOverviewTurns,
  type Cat,
  type OverviewTurn,
} from "../activityOverviewModel";

/**
 * Time-resolved overview of a (potentially very long) activity trace.
 *
 * IMPORTANT data caveat: Hermes batch-flushes a whole turn's messages to the DB
 * at one instant, so per-event timestamps are NOT reliable for sub-turn timing.
 * What IS reliable is (a) event *order* and (b) *user-message* timestamps (each
 * starts a turn). So we segment the trace into turns by user-message boundaries,
 * lay each turn's agent events out in order across its active window, and bucket
 * by those per-event slices (a turn with no agent events = idle).
 *
 * Long idle stretches — most importantly the app being stopped between a previous
 * session and a resume — are COLLAPSED to a fixed-width break on a compressed time
 * axis (see GAP_THRESHOLD). Without this, hours of downtime would dominate the axis
 * and squash a prior session's calls into an invisible sliver, so the overview
 * would appear to show only the current session. Bucket edges are mapped back to
 * real wall-clock (compToReal) for the axis labels and hover readout.
 */

const CAT_ORDER: Cat[] = ["reasoning", "generating", "tool", "idle"]; // top → bottom
const CAT_META: Record<Cat, { label: string; color: string }> = {
  reasoning:  { label: "Reasoning",     color: "#a78bfa" },
  generating: { label: "Generating",    color: "#4edca3" },
  tool:       { label: "Tool / waiting", color: "#4ea3dc" },
  idle:       { label: "Idle",           color: "#3a3a46" },
};

const RES: { label: string; s: number }[] = [
  { label: "5s", s: 5 },
  { label: "1m", s: 60 },
  { label: "10m", s: 600 },
  { label: "1h", s: 3600 },
  { label: "12h", s: 43200 },
  { label: "24h", s: 86400 },
];
// Cap on rendered bars. High enough that fine resolutions stay selectable for
// long spans — the chart scrolls horizontally instead of cramming everything
// into the panel width. (For a 7-day span this enables 10m; 1m needs span < ~7d;
// 5s needs span < ~14h.)
const MAX_BUCKETS = 10000;
// Min width (px) per bucket bar. Bars grow to fill the panel when few; once the
// total exceeds the panel they keep this width and the strip scrolls.
const BAR_PX = 4;
// Rough wall-clock an agent event represents. Batch-flushing hides true per-event
// timing, so within a turn we model the first (events × this) seconds as active
// work and the remainder (e.g. waiting for the user to reply) as idle.
const PER_EVENT_SECS = 1;
// An idle stretch longer than this (e.g. the app being stopped between a previous
// session and a resume) is collapsed to GAP_COMPRESSED on the axis and shown as a
// "break", so a long downtime can't bury a prior session's calls under dead time.
const GAP_THRESHOLD = 1800;   // 30 min
const GAP_COMPRESSED = 120;   // collapsed gap width on the (compressed) axis, in secs

interface Bucket {
  /** Real (wall-clock) start/end this bucket maps back to, for axis labels +
   *  hover. Buckets are laid out on a COMPRESSED axis (long idle gaps removed),
   *  so these are derived by mapping the compressed bucket edges back to real time. */
  realStart: number;
  realEnd: number;
  cats: Record<Cat, number>;
  taskSecs: Record<string, number>;
  total: number;       // active + idle secs (excludes collapsed gap)
  gap: number;         // collapsed-gap secs landing in this bucket
  isGap: boolean;      // bucket is (mostly) a collapsed downtime break
  topTask: string;
}

// One contiguous stretch of the timeline on both the real and compressed axes.
// Idle stretches over GAP_THRESHOLD compress (compLen < realLen); everything else
// maps 1:1. Used to lay events onto the compressed axis and to map bucket edges
// back to real wall-clock for labels.
interface Seg {
  kind: "active" | "idle" | "gap";
  realStart: number; realLen: number;
  compStart: number; compLen: number;
  turn: OverviewTurn;
}

export function ActivityOverview({
  events,
  endTime,
  startTime,
  deskEndTime,
  taskContent,
  liveEvents = [],
}: {
  events: ActivityEvent[];
  endTime?: number;
  startTime?: string;
  deskEndTime?: string;
  taskContent?: string | null;
  liveEvents?: ActivityEvent[];
}) {
  const turns = useMemo<OverviewTurn[]>(
    () => buildOverviewTurns(events, { endTime, startTime, deskEndTime, taskContent, liveEvents }),
    [events, endTime, startTime, deskEndTime, taskContent, liveEvents],
  );

  const t0 = turns.length ? turns[0].start : 0;
  const t1 = turns.length ? turns[turns.length - 1].end : 0;
  const realSpan = t1 - t0;

  // Compressed timeline: split each turn into an active head ([start, activeEnd),
  // events laid out in order) and an idle tail. Idle tails over GAP_THRESHOLD
  // (e.g. the app being stopped before a resume) compress to GAP_COMPRESSED so
  // downtime can't dominate the axis and hide a prior session's calls.
  const timeline = useMemo(() => {
    const segs: Seg[] = [];
    let comp = 0;
    for (const turn of turns) {
      const dur = turn.end - turn.start;
      if (dur <= 0) continue;
      const activeDur = Math.min(dur, turn.seq.length * PER_EVENT_SECS);
      const idleDur = dur - activeDur;
      if (activeDur > 0) {
        segs.push({ kind: "active", realStart: turn.start, realLen: activeDur, compStart: comp, compLen: activeDur, turn });
        comp += activeDur;
      }
      if (idleDur > 0) {
        const isGap = idleDur > GAP_THRESHOLD;
        const compLen = isGap ? GAP_COMPRESSED : idleDur;
        segs.push({ kind: isGap ? "gap" : "idle", realStart: turn.start + activeDur, realLen: idleDur, compStart: comp, compLen, turn });
        comp += compLen;
      }
    }
    return { segs, compTotal: comp };
  }, [turns]);

  const { segs, compTotal } = timeline;

  const [res, setRes] = useState(5);
  const resolution = res;
  const [hover, setHover] = useState<number | null>(null);

  // Map a compressed-axis time back to real wall-clock (for labels + hover).
  const compToReal = (c: number): number => {
    if (!segs.length) return t0;
    for (const s of segs) {
      if (c <= s.compStart + s.compLen) {
        const frac = s.compLen > 0 ? (c - s.compStart) / s.compLen : 0;
        return s.realStart + frac * s.realLen;
      }
    }
    const last = segs[segs.length - 1];
    return last.realStart + last.realLen;
  };

  const buckets = useMemo<Bucket[]>(() => {
    if (compTotal <= 0) return [];
    const n = Math.min(Math.ceil(compTotal / resolution), MAX_BUCKETS);
    const out: Bucket[] = Array.from({ length: n }, (_, k) => ({
      realStart: compToReal(k * resolution),
      realEnd: compToReal((k + 1) * resolution),
      cats: { reasoning: 0, generating: 0, tool: 0, idle: 0 },
      taskSecs: {},
      total: 0,
      gap: 0,
      isGap: false,
      topTask: "",
    }));
    // Spread `secs` of one category over compressed-axis range [from, to) into the
    // buckets it overlaps, crediting the turn's task. A bucket's split comes from
    // the events whose (ordered) time-slices land in it.
    const place = (cat: Cat | "gap", task: string, from: number, to: number) => {
      if (to <= from) return;
      const kStart = Math.max(0, Math.floor(from / resolution));
      const kEnd = Math.min(n - 1, Math.floor(to / resolution));
      for (let k = kStart; k <= kEnd; k++) {
        const bs = k * resolution;
        const ov = Math.min(to, bs + resolution) - Math.max(from, bs);
        if (ov <= 0) continue;
        if (cat === "gap") {
          out[k].gap += ov;
        } else {
          out[k].cats[cat] += ov;
          out[k].total += ov;
          out[k].taskSecs[task] = (out[k].taskSecs[task] || 0) + ov;
        }
      }
    };
    for (const s of segs) {
      if (s.kind === "active") {
        const seq = s.turn.seq;
        const slot = s.compLen / seq.length;
        seq.forEach((cat, i) => place(cat, s.turn.task, s.compStart + i * slot, s.compStart + (i + 1) * slot));
      } else if (s.kind === "idle") {
        place("idle", s.turn.task, s.compStart, s.compStart + s.compLen);
      } else {
        place("gap", s.turn.task, s.compStart, s.compStart + s.compLen);
      }
    }
    for (const b of out) {
      b.isGap = b.gap > b.total;
      if (b.isGap) { b.topTask = "⋯ paused"; continue; }
      let best = "", bestS = -1;
      for (const [tk, s] of Object.entries(b.taskSecs)) if (s > bestS) { bestS = s; best = tk; }
      b.topTask = best;
    }
    return out;
  }, [segs, compTotal, resolution]); // eslint-disable-line react-hooks/exhaustive-deps

  // Consecutive buckets with the same dominant task collapse into a label band.
  const bands = useMemo(() => {
    const out: { task: string; span: number }[] = [];
    for (const b of buckets) {
      const last = out[out.length - 1];
      if (last && last.task === b.topTask) last.span += 1;
      else out.push({ task: b.topTask || "—", span: 1 });
    }
    return out;
  }, [buckets]);

  // Memoize the (potentially thousands of) bar/band elements so hovering — which
  // only updates the readout via React state — doesn't recreate the whole strip.
  // The hover outline is pure CSS (.ov-bar:hover) so bars never depend on `hover`.
  const barEls = useMemo(() => buckets.map((b, i) => (
    b.isGap ? (
      // Collapsed downtime — render a narrow hatched "break" instead of a bar.
      <div
        key={i}
        className="ov-bar"
        onMouseEnter={() => setHover(i)}
        title="paused (downtime collapsed)"
        style={{
          flex: `0 0 ${BAR_PX * 2}px`, minWidth: BAR_PX * 2, height: "100%", boxSizing: "border-box",
          background: "repeating-linear-gradient(135deg, transparent 0 2px, rgba(255,255,255,0.06) 2px 4px)",
          borderRight: "1px solid var(--bg2)",
        }}
      />
    ) : (
      <div
        key={i}
        className="ov-bar"
        onMouseEnter={() => setHover(i)}
        style={{
          flex: `1 0 ${BAR_PX}px`, minWidth: BAR_PX, height: "100%", boxSizing: "border-box",
          borderRight: "1px solid var(--bg2)",
          display: "flex", flexDirection: "column", justifyContent: "flex-end",
        }}
      >
        {CAT_ORDER.map((c) => {
          const frac = b.total > 0 ? b.cats[c] / b.total : 0;
          if (frac <= 0) return null;
          return <div key={c} style={{ height: `${frac * 100}%`, background: CAT_META[c].color }} />;
        })}
      </div>
    )
  )), [buckets]);

  const bandEls = useMemo(() => bands.map((b, i) => (
    <div key={i} title={b.task} style={{
      flex: `${b.span} 0 ${b.span * BAR_PX}px`, minWidth: 0, boxSizing: "border-box",
      padding: "2px 4px", borderRadius: 3, background: "rgba(255,255,255,0.05)", color: "var(--text)",
      fontSize: 9.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
      borderLeft: "2px solid var(--accent2)",
    }}>{b.task}</div>
  )), [bands]);

  if (compTotal <= 0 || !buckets.length) {
    return <div style={{ padding: 24, fontSize: 12, color: "var(--text-dim)" }}>
      Not enough timed activity to chart yet.
    </div>;
  }

  const totals: Record<Cat, number> = { reasoning: 0, generating: 0, tool: 0, idle: 0 };
  for (const b of buckets) for (const c of CAT_ORDER) totals[c] += b.cats[c];
  const grand = CAT_ORDER.reduce((s, c) => s + totals[c], 0) || 1;

  const fmt = (sec: number) => {
    const d = new Date(sec * 1000);
    if (resolution >= 86400) {
      return d.toLocaleDateString([], { month: "short", day: "numeric" });
    }
    if (resolution >= 60) {
      return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };
  const spanFmt = (s: number) => s >= 86400 ? `${(s / 86400).toFixed(1)} d` : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.max(1, Math.round(s / 60))} min` : `${Math.max(1, Math.round(s))}s`;
  const spanLabel = spanFmt(realSpan);

  const H = 150;
  const hb = hover != null ? buckets[hover] : null;

  return (
    <div style={{ padding: "10px 14px", fontSize: 11 }}>
      {/* Resolution selector + legend */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
        <span style={{ color: "var(--text-dim)" }}>Resolution</span>
        <div style={{ display: "flex", gap: 4 }}>
          {RES.map((r) => {
            const tooFine = compTotal / r.s > MAX_BUCKETS;
            const active = r.s === resolution;
            return (
              <button
                key={r.label}
                disabled={tooFine}
                onClick={() => setRes(r.s)}
                style={{
                  fontSize: 10, padding: "2px 8px", borderRadius: 5,
                  cursor: tooFine ? "not-allowed" : "pointer", opacity: tooFine ? 0.35 : 1,
                  background: active ? "var(--accent2)" : "transparent",
                  color: active ? "#fff" : "var(--text-dim)",
                  border: `1px solid ${active ? "var(--accent2)" : "var(--card-border)"}`,
                }}
              >{r.label}</button>
            );
          })}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10 }}>
          {CAT_ORDER.map((c) => (
            <span key={c} style={{ display: "flex", alignItems: "center", gap: 4, color: "var(--text-dim)" }}>
              <span style={{ width: 9, height: 9, borderRadius: 2, background: CAT_META[c].color }} />
              {CAT_META[c].label} {Math.round((totals[c] / grand) * 100)}%
            </span>
          ))}
        </div>
      </div>

      {/* Scrollable chart — task bands + stacked bars share one scroll area so
          they stay column-aligned. Bars grow to fill the panel when few; once
          there are more than fit, they keep BAR_PX and the strip scrolls. */}
      <style>{`.ov-bar:hover{outline:1px solid var(--text);outline-offset:-1px;}`}</style>
      <div style={{ overflowX: "auto", overflowY: "hidden" }} onMouseLeave={() => setHover(null)}>
        <div style={{ minWidth: "100%", display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", marginBottom: 3 }}>{bandEls}</div>
          <div style={{ display: "flex", height: H, alignItems: "flex-end" }}>{barEls}</div>
        </div>
      </div>

      {/* Time axis */}
      <div style={{ display: "flex", justifyContent: "space-between", color: "var(--text-dim)", marginTop: 4, fontSize: 9.5 }}>
        <span>{fmt(buckets[0].realStart)}</span>
        <span>{spanLabel} span · {buckets.length} × {RES.find((r) => r.s === resolution)?.label}</span>
        <span>{fmt(buckets[buckets.length - 1].realEnd)}</span>
      </div>

      {/* Hover readout */}
      <div style={{
        marginTop: 8, padding: "6px 8px", borderRadius: 5, minHeight: 34,
        background: "var(--bg)", border: "1px solid var(--card-border)", color: "var(--text-dim)",
      }}>
        {hb ? (
          <span>
            <strong style={{ color: "var(--text)" }}>{fmt(hb.realStart)} → {fmt(hb.realEnd)}</strong>
            {hb.isGap ? (
              <>{"  ·  "}paused — {spanFmt(hb.realEnd - hb.realStart)} of downtime collapsed</>
            ) : (
              <>
                {"  ·  "}{hb.topTask || "—"}{"  ·  "}
                {CAT_ORDER.map((c) => `${CAT_META[c].label.split(" ")[0]} ${hb.total > 0 ? Math.round((hb.cats[c] / hb.total) * 100) : 0}%`).join(" / ")}
              </>
            )}
          </span>
        ) : (
          <span>Per-turn estimate (Hermes batches event times). Hover a bar for its range, task, and breakdown.</span>
        )}
      </div>
    </div>
  );
}
