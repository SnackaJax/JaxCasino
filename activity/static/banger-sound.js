// Banger Bash sounds, synthesized with the Web Audio API (no audio files, so nothing to license or download).
// Browsers only allow sound after the player has tapped or clicked, so unlock() runs on the first interaction.
// The mute switch is shared with Spin Frenzy. Tweak a sound by changing its numbers below.

let ctx = null;
let master = null;
let muted = false;
try { muted = localStorage.getItem('spinfrenzy-muted') === '1'; } catch (e) { /* storage blocked */ }

const VOLUME = 0.6;

function audio() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -14; comp.ratio.value = 4;
    master = ctx.createGain();
    master.gain.value = muted ? 0 : VOLUME;
    master.connect(comp);
    comp.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}

export function unlock() { audio(); }
export function isMuted() { return muted; }
export function setMuted(value) {
  muted = value;
  try { localStorage.setItem('spinfrenzy-muted', value ? '1' : '0'); } catch (e) { /* storage blocked */ }
  if (master) master.gain.setTargetAtTime(value ? 0 : VOLUME, ctx.currentTime, 0.02);
}
function ready() { return !muted && ctx && ctx.state === 'running'; }

// One tone with a quick attack and exponential fade, optionally sliding in pitch, optionally with vibrato.
function tone({ f = 440, f2 = null, at = 0, dur = 0.12, type = 'sine', vol = 0.3, attack = 0.004, vib = 0, lp = 0 }) {
  if (!ready()) return;
  const t = ctx.currentTime + at;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(f, t);
  if (f2) osc.frequency.exponentialRampToValueAtTime(f2, t + dur);
  if (vib) {
    const lfo = ctx.createOscillator(); const lg = ctx.createGain();
    lfo.frequency.value = 6; lg.gain.value = vib;
    lfo.connect(lg); lg.connect(osc.frequency); lfo.start(t); lfo.stop(t + dur + 0.05);
  }
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  let node = osc;
  if (lp) {
    const f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = lp;
    osc.connect(f); node = f;
  }
  node.connect(g); g.connect(master);
  osc.start(t); osc.stop(t + dur + 0.05);
}

let noiseBuffer = null;
function noise({ at = 0, dur = 0.25, vol = 0.2, f = 1200, f2 = null, q = 1.2, type = 'bandpass', attack = 0.3 }) {
  if (!ready()) return;
  if (!noiseBuffer) {
    noiseBuffer = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const d = noiseBuffer.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  }
  const t = ctx.currentTime + at;
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer;
  const bp = ctx.createBiquadFilter();
  bp.type = type; bp.Q.value = q;
  bp.frequency.setValueAtTime(f, t);
  if (f2) bp.frequency.exponentialRampToValueAtTime(f2, t + dur);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + Math.max(0.005, dur * attack));
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(bp); bp.connect(g); g.connect(master);
  src.start(t, Math.random()); src.stop(t + dur + 0.05);
}

const NOTE = (n) => 440 * Math.pow(2, (n - 69) / 12);   // MIDI note number -> Hz
let lastTick = 0;
function throttle(ms) {
  const now = performance.now();
  if (now - lastTick < ms) return false;
  lastTick = now; return true;
}

// a short brassy chord (detuned saws through a low-pass), the backbone of the fanfares
function brass(notes, at, dur, vol = 0.07) {
  for (const n of notes) {
    tone({ f: NOTE(n), at, dur, type: 'sawtooth', vol, attack: 0.03, lp: 2400 });
    tone({ f: NOTE(n) * 1.004, at, dur, type: 'sawtooth', vol: vol * 0.7, attack: 0.03, lp: 1800 });
  }
}
function coins(n, at = 0, spread = 1.2) {
  for (let i = 0; i < n; i++) {
    const t = at + Math.random() * spread;
    const f = 2400 + Math.random() * 2600;
    tone({ f, at: t, dur: 0.09, type: 'sine', vol: 0.05 });
    tone({ f: f * 1.5, at: t + 0.02, dur: 0.07, type: 'sine', vol: 0.03 });
  }
}

export const sfx = {
  click() { tone({ f: 1800, f2: 1200, dur: 0.04, type: 'triangle', vol: 0.12 }); },
  error() { tone({ f: 220, dur: 0.14, type: 'square', vol: 0.06 }); tone({ f: 180, dur: 0.16, type: 'square', vol: 0.06, at: 0.1 }); },
  bet(up) { tone({ f: up ? 900 : 700, f2: up ? 1300 : 500, dur: 0.07, type: 'triangle', vol: 0.12 }); },

  // ---- reels
  spinStart() {
    noise({ dur: 0.35, vol: 0.2, f: 300, f2: 1800, q: 0.7 });
    tone({ f: 220, f2: 660, dur: 0.18, type: 'triangle', vol: 0.12 });
  },
  // the clatter of reels going round; called every frame, throttled
  spinTick(speed = 1) {
    if (!throttle(70 / speed)) return;
    tone({ f: 1100 + Math.random() * 300, dur: 0.025, type: 'square', vol: 0.018 });
  },
  reelStop(i, special = false) {
    tone({ f: 150 + i * 12, f2: 70, dur: 0.16, type: 'sine', vol: 0.32 });
    noise({ dur: 0.06, vol: 0.12, f: 2500, q: 1, attack: 0.05 });
    if (special) tone({ f: NOTE(84 + i * 2), dur: 0.3, type: 'triangle', vol: 0.12, at: 0.02 });
  },
  // a grill landing: sizzle + bell, higher for each grill on the screen
  grill(n) {
    noise({ dur: 0.5, vol: 0.12, f: 5000, q: 0.6, type: 'highpass', attack: 0.05 });
    const base = [72, 76, 79, 84, 88][Math.min(n, 5) - 1] || 72;
    tone({ f: NOTE(base), dur: 0.5, type: 'triangle', vol: 0.2 });
    tone({ f: NOTE(base + 12), dur: 0.35, type: 'sine', vol: 0.08, at: 0.02 });
  },
  // near miss: the last reels spin longer with a rising tremolo
  tease(durS = 1.2) {
    tone({ f: NOTE(55), f2: NOTE(67), dur: durS, type: 'sawtooth', vol: 0.05, vib: 18, lp: 1500, attack: 0.2 });
    tone({ f: NOTE(62), f2: NOTE(74), dur: durS, type: 'sawtooth', vol: 0.04, vib: 22, lp: 1500, attack: 0.2 });
    noise({ dur: durS, vol: 0.06, f: 600, f2: 3000, q: 0.8, attack: 0.8 });
  },

  // ---- wins
  // a line lighting up during the win cycle
  line(i = 0) {
    const n = [72, 74, 76, 79, 81, 84][i % 6];
    tone({ f: NOTE(n), dur: 0.22, type: 'triangle', vol: 0.12 });
    tone({ f: NOTE(n + 7), dur: 0.18, type: 'sine', vol: 0.05, at: 0.03 });
  },
  // the jingle when a win is shown; level 0 (small) to 2 (nice)
  win(level = 0) {
    const seqs = [[72, 76, 79], [72, 76, 79, 84], [67, 72, 76, 79, 84, 88]];
    const seq = seqs[Math.min(level, 2)];
    seq.forEach((n, i) => {
      tone({ f: NOTE(n), at: i * 0.07, dur: 0.3, type: 'triangle', vol: 0.15 });
      tone({ f: NOTE(n + 12), at: i * 0.07, dur: 0.2, type: 'sine', vol: 0.05 });
    });
    coins(4 + level * 6, 0.1, 0.4 + level * 0.3);
  },
  // the counter rolling up; pitch climbs with progress (0..1)
  count(p = 0) {
    if (!throttle(55)) return;
    tone({ f: 900 + p * 1300, dur: 0.035, type: 'square', vol: 0.03 });
    tone({ f: 2400 + p * 1600, dur: 0.05, type: 'sine', vol: 0.03 });
  },
  countEnd() {
    tone({ f: NOTE(84), dur: 0.5, type: 'sine', vol: 0.18 });
    tone({ f: NOTE(91), dur: 0.4, type: 'sine', vol: 0.08, at: 0.03 });
  },
  // BIG / MEGA / EPIC WIN fanfare (tier 1..3); returns how long it lasts
  bigWin(tier = 1) {
    const t0 = 0;
    brass([60, 64, 67], t0, 0.25);
    brass([60, 64, 67], t0 + 0.28, 0.12);
    brass([65, 69, 72], t0 + 0.42, 0.25);
    brass([67, 71, 74], t0 + 0.7, 0.25);
    brass([72, 76, 79, 84], t0 + 1.0, 1.2, 0.08);
    tone({ f: NOTE(96), at: t0 + 1.0, dur: 1.4, type: 'sine', vol: 0.06, vib: 8 });
    noise({ at: t0 + 1.0, dur: 1.2, vol: 0.1, f: 6000, q: 0.5, type: 'highpass', attack: 0.02 });
    coins(20 + tier * 15, 0.9, 1.5 + tier * 0.8);
    if (tier >= 2) {
      brass([74, 77, 81, 86], t0 + 2.3, 0.25, 0.07);
      brass([76, 79, 84, 88], t0 + 2.6, 1.0, 0.08);
    }
    if (tier >= 3) {
      brass([77, 81, 84, 89], t0 + 3.7, 0.25, 0.07);
      brass([79, 84, 88, 91], t0 + 4.0, 1.3, 0.09);
      noise({ at: t0 + 4.0, dur: 1.4, vol: 0.12, f: 5000, q: 0.4, type: 'highpass', attack: 0.02 });
    }
    return 2.4 + (tier >= 2 ? 1.3 : 0) + (tier >= 3 ? 1.5 : 0);
  },

  // ---- random features
  feature(kind) {
    switch (kind) {
      case 'flame': case 'hot':
        noise({ dur: 0.9, vol: 0.28, f: 200, f2: 2400, q: 0.6 });
        for (let i = 0; i < 10; i++) noise({ at: 0.1 + Math.random() * 0.8, dur: 0.04, vol: 0.12, f: 3000 + Math.random() * 3000, q: 3, attack: 0.1 });
        tone({ f: NOTE(48), f2: NOTE(60), dur: 0.7, type: 'sawtooth', vol: 0.06, lp: 900 });
        break;
      case 'mystery':
        for (let i = 0; i < 14; i++) noise({ at: i * 0.05, dur: 0.05, vol: 0.06 + i * 0.008, f: 180, q: 1, attack: 0.1 });
        break;
      case 'mega':
        tone({ f: 70, f2: 40, dur: 0.8, type: 'sine', vol: 0.5 });
        brass([48, 55, 60, 64], 0.05, 0.9, 0.08);
        break;
      case 'charcoal':
        for (let i = 0; i < 16; i++) noise({ at: Math.random() * 1, dur: 0.03, vol: 0.14, f: 2000 + Math.random() * 4000, q: 4, attack: 0.1 });
        noise({ dur: 1.0, vol: 0.08, f: 400, q: 0.5 });
        break;
      case 'sure':
        [76, 79, 84, 88].forEach((n, i) => tone({ f: NOTE(n), at: i * 0.06, dur: 0.35, type: 'triangle', vol: 0.14 }));
        break;
      case 'respin':
        noise({ dur: 0.45, vol: 0.2, f: 2500, f2: 300, q: 0.8 });
        tone({ f: 900, f2: 300, dur: 0.4, type: 'triangle', vol: 0.08 });
        break;
      default: break;
    }
  },
  // a bun / cell flipping to show what it is
  reveal() { tone({ f: NOTE(79), f2: NOTE(91), dur: 0.15, type: 'triangle', vol: 0.15 }); coins(3, 0.05, 0.2); },
  ignite() { noise({ dur: 0.25, vol: 0.18, f: 900, f2: 4000, q: 0.7 }); tone({ f: NOTE(72), dur: 0.2, type: 'triangle', vol: 0.08 }); },

  // ---- Grill Bonus
  bonusTrigger() {
    [60, 64, 67, 72, 76, 79, 84].forEach((n, i) => tone({ f: NOTE(n), at: i * 0.07, dur: 0.25, type: 'square', vol: 0.05 }));
    brass([72, 76, 79], 0.5, 1.0, 0.08);
    noise({ at: 0.5, dur: 1.0, vol: 0.12, f: 5000, q: 0.5, type: 'highpass', attack: 0.02 });
    coins(14, 0.5, 1.0);
  },
  chase(i = 0) { tone({ f: NOTE(72 + (i % 5) * 2), dur: 0.06, type: 'square', vol: 0.04 }); },
  award() { brass([67, 72, 76], 0, 0.7, 0.08); tone({ f: NOTE(88), dur: 0.6, type: 'sine', vol: 0.08, at: 0.05 }); },
  heatUp() {
    [60, 64, 67, 72, 76].forEach((n, i) => tone({ f: NOTE(n), at: i * 0.05, dur: 0.2, type: 'triangle', vol: 0.14 }));
    noise({ dur: 0.6, vol: 0.2, f: 300, f2: 3000, q: 0.6 });
    brass([72, 76, 79], 0.3, 0.6, 0.07);
  },
  heatDown() {
    [72, 67, 63, 60].forEach((n, i) => tone({ f: NOTE(n), at: i * 0.09, dur: 0.25, type: 'triangle', vol: 0.12 }));
    noise({ dur: 0.6, vol: 0.12, f: 3000, f2: 400, q: 0.8 });
  },
  burnt() {
    noise({ dur: 1.4, vol: 0.25, f: 6000, f2: 1500, q: 0.4, type: 'highpass', attack: 0.02 });
    tone({ f: 200, f2: 60, dur: 0.8, type: 'sawtooth', vol: 0.08, lp: 600 });
  },
  pick() { tone({ f: 600, f2: 1000, dur: 0.08, type: 'triangle', vol: 0.12 }); noise({ dur: 0.15, vol: 0.08, f: 2000, q: 1 }); },
  prize(p = 0) {
    tone({ f: NOTE(76 + p * 2), dur: 0.3, type: 'triangle', vol: 0.16 });
    tone({ f: NOTE(83 + p * 2), dur: 0.3, type: 'sine', vol: 0.08, at: 0.05 });
    coins(6, 0.05, 0.3);
  },
  ants() {
    for (let i = 0; i < 6; i++) tone({ f: 140 + Math.random() * 40, dur: 0.12, type: 'sawtooth', vol: 0.05, at: i * 0.08, lp: 900 });
    tone({ f: 300, f2: 120, dur: 0.5, type: 'triangle', vol: 0.1, at: 0.1 });
  },
  double() {
    tone({ f: 300, f2: 1500, dur: 0.4, type: 'sawtooth', vol: 0.06, lp: 3000 });
    tone({ f: 450, f2: 2250, dur: 0.4, type: 'triangle', vol: 0.08 });
    tone({ f: NOTE(96), dur: 0.5, type: 'sine', vol: 0.08, at: 0.38 });
  },
  sizzle() { noise({ dur: 0.7, vol: 0.14, f: 6000, q: 0.5, type: 'highpass', attack: 0.05 }); },
  // Golden Grill: each step chimes higher with the value
  step(value = 10) {
    const n = 64 + Math.min(30, Math.round(Math.log2(value) * 3.4));
    tone({ f: NOTE(n), dur: 0.25, type: 'triangle', vol: 0.14 });
    tone({ f: NOTE(n + 12), dur: 0.2, type: 'sine', vol: 0.06, at: 0.04 });
    noise({ dur: 0.3, vol: 0.08, f: 800, f2: 3000, q: 0.7 });
  },
  cool() { noise({ dur: 1.2, vol: 0.18, f: 5000, f2: 1200, q: 0.4, type: 'highpass', attack: 0.02 }); tone({ f: NOTE(60), dur: 0.8, type: 'sine', vol: 0.1 }); },
  freeStart() { [67, 72, 76, 79, 84].forEach((n, i) => tone({ f: NOTE(n), at: i * 0.08, dur: 0.3, type: 'square', vol: 0.05 })); brass([60, 67, 72], 0.4, 0.8, 0.08); },
  multUp() { tone({ f: NOTE(79), f2: NOTE(91), dur: 0.2, type: 'triangle', vol: 0.14 }); tone({ f: NOTE(91), dur: 0.3, type: 'sine', vol: 0.06, at: 0.15 }); },
  link() { tone({ f: NOTE(84), dur: 0.12, type: 'square', vol: 0.05 }); tone({ f: NOTE(88), dur: 0.2, type: 'square', vol: 0.05, at: 0.08 }); },
  feed() { tone({ f: NOTE(88), dur: 0.15, type: 'sine', vol: 0.07 }); tone({ f: NOTE(93), dur: 0.2, type: 'sine', vol: 0.06, at: 0.08 }); },
};
