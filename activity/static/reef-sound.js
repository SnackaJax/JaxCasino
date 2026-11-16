// Reef Rush sounds, synthesized with the Web Audio API (no audio files, so nothing to license or download).
// Browsers only allow sound after the player has tapped or clicked, so unlock() runs on the first interaction.
// The mute switch is shared with the other games. Tweak a sound by changing its numbers below.

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

function tone({ f = 440, f2 = null, at = 0, dur = 0.12, type = 'sine', vol = 0.3, attack = 0.004, lp = 0 }) {
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
  let node = osc;
  if (lp) {
    const fl = ctx.createBiquadFilter(); fl.type = 'lowpass'; fl.frequency.value = lp;
    osc.connect(fl); node = fl;
  }
  node.connect(g); g.connect(master);
  osc.start(t); osc.stop(t + dur + 0.05);
}

// filtered noise: water swooshes and splashes
function noise({ at = 0, dur = 0.4, vol = 0.2, f = 900, f2 = null, q = 0.8, type = 'bandpass' }) {
  if (!ready()) return;
  const t = ctx.currentTime + at;
  const len = Math.ceil(ctx.sampleRate * (dur + 0.05));
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  const src = ctx.createBufferSource(); src.buffer = buf;
  const fl = ctx.createBiquadFilter(); fl.type = type; fl.Q.value = q;
  fl.frequency.setValueAtTime(f, t);
  if (f2) fl.frequency.exponentialRampToValueAtTime(f2, t + dur);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + Math.min(0.06, dur / 3));
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(fl); fl.connect(g); g.connect(master);
  src.start(t); src.stop(t + dur + 0.05);
}

const NOTES = [523.25, 587.33, 659.25, 783.99, 880, 1046.5, 1174.66, 1318.5, 1568, 1760];

export const sfx = {
  click() { tone({ f: 900, dur: 0.05, type: 'triangle', vol: 0.15 }); },
  bet(up) { tone({ f: up ? 700 : 520, f2: up ? 900 : 420, dur: 0.08, type: 'triangle', vol: 0.18 }); },
  error() { tone({ f: 220, dur: 0.18, type: 'square', vol: 0.12, lp: 900 }); tone({ f: 180, at: 0.1, dur: 0.2, type: 'square', vol: 0.1, lp: 900 }); },
  spinStart() { noise({ dur: 0.45, vol: 0.18, f: 400, f2: 1600, q: 0.7 }); },
  // one column of symbols lands
  land(c) { tone({ f: 180 + c * 12, f2: 90, dur: 0.12, type: 'sine', vol: 0.22 }); noise({ dur: 0.08, vol: 0.05, f: 2500, q: 2 }); },
  // a cluster lights up; cascade number raises the pitch
  win(cascade, big) {
    const base = Math.min(cascade, NOTES.length - 3);
    [0, 2, 4].forEach((k, i) => tone({ f: NOTES[base + Math.min(k, 2)] * (1 + i * 0.0), at: i * 0.06, dur: 0.22, type: 'triangle', vol: big ? 0.22 : 0.16 }));
    if (big) tone({ f: NOTES[base] * 2, at: 0.18, dur: 0.35, type: 'sine', vol: 0.14 });
  },
  pop() { noise({ dur: 0.12, vol: 0.12, f: 1800, f2: 3500, q: 3 }); tone({ f: 1300, f2: 2200, dur: 0.07, type: 'sine', vol: 0.08 }); },
  fall() { noise({ dur: 0.25, vol: 0.07, f: 700, f2: 300, q: 1 }); },
  count(p) { if (Math.random() < 0.35) tone({ f: 1400 + p * 900, dur: 0.03, type: 'square', vol: 0.04, lp: 4000 }); },
  countEnd() { tone({ f: 1568, dur: 0.12, type: 'triangle', vol: 0.15 }); tone({ f: 2093, at: 0.07, dur: 0.18, type: 'triangle', vol: 0.12 }); },
  meter() { tone({ f: 660, f2: 990, dur: 0.12, type: 'sine', vol: 0.08 }); },
  charged() { [0, 1, 2, 3].forEach((i) => tone({ f: NOTES[i * 2], at: i * 0.07, dur: 0.18, type: 'triangle', vol: 0.15 })); },
  surge() {
    noise({ dur: 1.1, vol: 0.28, f: 250, f2: 2200, q: 0.6 });
    tone({ f: 220, f2: 660, dur: 0.9, type: 'sawtooth', vol: 0.08, lp: 1500 });
  },
  wild() { tone({ f: 880, f2: 1760, dur: 0.18, type: 'sine', vol: 0.14 }); tone({ f: 1320, at: 0.05, dur: 0.2, type: 'triangle', vol: 0.08 }); },
  wash() { noise({ dur: 0.7, vol: 0.25, f: 1800, f2: 300, q: 0.5 }); },
  shift() { [0, 1, 2, 3, 4].forEach((i) => tone({ f: 600 + i * 180, at: i * 0.05, dur: 0.12, type: 'sine', vol: 0.1 })); },
  kraken() {
    tone({ f: 70, f2: 45, dur: 1.6, type: 'sawtooth', vol: 0.3, lp: 400 });
    tone({ f: 105, f2: 60, dur: 1.4, type: 'square', vol: 0.1, lp: 300 });
    noise({ dur: 1.4, vol: 0.3, f: 160, f2: 600, q: 0.6, type: 'lowpass' });
    [0, 1, 2].forEach((i) => tone({ f: [196, 233, 294][i], at: 0.6 + i * 0.18, dur: 0.6, type: 'triangle', vol: 0.12 }));
  },
  thud() { tone({ f: 90, f2: 40, dur: 0.35, type: 'sine', vol: 0.4 }); noise({ dur: 0.3, vol: 0.2, f: 300, type: 'lowpass' }); },
  bubble() { for (let i = 0; i < 7; i++) tone({ f: 500 + Math.random() * 900, f2: 1500 + Math.random() * 900, at: i * 0.07, dur: 0.08, type: 'sine', vol: 0.1 }); },
  feed() { tone({ f: 1046, dur: 0.1, type: 'triangle', vol: 0.1 }); tone({ f: 1568, at: 0.08, dur: 0.14, type: 'triangle', vol: 0.08 }); },
  // big win fanfare; returns how long it plays (seconds)
  bigWin(tier) {
    const seq = [0, 2, 4, 5, 4, 5, 7, 9].slice(0, 4 + tier * 2);
    seq.forEach((n, i) => {
      tone({ f: NOTES[Math.min(n, NOTES.length - 1)], at: i * 0.16, dur: 0.3, type: 'triangle', vol: 0.18 });
      tone({ f: NOTES[Math.min(n, NOTES.length - 1)] / 2, at: i * 0.16, dur: 0.3, type: 'sine', vol: 0.1 });
    });
    const end = seq.length * 0.16;
    tone({ f: NOTES[4], at: end, dur: 1.0, type: 'triangle', vol: 0.18 });
    tone({ f: NOTES[2], at: end, dur: 1.0, type: 'triangle', vol: 0.12 });
    return end + 1.6 + tier * 0.6;
  },
};
