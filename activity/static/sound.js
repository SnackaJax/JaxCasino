// Spin Frenzy sounds, generated with the Web Audio API (no audio files).
// Browsers only allow sound after the player has tapped or clicked something, so unlock() is called on the
// first interaction. The mute choice is remembered per device. Tweak a sound by changing its numbers below.

let ctx = null;
let master = null;
let muted = false;
try { muted = localStorage.getItem('spinfrenzy-muted') === '1'; } catch (e) { /* storage blocked */ }

const VOLUME = 0.55;

function audio() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    const comp = ctx.createDynamicsCompressor();
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

// One tone with a quick attack and exponential fade, optionally sliding in pitch.
function tone({ f = 440, f2 = null, at = 0, dur = 0.12, type = 'sine', vol = 0.3, attack = 0.004 }) {
  if (!ready()) return;
  const t = ctx.currentTime + at;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(f, t);
  if (f2) osc.frequency.exponentialRampToValueAtTime(f2, t + dur);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  osc.connect(g); g.connect(master);
  osc.start(t); osc.stop(t + dur + 0.02);
}

let noiseBuffer = null;
// Filtered white noise: whooshes, rustles.
function noise({ at = 0, dur = 0.25, vol = 0.2, f = 1200, f2 = null, q = 1.2 }) {
  if (!ready()) return;
  if (!noiseBuffer) {
    noiseBuffer = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const d = noiseBuffer.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  }
  const t = ctx.currentTime + at;
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer;
  const bp = ctx.createBiquadFilter();
  bp.type = 'bandpass'; bp.Q.value = q;
  bp.frequency.setValueAtTime(f, t);
  if (f2) bp.frequency.exponentialRampToValueAtTime(f2, t + dur);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + dur * 0.3);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(bp); bp.connect(g); g.connect(master);
  src.start(t); src.stop(t + dur + 0.02);
}

let lastTick = 0;
function throttled(minGapMs) {
  const now = performance.now();
  if (now - lastTick < minGapMs) return false;
  lastTick = now;
  return true;
}

export const sfx = {
  // placing a chip
  chip() {
    tone({ f: 1500, f2: 900, dur: 0.06, type: 'triangle', vol: 0.22 });
    tone({ f: 3200, dur: 0.03, type: 'sine', vol: 0.06, at: 0.012 });
  },
  clear() { tone({ f: 700, f2: 350, dur: 0.12, type: 'triangle', vol: 0.15 }); },
  error() { tone({ f: 220, dur: 0.14, type: 'square', vol: 0.06 }); tone({ f: 180, dur: 0.16, type: 'square', vol: 0.06, at: 0.1 }); },
  // last seconds of betting, and betting closing
  countdown() { tone({ f: 1046, dur: 0.07, type: 'sine', vol: 0.18 }); },
  closed() { tone({ f: 784, dur: 0.12, type: 'triangle', vol: 0.2 }); tone({ f: 523, dur: 0.2, type: 'triangle', vol: 0.2, at: 0.11 }); },
  // Boost Reel
  reelTick() { tone({ f: 700 + Math.random() * 500, dur: 0.035, type: 'square', vol: 0.035 }); },
  reelHit() {
    [880, 1109, 1319, 1760].forEach((f, i) => tone({ f, dur: 0.18, type: 'triangle', vol: 0.18, at: i * 0.06 }));
    tone({ f: 2637, dur: 0.5, type: 'sine', vol: 0.08, at: 0.24 });
  },
  reelMiss() { tone({ f: 330, f2: 196, dur: 0.3, type: 'triangle', vol: 0.14 }); },
  // a wheel segment passing the pointer (pitch varies a little)
  tick(pitch = 1) {
    if (!throttled(28)) return;
    tone({ f: 2200 * pitch, dur: 0.022, type: 'square', vol: 0.05 });
    tone({ f: 900 * pitch, dur: 0.03, type: 'triangle', vol: 0.05 });
  },
  // the wheel stopping on a number
  land() {
    tone({ f: 1047, dur: 0.6, type: 'sine', vol: 0.22 });
    tone({ f: 1568, dur: 0.5, type: 'sine', vol: 0.12, at: 0.02 });
    tone({ f: 2093, dur: 0.35, type: 'sine', vol: 0.06, at: 0.04 });
  },
  // the wheel stopping on a bonus
  bonus() {
    [523, 659, 784, 1047, 1319].forEach((f, i) => tone({ f, dur: 0.16, type: 'square', vol: 0.07, at: i * 0.085 }));
    [1047, 1319, 1568].forEach((f) => tone({ f, dur: 0.8, type: 'triangle', vol: 0.12, at: 0.45 }));
  },
  // DOUBLE / TRIPLE
  boost() {
    tone({ f: 260, f2: 1400, dur: 0.45, type: 'sawtooth', vol: 0.06 });
    tone({ f: 390, f2: 2100, dur: 0.45, type: 'triangle', vol: 0.08 });
    tone({ f: 1760, dur: 0.4, type: 'sine', vol: 0.12, at: 0.42 });
  },
  pop() { tone({ f: 500, f2: 1200, dur: 0.08, type: 'sine', vol: 0.16 }); },
  whoosh() { noise({ dur: 0.5, vol: 0.25, f: 400, f2: 2600, q: 0.8 }); },
  shuffle() { for (let i = 0; i < 6; i++) noise({ at: i * 0.11, dur: 0.1, vol: 0.12, f: 1800 + Math.random() * 1500, q: 2 }); },
  peg(pitch = 1) { tone({ f: 1300 * pitch, dur: 0.05, type: 'triangle', vol: 0.09 }); },
  // winning credits; bigger wins jingle longer
  win(big = false) {
    const n = big ? 14 : 7;
    for (let i = 0; i < n; i++) {
      tone({ f: 1800 + Math.random() * 1400, dur: 0.12, type: 'sine', vol: 0.09, at: i * 0.065 });
    }
    [784, 988, 1175, 1568].forEach((f, i) => tone({ f, dur: 0.25, type: 'triangle', vol: 0.12, at: i * 0.07 }));
  },
};
