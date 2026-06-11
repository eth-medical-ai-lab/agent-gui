import React from "react";

type Props = {
  active?: boolean;
  bind: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: (e: React.PointerEvent) => void;
  };
};

/** Bottom-right resize grip for floating panels. */
export function PanelResizeHandle({ active, bind }: Props) {
  return (
    <div
      {...bind}
      title="Drag to resize"
      style={{
        position: "absolute", right: 0, bottom: 0,
        width: 14, height: 14, cursor: "nwse-resize", zIndex: 3,
        touchAction: "none",
      }}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <svg width={14} height={14} viewBox="0 0 14 14" style={{ display: "block", opacity: active ? 0.9 : 0.55 }}>
        <path d="M14 14H8V12H12V8H14V14Z" fill="var(--text-dim)" />
        <path d="M14 14H10V10H14V14Z" fill="var(--text-dim)" />
      </svg>
    </div>
  );
}
