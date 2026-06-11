import { useLayoutEffect, useState, type RefObject } from "react";
import type { PanelPoint } from "./usePanelDrag";

/** Panel top-left relative to a desk element's top-left (layout space). */
export type DeskOffset = { top: number; left: number };

export function viewportFromDeskOffset(deskEl: HTMLElement, offset: DeskOffset): PanelPoint {
  const r = deskEl.getBoundingClientRect();
  return { top: r.top + offset.top, left: r.left + offset.left };
}

export function deskOffsetFromViewport(deskEl: HTMLElement, vp: PanelPoint): DeskOffset {
  const r = deskEl.getBoundingClientRect();
  return { top: vp.top - r.top, left: vp.left - r.left };
}

export function rowPositionFromDeskOffset(
  deskEl: HTMLElement,
  rowEl: HTMLElement,
  offset: DeskOffset,
): PanelPoint {
  const dr = deskEl.getBoundingClientRect();
  const rr = rowEl.getBoundingClientRect();
  return { top: dr.top - rr.top + offset.top, left: dr.left - rr.left + offset.left };
}

export function viewportToRow(rowEl: HTMLElement, vp: PanelPoint): PanelPoint {
  const rr = rowEl.getBoundingClientRect();
  return { top: vp.top - rr.top, left: vp.left - rr.left };
}

export function rowToViewport(rowEl: HTMLElement, rowPos: PanelPoint): PanelPoint {
  const rr = rowEl.getBoundingClientRect();
  return { top: rowPos.top + rr.top, left: rowPos.left + rr.left };
}

/** Default: panel top-left centered below the desk. */
export function defaultBelowDeskOffset(deskEl: HTMLElement, panelWidth: number, gap = 10): DeskOffset {
  const r = deskEl.getBoundingClientRect();
  return { top: r.height + gap, left: r.width / 2 - panelWidth / 2 };
}

/** Convert a viewport anchor (center-x, top edge) to a desk-local panel offset. */
export function centeredAnchorToDeskOffset(
  deskEl: HTMLElement,
  anchorCenterX: number,
  anchorTop: number,
  panelWidth: number,
): DeskOffset {
  const r = deskEl.getBoundingClientRect();
  return { top: anchorTop - r.top, left: anchorCenterX - panelWidth / 2 - r.left };
}

/** Map desk-local offset → team-row coords; updates on floor / strip scroll. */
export function useDeskAnchoredRowPosition(
  deskRef: RefObject<HTMLElement | null>,
  rowRef: RefObject<HTMLElement | null>,
  offset: DeskOffset | null,
  active: boolean,
): PanelPoint | null {
  const [pos, setPos] = useState<PanelPoint | null>(null);
  const offTop = offset?.top;
  const offLeft = offset?.left;

  useLayoutEffect(() => {
    if (!active || offTop == null || offLeft == null) {
      setPos(null);
      return;
    }
    const deskOffset: DeskOffset = { top: offTop, left: offLeft };
    function sync() {
      const desk = deskRef.current;
      const row = rowRef.current;
      if (!desk || !row) return;
      setPos(rowPositionFromDeskOffset(desk, row, deskOffset));
    }
    sync();
    window.addEventListener("scroll", sync, true);
    window.addEventListener("resize", sync);
    return () => {
      window.removeEventListener("scroll", sync, true);
      window.removeEventListener("resize", sync);
    };
  }, [active, offTop, offLeft, deskRef, rowRef]);

  return pos;
}
