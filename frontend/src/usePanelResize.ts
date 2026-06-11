import { useCallback, useRef, useState } from "react";

export type PanelSize = { width: number; height: number };

/** Pointer-drag resize for floating panels (bottom-right corner handle). */
export function usePanelResize(min: PanelSize, pad = 12) {
  const [size, setSize] = useState<PanelSize | null>(null);
  const [resizing, setResizing] = useState(false);
  const resizeRef = useRef<{ pid: number; sx: number; sy: number; ow: number; oh: number } | null>(null);

  const resetSize = useCallback(() => setSize(null), []);

  const bindResize = useCallback((getCurrent: () => PanelSize) => ({
    onPointerDown: (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const cur = size ?? getCurrent();
      if (!size) setSize(cur);
      resizeRef.current = {
        pid: e.pointerId,
        sx: e.clientX,
        sy: e.clientY,
        ow: cur.width,
        oh: cur.height,
      };
      setResizing(true);
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent) => {
      const r = resizeRef.current;
      if (!r || r.pid !== e.pointerId) return;
      const maxW = Math.max(min.width, window.innerWidth - pad * 2);
      const maxH = Math.max(min.height, window.innerHeight - pad * 2);
      setSize({
        width: Math.max(min.width, Math.min(maxW, r.ow + e.clientX - r.sx)),
        height: Math.max(min.height, Math.min(maxH, r.oh + e.clientY - r.sy)),
      });
    },
    onPointerUp: (e: React.PointerEvent) => {
      const r = resizeRef.current;
      if (!r || r.pid !== e.pointerId) return;
      resizeRef.current = null;
      setResizing(false);
      try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* ignore */ }
    },
  }), [min.height, min.width, pad, size]);

  return { size, resetSize, resizing, bindResize };
}
