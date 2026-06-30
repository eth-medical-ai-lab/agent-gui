import { describe, it, expect } from "vitest";
import { buildOverviewTurns } from "../activityOverviewModel";
import type { ActivityEvent } from "../types";

function ev(
  event_type: ActivityEvent["event_type"],
  timestamp = "2026-06-01T12:00:00.000Z",
  detail = "",
): ActivityEvent {
  return {
    timestamp,
    event_type,
    icon: "",
    title: "",
    detail,
    tool_name: "",
    is_error: false,
    files_touched: [],
  };
}

describe("buildOverviewTurns", () => {
  it("charts a single turn when Hermes batch-flushes identical timestamps", () => {
    const ts = "2026-06-01T12:00:00.000Z";
    const turns = buildOverviewTurns(
      [
        ev("user_message", ts, "Build a CNN"),
        ev("tool_call", ts),
        ev("tool_result", ts),
        ev("message", ts),
      ],
      {
        startTime: ts,
        endTime: Date.parse("2026-06-01T13:00:00.000Z") / 1000,
      },
    );
    expect(turns.length).toBe(1);
    expect(turns[0].end).toBeGreaterThan(turns[0].start);
    expect(turns[0].counts.tool).toBe(1);
  });

  it("records agent events in order so the overview can time-resolve them", () => {
    const ts = "2026-06-01T12:00:00.000Z";
    const turns = buildOverviewTurns(
      [
        ev("user_message", ts, "Build a CNN"),
        ev("thinking_start", ts),
        ev("tool_result", ts),
        ev("message", ts),
      ],
      { startTime: ts, endTime: Date.parse("2026-06-01T13:00:00.000Z") / 1000 },
    );
    // Sequence preserves order (reasoning → tool → generating) and excludes the
    // user_message that opened the turn.
    expect(turns[0].seq).toEqual(["reasoning", "tool", "generating"]);
  });

  it("uses taskContent when the DB has agent events but no user_message yet", () => {
    const turns = buildOverviewTurns(
      [ev("tool_call"), ev("tool_result"), ev("message")],
      {
        taskContent: "Train MNIST classifier",
        startTime: "2026-06-01T10:00:00.000Z",
        endTime: Date.parse("2026-06-01T11:00:00.000Z") / 1000,
      },
    );
    expect(turns.length).toBe(1);
    expect(turns[0].task).toContain("Train MNIST");
    expect(turns[0].end).toBeGreaterThan(turns[0].start);
  });

  it("spreads multiple turns that share one flush timestamp", () => {
    const ts = "2026-06-01T12:00:00.000Z";
    const turns = buildOverviewTurns(
      [
        ev("user_message", ts, "Task A"),
        ev("tool_call", ts),
        ev("user_message", ts, "Task B"),
        ev("message", ts),
      ],
      {
        startTime: "2026-06-01T11:00:00.000Z",
        endTime: Date.parse("2026-06-01T13:00:00.000Z") / 1000,
      },
    );
    expect(turns.length).toBe(2);
    expect(turns[0].end).toBeGreaterThan(turns[0].start);
    expect(turns[1].end).toBeGreaterThan(turns[1].start);
  });

  it("spreads across full desk span when flush timestamps cluster at resume time", () => {
    const flush = "2026-06-05T15:43:00.000Z";
    const deskStart = "2026-06-05T13:43:00.000Z";
    const deskEnd = "2026-06-05T16:00:00.000Z";
    const turns = buildOverviewTurns(
      [
        ev("user_message", flush, "Task A"),
        ev("tool_call", flush),
        ev("user_message", flush, "Task B"),
        ev("message", flush),
      ],
      { startTime: deskStart, deskEndTime: deskEnd },
    );
    expect(turns.length).toBe(2);
    const span = turns[turns.length - 1].end - turns[0].start;
    expect(span).toBeGreaterThan(3600);
    // Clustered timestamps are redistributed onto a synthetic axis — per-event
    // times aren't trustworthy, so the overview keeps its synthetic layout.
    expect(turns.every((t) => !t.timed)).toBe(true);
  });

  // Regression: a single long agentic turn (one user message, many events over
  // an hour) used to collapse into a ~Nx1s sliver because per-event times were
  // ignored. With recorded emit-times it must span the real wall-clock instead.
  it("places events at their real recorded times across one long turn", () => {
    const start = "2026-06-23T12:48:11.000Z";
    const startSec = Date.parse(start) / 1000;
    const at = (type: ActivityEvent["event_type"], secOffset: number): ActivityEvent => ({
      ...ev(type, new Date((startSec + secOffset) * 1000).toISOString()),
      time_exact: true,
    });
    const turns = buildOverviewTurns(
      [
        { ...ev("user_message", start, "Train a model"), time_exact: true },
        at("thinking_start", 5),
        at("tool_result", 60),     // 1 min in
        at("message", 1800),       // 30 min in
        at("tool_result", 3600),   // 60 min in
      ],
      { startTime: start, deskEndTime: new Date((startSec + 3900) * 1000).toISOString() },
    );
    expect(turns.length).toBe(1);
    expect(turns[0].timed).toBe(true);
    expect(turns[0].times.length).toBe(turns[0].seq.length);
    // Times are anchored to the real offsets, monotonic, and span ~the full hour.
    expect(turns[0].times[0]).toBeCloseTo(startSec + 5, 0);
    expect(turns[0].times[turns[0].times.length - 1]).toBeCloseTo(startSec + 3600, 0);
    const span = turns[0].times[turns[0].times.length - 1] - turns[0].times[0];
    expect(span).toBeGreaterThan(3000);
  });

  it("interpolates approximate events between exact anchors (monotonic)", () => {
    const start = "2026-06-23T12:00:00.000Z";
    const startSec = Date.parse(start) / 1000;
    const exact = (type: ActivityEvent["event_type"], secOffset: number): ActivityEvent => ({
      ...ev(type, new Date((startSec + secOffset) * 1000).toISOString()),
      time_exact: true,
    });
    const approx = (type: ActivityEvent["event_type"]): ActivityEvent => ({
      ...ev(type, start),
      time_exact: false,
    });
    const turns = buildOverviewTurns(
      [
        { ...ev("user_message", start, "Task"), time_exact: true },
        exact("tool_result", 100),
        approx("message"),          // no real time — interpolated to ~200s
        approx("message"),          // ~300s
        exact("tool_result", 400),
      ],
      { startTime: start, deskEndTime: new Date((startSec + 500) * 1000).toISOString() },
    );
    expect(turns[0].timed).toBe(true);
    const t = turns[0].times;
    // Non-decreasing, with the two approximate events landing strictly between
    // their bracketing anchors (100s and 400s).
    for (let i = 1; i < t.length; i++) expect(t[i]).toBeGreaterThanOrEqual(t[i - 1]);
    expect(t[1]).toBeGreaterThan(startSec + 100);
    expect(t[2]).toBeLessThan(startSec + 400);
  });

  it("a single turn with only approximate times stays synthetic (timed:false)", () => {
    const ts = "2026-06-01T12:00:00.000Z";
    const approx = (type: ActivityEvent["event_type"]): ActivityEvent => ({ ...ev(type, ts), time_exact: false });
    const turns = buildOverviewTurns(
      [approx("user_message"), approx("tool_result"), approx("message")],
      { startTime: ts, endTime: Date.parse("2026-06-01T13:00:00.000Z") / 1000 },
    );
    expect(turns.length).toBe(1);
    expect(turns[0].timed).toBe(false);
  });

  // Regression (recover-desk-timestamps companion): a recovered desk has REAL,
  // well-spread per-event times written back into state.db, but they arrive
  // exact:false because the in-memory time_exact markers were lost on the server
  // restart. The overview must honour those spread DB times (so the chart spans
  // the real ~45-min run) instead of collapsing to the synthetic 1s/event sliver.
  it("honours spread DB times even when events are approximate (recovered desk)", () => {
    const start = "2026-06-23T12:48:13.000Z";
    const startSec = Date.parse(start) / 1000;
    // approximate (time_exact:false) but each at its own real wall-clock offset —
    // exactly what recover_timestamps.py leaves in the DB after a restart.
    const recovered = (type: ActivityEvent["event_type"], secOffset: number): ActivityEvent => ({
      ...ev(type, new Date((startSec + secOffset) * 1000).toISOString()),
      time_exact: false,
    });
    const turns = buildOverviewTurns(
      [
        { ...ev("user_message", start, "Train a model"), time_exact: false },
        recovered("tool_result", 5),
        recovered("message", 900),     // 15 min in
        recovered("tool_result", 1800), // 30 min in
        recovered("message", 2748),    // ~45 min in (real run end)
      ],
      { startTime: start, deskEndTime: new Date((startSec + 2748) * 1000).toISOString() },
    );
    expect(turns.length).toBe(1);
    expect(turns[0].timed).toBe(true);
    expect(turns[0].times.length).toBe(turns[0].seq.length);
    // Times track the real offsets and span the full run, not a ~Nx1s sliver.
    expect(turns[0].times[0]).toBeCloseTo(startSec + 5, 0);
    expect(turns[0].times[turns[0].times.length - 1]).toBeCloseTo(startSec + 2748, 0);
    expect(turns[0].times[turns[0].times.length - 1] - turns[0].times[0]).toBeGreaterThan(2000);
  });

  // Regression (recover-overview, multi-turn): the single-turn recovered case above
  // dodges the redistribution path because `timestampsClustered` requires >1 turn. A
  // real recovered desk has SEVERAL user turns, and when viewed long after it ran the
  // model pins `end` to `now`, inflating deskSpan so the genuine ~45-min eventSpan
  // looks "clustered" (< 15% of deskSpan) — which used to trip redistribution and
  // throw away the recovered times, collapsing the run to the synthetic sliver. The
  // spread DB times must win: every turn stays timed and tracks its real offsets.
  it("honours spread DB times across multiple turns viewed long after the run", () => {
    const start = "2026-06-23T12:48:13.000Z";
    const startSec = Date.parse(start) / 1000;
    const recovered = (type: ActivityEvent["event_type"], secOffset: number, detail = ""): ActivityEvent => ({
      ...ev(type, new Date((startSec + secOffset) * 1000).toISOString(), detail),
      time_exact: false,
    });
    const turns = buildOverviewTurns(
      [
        recovered("user_message", 0, "Task A"),
        recovered("tool_result", 5),
        recovered("message", 900),       // 15 min in
        recovered("user_message", 1800, "Task B"), // 30 min in
        recovered("tool_result", 1805),
        recovered("message", 2748),      // ~45 min in (real run end)
      ],
      // deskEndTime is the REAL end (in the past); `end` still snaps to now() in the
      // model, so deskSpan dwarfs eventSpan — the condition that used to mis-fire.
      { startTime: start, deskEndTime: new Date((startSec + 2748) * 1000).toISOString() },
    );
    expect(turns.length).toBe(2);
    // Both turns keep their real per-event times instead of a synthetic redistribution.
    expect(turns.every((t) => t.timed)).toBe(true);
    // Turn A spans its first ~15 min; turn B ends at the real ~45-min mark.
    expect(turns[0].times[0]).toBeCloseTo(startSec + 5, 0);
    expect(turns[1].times[turns[1].times.length - 1]).toBeCloseTo(startSec + 2748, 0);
    // The run as a whole spans the real ~45 min, not a few-second sliver.
    expect(turns[1].times[turns[1].times.length - 1] - turns[0].times[0]).toBeGreaterThan(2000);
  });

  // Regression (persisted markers, viewed long after the run): a forward desk born
  // under the marker-persistence path has REAL per-event times (time_exact:true) for
  // EVERY turn — they now survive restart. Viewed days later, `end` snaps to now() so
  // deskSpan dwarfs the genuine ~45-min eventSpan, which used to trip the
  // `timestampsClustered` redistribution and throw the real times away (collapsing
  // the run to the synthetic 1s/event sliver). The exact times must win: every turn
  // stays timed and tracks its real offsets, regardless of how late it's viewed.
  it("honours exact per-event times across multiple turns viewed long after the run", () => {
    const start = "2026-06-23T12:48:13.000Z";
    const startSec = Date.parse(start) / 1000;
    const exact = (type: ActivityEvent["event_type"], secOffset: number, detail = ""): ActivityEvent => ({
      ...ev(type, new Date((startSec + secOffset) * 1000).toISOString(), detail),
      time_exact: true,
    });
    const turns = buildOverviewTurns(
      [
        exact("user_message", 0, "Task A"),
        exact("tool_result", 5),
        exact("message", 900),       // 15 min in
        exact("user_message", 1800, "Task B"), // 30 min in
        exact("tool_result", 1805),
        exact("message", 2748),      // ~45 min in (real run end)
      ],
      // Real end is in the past; the model still snaps `end` to now(), so deskSpan
      // dwarfs eventSpan — the condition that used to mis-fire redistribution.
      { startTime: start, deskEndTime: new Date((startSec + 2748) * 1000).toISOString() },
    );
    expect(turns.length).toBe(2);
    // No redistribution: both turns keep their real per-event times.
    expect(turns.every((t) => t.timed)).toBe(true);
    expect(turns[0].times[0]).toBeCloseTo(startSec + 5, 0);
    expect(turns[1].times[turns[1].times.length - 1]).toBeCloseTo(startSec + 2748, 0);
    // The run as a whole spans the real ~45 min, not a few-second sliver.
    expect(turns[1].times[turns[1].times.length - 1] - turns[0].times[0]).toBeGreaterThan(2000);
  });

  // Regression: real per-event times must win even when the turn *boundaries* are
  // degenerate. If several user messages share one DB timestamp, the same-start
  // clause of `needsEvenSpread` fires; without gating it on real-times, that
  // redistributed onto the synthetic axis and dropped the exact times. The last
  // turn (which has room to span) must still resolve its real recorded times.
  it("keeps exact times even when turn boundaries are degenerate (needsEvenSpread)", () => {
    const base = "2026-06-23T12:00:00.000Z";
    const baseSec = Date.parse(base) / 1000;
    const at = (type: ActivityEvent["event_type"], sec: number): ActivityEvent => ({
      ...ev(type, new Date((baseSec + sec) * 1000).toISOString()),
      time_exact: true,
    });
    const turns = buildOverviewTurns(
      [
        { ...ev("user_message", base, "A"), time_exact: true },
        at("tool_result", 10), at("message", 200),
        { ...ev("user_message", base, "B"), time_exact: true }, // identical start ts
        at("tool_result", 210), at("message", 400),
      ],
      { startTime: base, deskEndTime: new Date((baseSec + 410) * 1000).toISOString() },
    );
    expect(turns.length).toBe(2);
    // Real per-event placement survives the degenerate (identical) turn starts —
    // without the gate, redistribution would force every turn to timed:false.
    expect(turns.some((t) => t.timed)).toBe(true);
  });

  // Guard the gate: approximate times that are CLUSTERED (all within a second —
  // the normal batch-flush case, no recovery) must NOT be trusted; the overview
  // keeps its synthetic layout rather than reading meaning into flush times.
  it("still ignores clustered approximate times (no false positive)", () => {
    const ts = "2026-06-01T12:00:00.000Z";
    const approx = (type: ActivityEvent["event_type"]): ActivityEvent => ({ ...ev(type, ts), time_exact: false });
    const turns = buildOverviewTurns(
      [approx("user_message"), approx("thinking_start"), approx("tool_result"), approx("message")],
      { startTime: ts, endTime: Date.parse("2026-06-01T13:00:00.000Z") / 1000 },
    );
    expect(turns.length).toBe(1);
    expect(turns[0].timed).toBe(false);
  });
});
