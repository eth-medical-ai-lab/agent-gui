import React from "react";

export interface Scene { id: string; name: string; }

export const SCENES: Scene[] = [
  { id: "default",  name: "Default" },
  { id: "office",   name: "Office" },
  { id: "lab",      name: "Lab" },
  { id: "hospital", name: "Hospital" },
  { id: "night",    name: "Night city" },
];

export const DEFAULT_SCENE = "default";

// Full-bleed backdrop behind the desks. pointerEvents:none so desks stay clickable;
// zIndex 0 keeps it under the desk strip, clock, bed and bell. Desaturated + faded
// so the scene recedes and the foreground desks/agents stay the focus.
const LAYER: React.CSSProperties = {
  position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 0,
  opacity: 0.7, filter: "saturate(0.55)",
};

const pc = (v: number | string) => (typeof v === "number" ? `${v}%` : v);
const at = (top: number | string, left: number | string, extra?: React.CSSProperties): React.CSSProperties =>
  ({ position: "absolute", top: pc(top), left: pc(left), transform: "translate(-50%,-50%)", ...extra });

function tileBg(base: string, line: string, size: number): React.CSSProperties {
  return {
    backgroundColor: base,
    backgroundImage:
      `repeating-linear-gradient(0deg, ${line} 0 1px, transparent 1px ${size}px),` +
      `repeating-linear-gradient(90deg, ${line} 0 1px, transparent 1px ${size}px)`,
  };
}

// ── Props (clean flat SVG / divs) ────────────────────────────────────────────

function SnakePlant({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={44 * s} height={72 * s} viewBox="0 0 44 72" style={at(top, left)}>
      <path d="M12 52 h20 l-3 18 h-14 Z" fill="#c08552" /><rect x="11" y="50" width="22" height="5" rx="2" fill="#d49a66" />
      <path d="M22 54 C19 32 19 16 21 5 C24 16 24 34 22 54Z" fill="#3a7d42" />
      <path d="M22 54 C14 36 12 24 12 13 C18 24 22 38 22 54Z" fill="#48994f" />
      <path d="M22 54 C30 36 32 24 32 13 C26 24 22 38 22 54Z" fill="#2f6e38" />
    </svg>
  );
}

function Monstera({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={58 * s} height={66 * s} viewBox="0 0 58 66" style={at(top, left)}>
      <path d="M21 52 h16 l-3 13 h-10 Z" fill="#b07a4a" /><rect x="20" y="50" width="18" height="5" rx="2" fill="#c89263" />
      <ellipse cx="18" cy="28" rx="16" ry="18" fill="#3a8f44" />
      <ellipse cx="40" cy="32" rx="15" ry="17" fill="#48994f" />
      <ellipse cx="29" cy="17" rx="14" ry="15" fill="#2f7d3a" />
      <circle cx="29" cy="15" r="4" fill="#5fb066" opacity="0.7" />
    </svg>
  );
}

function Cactus({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={40 * s} height={62 * s} viewBox="0 0 40 62" style={at(top, left)}>
      <path d="M13 46 h14 l-2 14 h-10 Z" fill="#c97f5a" /><rect x="12" y="44" width="16" height="5" rx="2" fill="#dd9468" />
      <rect x="16" y="18" width="8" height="28" rx="4" fill="#4f9e5a" />
      <rect x="6" y="28" width="6" height="14" rx="3" fill="#4f9e5a" /><rect x="9" y="34" width="9" height="6" fill="#4f9e5a" />
      <rect x="28" y="24" width="6" height="16" rx="3" fill="#48994f" /><rect x="22" y="30" width="9" height="6" fill="#48994f" />
      <circle cx="20" cy="20" r="2.5" fill="#ffd24d" />
    </svg>
  );
}

function WaterCooler({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={34 * s} height={64 * s} viewBox="0 0 34 64" style={at(top, left)}>
      <path d="M11 4 h12 v6 l4 8 v8 h-20 v-8 l4 -8 Z" fill="#9fd6ee" opacity="0.85" />
      <rect x="6" y="26" width="22" height="34" rx="3" fill="#e8edf2" stroke="#c4ccd4" strokeWidth="1" />
      <rect x="12" y="36" width="4" height="5" fill="#5a9bd0" /><rect x="18" y="36" width="4" height="5" fill="#d8584e" />
      <rect x="4" y="58" width="26" height="4" rx="2" fill="#aab2bb" />
    </svg>
  );
}

function Coffee({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={34 * s} height={42 * s} viewBox="0 0 34 42" style={at(top, left)}>
      <rect x="4" y="4" width="26" height="34" rx="3" fill="#3a3f57" />
      <rect x="9" y="8" width="16" height="8" rx="2" fill="#5a6088" />
      <rect x="12" y="22" width="10" height="11" rx="1" fill="#6e4320" /><rect x="12" y="22" width="10" height="3" fill="#8a5a30" />
      <circle cx="26" cy="12" r="2" fill="#d8584e" />
    </svg>
  );
}

function FloorLamp({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={32 * s} height={64 * s} viewBox="0 0 32 64" style={at(top, left)}>
      <polygon points="7,6 25,6 28,24 4,24" fill="#e6c25f" />
      <rect x="14" y="24" width="4" height="34" fill="#3a3f57" />
      <ellipse cx="16" cy="60" rx="11" ry="4" fill="#2a2e44" />
    </svg>
  );
}

function TPlant({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={40 * s} height={40 * s} viewBox="0 0 40 40" style={at(top, left)}>
      <rect x="12" y="13" width="16" height="15" rx="3" fill="#b5763e" />
      <rect x="12" y="13" width="16" height="4" fill="#caa06a" />
      <circle cx="20" cy="16" r="13" fill="#358a3e" />
      <circle cx="14" cy="14" r="7" fill="#46a64e" />
      <circle cx="26" cy="18" r="7" fill="#2f7d36" />
      <circle cx="20" cy="12" r="5" fill="#74c96f" />
    </svg>
  );
}

function Bench({ top, left, w = 200 }: { top: number; left: number; w?: number }) {
  return <div style={at(top, left, { width: w, height: 40, background: "#cfd8dd", borderRadius: 5, boxShadow: "inset 0 0 0 2px #aab8bf, 0 2px 0 #9fb3bd" })} />;
}

function Cabinet({ top, left }: { top: number; left: number }) {
  const cols = ["#5fd0e6", "#5fe6a3", "#e66fb0", "#e6c25f", "#6fb0e6"];
  return (
    <div style={at(top, left, { width: 96, height: 30, background: "#aebcc4", borderRadius: 3, display: "flex", alignItems: "flex-end", gap: 5, padding: "0 7px 5px" })}>
      {cols.map((c, i) => <div key={i} style={{ width: 11, height: 12 + (i % 3) * 6, background: c, opacity: 0.75, borderRadius: "3px 3px 0 0" }} />)}
    </div>
  );
}

function HospitalBed({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <div style={at(top, left, { width: 46 * s, height: 84 * s })}>
      <div style={{ position: "absolute", inset: 0, background: "#c2ced3", borderRadius: 6 }} />
      <div style={{ position: "absolute", top: "6%", left: "10%", right: "10%", bottom: "7%", background: "#f4f8f9", borderRadius: 4 }} />
      <div style={{ position: "absolute", top: "9%", left: "18%", right: "18%", height: "18%", background: "#e6eef0", borderRadius: 4, border: "1px solid #d2dde0" }} />
      <div style={{ position: "absolute", bottom: "9%", left: "14%", right: "14%", height: "44%", background: "#9cc6d6", borderRadius: 4 }} />
      <div style={{ position: "absolute", bottom: "50%", left: "14%", right: "14%", height: "5%", background: "#7fb2c6" }} />
    </div>
  );
}

function IVStand({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={22 * s} height={58 * s} viewBox="0 0 22 58" style={at(top, left)}>
      <rect x="10" y="14" width="2" height="36" fill="#9aa3ad" />
      <rect x="6" y="6" width="9" height="13" rx="2" fill="#bfe3c0" stroke="#8fc890" strokeWidth="1" />
      <line x1="11" y1="19" x2="11" y2="30" stroke="#9aa3ad" strokeWidth="1" />
      <ellipse cx="11" cy="54" rx="8" ry="3" fill="#7a828c" />
    </svg>
  );
}

function VitalsMonitor({ top, left, s = 1 }: { top: number; left: number; s?: number }) {
  return (
    <svg width={32 * s} height={40 * s} viewBox="0 0 32 40" style={at(top, left)}>
      <rect x="2" y="2" width="28" height="24" rx="2" fill="#1b2740" />
      <polyline points="4,16 9,16 12,7 15,24 18,16 28,16" fill="none" stroke="#4ec98a" strokeWidth="1.5" />
      <rect x="13" y="26" width="6" height="14" fill="#3a4150" />
    </svg>
  );
}

function Building({ left, w, h, lit }: { left: string; w: number; h: number; lit: number }) {
  const rows = Math.floor((h - 10) / 14), cols = Math.max(1, Math.floor(w / 14));
  return (
    <div style={{ position: "absolute", bottom: 56, left, width: w, height: h, background: "#15152e", borderTop: "2px solid #2a2a52" }}>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} style={{ position: "absolute", top: 8 + r * 14, left: 5, right: 5, display: "flex", justifyContent: "space-between" }}>
          {Array.from({ length: cols }).map((_, c) => <div key={c} style={{ width: 6, height: 7, background: (r * 5 + c * 3 + lit) % 3 === 0 ? "#ffd86b" : "rgba(255,216,107,0.13)" }} />)}
        </div>
      ))}
    </div>
  );
}

// ── Scenes ───────────────────────────────────────────────────────────────────

// The original minimal backdrop: dark floor + subtle grid + top rule. Rendered at
// full strength (no muting) since there's nothing to compete with the foreground.
function DefaultScene() {
  return (
    <div style={{ ...LAYER, opacity: 1, filter: "none", background: "var(--floor)" }}>
      <div style={{
        position: "absolute", inset: 0, opacity: 0.22,
        backgroundImage:
          "repeating-linear-gradient(90deg, transparent 0, transparent 79px, var(--floor-line) 79px, var(--floor-line) 80px)," +
          "repeating-linear-gradient(180deg, transparent 0, transparent 39px, var(--floor-line) 39px, var(--floor-line) 40px)",
      }} />
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 6, background: "linear-gradient(180deg, #22224a 0%, transparent 100%)" }} />
    </div>
  );
}

// Sparse accents placed in the gaps around the desks, leaving the centre open.
const OFFICE_PROPS: { Comp: React.FC<{ top: number; left: number; s?: number }>; top: number; left: number; s: number }[] = [
  { Comp: WaterCooler, top: 84, left: 12, s: 1.1 },
  { Comp: SnakePlant,  top: 86, left: 30, s: 1.1 },
  { Comp: Monstera,    top: 84, left: 50, s: 1.0 },
  { Comp: TPlant,      top: 87, left: 70, s: 1.2 },
  { Comp: Cactus,      top: 86, left: 88, s: 1.0 },
  { Comp: FloorLamp,   top: 52, left: 6,  s: 1.2 },
  { Comp: Coffee,      top: 54, left: 94, s: 1.1 },
  { Comp: TPlant,      top: 22, left: 33, s: 0.8 },
  { Comp: SnakePlant,  top: 22, left: 67, s: 0.75 },
];

function OfficeScene() {
  return (
    <div style={{ ...LAYER, ...tileBg("#3a2c1d", "rgba(0,0,0,0.16)", 40) }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 30, background: "#2b2c4a", borderBottom: "4px solid #1c1d33" }} />
      {/* hanging pendant lamps */}
      {[18, 40, 62, 84].map((l, i) => (
        <div key={`lamp${i}`} style={{ position: "absolute", top: 0, left: `${l}%` }}>
          <div style={{ width: 2, height: 24, background: "#1c1d33", margin: "0 auto" }} />
          <div style={{ width: 26, height: 13, borderRadius: "0 0 13px 13px", background: "#e6c25f", boxShadow: "0 10px 26px rgba(230,194,95,0.45)" }} />
        </div>
      ))}
      {OFFICE_PROPS.map(({ Comp, top, left, s }, i) => <Comp key={i} top={top} left={left} s={s} />)}
    </div>
  );
}

function LabScene() {
  return (
    <div style={{ ...LAYER, ...tileBg("#dfe7ec", "rgba(80,120,140,0.2)", 34) }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 30, background: "#c4d3da", borderBottom: "4px solid #9fb3bd" }} />
      <Cabinet top={6} left={34} /><Cabinet top={6} left={56} />
      {/* periodic-table poster on the wall */}
      <div style={{ ...at(7, 74, { width: 92, height: 18, display: "grid", gridTemplateColumns: "repeat(8,1fr)", gap: 2, padding: 2, background: "#1b2740", borderRadius: 3 }) }}>
        {Array.from({ length: 24 }).map((_, i) => <div key={i} style={{ background: ["#4ec9a3", "#e6c25f", "#6fb0e6", "#e66fb0"][i % 4], opacity: 0.75 }} />)}
      </div>
      {/* bench rows with equipment */}
      {[40, 64, 88].map((t, ri) => (
        <React.Fragment key={ri}>
          <Bench top={t} left={50} w={620} />
          {[18, 38, 58, 78].map((l, ci) => {
            const kind = (ri + ci) % 4;
            const col = ["#5fd0e6", "#5fe6a3", "#e66fb0", "#6fb0e6"][kind];
            return (
              <svg key={ci} width="34" height="34" viewBox="0 0 34 34" style={at(t - 2, l)}>
                {kind === 0 && <path d="M12 6 h10 v10 l8 12 h-26 l8 -12 Z" fill={col} opacity="0.75" stroke={col} strokeWidth="1.5" />}
                {kind === 1 && <><rect x="9" y="8" width="16" height="20" rx="2" fill={col} opacity="0.6" stroke={col} strokeWidth="1.5" /><rect x="9" y="18" width="16" height="10" fill={col} opacity="0.5" /></>}
                {kind === 2 && <g><rect x="6" y="26" width="22" height="4" fill="#7a5230" /><rect x="9" y="6" width="5" height="22" rx="2" fill={col} opacity="0.7" /><rect x="16" y="9" width="5" height="19" rx="2" fill="#e6c25f" opacity="0.7" /></g>}
                {kind === 3 && <g><rect x="14" y="20" width="6" height="10" fill="#445560" /><circle cx="17" cy="12" r="7" fill="#33414a" /><rect x="15" y="16" width="4" height="8" fill="#5a6e78" /></g>}
              </svg>
            );
          })}
        </React.Fragment>
      ))}
      {[[16, 8], [16, 92], [92, 10], [92, 90]].map(([t, l], i) => <TPlant key={i} top={t} left={l} s={1.1} />)}
    </div>
  );
}

function HospitalScene() {
  return (
    <div style={{ ...LAYER, ...tileBg("#e4eef0", "rgba(120,160,170,0.18)", 34) }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 30, background: "#cde0e3", borderBottom: "4px solid #a8c2c6" }} />
      {/* red cross signs on the wall (clear of bed & clock) */}
      {[30, 70].map((l, i) => (
        <svg key={i} width="22" height="22" viewBox="0 0 22 22" style={{ position: "absolute", top: 4, left: `${l}%` }}>
          <rect width="22" height="22" rx="3" fill="#f4f8f9" stroke="#cdd9dc" strokeWidth="1" />
          <rect x="9" y="3" width="4" height="16" fill="#d8584e" /><rect x="3" y="9" width="16" height="4" fill="#d8584e" />
        </svg>
      ))}
      {/* aisle guide line down the centre */}
      <div style={{ position: "absolute", top: 34, bottom: 0, left: "50%", width: 6, marginLeft: -3, background: "#6fb6c9", opacity: 0.4 }} />
      {/* beds along both walls, aisle open in the middle */}
      <HospitalBed top={46} left={13} /><HospitalBed top={80} left={13} />
      <HospitalBed top={46} left={87} /><HospitalBed top={80} left={87} />
      {/* vitals monitors by the left beds, IV stands by the right beds */}
      <VitalsMonitor top={38} left={25} /><VitalsMonitor top={72} left={25} />
      <IVStand top={42} left={75} /><IVStand top={76} left={75} />
      {/* plants in the corners */}
      {[[24, 7], [24, 93], [96, 50]].map(([t, l], i) => <TPlant key={i} top={t} left={l} s={1.1} />)}
    </div>
  );
}

const STARS = Array.from({ length: 60 }, (_, i) => ({ top: `${(i * 53) % 48}%`, left: `${(i * 71) % 100}%`, size: (i % 3) + 1 }));

function NightScene() {
  const buildings: [string, number, number][] = [
    ["0%", 56, 100], ["6%", 44, 150], ["13%", 72, 76], ["21%", 50, 184], ["29%", 64, 120], ["37%", 40, 160],
    ["44%", 84, 70], ["54%", 52, 140], ["62%", 60, 104], ["70%", 46, 190], ["78%", 70, 86], ["86%", 54, 134], ["93%", 64, 110],
  ];
  return (
    <div style={{ ...LAYER, background: "linear-gradient(180deg,#0a0a23 0%, #141438 58%, #20204a 100%)" }}>
      {STARS.map((s, i) => <div key={i} style={{ position: "absolute", top: s.top, left: s.left, width: s.size, height: s.size, borderRadius: "50%", background: "#fff", opacity: 0.7 }} />)}
      <div style={{ position: "absolute", top: 34, right: "22%", width: 60, height: 60, borderRadius: "50%", background: "radial-gradient(circle at 35% 35%,#fdfdf0,#d8d8c0)", boxShadow: "0 0 30px rgba(240,240,210,0.4)" }} />
      {buildings.map(([l, w, h], i) => <Building key={i} left={l} w={w} h={h} lit={i} />)}
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 56, background: "#101024" }} />
      <div style={{ position: "absolute", bottom: 26, left: 0, right: 0, height: 3, background: "repeating-linear-gradient(90deg,#e6c25f 0 16px,transparent 16px 34px)", opacity: 0.5 }} />
      {["14%", "46%", "78%"].map((l, i) => (
        <div key={i} style={{ position: "absolute", bottom: 40, left: l }}>
          <div style={{ width: 4, height: 46, background: "#2a2a3a", margin: "0 auto" }} />
          <div style={{ position: "absolute", top: -6, left: -6, width: 16, height: 12, borderRadius: 4, background: "#ffe07a", boxShadow: "0 0 16px rgba(255,224,122,0.7)" }} />
        </div>
      ))}
    </div>
  );
}

export function SceneBackground({ scene }: { scene: string }) {
  switch (scene) {
    case "office":   return <OfficeScene />;
    case "lab":      return <LabScene />;
    case "hospital": return <HospitalScene />;
    case "night":    return <NightScene />;
    case "default":
    default:         return <DefaultScene />;
  }
}
