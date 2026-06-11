import React, { useEffect, useState } from "react";

// 1 logical pixel = 2 SVG units on a 20×30 grid → 40×60 viewBox.
const P = 2;
const r = (x: number, y: number, w: number, h: number, c: string, k?: string) =>
  <rect key={k ?? `${x},${y},${w},${h},${c}`} x={x * P} y={y * P} width={w * P} height={h * P} fill={c} shapeRendering="crispEdges" />;

// Shared neutral palette — carried over from the merge office-worker so every
// figure reads as the same art style regardless of accent tint.
const O = "#0e1a0a";   // outline
const SK = "#ffe0b0";  // skin
const WT = "#f2f0ec";  // shirt white
const NT = "#181822";  // trousers
const SH = "#0e0e14";  // shoes

export type AgentArchetype = "coder" | "researcher" | "cloud" | "local" | "default";

// Roster prototype ids → the pixel-art look they get. Clones inherit their
// source prototype's look (via clone_from); everything else is the default
// office-worker from the merge branch.
const ARCHETYPE_BY_ID: Record<string, AgentArchetype> = {
  coder: "coder",
  researcher: "researcher",
  cloud: "cloud",
  "local-ollama": "local",
};

export const PROTOTYPE_IDS = new Set(Object.keys(ARCHETYPE_BY_ID));

function archetypeFromBase(base: string | undefined): AgentArchetype {
  if (!base) return "default";
  return ARCHETYPE_BY_ID[base] ?? "default";
}

function defaultAccent(archetype: AgentArchetype): string {
  switch (archetype) {
    case "coder": return "#4a8eff";
    case "researcher": return "#e67e22";
    case "cloud": return "#a78bfa";
    case "local": return "#58a6ff";
    default: return "#6a7a9a";
  }
}

function shade(hex: string, amt: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  if (Number.isNaN(n)) return hex;
  const R = Math.min(255, Math.max(0, ((n >> 16) & 0xff) + amt));
  const G = Math.min(255, Math.max(0, ((n >> 8) & 0xff) + amt));
  const B = Math.min(255, Math.max(0, (n & 0xff) + amt));
  return `#${((R << 16) | (G << 8) | B).toString(16).padStart(6, "0")}`;
}

type AgentState = "idle" | "working" | "thinking";

interface Props {
  state?: AgentState;
  scale?: number;
  selected?: boolean;
  walking?: boolean;
  onClick?: () => void;
  agentId?: string;
  color?: string;
  isPrototype?: boolean;
  cloneFrom?: string | null;
  /** Explicit avatar look — overrides the lineage-derived archetype. */
  archetype?: AgentArchetype;
  onMouseDown?: (e: React.MouseEvent) => void;
  style?: React.CSSProperties;
}

/** Clone badge worn on top of the head — marks a cloned (non-prototype) agent. */
function CloneCap({ accent }: { accent: string }) {
  const top = shade(accent, -30);
  return (
    <>
      {r(6, 1, 8, 1, top, "cap-top")}
      {r(5, 2, 10, 1, accent, "cap-brim")}
      {r(9, 0, 2, 1, O, "cap-tassel")}
    </>
  );
}

// ── Shared limbs ───────────────────────────────────────────────────────────
// Legs and the right arm live inside the animated <g> groups so walking +
// typing animate (this is what regressed when the archetypes were first added).

function Legs({ walking }: { walking: boolean }) {
  return (
    <>
      <g className={`fn-leg-l ${walking ? "walking" : ""}`}>
        {r(5, 25, 4, 3, NT, "ll")}{r(5, 27, 3, 2, SH, "lls")}
      </g>
      <g className={`fn-leg-r ${walking ? "walking" : ""}`}>
        {r(11, 25, 4, 3, NT, "rl")}{r(12, 27, 3, 2, SH, "rls")}
      </g>
    </>
  );
}

function LeftArm({ garment, cuff }: { garment: string; cuff: string }) {
  return (
    <>
      {r(0, 18, 4, 8, garment, "la")}
      {r(0, 24, 4, 2, cuff, "lac")}
      {r(0, 24, 5, 2, SK, "lah")}
    </>
  );
}

function RightArm({ garment, cuff, working }: { garment: string; cuff: string; working: boolean }) {
  return (
    <g className={`fn-arm-r ${working ? "working" : ""}`}>
      {r(16, 18, 4, 8, garment, "ra")}
      {r(16, 24, 4, 2, cuff, "rac")}
      {r(15, 24, 5, 2, SK, "rah")}
    </g>
  );
}

function Head() {
  return r(5, 7, 10, 9, SK, "head");
}

// ── Per-archetype outfit ─────────────────────────────────────────────────────
// Each outfit shares the office-worker silhouette and only varies the garment
// colour, chest detail, hair, face accessories, mouth, and any floating prop —
// so the four prototypes stay visually consistent with the merge base figure.
interface Outfit {
  garment: string;
  garmentHi: string;
  cuff: string;
  collar: string;
  torso: React.ReactNode;       // drawn over the blazer centre (shirt/tie/detail)
  hair: React.ReactNode;        // hair + head-worn accessories (headphones, etc.)
  face?: React.ReactNode;       // drawn over the eyes (glasses, cheeks, beard)
  mouth: React.ReactNode;
  floating?: React.ReactNode;   // props beside the head (magnifier, cloud…)
}

function getOutfit(archetype: AgentArchetype, accent: string): Outfit {
  switch (archetype) {
    case "coder": {
      const hood = shade(accent, -50);
      const hoodHi = shade(accent, -25);
      const glow = "#3dff90";
      return {
        garment: hood, garmentHi: hoodHi, cuff: "#1a2a1a", collar: hood,
        torso: (
          <>
            {r(8, 17, 4, 9, "#16201c", "shirt")}
            {r(7, 17, 1, 4, hoodHi, "draw-l")}{r(12, 17, 1, 4, hoodHi, "draw-r")}
            {r(9, 19, 2, 5, "#0a140a", "screen")}
            {r(9, 19, 2, 1, glow, "code1")}{r(9, 21, 1, 1, glow, "code2")}{r(10, 23, 1, 1, glow, "code3")}
          </>
        ),
        hair: (
          <>
            {r(5, 5, 10, 3, "#2b2f3a", "hair")}
            {r(7, 3, 2, 2, "#2b2f3a", "spike-l")}{r(11, 3, 2, 2, "#2b2f3a", "spike-r")}
            {/* headphones */}
            {r(5, 4, 10, 1, "#26262e", "hp-band")}
            {r(4, 7, 1, 4, "#26262e", "hp-cup-l")}{r(15, 7, 1, 4, "#26262e", "hp-cup-r")}
          </>
        ),
        face: (
          <>
            {r(6, 9, 3, 1, "#101820", "gl-l")}{r(11, 9, 3, 1, "#101820", "gl-r")}
            {r(9, 9, 1, 1, "#101820", "gl-bridge")}
            {r(7, 9, 1, 1, glow, "gl-glint")}
          </>
        ),
        mouth: r(8, 12, 4, 1, O, "mouth"),
      };
    }
    case "researcher": {
      const jacket = shade(accent, -35);
      const jacketHi = shade(accent, -10);
      const tie = shade(accent, 15);
      return {
        garment: jacket, garmentHi: jacketHi, cuff: "#f2ece0", collar: "#f2ece0",
        torso: (
          <>
            {r(9, 17, 2, 9, "#f2ece0", "vest")}
            {r(9, 18, 2, 6, tie, "tie")}{r(9, 18, 2, 1, O, "knot")}
            {r(6, 17, 2, 4, jacketHi, "lapel-l")}{r(12, 17, 2, 4, jacketHi, "lapel-r")}
          </>
        ),
        hair: (
          <>
            {r(5, 5, 10, 3, "#5a4030", "hair")}
            {r(5, 6, 2, 4, "#5a4030", "hair-l")}{r(13, 6, 2, 4, "#5a4030", "hair-r")}
            {r(7, 4, 6, 1, "#6a5040", "hair-part")}
          </>
        ),
        face: (
          <>
            {r(6, 9, 3, 1, O, "gtop-l")}{r(6, 10, 3, 1, O, "gbot-l")}{r(6, 10, 1, 1, "#a8d8ff", "lens-l")}
            {r(11, 9, 3, 1, O, "gtop-r")}{r(11, 10, 3, 1, O, "gbot-r")}{r(13, 10, 1, 1, "#a8d8ff", "lens-r")}
            {r(9, 10, 1, 1, O, "bridge")}
          </>
        ),
        mouth: <>{r(7, 12, 6, 1, O, "mouth")}{r(8, 12, 2, 1, SK, "m1")}{r(10, 12, 2, 1, SK, "m2")}</>,
        floating: (
          // magnifying glass — web-search motif, top-right of the figure
          <>
            {r(16, 2, 3, 1, accent, "mag-t")}{r(16, 4, 3, 1, accent, "mag-b")}
            {r(16, 3, 1, 1, accent, "mag-l")}{r(18, 3, 1, 1, accent, "mag-r")}
            {r(17, 3, 1, 1, "#cfe8ff", "mag-glass")}
            {r(19, 5, 1, 2, shade(accent, -25), "mag-handle")}
          </>
        ),
      };
    }
    case "cloud": {
      const robe = shade(accent, -30);
      const robeHi = shade(accent, -5);
      const robeLt = shade(accent, 15);
      return {
        garment: robe, garmentHi: robeHi, cuff: robeLt, collar: robe,
        torso: (
          <>
            {r(9, 17, 2, 9, robeLt, "robe")}
            {r(6, 17, 2, 4, robeHi, "fold-l")}{r(12, 17, 2, 4, robeHi, "fold-r")}
            {r(10, 20, 1, 1, "#ffe066", "emblem")}
          </>
        ),
        hair: (
          <>
            {r(5, 5, 10, 3, "#9aa0b0", "hair")}
            {r(5, 6, 2, 5, "#9aa0b0", "hair-l")}{r(13, 6, 2, 5, "#9aa0b0", "hair-r")}
            {r(6, 4, 8, 1, "#b6bcc8", "hair-top")}
          </>
        ),
        // wise sage — short grey beard
        face: <>{r(6, 13, 8, 1, "#b8bcc8", "beard1")}{r(7, 14, 6, 1, "#b8bcc8", "beard2")}</>,
        mouth: r(7, 12, 6, 1, O, "mouth"),
        floating: (
          // cloud puff + star, above-right (Gemini sky / "wise")
          <>
            {r(14, 2, 6, 1, "#dfe6ff", "cloud-b")}
            {r(15, 1, 4, 1, "#eef2ff", "cloud-m")}
            {r(16, 0, 2, 1, "#ffffff", "cloud-t")}
            {r(13, 4, 1, 1, "#ffe066", "star")}
          </>
        ),
      };
    }
    case "local": {
      const sweater = shade(accent, -40);
      const sweaterHi = shade(accent, -15);
      return {
        garment: sweater, garmentHi: sweaterHi, cuff: sweater, collar: sweater,
        torso: (
          <>
            {r(4, 21, 12, 1, shade(accent, 8), "stripe")}
            {r(8, 17, 4, 2, sweaterHi, "neck-knit")}
          </>
        ),
        hair: (
          <>
            {r(5, 4, 10, 4, "#4a3828", "hair")}
            {r(5, 6, 2, 4, "#4a3828", "hair-l")}{r(13, 6, 2, 4, "#4a3828", "hair-r")}
          </>
        ),
        // friendly — rosy cheeks
        face: <>{r(6, 11, 1, 1, "#ff9a9a", "cheek-l")}{r(13, 11, 1, 1, "#ff9a9a", "cheek-r")}</>,
        // big warm smile
        mouth: (
          <>
            {r(7, 11, 1, 1, O, "smile-l")}{r(12, 11, 1, 1, O, "smile-r")}
            {r(7, 12, 6, 1, O, "smile-b")}{r(8, 12, 4, 1, WT, "teeth")}
          </>
        ),
      };
    }
    default: {
      const blazer = shade(accent, -45);
      const blazerHi = shade(accent, -20);
      return {
        garment: blazer, garmentHi: blazerHi, cuff: WT, collar: blazer,
        torso: (
          <>
            {r(9, 17, 2, 9, WT, "shirt")}
            {r(9, 18, 2, 1, accent, "tie-knot-bg")}{r(9, 19, 2, 1, O, "tie-knot")}{r(9, 20, 2, 6, accent, "tie")}
            {r(9, 18, 2, 1, O, "tie-top")}
            {r(7, 17, 2, 4, blazerHi, "lapel-l")}{r(6, 17, 1, 2, blazerHi, "lapel-l2")}
            {r(11, 17, 2, 4, blazerHi, "lapel-r")}{r(13, 17, 1, 2, blazerHi, "lapel-r2")}
            {r(5, 21, 3, 2, WT, "badge")}{r(5, 21, 3, 1, accent, "badge-top")}{r(6, 20, 1, 1, accent, "lanyard")}
          </>
        ),
        hair: (
          <>
            {r(5, 5, 10, 3, "#3a322a", "hair")}{r(5, 6, 2, 5, "#3a322a", "hair-l")}{r(13, 6, 2, 5, "#3a322a", "hair-r")}
            {r(6, 4, 8, 2, "#3a322a", "hair-up")}{r(7, 3, 6, 1, "#3a322a", "hair-top")}
            {r(10, 4, 2, 2, "#4a4034", "hair-part")}
            {r(5, 10, 1, 2, "#3a322a", "burn-l")}{r(14, 10, 1, 2, "#3a322a", "burn-r")}
          </>
        ),
        mouth: <>{r(7, 12, 6, 1, O, "mouth")}{r(8, 12, 2, 1, SK, "m1")}{r(10, 12, 2, 1, SK, "m2")}</>,
      };
    }
  }
}

export function AgentFigure({
  state = "idle", scale = 1, selected = false, walking = false, onClick,
  agentId, color, isPrototype, cloneFrom, archetype: archetypeOverride, onMouseDown, style,
}: Props) {
  const [blink, setBlink] = useState(false);

  const base = isPrototype ? agentId : (cloneFrom || agentId);
  const archetype = archetypeOverride ?? archetypeFromBase(base);
  const accent = color || defaultAccent(archetype);
  const showCap = isPrototype !== true
    && !(isPrototype === undefined && agentId != null && PROTOTYPE_IDS.has(agentId));

  useEffect(() => {
    const t = setInterval(() => {
      setBlink(true);
      setTimeout(() => setBlink(false), 100);
    }, 2800 + Math.random() * 2000);
    return () => clearInterval(t);
  }, []);

  const isWorking = state === "working" && !walking;
  const o = getOutfit(archetype, accent);

  return (
    <svg
      width={40 * scale}
      height={60 * scale}
      viewBox="0 0 40 60"
      style={{
        overflow: "visible",
        cursor: onMouseDown ? "grab" : onClick ? "pointer" : "default",
        imageRendering: "pixelated",
        filter: selected
          ? "drop-shadow(0 0 5px white) drop-shadow(0 0 10px rgba(255,255,255,0.5))"
          : "none",
        transition: "filter 0.2s ease",
        ...style,
      }}
      onClick={onClick}
      onMouseDown={onMouseDown}
    >
      <style>{`
        @keyframes fnbob    { 0%,100%{transform:translateY(0)}  50%{transform:translateY(-2px)} }
        @keyframes fntype   { 0%,100%{transform:rotate(-10deg)} 50%{transform:rotate(10deg)} }
        @keyframes fndot    { 0%,100%{opacity:0.2} 50%{opacity:1} }
        @keyframes fnwalkL  { 0%,49%{transform:translateY(0)}   50%,100%{transform:translateY(3px)} }
        @keyframes fnwalkR  { 0%,49%{transform:translateY(3px)} 50%,100%{transform:translateY(0)} }
        .fn-root            { animation: fnbob 2.6s ease-in-out infinite; transform-origin: 20px 30px; }
        .fn-root.working    { animation: none; }
        .fn-root.walking    { animation: none; }
        .fn-arm-r.working   { animation: fntype 0.28s ease-in-out infinite; transform-origin: 32px 36px; }
        .fn-leg-l.walking   { animation: fnwalkL 0.13s steps(1) infinite; transform-origin: 12px 50px; }
        .fn-leg-r.walking   { animation: fnwalkR 0.13s steps(1) infinite; transform-origin: 26px 50px; }
      `}</style>

      <g className={`fn-root ${isWorking ? "working" : ""} ${walking ? "walking" : ""}`}>

        {/* Shadow */}
        <ellipse cx="20" cy="59" rx="11" ry="2" fill="rgba(0,0,0,0.28)" />

        <Legs walking={walking} />

        {/* ── Body ── */}
        {r(4, 17, 12, 9, o.garment, "body")}
        {r(4, 17, 1, 8, o.garmentHi, "shoulder-l")}{r(15, 17, 1, 8, o.garmentHi, "shoulder-r")}
        {o.torso}

        <LeftArm garment={o.garment} cuff={o.cuff} />
        <RightArm garment={o.garment} cuff={o.cuff} working={isWorking} />

        {/* ── Neck + collar ── */}
        {r(8, 14, 4, 2, WT, "collar-w")}{r(9, 14, 2, 1, O, "collar-o")}
        {r(7, 15, 6, 3, SK, "neck")}
        {r(7, 15, 6, 1, o.collar, "collar-shade")}

        <Head />
        {o.hair}

        {/* ── Eyes ── */}
        {blink ? (
          <>{r(7, 10, 2, 1, O, "blink-l")}{r(11, 10, 2, 1, O, "blink-r")}</>
        ) : (
          <>{r(7, 9, 2, 2, WT, "eye-l")}{r(11, 9, 2, 2, WT, "eye-r")}{r(8, 10, 1, 1, O, "pup-l")}{r(12, 10, 1, 1, O, "pup-r")}</>
        )}

        {o.face}
        {o.mouth}
        {o.floating}

        {showCap && <CloneCap accent={accent} />}

        {/* ── State FX ── */}
        {isWorking && [0, 1, 2].map((i) => (
          <rect key={i}
            x={(14 + i * 3) * P} y={0} width={P} height={P}
            fill="var(--accent2)" shapeRendering="crispEdges"
            style={{ animation: `fndot 0.7s ease-in-out ${i * 0.2}s infinite` }}
          />
        ))}

        {state === "thinking" && !walking && (
          <g style={{ animation: "fndot 1.1s ease-in-out infinite" }}>
            {r(15, 4, 2, 2, "var(--yellow)", "th1")}{r(17, 2, 2, 2, "var(--yellow)", "th2")}{r(19, 0, 3, 3, "var(--yellow)", "th3")}
            <text x="41" y="7" fontSize="5" fill={O} textAnchor="middle" dominantBaseline="middle">?</text>
          </g>
        )}

        {selected && (
          [0, 4, 8, 12, 16].map((x) => (
            <rect key={`t${x}`} x={x * P / 1} y={-4} width={P * 1.5} height={2}
              fill="white" shapeRendering="crispEdges"
              style={{ animation: "fndot 0.9s ease-in-out infinite" }}
            />
          ))
        )}
      </g>
    </svg>
  );
}
