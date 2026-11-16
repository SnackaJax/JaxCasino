// The blackjack dealer's voice. Two ways to speak:
//  - the server's voice (activity/voice.py, Kokoro or Piper): the page sends the line a sentence at a time, gets
//    WAVs back and plays them through the game's sound output. Works everywhere, including Android's Discord app,
//    whose WebView has no speech synthesis of its own; everyone hears the same dealer.
//  - the device's own speech synthesis, with a female English voice when there is one: used when the server has
//    no voice, or a line from the server doesn't arrive.
// It can be switched off on its own (the 🗣 button), and the sound mute switch silences it too.
import { decodeAudio, playBuffer } from './blackjack-sound.js';

const KEY = 'blackjack-voice';
const synth = typeof window !== 'undefined' && 'speechSynthesis' in window ? window.speechSynthesis : null;
// voices that are female on the common systems: Windows (Zira, Aria, Jenny, Hazel, Susan, Libby, Sonia), Chrome
// ("Google UK English Female", "Google US English"), Apple (Samantha, Karen, Moira, Tessa, Fiona, Victoria, Serena)
const FEMALE = /female|zira|aria|jenny|hazel|susan|libby|sonia|michelle|emma|ava|allison|samantha|karen|moira|tessa|fiona|victoria|serena|kate|catherine|google us english|google uk english female/i;
const MALE = /male|david|mark|george|ryan|guy|daniel|alex|fred|oliver|thomas|james/i;
const STALE_MS = 6000;   // a sentence from the server that takes longer than this to come back is skipped (and the
                         // rest of its line): the table moved on

let enabled = true;
try { enabled = localStorage.getItem(KEY) !== '0'; } catch (e) { /* storage blocked */ }
let voice = null;
let isMuted = () => false;
let queued = 0;
let server = null;   // { session } while the server voice is on

function pick() {
  if (!synth) return;
  const all = synth.getVoices().filter((v) => /^en(-|_|$)/i.test(v.lang));
  const score = (v) => (FEMALE.test(v.name) ? 10 : 0) - (MALE.test(v.name) && !/female/i.test(v.name) ? 10 : 0)
    + (/en-(GB|US)/i.test(v.lang) ? 2 : 0) + (/natural|online|neural/i.test(v.name) ? 3 : 0) + (v.localService ? 1 : 0);
  voice = all.sort((a, b) => score(b) - score(a))[0] || null;
}
if (synth) {
  pick();
  if (synth.addEventListener) synth.addEventListener('voiceschanged', pick);
}

export const voiceAvailable = () => !!server || !!synth;
export const voiceOn = () => enabled;
export function setVoiceOn(v) {
  enabled = v;
  try { localStorage.setItem(KEY, v ? '1' : '0'); } catch (e) { /* storage blocked */ }
  if (!v) hush();
}
export function linkMute(fn) { isMuted = fn; }

// ------------------------------------------------------------------ the server's voice
const lines = new Map();   // text -> Promise<AudioBuffer | null>, the last few lines (the fixed ones come back a lot)
function fetchLine(text) {
  if (lines.has(text)) {
    const p = lines.get(text);
    lines.delete(text); lines.set(text, p);   // most recently used last
    return p;
  }
  const p = fetch('/api/voice', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: server.session, text }) })
    .then((r) => (r.ok ? r.arrayBuffer() : null))
    .then((ab) => (ab ? decodeAudio(ab) : null))
    .catch(() => null);
  p.then((b) => { if (!b) lines.delete(text); });   // a failed line may be asked for again
  lines.set(text, p);
  while (lines.size > 40) lines.delete(lines.keys().next().value);
  return p;
}
// Turns the server's voice on (the server said it has one), and gets the lines every round needs ready.
export function useServerVoice(session) {
  server = { session };
  for (const t of ['Place your bets, please.', 'No more bets, thank you.', 'Dealer busts!', 'Dealer has blackjack.']) fetchLine(t);
}

// A line in sentences, asked for one after another: the first one is back quickly and plays while the server
// makes the next, and the sentences that come back every round ("Dealer has 19.") are made only once.
function sentences(text) {
  let prev = Promise.resolve();
  return text.split(/(?<=[.!?])\s+/).filter(Boolean).map((t) => {
    const buf = prev.then(() => fetchLine(t));
    prev = buf;
    return { text: t, buf };
  });
}

const waiting = [];
let playing = null;
async function pump() {
  if (playing || !waiting.length) return;
  const item = waiting.shift();
  playing = item;
  let since = item.at;
  for (let i = 0; i < item.parts.length; i++) {
    const buf = await item.parts[i].buf;
    if (playing !== item || !enabled || isMuted()) break;
    if (!buf) {   // the server couldn't say it: the device's own voice, if it has one
      speakHere(item.parts.slice(i).map((p) => p.text).join(' '));
      break;
    }
    if (performance.now() - since > STALE_MS) break;
    const h = playBuffer(buf);
    item.stop = h.stop;
    await h.done;
    since = performance.now();
  }
  if (playing === item) playing = null;
  pump();
}

// ------------------------------------------------------------------ the device's own voice
function speakHere(text, urgent = false) {
  if (!synth) return;
  if (urgent || queued > 1) { synth.cancel(); queued = 0; }
  if (!voice) pick();
  const u = new SpeechSynthesisUtterance(text);
  if (voice) { u.voice = voice; u.lang = voice.lang; } else u.lang = 'en-US';
  u.rate = 1.02;
  u.pitch = voice && FEMALE.test(voice.name) ? 1.05 : 1.25;   // no female voice on this device: a higher pitch
  u.volume = 0.95;
  queued++;
  u.onend = u.onerror = () => { queued = Math.max(0, queued - 1); };
  synth.speak(u);
}

// Says something. A line still waiting while the table has moved on is dropped, so the dealer never lags behind
// the cards: with more than one line waiting, the older ones go.
export function say(text, { urgent = false } = {}) {
  if (!enabled || isMuted() || !text) return;
  if (!server) { speakHere(text, urgent); return; }
  if (urgent) hush();
  while (waiting.length > 1) waiting.shift();
  waiting.push({ parts: sentences(text), at: performance.now() });
  pump();
}
export function hush() {
  waiting.length = 0;
  if (playing && playing.stop) playing.stop();
  playing = null;
  if (synth) { synth.cancel(); queued = 0; }
}

// names as the voice should say them: no emoji or symbols, at most a few words
export function speakName(name) {
  const clean = String(name || '').replace(/[^\p{L}\p{N}' .-]/gu, ' ').replace(/\s+/g, ' ').trim();
  return clean.split(' ').slice(0, 3).join(' ') || 'player';
}
// amounts as words the voice reads well: 1,250 / 2.5 million
export function speakAmount(n) {
  if (n >= 1e6) return `${+(n / 1e6).toFixed(n >= 1e7 ? 1 : 2)} million`;
  return Math.round(n).toLocaleString('en-US');
}
export function list(items) {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}
