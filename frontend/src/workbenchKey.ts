/**
 * localStorage key for the office layout (teams + desk order + pending cards).
 *
 * The layout is scoped per *office* = per Hermes home the server was started
 * with, so serving another home (e.g. `--hermes-home ~/.hermes-lab`) on the
 * usual port shows a fresh layout seeded from that home's desks, and switching
 * back to `./start.sh` (~/.hermes) restores the normal office untouched.
 *
 * The default home keeps the historical bare key, so nobody's existing layout
 * moves; only non-default homes get a suffix (the server's `office_id`).
 */
export const WORKBENCH_KEY_V2 = "hermes-workbench-v2";
export const WORKBENCH_KEY_V1 = "hermes-workbench-v1"; // read-only, backward compat (default office only)

export function workbenchStorageKey(officeId: string | null | undefined): string {
  const id = (officeId ?? "").trim();
  if (!id) return WORKBENCH_KEY_V2;
  return `${WORKBENCH_KEY_V2}:${id.replace(/[^A-Za-z0-9_.-]+/g, "-")}`;
}

// ── Active office (module singleton) ─────────────────────────────────────────
// App resolves the office once (GET /api/office) before restoring the layout and
// records it here, so every other reader/writer of the layout key (SnapshotMenu,
// TeamRow's reveal heuristic) agrees with App instead of hardcoding the bare key.
let activeOfficeId: string | null = null;

/** Record the office this page talks to ("" = default ~/.hermes). */
export function setActiveOfficeId(officeId: string | null | undefined): void {
  activeOfficeId = (officeId ?? "").trim();
}

/** True once App has learned which office it is talking to. */
export function isOfficeResolved(): boolean {
  return activeOfficeId !== null;
}

/** The layout key for the active office (bare key until the office is resolved). */
export function getWorkbenchKey(): string {
  return workbenchStorageKey(activeOfficeId);
}

/** Test hook: forget the active office. */
export function resetActiveOffice(): void {
  activeOfficeId = null;
}
