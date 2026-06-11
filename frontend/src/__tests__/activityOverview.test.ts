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
  });
});
