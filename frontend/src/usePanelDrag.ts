import { useCallback, useRef, useState } from "react";

export type PanelPoint = { top: number; left: number };

const DRAG_THRESHOLD_PX = 5;

/** Pointer-drag repositioning for floating panels (header is the drag handle). */
export function usePanelDrag(pad = 12, onCommit?: (pos: PanelPoint) => void) {
  const [pos, setPos] = useState<PanelPoint | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{
    pid: number; sx: number; sy: number; ox: number; oy: number; active: boolean;
  } | null>(null);

  const resetPos = useCallback(() => setPos(null), []);

  const bindHandle = useCallback((getCurrentPos: () => PanelPoint) => ({
    onPointerDown: (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      if ((e.target as HTMLElement).closest("button, input, select, textarea, a")) return;
      e.stopPropagation();
      const cur = pos ?? getCurrentPos();
      setPos(cur);
      dragRef.current = {
        pid: e.pointerId,
        sx: e.clientX,
        sy: e.clientY,
        ox: cur.left,
        oy: cur.top,
        active: false,
      };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent) => {
      const d = dragRef.current;
      if (!d || d.pid !== e.pointerId) return;
      const dx = e.clientX - d.sx;
      const dy = e.clientY - d.sy;
      if (!d.active) {
        if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
        d.active = true;
        setDragging(true);
      }
      setPos({
        left: Math.max(pad, d.ox + dx),
        top: Math.max(pad, d.oy + dy),
      });
    },
    onPointerUp: (e: React.PointerEvent) => {
      const d = dragRef.current;
      if (!d || d.pid !== e.pointerId) return;
      if (d.active) {
        const finalPos: PanelPoint = {
          left: Math.max(pad, d.ox + e.clientX - d.sx),
          top: Math.max(pad, d.oy + e.clientY - d.sy),
        };
        setPos(finalPos);
        onCommit?.(finalPos);
      }
      dragRef.current = null;
      setDragging(false);
      try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* ignore */ }
    },
  }), [pad, pos, onCommit]);

  return { pos, resetPos, dragging, bindHandle };
}
