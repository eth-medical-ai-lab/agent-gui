import { useEffect, useState } from "react";

// Each logical pixel = 2 SVG units on a 20×30 grid → 40×60 viewBox
const P = 2;
const r = (x: number, y: number, w: number, h: number, c: string) =>
  <rect key={`${x},${y},${w},${h},${c}`} x={x * P} y={y * P} width={w * P} height={h * P} fill={c} shapeRendering="crispEdges" />;

// Team Manager — professional woman in navy business suit
const SK  = "#fde8c8";  // skin
const SK2 = "#e8c8a0";  // skin shadow
const HR  = "#110808";  // very dark brown hair
const BN  = "#1e1010";  // bun highlight
const JK  = "#1a2050";  // navy jacket
const LJK = "#2a305e";  // jacket lapel/highlight
const WT  = "#f0eef0";  // white blouse
const SKT = "#0d0d28";  // dark skirt
const LE  = "#fde8c8";  // leg skin
const HE  = "#080810";  // black heels
const STK = "#8b5e3c";  // wooden stick (brown)
const STK2 = "#6b4020"; // stick shadow
const O   = "#08080c";  // outline
const LR  = "#d07080";  // lip colour
const CB  = "#d4a860";  // clipboard tan
const CBL = "#8b6830";  // clipboard border

export type ManagerState = "idle" | "walking" | "inspecting" | "poking" | "writing";
export type ManagerDirection = "right" | "left" | "up" | "down";

interface Props {
  state?: ManagerState;
  direction?: ManagerDirection;
  scale?: number;
}

export function ManagerFigure({ state = "idle", direction = "right", scale = 1 }: Props) {
  const [blink, setBlink] = useState(false);
  const [pokeFwd, setPokeFwd] = useState(false);

  useEffect(() => {
    const t = setInterval(() => {
      setBlink(true);
      setTimeout(() => setBlink(false), 100);
    }, 3200 + Math.random() * 2000);
    return () => clearInterval(t);
  }, []);

  // Poke pulse animation
  useEffect(() => {
    if (state !== "poking") { setPokeFwd(false); return; }
    let on = true;
    const cycle = () => {
      if (!on) return;
      setPokeFwd(true);
      setTimeout(() => { if (on) { setPokeFwd(false); setTimeout(() => { if (on) cycle(); }, 400); } }, 350);
    };
    cycle();
    return () => { on = false; };
  }, [state]);

  const isWalking  = state === "walking";
  const isPoking   = state === "poking";
  const isWriting  = state === "writing";
  const isInspect  = state === "inspecting";
  // Flip for left/up direction
  const flip = direction === "left" || direction === "up";

  // Stick x offset when poking
  const stickX = pokeFwd ? 17 : 14;

  return (
    <svg
      width={40 * scale}
      height={60 * scale}
      viewBox="0 0 40 60"
      style={{
        overflow: "visible",
        imageRendering: "pixelated",
      }}
    >
      <style>{`
        @keyframes mgbob   { 0%,100%{transform:translateY(0)}  50%{transform:translateY(-2px)} }
        @keyframes mgwalkL { 0%,49%{transform:translateY(0)}   50%,100%{transform:translateY(3px)} }
        @keyframes mgwalkR { 0%,49%{transform:translateY(3px)} 50%,100%{transform:translateY(0)} }
        @keyframes mgwrite { 0%,100%{transform:rotate(0deg)}   50%{transform:rotate(12deg)} }
        @keyframes mgpulse { 0%,100%{opacity:0.3} 50%{opacity:1} }
        .mg-root           { animation: mgbob 2.8s ease-in-out infinite; transform-origin: 20px 30px; }
        .mg-root.walking   { animation: none; }
        .mg-root.poking    { animation: none; }
        .mg-root.writing   { animation: none; }
        .mg-leg-l.walking  { animation: mgwalkL 0.13s steps(1) infinite; transform-origin: 11px 50px; }
        .mg-leg-r.walking  { animation: mgwalkR 0.13s steps(1) infinite; transform-origin: 15px 50px; }
        .mg-arm-r.writing  { animation: mgwrite 0.6s ease-in-out infinite; transform-origin: 17px 22px; }
      `}</style>

      <g transform={flip ? `scale(-1,1) translate(-40,0)` : undefined}>
        <g className={`mg-root ${isWalking ? "walking" : ""} ${isPoking ? "poking" : ""} ${isWriting ? "writing" : ""}`}>

          {/* Shadow */}
          <ellipse cx="20" cy="59" rx="10" ry="2" fill="rgba(0,0,0,0.25)" />

          {/* ── Left leg (skirt hem → calf → heel) ── */}
          <g className={`mg-leg-l ${isWalking ? "walking" : ""}`}>
            {r(8, 25, 3, 3, LE)}
            {r(8, 27, 2, 1, SKT)}
            {/* heel */}
            {r(8, 28, 2, 1, HE)}
            {r(9, 29, 1, 1, HE)}
          </g>

          {/* ── Right leg ── */}
          <g className={`mg-leg-r ${isWalking ? "walking" : ""}`}>
            {r(12, 25, 3, 3, LE)}
            {r(13, 27, 2, 1, SKT)}
            {/* heel */}
            {r(13, 28, 2, 1, HE)}
            {r(14, 29, 1, 1, HE)}
          </g>

          {/* ── Skirt (A-line, slightly wider than trousers) ── */}
          {r(7, 20, 9, 6, SKT)}
          {/* skirt highlight top edge */}
          {r(7, 20, 9, 1, "#18183a")}
          {/* slight flare at hem */}
          {r(6, 24, 11, 2, SKT)}

          {/* ── Jacket body ── */}
          {r(4, 12, 15, 9, JK)}
          {/* lapel highlights */}
          {r(4, 12, 1, 8, LJK)}
          {r(18, 12, 1, 8, LJK)}
          {/* white blouse centre */}
          {r(9, 12, 5, 9, WT)}
          {/* blouse shadow line */}
          {r(9, 12, 1, 9, "#d8d6d8")}
          {/* left lapel */}
          {r(7, 12, 2, 4, LJK)}
          {r(6, 12, 1, 2, LJK)}
          {/* right lapel */}
          {r(14, 12, 2, 4, LJK)}
          {r(16, 12, 1, 2, LJK)}
          {/* small brooch */}
          {r(10, 14, 2, 1, "#c8d860")}

          {/* ── Left arm ── */}
          <g>
            {r(0, 13, 4, 7, JK)}
            {/* cuff */}
            {r(0, 19, 4, 1, WT)}
            {r(0, 20, 4, 1, SK)}
          </g>

          {/* ── Right arm — changes shape based on state ── */}
          <g className={`mg-arm-r ${isWriting ? "writing" : ""}`}>
            {isPoking ? (
              <>
                {/* Arm extended right, holding stick */}
                {r(19, 13, 4, 3, JK)}
                {/* hand */}
                {r(19, 15, 4, 2, SK)}
                {/* wooden stick (extends further right) */}
                <rect x={stickX * P} y={16 * P} width={8 * P} height={P} fill={STK} shapeRendering="crispEdges" />
                <rect x={stickX * P} y={17 * P} width={8 * P} height={P / 2} fill={STK2} shapeRendering="crispEdges" />
                {/* stick tip */}
                <rect x={(stickX + 7) * P} y={15 * P} width={P} height={P * 2} fill={STK2} shapeRendering="crispEdges" />
              </>
            ) : isWriting ? (
              <>
                {/* Arm angled down, pen in hand */}
                {r(16, 13, 4, 7, JK)}
                {r(16, 19, 4, 1, WT)}
                {r(16, 20, 4, 1, SK)}
                {/* pen */}
                {r(19, 20, 1, 3, O)}
                {r(19, 22, 1, 1, "#e8e830")}
              </>
            ) : (
              <>
                {/* Normal arm */}
                {r(16, 13, 4, 7, JK)}
                {r(16, 19, 4, 1, WT)}
                {r(16, 20, 4, 1, SK)}
              </>
            )}
          </g>

          {/* ── Clipboard when inspecting ── */}
          {isInspect && (
            <g>
              {r(16, 14, 5, 7, CB)}
              {r(16, 14, 5, 1, CBL)}
              {r(16, 14, 1, 7, CBL)}
              {r(20, 14, 1, 7, CBL)}
              {r(16, 20, 5, 1, CBL)}
              {/* clip at top */}
              {r(18, 13, 2, 2, CBL)}
              {/* lines on clipboard */}
              {r(17, 16, 3, 1, "#b8882a")}
              {r(17, 18, 3, 1, "#b8882a")}
              {/* scanning pulse dot */}
              <rect x={19 * P} y={16 * P} width={P} height={P}
                fill="var(--accent, #4af)"
                style={{ animation: "mgpulse 0.9s ease-in-out infinite" }}
                shapeRendering="crispEdges"
              />
            </g>
          )}

          {/* ── Neck + collar ── */}
          {r(9, 10, 5, 2, WT)}
          {r(9, 10, 1, 1, O)}
          {r(13, 10, 1, 1, O)}
          {r(9, 11, 5, 2, SK)}
          {r(9, 11, 5, 1, JK)}

          {/* ── Head ── */}
          {r(6, 3, 11, 9, SK)}
          {/* cheek shadow */}
          {r(6, 9, 1, 2, SK2)}
          {r(16, 9, 1, 2, SK2)}

          {/* ── Hair — dark, swept back into bun ── */}
          {/* sides */}
          {r(5, 3, 2, 8, HR)}
          {r(16, 3, 2, 8, HR)}
          {/* top */}
          {r(6, 1, 11, 4, HR)}
          {r(7, 0, 9, 2, HR)}
          {/* bun shape on top-back */}
          {r(14, 0, 4, 4, HR)}
          {r(15, 0, 3, 2, BN)}
          {/* part highlight */}
          {r(9, 1, 2, 2, BN)}

          {/* ── Eyes ── */}
          {blink ? (
            <>{r(8, 7, 2, 1, O)}{r(12, 7, 2, 1, O)}</>
          ) : (
            <>
              {r(8, 6, 2, 2, WT)}
              {r(12, 6, 2, 2, WT)}
              {r(9, 7, 1, 1, O)}
              {r(13, 7, 1, 1, O)}
              {/* lashes */}
              {r(8, 6, 2, 1, O)}
              {r(12, 6, 2, 1, O)}
            </>
          )}

          {/* ── Nose ── */}
          {r(11, 9, 1, 1, SK2)}

          {/* ── Mouth ── */}
          {isInspect || isWriting ? (
            // Slightly pursed/focused mouth
            <>{r(8, 10, 5, 1, O)}{r(9, 10, 3, 1, LR)}</>
          ) : isPoking ? (
            // Determined expression
            <>{r(8, 10, 5, 1, O)}{r(9, 10, 1, 1, SK)}{r(11, 10, 1, 1, SK)}</>
          ) : (
            // Neutral professional smile
            <>{r(8, 10, 5, 1, O)}{r(9, 10, 3, 1, LR)}{r(9, 11, 3, 1, O)}</>
          )}

          {/* ── Glasses (thin wire frames) ── */}
          {r(8, 6, 2, 3, "none")}
          <rect x={8 * P} y={6 * P} width={2 * P} height={2 * P} fill="none" stroke="#4a6882" strokeWidth="0.8" shapeRendering="crispEdges" />
          <rect x={12 * P} y={6 * P} width={2 * P} height={2 * P} fill="none" stroke="#4a6882" strokeWidth="0.8" shapeRendering="crispEdges" />
          {/* nose bridge */}
          <line x1={10 * P} y1={7 * P} x2={12 * P} y2={7 * P} stroke="#4a6882" strokeWidth="0.8" />
          {/* left temple */}
          <line x1={8 * P} y1={7 * P} x2={6 * P} y2={7 * P} stroke="#4a6882" strokeWidth="0.8" />
          {/* right temple */}
          <line x1={14 * P} y1={7 * P} x2={16 * P} y2={7 * P} stroke="#4a6882" strokeWidth="0.8" />

        </g>
      </g>
    </svg>
  );
}
