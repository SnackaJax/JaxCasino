// Poker sounds, synthesized with the Web Audio API (no audio files). The mute switch is shared with the other
// games. Browsers only allow sound after a tap or click, so unlock() runs on the first interaction.

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
    master = ctx.createGain();
    master.gain.value = muted ? 0 : VOLUME;
    master.connect(comp); comp.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}
export function unlock() { audio(); }
export function isMuted() { return muted; }
export function setMuted(v) {
  muted = v;
  try { localStorage.setItem('spinfrenzy-muted', v ? '1' : '0'); } catch (e) { /* storage blocked */ }
  if (master) master.gain.setTargetAtTime(v ? 0 : VOLUME, ctx.currentTime, 0.02);
}
const ready = () => !muted && ctx && ctx.state === 'running';
// the announcer's lines from the server (dealer-voice.js), played through this output so the mute switch covers them
export function decodeAudio(arrayBuffer) {
  const c = audio();
  if (!c) return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      const p = c.decodeAudioData(arrayBuffer, resolve, () => resolve(null));
      if (p && p.catch) p.catch(() => resolve(null));
    } catch (e) { resolve(null); }
  });
}
export function playBuffer(buffer, volume = 1.4) {
  if (!ready() || !buffer) return { done: Promise.resolve(), stop() {} };
  const src = ctx.createBufferSource();
  const g = ctx.createGain();
  src.buffer = buffer; g.gain.value = volume;
  src.connect(g); g.connect(master);
  const done = new Promise((r) => { src.onended = r; });
  src.start();
  return { done, stop() { try { src.stop(); } catch (e) { /* already stopped */ } } };
}

function tone({ f = 440, f2 = null, at = 0, dur = 0.12, type = 'sine', vol = 0.3, attack = 0.004 }) {
  if (!ready()) return;
  const t = ctx.currentTime + at;
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type; o.frequency.setValueAtTime(f, t);
  if (f2) o.frequency.exponentialRampToValueAtTime(f2, t + dur);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.05);
}
let buf = null;
function noise({ at = 0, dur = 0.1, vol = 0.2, f = 2000, f2 = null, q = 1, type = 'bandpass', attack = 0.2 }) {
  if (!ready()) return;
  if (!buf) {
    buf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  }
  const t = ctx.currentTime + at;
  const s = ctx.createBufferSource(); s.buffer = buf;
  const fl = ctx.createBiquadFilter(); fl.type = type; fl.Q.value = q; fl.frequency.setValueAtTime(f, t);
  if (f2) fl.frequency.exponentialRampToValueAtTime(f2, t + dur);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + Math.max(0.004, dur * attack));
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  s.connect(fl); fl.connect(g); g.connect(master); s.start(t, Math.random() * 0.5); s.stop(t + dur + 0.05);
}
const NOTE = (n) => 440 * Math.pow(2, (n - 69) / 12);

export const sfx = {
  click() { tone({ f: 1500, f2: 1000, dur: 0.04, type: 'triangle', vol: 0.1 }); },
  error() { tone({ f: 220, dur: 0.14, type: 'square', vol: 0.06 }); },
  // a card sliding across the felt
  deal() { noise({ dur: 0.11, vol: 0.22, f: 3500, f2: 1500, q: 0.8, attack: 0.15 }); },
  // a card snapping over
  flip() { noise({ dur: 0.05, vol: 0.25, f: 2600, q: 1.5, attack: 0.05 }); tone({ f: 900, f2: 500, dur: 0.05, type: 'triangle', vol: 0.06 }); },
  // chips clicking onto the table; more chips, more clicks
  chips(n = 3) {
    for (let i = 0; i < n; i++) {
      const at = i * 0.035 + Math.random() * 0.015;
      tone({ f: 3800 + Math.random() * 1200, dur: 0.04, type: 'sine', vol: 0.09, at });
      noise({ at, dur: 0.03, vol: 0.08, f: 5000, q: 2, attack: 0.05 });
    }
  },
  check() { [0, 0.11].forEach((at) => { tone({ f: 160, f2: 90, dur: 0.08, type: 'sine', vol: 0.35, at }); noise({ at, dur: 0.04, vol: 0.12, f: 900, q: 1, attack: 0.05 }); }); },
  fold() { noise({ dur: 0.28, vol: 0.18, f: 1800, f2: 500, q: 0.7, attack: 0.1 }); },
  allin() {
    tone({ f: NOTE(55), f2: NOTE(67), dur: 0.5, type: 'sawtooth', vol: 0.05 });
    sfx.chips(10);
    [67, 71, 74, 79].forEach((n, i) => tone({ f: NOTE(n), at: 0.25 + i * 0.07, dur: 0.25, type: 'triangle', vol: 0.12 }));
  },
  yourTurn() { tone({ f: NOTE(76), dur: 0.18, type: 'sine', vol: 0.18 }); tone({ f: NOTE(83), dur: 0.3, type: 'sine', vol: 0.14, at: 0.12 }); },
  tick() { tone({ f: 1200, dur: 0.03, type: 'square', vol: 0.04 }); },
  sweep() { for (let i = 0; i < 8; i++) tone({ f: 3500 + Math.random() * 1500, dur: 0.04, vol: 0.06, at: 0.05 + i * 0.03 }); noise({ dur: 0.3, vol: 0.1, f: 2000, q: 0.6 }); },
  reveal() { tone({ f: NOTE(72), dur: 0.2, type: 'triangle', vol: 0.12 }); tone({ f: NOTE(79), dur: 0.3, type: 'triangle', vol: 0.1, at: 0.08 }); },
  win(big = false) {
    const seq = big ? [60, 64, 67, 72, 76, 79, 84] : [67, 72, 76, 79];
    seq.forEach((n, i) => { tone({ f: NOTE(n), at: i * 0.075, dur: 0.3, type: 'triangle', vol: 0.15 }); tone({ f: NOTE(n + 12), at: i * 0.075, dur: 0.2, vol: 0.05 }); });
    sfx.chips(big ? 16 : 9);
  },
  lose() { [64, 60, 57].forEach((n, i) => tone({ f: NOTE(n), at: i * 0.12, dur: 0.3, type: 'triangle', vol: 0.08 })); },
};
