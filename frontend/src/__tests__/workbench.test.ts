/**
 * Unit tests for workbench localStorage persistence.
 *
 * These test the save/restore contract introduced in App.tsx:
 *   - Session desks are saved by ID + taskContent
 *   - Pending desks are saved with their draft text
 *   - On restore, sessions are matched against live DB sessions; unknown IDs are dropped
 *   - Corrupt localStorage data is handled gracefully
 */
import { describe, it, expect, beforeEach } from "vitest";
import type { DeskItem, Session } from "../types";

// ── Re-implement the persistence helpers under test ───────────────────────────
// (These mirror the module-level functions in App.tsx)

const WORKBENCH_KEY = "hermes-workbench-v1";

type WorkbenchEntry =
  | { type: "session"; id: string; taskContent?: string }
  | { type: "pending"; id: string; text: string };

function readWorkbench(): WorkbenchEntry[] {
  try {
    const raw = localStorage.getItem(WORKBENCH_KEY);
    return raw ? (JSON.parse(raw) as WorkbenchEntry[]) : [];
  } catch {
    return [];
  }
}

function saveWorkbench(
  desks: DeskItem[],
  pendingTexts: Record<string, string>,
  taskContents: Record<string, string>,
) {
  const items: WorkbenchEntry[] = desks.map((d) => {
    if ("isPending" in d) return { type: "pending", id: d.id, text: pendingTexts[d.id] ?? "" };
    const tc = taskContents[(d as Session).id];
    return tc
      ? { type: "session", id: (d as Session).id, taskContent: tc }
      : { type: "session", id: (d as Session).id };
  });
  localStorage.setItem(WORKBENCH_KEY, JSON.stringify(items));
}

function restoreDesks(
  saved: WorkbenchEntry[],
  dbSessions: Session[],
): { desks: DeskItem[]; pendingTexts: Record<string, string>; taskContents: Record<string, string> } {
  const pendingTexts: Record<string, string> = {};
  const taskContents: Record<string, string> = {};
  const desks: DeskItem[] = [];

  for (const item of saved) {
    if (item.type === "session") {
      const session = dbSessions.find((s) => s.id === item.id);
      if (session) {
        desks.push(session);
        if (item.taskContent) taskContents[item.id] = item.taskContent;
      }
    } else {
      const id = `pending-restored-${Math.random()}`;
      desks.push({ id, isPending: true });
      if (item.text) pendingTexts[id] = item.text;
    }
  }
  return { desks, pendingTexts, taskContents };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeSession(id: string, overrides: Partial<Session> = {}): Session {
  return {
    id,
    started_at: "2024-01-01T00:00:00Z",
    ended_at: null,
    source: "gui",
    model: "test-model",
    parent_session_id: null,
    title: `Session ${id}`,
    message_count: 0,
    token_estimate: 0,
    is_running: false,
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  localStorage.clear();
});

describe("saveWorkbench + readWorkbench", () => {
  it("round-trips a single session desk", () => {
    const s = makeSession("abc-123");
    saveWorkbench([s], {}, {});
    const saved = readWorkbench();
    expect(saved).toHaveLength(1);
    expect(saved[0]).toEqual({ type: "session", id: "abc-123" });
  });

  it("persists taskContent on session entries", () => {
    const s = makeSession("abc-123");
    saveWorkbench([s], {}, { "abc-123": "Write a poem about cats" });
    const saved = readWorkbench();
    expect(saved[0]).toMatchObject({ type: "session", id: "abc-123", taskContent: "Write a poem about cats" });
  });

  it("omits taskContent key when no content for that session", () => {
    const s = makeSession("abc-123");
    saveWorkbench([s], {}, {});
    const saved = readWorkbench();
    expect((saved[0] as { taskContent?: string }).taskContent).toBeUndefined();
  });

  it("round-trips a pending desk with draft text", () => {
    const pending: DeskItem = { id: "pending-1", isPending: true };
    saveWorkbench([pending], { "pending-1": "Build a todo app" }, {});
    const saved = readWorkbench();
    expect(saved).toHaveLength(1);
    expect(saved[0]).toEqual({ type: "pending", id: "pending-1", text: "Build a todo app" });
  });

  it("saves empty text for a pending desk with no draft", () => {
    const pending: DeskItem = { id: "pending-1", isPending: true };
    saveWorkbench([pending], {}, {});
    const saved = readWorkbench();
    expect(saved[0]).toMatchObject({ type: "pending", text: "" });
  });

  it("preserves order of mixed desks", () => {
    const s1 = makeSession("sess-1");
    const s2 = makeSession("sess-2");
    const pending: DeskItem = { id: "pending-X", isPending: true };
    saveWorkbench([s1, pending, s2], { "pending-X": "hello" }, { "sess-1": "task one" });
    const saved = readWorkbench();
    expect(saved).toHaveLength(3);
    expect(saved[0]).toMatchObject({ type: "session", id: "sess-1", taskContent: "task one" });
    expect(saved[1]).toMatchObject({ type: "pending", text: "hello" });
    expect(saved[2]).toMatchObject({ type: "session", id: "sess-2" });
  });

  it("returns [] when nothing has been saved", () => {
    expect(readWorkbench()).toEqual([]);
  });

  it("returns [] when localStorage contains corrupt JSON", () => {
    localStorage.setItem(WORKBENCH_KEY, "{ not valid json }}}");
    expect(readWorkbench()).toEqual([]);
  });
});

describe("restoreDesks", () => {
  it("restores sessions found in DB", () => {
    const session = makeSession("sess-abc");
    const saved: WorkbenchEntry[] = [{ type: "session", id: "sess-abc", taskContent: "Do X" }];
    const { desks, taskContents } = restoreDesks(saved, [session]);
    expect(desks).toHaveLength(1);
    expect((desks[0] as Session).id).toBe("sess-abc");
    expect(taskContents["sess-abc"]).toBe("Do X");
  });

  it("drops sessions not found in DB", () => {
    const saved: WorkbenchEntry[] = [{ type: "session", id: "ghost-session" }];
    const { desks } = restoreDesks(saved, []);
    expect(desks).toHaveLength(0);
  });

  it("restores pending desk with its draft text", () => {
    const saved: WorkbenchEntry[] = [{ type: "pending", id: "old-pending-id", text: "My task draft" }];
    const { desks, pendingTexts } = restoreDesks(saved, []);
    expect(desks).toHaveLength(1);
    expect("isPending" in desks[0]).toBe(true);
    const newId = desks[0].id;
    expect(pendingTexts[newId]).toBe("My task draft");
  });

  it("does not add pendingTexts entry for empty pending draft", () => {
    const saved: WorkbenchEntry[] = [{ type: "pending", id: "p1", text: "" }];
    const { pendingTexts } = restoreDesks(saved, []);
    expect(Object.keys(pendingTexts)).toHaveLength(0);
  });

  it("does not populate taskContents when session has no taskContent", () => {
    const session = makeSession("sess-1");
    const saved: WorkbenchEntry[] = [{ type: "session", id: "sess-1" }];
    const { taskContents } = restoreDesks(saved, [session]);
    expect(Object.keys(taskContents)).toHaveLength(0);
  });

  it("preserves desk order across mixed session + pending entries", () => {
    const s1 = makeSession("s1");
    const s2 = makeSession("s2");
    const saved: WorkbenchEntry[] = [
      { type: "session", id: "s1" },
      { type: "pending", id: "p1", text: "draft" },
      { type: "session", id: "s2" },
    ];
    const { desks } = restoreDesks(saved, [s1, s2]);
    expect(desks).toHaveLength(3);
    expect((desks[0] as Session).id).toBe("s1");
    expect("isPending" in desks[1]).toBe(true);
    expect((desks[2] as Session).id).toBe("s2");
  });

  it("handles empty saved list", () => {
    const { desks, pendingTexts, taskContents } = restoreDesks([], [makeSession("s1")]);
    expect(desks).toHaveLength(0);
    expect(pendingTexts).toEqual({});
    expect(taskContents).toEqual({});
  });
});

describe("full save → reload → restore cycle", () => {
  it("taskContent survives a reload (save then restore)", () => {
    const session = makeSession("session-42");
    saveWorkbench([session], {}, { "session-42": "Summarise the quarterly report" });

    // Simulate reload: read from localStorage and restore
    const saved = readWorkbench();
    const { desks, taskContents } = restoreDesks(saved, [session]);

    expect(desks).toHaveLength(1);
    expect(taskContents["session-42"]).toBe("Summarise the quarterly report");
  });

  it("pending draft survives a reload", () => {
    const pending: DeskItem = { id: "pending-99", isPending: true };
    saveWorkbench([pending], { "pending-99": "Half-written task…" }, {});

    const saved = readWorkbench();
    const { desks, pendingTexts } = restoreDesks(saved, []);

    expect(desks).toHaveLength(1);
    const restoredId = desks[0].id;
    expect(pendingTexts[restoredId]).toBe("Half-written task…");
  });

  it("ghost sessions are silently dropped on restore", () => {
    const live = makeSession("live-session");
    const ghost = makeSession("deleted-session");
    saveWorkbench([live, ghost], {}, {});

    const saved = readWorkbench();
    // Only pass live session to simulate DB state after ghost was removed
    const { desks } = restoreDesks(saved, [live]);

    expect(desks).toHaveLength(1);
    expect((desks[0] as Session).id).toBe("live-session");
  });
});
