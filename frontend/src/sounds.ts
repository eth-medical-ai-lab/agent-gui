// Web Audio bell / chime synthesis.
//
// Real bells, doorbells and chimes use *inharmonic* partials (non-integer
// frequency ratios) plus a short noise "strike" transient. That combination is
// what makes them read as a struck physical object rather than a digital beep.

function audioCtx(): AudioContext {
  return new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
}

interface Partial { freq: number; gain: number; decay: number; type?: OscillatorType; attack?: number; delay?: number; }

function ringPartials(ctx: AudioContext, partials: Partial[], lowpass?: number): number {
  const t0 = ctx.currentTime;
  let dest: AudioNode = ctx.destination;
  if (lowpass) {
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = lowpass;
    lp.connect(ctx.destination);
    dest = lp;
  }
  let maxEnd = 0;
  for (const p of partials) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = p.type ?? "sine";
    osc.frequency.value = p.freq;
    osc.connect(g); g.connect(dest);
    const start = t0 + (p.delay ?? 0);
    const atk = p.attack ?? 0.004;
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(p.gain, start + atk);
    g.gain.exponentialRampToValueAtTime(0.0001, start + atk + p.decay);
    osc.start(start);
    const end = start + atk + p.decay + 0.05;
    osc.stop(end);
    maxEnd = Math.max(maxEnd, end - t0);
  }
  return maxEnd;
}

function noiseClick(ctx: AudioContext, dur = 0.025, gain = 0.12, center = 3500): void {
  const n = Math.floor(ctx.sampleRate * dur);
  const buf = ctx.createBuffer(1, n, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const bp = ctx.createBiquadFilter();
  bp.type = "bandpass"; bp.frequency.value = center; bp.Q.value = 0.8;
  const g = ctx.createGain(); g.gain.value = gain;
  src.connect(bp); bp.connect(g); g.connect(ctx.destination);
  src.start();
}

function closeAfter(ctx: AudioContext, after: number): void {
  setTimeout(() => { ctx.close().catch(() => {}); }, (after + 0.3) * 1000);
}

// Bright inharmonic ring with a metallic warble (tremolo). Shared by hand bell & gong.
function ringWithTremolo(
  ctx: AudioContext, partials: [number, number, number][],
  lfoHz: number, lfoDepth: number, stopAt: number,
): void {
  const master = ctx.createGain();
  master.gain.value = 1;
  master.connect(ctx.destination);
  const lfo = ctx.createOscillator();
  const lfoGain = ctx.createGain();
  lfo.frequency.value = lfoHz; lfoGain.gain.value = lfoDepth;
  lfo.connect(lfoGain); lfoGain.connect(master.gain);
  lfo.start();
  const t0 = ctx.currentTime;
  for (const [freq, gain, decay] of partials) {
    const osc = ctx.createOscillator(); const g = ctx.createGain();
    osc.frequency.value = freq; osc.connect(g); g.connect(master);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.004);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.004 + decay);
    osc.start(t0); osc.stop(t0 + decay + 0.1);
  }
  lfo.stop(t0 + stopAt);
}

// 1 — Brass desk service bell: bright metallic "ding" with a strike click.
function bellService(): void {
  const ctx = audioCtx();
  noiseClick(ctx, 0.02, 0.10, 4200);
  const f = 1180;
  const dur = ringPartials(ctx, [
    { freq: f,        gain: 0.30, decay: 1.9 },
    { freq: f * 2.76, gain: 0.16, decay: 1.4 },
    { freq: f * 5.40, gain: 0.07, decay: 0.9 },
    { freq: f * 8.93, gain: 0.03, decay: 0.5 },
  ]);
  closeAfter(ctx, dur);
}

// 2 — Two-tone doorbell: classic "ding-dong" (E5 then C5), soft mallet tone.
function bellDoorbell(): void {
  const ctx = audioCtx();
  const note = (freq: number, delay: number): Partial[] => ([
    { freq,           gain: 0.34, decay: 1.1, type: "triangle", attack: 0.006, delay },
    { freq: freq * 2, gain: 0.10, decay: 0.8, type: "sine",     attack: 0.006, delay },
  ]);
  const dur = ringPartials(ctx, [...note(659.25, 0), ...note(523.25, 0.34)], 5200);
  closeAfter(ctx, dur + 0.34);
}

// 3 — Hand / bicycle bell: bright inharmonic ring with a metallic warble.
function bellHand(): void {
  const ctx = audioCtx();
  noiseClick(ctx, 0.02, 0.08, 5000);
  const f = 2100;
  ringWithTremolo(ctx, [
    [f, 0.22, 1.6], [f * 1.5, 0.14, 1.3], [f * 2.13, 0.08, 1.0], [f * 2.74, 0.05, 0.7],
  ], 6.5, 0.35, 1.8);
  closeAfter(ctx, 1.8);
}

// 4 — Tubular / church bell: FM bell with a long resonant decay.
function bellTubular(): void {
  const ctx = audioCtx();
  const t0 = ctx.currentTime;
  const carrier = ctx.createOscillator();
  const mod = ctx.createOscillator();
  const modGain = ctx.createGain();
  const amp = ctx.createGain();
  carrier.frequency.value = 420;
  mod.frequency.value = 420 * 1.41;        // inharmonic ratio → bell timbre
  modGain.gain.setValueAtTime(800, t0);
  modGain.gain.exponentialRampToValueAtTime(1, t0 + 2.6);
  mod.connect(modGain); modGain.connect(carrier.frequency);
  amp.gain.setValueAtTime(0.0001, t0);
  amp.gain.exponentialRampToValueAtTime(0.85, t0 + 0.006);
  amp.gain.exponentialRampToValueAtTime(0.0001, t0 + 3.0);
  carrier.connect(amp); amp.connect(ctx.destination);
  mod.start(t0); carrier.start(t0); mod.stop(t0 + 3.1); carrier.stop(t0 + 3.1);
  closeAfter(ctx, 3.0);
}

// 5 — Soft chime / glockenspiel: gentle mallet tone, mellow and short.
function bellChime(): void {
  const ctx = audioCtx();
  noiseClick(ctx, 0.015, 0.05, 6000);
  const f = 1046.5; // C6
  const dur = ringPartials(ctx, [
    { freq: f,       gain: 0.28, decay: 1.4, attack: 0.003 },
    { freq: f * 2.7, gain: 0.10, decay: 1.0, attack: 0.003 },
    { freq: f * 5.2, gain: 0.04, decay: 0.6, attack: 0.003 },
  ], 6500);
  closeAfter(ctx, dur);
}

// 6 — Glass ping: very high, short, crystalline.
function bellGlass(): void {
  const ctx = audioCtx();
  noiseClick(ctx, 0.01, 0.04, 8000);
  const f = 2093; // C7
  const dur = ringPartials(ctx, [
    { freq: f,        gain: 0.22, decay: 0.9, attack: 0.002 },
    { freq: f * 2.4,  gain: 0.08, decay: 0.6, attack: 0.002 },
    { freq: f * 4.1,  gain: 0.03, decay: 0.35, attack: 0.002 },
  ]);
  closeAfter(ctx, dur);
}

// 7 — Marimba: warm woody mallet tone (strong 4th + 10th partials), short.
function bellMarimba(): void {
  const ctx = audioCtx();
  noiseClick(ctx, 0.012, 0.06, 1800);
  const f = 523.25; // C5
  const dur = ringPartials(ctx, [
    { freq: f,        gain: 0.34, decay: 0.85, type: "sine",     attack: 0.003 },
    { freq: f * 3.99, gain: 0.14, decay: 0.5,  type: "sine",     attack: 0.003 },
    { freq: f * 9.2,  gain: 0.05, decay: 0.28, type: "triangle", attack: 0.003 },
  ], 4200);
  closeAfter(ctx, dur);
}

// 8 — Gong: low inharmonic spread with a long shimmering decay.
function bellGong(): void {
  const ctx = audioCtx();
  noiseClick(ctx, 0.04, 0.10, 900);
  const f = 165;
  ringWithTremolo(ctx, [
    [f, 0.26, 3.4], [f * 2.4, 0.16, 3.0], [f * 3.9, 0.10, 2.4],
    [f * 5.8, 0.06, 1.8], [f * 8.1, 0.04, 1.2],
  ], 3.2, 0.28, 3.5);
  closeAfter(ctx, 3.5);
}

export interface BellSound { id: string; name: string; play: () => void; }

export const BELL_SOUNDS: BellSound[] = [
  { id: "chime",    name: "Soft chime",   play: bellChime },
  { id: "service",  name: "Service bell", play: bellService },
  { id: "doorbell", name: "Doorbell",     play: bellDoorbell },
  { id: "hand",     name: "Hand bell",    play: bellHand },
  { id: "church",   name: "Church bell",  play: bellTubular },
  { id: "glass",    name: "Glass ping",   play: bellGlass },
  { id: "marimba",  name: "Marimba",      play: bellMarimba },
  { id: "gong",     name: "Gong",         play: bellGong },
];

export const DEFAULT_BELL = "chime";

export function playBell(id: string = DEFAULT_BELL): void {
  try { (BELL_SOUNDS.find((b) => b.id === id) ?? BELL_SOUNDS[0]).play(); }
  catch { /* audio unavailable */ }
}
