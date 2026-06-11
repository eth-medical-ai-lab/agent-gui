/** Base z-index for desk detail panels (activity/files/console). */
export const DESK_PANEL_Z_BASE = 6000;

/** Increment and return the next stacking z-index for floating panels. */
export function nextPanelZ(counter: { current: number }): number {
  counter.current += 1;
  return counter.current;
}
