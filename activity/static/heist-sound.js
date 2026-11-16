// Raccoon Heist sounds, synthesized with the Web Audio API (no audio files, so nothing to license or download).
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
  g.gain.exponentialRampToValueAtTime(vol, t + Math.min(0.03, dur / 3));
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(fl); fl.connect(g); g.connect(master);
  src.start(t); src.stop(t + dur + 0.05);
}

// a minor-pentatonic scale for a sneaky feel
const NOTES = [392, 466.16, 523.25, 587.33, 698.46, 784, 932.33, 1046.5, 1174.66, 1396.91, 1568, 1864.66];

export const sfx = {
  click() { tone({ f: 900, dur: 0.05, type: 'triangle', vol: 0.15 }); },
  bet(up) { tone({ f: up ? 700 : 520, f2: up ? 900 : 420, dur: 0.08, type: 'triangle', vol: 0.18 }); },
  error() { tone({ f: 220, dur: 0.18, type: 'square', vol: 0.12, lp: 900 }); tone({ f: 180, at: 0.1, dur: 0.2, type: 'square', vol: 0.1, lp: 900 }); },
  spinStart() { noise({ dur: 0.35, vol: 0.12, f: 300, f2: 1400, q: 0.7 }); tone({ f: 196, f2: 392, dur: 0.3, type: 'triangle', vol: 0.1 }); },
  land(c) { tone({ f: 150 + c * 14, f2: 80, dur: 0.1, type: 'sine', vol: 0.2 }); noise({ dur: 0.06, vol: 0.05, f: 2200, q: 2 }); },
  fall() { noise({ dur: 0.2, vol: 0.06, f: 600, f2: 250, q: 1 }); },
  // a raccoon hops to the next grab; n counts the grabs in this raid (pitch climbs)
  hop(n) { tone({ f: 300 + Math.min(n, 20) * 18, f2: 520 + Math.min(n, 20) * 22, dur: 0.07, type: 'triangle', vol: 0.09 }); },
  grab(n) {
    const f = NOTES[Math.min(n, NOTES.length - 1)];
    tone({ f, dur: 0.12, type: 'triangle', vol: 0.15 });
    tone({ f: f * 2, at: 0.02, dur: 0.08, type: 'sine', vol: 0.06 });
  },
  coin() { tone({ f: 1568, dur: 0.08, type: 'square', vol: 0.06, lp: 5000 }); tone({ f: 2093, at: 0.06, dur: 0.16, type: 'square', vol: 0.06, lp: 5000 }); },
  junk() { noise({ dur: 0.12, vol: 0.12, f: 900, q: 4 }); },
  levelUp() { [0, 2, 4].forEach((k, i) => tone({ f: NOTES[4 + k], at: i * 0.07, dur: 0.18, type: 'triangle', vol: 0.14 })); },
  teamUp() {
    [0, 1, 2, 3, 4, 5].forEach((k, i) => tone({ f: NOTES[2 + k], at: i * 0.06, dur: 0.22, type: 'triangle', vol: 0.15 }));
    tone({ f: NOTES[9], at: 0.4, dur: 0.6, type: 'sine', vol: 0.12 });
  },
  drill() {
    tone({ f: 120, f2: 180, dur: 0.7, type: 'sawtooth', vol: 0.12, lp: 900 });
    noise({ dur: 0.7, vol: 0.12, f: 2500, q: 3 });
  },
  crumble() { noise({ dur: 0.6, vol: 0.3, f: 300, f2: 90, q: 0.6, type: 'lowpass' }); tone({ f: 70, f2: 40, dur: 0.4, type: 'sine', vol: 0.3 }); },
  paint() { noise({ dur: 0.25, vol: 0.2, f: 1200, f2: 400, q: 1.5 }); tone({ f: 500, f2: 250, dur: 0.15, type: 'sine', vol: 0.12 }); },
  hook() { noise({ dur: 0.3, vol: 0.12, f: 3000, f2: 800, q: 2 }); tone({ f: 1200, f2: 300, dur: 0.25, type: 'triangle', vol: 0.08 }); },
  radio() { tone({ f: 1800, dur: 0.06, type: 'square', vol: 0.06, lp: 3000 }); noise({ at: 0.05, dur: 0.3, vol: 0.08, f: 2000, q: 0.5 }); tone({ f: 1800, at: 0.36, dur: 0.06, type: 'square', vol: 0.06, lp: 3000 }); },
  blueprint() { [0, 4, 7].forEach((k, i) => tone({ f: NOTES[k], at: i * 0.08, dur: 0.25, type: 'sine', vol: 0.14 })); },
  safe() { tone({ f: 300, dur: 0.05, type: 'square', vol: 0.08, lp: 1500 }); tone({ f: 340, at: 0.1, dur: 0.05, type: 'square', vol: 0.08, lp: 1500 }); tone({ f: 400, at: 0.2, dur: 0.18, type: 'triangle', vol: 0.12 }); },
  wild() { tone({ f: 880, f2: 1760, dur: 0.18, type: 'sine', vol: 0.14 }); tone({ f: 1320, at: 0.05, dur: 0.2, type: 'triangle', vol: 0.08 }); },
  scuffle() {
    for (let i = 0; i < 6; i++) noise({ at: i * 0.07, dur: 0.08, vol: 0.18, f: 400 + Math.random() * 900, q: 1.5 });
    tone({ f: 220, f2: 140, at: 0.1, dur: 0.2, type: 'square', vol: 0.06, lp: 800 });
  },
  meter() { tone({ f: 660, f2: 990, dur: 0.1, type: 'sine', vol: 0.06 }); },
  boss() {
    tone({ f: 82, f2: 55, dur: 1.2, type: 'sawtooth', vol: 0.22, lp: 500 });
    noise({ dur: 0.9, vol: 0.2, f: 200, f2: 700, q: 0.6, type: 'lowpass' });
    [0, 1, 2].forEach((i) => tone({ f: [196, 233.08, 293.66][i], at: 0.5 + i * 0.16, dur: 0.5, type: 'triangle', vol: 0.12 }));
  },
  stomp() { tone({ f: 90, f2: 40, dur: 0.3, type: 'sine', vol: 0.4 }); noise({ dur: 0.25, vol: 0.2, f: 300, type: 'lowpass' }); },
  dash() { noise({ dur: 0.45, vol: 0.2, f: 600, f2: 3000, q: 0.8 }); },
  alarm() {
    for (let i = 0; i < 4; i++) {
      tone({ f: 880, f2: 660, at: i * 0.22, dur: 0.2, type: 'square', vol: 0.08, lp: 3000 });
    }
  },
  locker() { tone({ f: 260, f2: 200, dur: 0.12, type: 'square', vol: 0.06, lp: 1200 }); noise({ dur: 0.1, vol: 0.08, f: 1500, q: 2 }); },
  night() {
    [0, 2, 4, 7, 9].forEach((k, i) => tone({ f: NOTES[k] / 2, at: i * 0.14, dur: 0.6, type: 'triangle', vol: 0.12 }));
    tone({ f: NOTES[7], at: 0.8, dur: 1.0, type: 'sine', vol: 0.12 });
  },
  extra() { tone({ f: NOTES[5], dur: 0.12, type: 'triangle', vol: 0.14 }); tone({ f: NOTES[8], at: 0.08, dur: 0.2, type: 'triangle', vol: 0.12 }); },
  count(p) { if (Math.random() < 0.35) tone({ f: 1400 + p * 900, dur: 0.03, type: 'square', vol: 0.04, lp: 4000 }); },
  countEnd() { tone({ f: 1568, dur: 0.12, type: 'triangle', vol: 0.15 }); tone({ f: 2093, at: 0.07, dur: 0.18, type: 'triangle', vol: 0.12 }); },
  feed() { tone({ f: 1046, dur: 0.1, type: 'triangle', vol: 0.1 }); tone({ f: 1568, at: 0.08, dur: 0.14, type: 'triangle', vol: 0.08 }); },
  // big win fanfare; returns how long it plays (seconds)
  bigWin(tier) {
    const seq = [0, 2, 4, 5, 4, 5, 7, 9].slice(0, 4 + tier * 2);
    seq.forEach((n, i) => {
      tone({ f: NOTES[Math.min(n, NOTES.length - 1)], at: i * 0.16, dur: 0.3, type: 'triangle', vol: 0.18 });
      tone({ f: NOTES[Math.min(n, NOTES.length - 1)] / 2, at: i * 0.16, dur: 0.3, type: 'sine', vol: 0.1 });
    });
    const end = seq.length * 0.16;
    tone({ f: NOTES[7], at: end, dur: 1.0, type: 'triangle', vol: 0.18 });
    tone({ f: NOTES[4], at: end, dur: 1.0, type: 'triangle', vol: 0.12 });
    return end + 1.6 + tier * 0.6;
  },
};
