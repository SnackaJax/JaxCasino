// Poker client for the Activity. The server (activity/poker.py) runs the tables; this page shows the room, the
// table you're watching or sitting at, and animates what happens: cards dealt from the deck and turned over,
// chips slid in and swept into the pot, the pot pushed to the winners. Sounds come from poker-sound.js; an announcer
// (the blackjack dealer's voice, dealer-voice.js) says who won, with what hand and how much.
import { signIn, retryAfterExpiredSession, go } from './auth.js';
import { sfx, unlock, isMuted, setMuted, decodeAudio, playBuffer } from './poker-sound.js';
import { say, hush, voiceOn, setVoiceOn, voiceAvailable, linkMute, speakName, speakAmount, list, useServerVoice,
  configure } from './dealer-voice.js';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// fmt: exact for any size (big balances arrive as strings of digits)
import { fmt, num, minus, BIG_UNITS, huge } from './money.js';
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const BUY_INS = [100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000];   // if the server sends none
// casino chips: value, colour, edge spots, text (the same set as blackjack.js), up to 10B for the high-stakes tables
const DENOMS = [
  [1, '#f2f2f2', '#3a6fd8', '#222'], [5, '#c8302c', '#fff', '#fff'], [10, '#2b5fc0', '#fff', '#fff'],
  [25, '#178a4c', '#fff', '#fff'], [100, '#1d1d1d', '#e8bd57', '#fff'], [500, '#7b3fb3', '#fff', '#fff'],
  [1000, '#e5a50a', '#5a3400', '#3a2400'], [5000, '#d9468f', '#fff', '#fff'], [10000, '#0f9a9a', '#fff', '#fff'],
  [25000, '#8b5a2b', '#ffe3b0', '#fff'], [100000, '#9aa4b1', '#1d1d1d', '#1d1d1d'], [500000, '#5b1020', '#e8bd57', '#fff'],
  [1000000, '#e8bd57', '#5b1020', '#3a2400'], [2000000, '#1f7a46', '#e8bd57', '#fff'],
  [5000000, '#d0631a', '#fff3d6', '#fff'], [10000000, '#1b2f73', '#e8bd57', '#fff'],
  [25000000, '#2c2c2c', '#c0c7d0', '#fff'], [50000000, '#a3122e', '#f2f2f2', '#fff'],
  [100000000, '#f4f1e8', '#b8860b', '#3a2400'], [250000000, '#6b2fb3', '#f2d27a', '#fff'],
  [500000000, '#0e7c86', '#e8bd57', '#fff'], [1000000000, '#141414', '#e8bd57', '#e8bd57'],
  [2500000000, '#b0b8c4', '#1b2f73', '#1b2f73'], [5000000000, '#0096d6', '#fff', '#fff'],
  [10000000000, '#8fce3a', '#1d1d1d', '#1d1d1d'],
];

{
  const p = new URLSearchParams(location.search);
  if (p.get('platform') === 'mobile' || p.get('mobile') === '1') document.body.classList.add('discord-mobile');
}

let W = null, myId = null, balance = 0, room = { tables: [], players: [] };
let T = null;              // the table being shown (after its animations)
let ws = null, session = null, joinInfo = null, fatal = false, retry = 0, offset = 0;
let buyIn = 1000, seatsWanted = 6;
const srv = () => Date.now() + offset;
// the announcer: its own on/off switch (🗣), played through the poker sound output, quiet while the sound is muted
configure({ key: 'poker-voice', audio: { decodeAudio, playBuffer } });
linkMute(isMuted);

// ------------------------------------------------------------------ connection

// The winstreak medal on a seat's name plate: rounds won in a row at that seat (a push leaves it, a loss ends it),
// as a number on a little medal: bronze, silver from 3, gold from 5, glowing gold from 10.
function setStreak(el, n) {
  el.classList.toggle('hidden', !(n >= 1));
  if (!(n >= 1)) return;
  const text = String(n);
  if (el.textContent !== text) el.textContent = text;
  el.title = n === 1 ? '1 win at this seat' : `${n} wins in a row at this seat`;
  const tier = n >= 10 ? 'blaze' : n >= 5 ? 'gold' : n >= 3 ? 'silver' : 'bronze';
  if (el.dataset.tier !== tier) {
    el.classList.remove('bronze', 'silver', 'gold', 'blaze');
    el.classList.add(tier);
    el.dataset.tier = tier;
  }
}

function loading(text) { $('loading').classList.remove('hidden'); $('loadingText').textContent = text; }
function showFatal(text) {
  fatal = true; loading(text); $('loading').querySelector('.spinner').classList.add('hidden');
}
async function boot() {
  const auth = await signIn(loading);
  if (auth.error) { showFatal(auth.error); return; }
  session = auth.session; joinInfo = auth.joinInfo;
  // local dev server only: lets a test show any table state (used to check the layout at every window size)
  if (auth.cfg && auth.cfg.dev) window.__poker = { show: (st) => enqueue(st), winSpeech };
  connect();
}
function connect() {
  loading('Joining the poker room…');
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/poker`);
  ws.onopen = () => { retry = 0; ws.send(JSON.stringify({ type: 'join', session, ...joinInfo })); };
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    if (fatal) return;
    loading('Connection lost, reconnecting…');
    setTimeout(connect, Math.min(8000, 1000 * 2 ** retry++));
  };
}
function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }
setInterval(() => send({ type: 'ping' }), 25000);

function onMessage(m) {
  if (m.now) offset = m.now - Date.now();
  switch (m.type) {
    case 'welcome':
      W = m; myId = m.you; setupForm(); setupInfo(); $('loading').classList.add('hidden');
      if (m.server_voice) useServerVoice(session, []);
      updateVoiceBtn();
      break;
    case 'me': balance = m.balance; showBalance(); renderForm(); break;
    case 'room': room = m; renderRoom(); break;
    case 'table': enqueue(m); break;
    case 'notice': toast(m.message, true); break;
    case 'error': toast(m.message); sfx.error(); setActButtons(true); break;
    case 'fatal':
      if (m.code === 'session' && retryAfterExpiredSession()) break;
      if (m.code === 'crux' && window.showCrux) window.showCrux(m.until);   // on a /crux break
      showFatal(m.message); try { ws.close(); } catch (e) { /* closed */ }
      break;
    default: break;
  }
}

// the balance in the header, shortened on narrow screens (9.08M) so the header always fits
function showBalance() {
  const el = $('balance');
  el.textContent = innerWidth < 520 && balance >= 1e6 ? shortNum(balance) : fmt(balance);
  el.parentElement.title = `${fmt(balance)} credits`;
}
window.addEventListener('resize', () => {
  showBalance();
  if (T && T.to_call !== undefined && T.turn === T.you) renderControls(T);   // button amounts: short or full
});

let toastTimer = null;
function toast(text, good = false) {
  const t = $('toast'); t.textContent = text; t.classList.remove('hidden'); t.classList.toggle('good', good);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add('hidden'), 3200);
}
function updateMuteBtn() { $('muteBtn').textContent = isMuted() ? '🔇' : '🔊'; }
$('muteBtn').addEventListener('click', () => { unlock(); setMuted(!isMuted()); updateMuteBtn(); if (isMuted()) hush(); });
updateMuteBtn();
function updateVoiceBtn() {
  const b = $('voiceBtn');
  b.classList.toggle('hidden', !voiceAvailable());
  b.classList.toggle('off', !voiceOn());
  b.title = voiceOn() ? "Announcer's voice: on" : "Announcer's voice: off";
}
$('voiceBtn').addEventListener('click', () => {
  unlock(); setVoiceOn(!voiceOn()); updateVoiceBtn();
  if (voiceOn()) say('The announcer is on.', { urgent: true });
});
updateVoiceBtn();
document.addEventListener('pointerdown', () => unlock(), { once: true });
$('gamesBtn').addEventListener('click', () => {
  if (T && T.you >= 0) { toast('Leave the table first (your chips go back to your balance).'); return; }
  go('/');
});
$('infoBtn').addEventListener('click', () => $('infoModal').classList.remove('hidden'));
$('infoClose').addEventListener('click', () => $('infoModal').classList.add('hidden'));
$('infoModal').addEventListener('click', (e) => { if (e.target === $('infoModal')) $('infoModal').classList.add('hidden'); });

// ------------------------------------------------------------------ chips
// Stacks are made of real chip values (like blackjack.js): a bet of 7.5M is a 5M, a 2M and a 500K chip, biggest at
// the bottom. A stack has at most a handful of chips; the amount next to it says exactly how much it is.
const denomFor = (v) => [...DENOMS].reverse().find((d) => d[0] <= v) || DENOMS[0];
function chipHtml(v, style = '') {
  const [, c, e, t] = denomFor(v);
  return `<div class="chip" style="--c:${c};--e:${e};--t:${t};${style}"></div>`;
}
function chipsFor(amount, max) {
  const out = [];
  let left = amount;
  for (let k = DENOMS.length - 1; k >= 0 && out.length < max; k--) {
    const v = DENOMS[k][0];
    while (left >= v && out.length < max) { out.push(v); left -= v; }
  }
  return out.length ? out : [DENOMS[0][0]];
}
function stackHtml(amount, max = 5) {
  if (!(amount > 0)) return '';
  const chips = chipsFor(amount, max);
  const step = chips.length > 4 ? 2.5 : 3;
  return chips.map((v, i) => chipHtml(v, `top:${(-i * step).toFixed(1)}px`)).join('');
}
// puts a stack worth `amount` in el (only redrawn when the amount changes)
function setChips(el, amount, max) {
  if (el._amount === amount) return;
  el._amount = amount;
  el.innerHTML = stackHtml(amount, max);
}

function avatarHtml(p, cls = 'avatar') {
  return p.avatar ? `<div class="${cls}" style="background-image:url('/avatar/${p.id}')"></div>`
    : `<div class="${cls}">${esc((p.name || '?').slice(0, 1).toUpperCase())}</div>`;
}
// a buy-in or blind in the room list and the table's info: 100M, 2.5M, 25,000
const money = (v) => (v >= 1e6 ? shortNum(v) : fmt(v));
const blindsFor = (b) => { const bb = Math.max(2, Math.floor(b / 50)); return [Math.max(1, Math.floor(bb / 2)), bb]; };

// ------------------------------------------------------------------ the room
function setupForm() {
  const ok = W.buy_ins && W.buy_ins.length ? W.buy_ins : BUY_INS.filter((v) => v >= W.min_buy_in);
  if (!ok.includes(buyIn)) buyIn = ok[0];
  $('seatPick').innerHTML = [2, 3, 4, 5, 6, 7, 8].map((n) => `<button data-n="${n}">${n}</button>`).join('');
  $('seatPick').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
    seatsWanted = Number(b.dataset.n); sfx.click(); renderForm();
  }));
  const step = (d) => {
    const i = clamp(ok.indexOf(buyIn) + d, 0, ok.length - 1);
    buyIn = ok[i]; sfx.click(); renderForm();
  };
  $('buyUp').addEventListener('click', () => step(1));
  $('buyDown').addEventListener('click', () => step(-1));
  $('createBtn').addEventListener('click', () => {
    unlock(); sfx.click();
    send({ type: 'create', buy_in: buyIn, max_players: seatsWanted });
  });
  renderForm();
}
function renderForm() {
  if (!W) return;
  $('buyIn').textContent = fmt(buyIn);
  const [sb, bb] = blindsFor(buyIn);
  $('blindsHint').textContent = `Blinds ${money(sb)} / ${money(bb)}` + (buyIn > balance ? ' · more than your balance' : '');
  $('seatPick').querySelectorAll('button').forEach((b) => b.classList.toggle('on', Number(b.dataset.n) === seatsWanted));
  $('createBtn').disabled = buyIn > balance;
}
// The tables in the room list, kept by id: the list is updated in place (the room list comes again on every bet and
// card at any table), so only a table that just opened fades in.
const tableCards = new Map();
function syncTables(host, list, cardHtml, emptyHtml) {
  const keep = new Set(list.map((t) => t.id));
  for (const [id, el] of tableCards) if (!keep.has(id)) { el.remove(); tableCards.delete(id); }
  for (const c of [...host.children]) if (!c._table && !(c.classList.contains('empty') && !list.length)) c.remove();
  if (!list.length && !host.children.length) host.innerHTML = emptyHtml;
  list.forEach((t, n) => {
    let el = tableCards.get(t.id);
    if (!el) {
      el = document.createElement('div');
      el.className = 'table-card';
      el._table = t.id;
      el.addEventListener('click', (e) => {
        const b = e.target.closest('button');
        if (!b || b.disabled) return;
        unlock(); sfx.click();
        send({ type: 'watch', table: el._table });
        if (b.dataset.sit !== undefined) send({ type: 'sit' });
      });
      tableCards.set(t.id, el);
    }
    const html = cardHtml(t);
    if (el._html !== html) { el._html = html; el.innerHTML = html; }
    if (host.children[n] !== el) host.insertBefore(el, host.children[n] || null);
  });
}
function renderRoom() {
  const list = room.tables;
  syncTables($('tables'), list, (t) => {
    const live = t.phase === 'playing' || t.phase === 'between';
    const full = t.seated >= t.max;
    return `<div class="tstatus ${live ? 'live' : ''}">${live ? `LIVE · hand ${t.hand}` : 'WAITING'}</div>
      <div class="tname">${esc(t.name)}</div>
      <div class="tmeta">Buy-in <b>${money(t.buy_in)}</b> · blinds ${money(t.sb)}/${money(t.bb)} · ${t.seated}/${t.max} seats</div>
      <div class="tplayers">${esc(t.players.join(', '))}</div>
      <div class="tbtns"><button class="btn" data-watch="${t.id}">Watch</button>
      <button class="btn btn-gold" data-sit="${t.id}" ${full || t.buy_in > balance ? 'disabled' : ''}>${full ? 'Full' : `Sit down · ${money(t.buy_in)}`}</button></div>`;
  }, '<div class="empty">No tables open yet. Open one and invite the others!</div>');
  $('roomCount').textContent = `(${room.players.length})`;
  $('people').innerHTML = room.players.map((p) => {
    const t = list.find((x) => x.id === p.table);
    return `<div class="person">${avatarHtml(p)}<div><div>${esc(p.name)}${p.id === myId ? ' (you)' : ''}</div>
      <div class="sub">${p.seated && t ? `at ${esc(t.name)}` : 'in the room'}</div></div></div>`;
  }).join('');
}

function setupInfo() {
  $('infoBody').innerHTML = `
    <p>No-limit Texas Hold'em with your server credits. Open a table (you pick the buy-in and the number of seats) or
    sit down at one. Your buy-in becomes your chips; when you leave the table, your chips go back to your balance.</p>
    <ul>
      <li>Everyone gets two cards; five shared cards come in three steps (the flop, the turn and the river). The best
      five-card hand out of your two and the five on the table wins the pot.</li>
      <li>The blinds are a fiftieth and a hundredth of the buy-in. The dealer button moves one seat every hand.</li>
      <li>On your turn: fold, check or call, or raise (the slider, or ½ pot, pot and all-in). You have ${W.turn_time}
      seconds; if time runs out you check (or fold), and after two timeouts in a row you're taken off the table.</li>
      <li>All-in players can only win what they matched from each player: side pots are made automatically.</li>
      <li>Anyone in the server can watch a table. The table's host deals the first hand once two players sit.</li>
    </ul>
    ${W.rake ? `<p>The house takes ${(W.rake * 100).toFixed(1)}% of every contested pot.</p>` : ''}
    <p>If the bot restarts during a hand, everyone gets back the chips they had when that hand started.</p>
    <p>The 🗣 button switches the announcer (who won, with what hand and how much) on or off.</p>`;
}

// ================================================================== the table
const area = $('tableArea');
const fly = $('flyLayer');
let layoutKey = '';
let seatEls = [];          // per seat index: { root, hand:[card, card], plate, ring, nm, stk, tag, bet, betAmt }
let boardEls = [];
let dealerEl = null;

function cardHtml(c) {
  if (!c) return '';
  const red = c.s === '♥' || c.s === '♦';
  return `<span class="rk">${c.r}</span><span class="st">${c.s}</span><span class="big">${c.s}</span>${red ? '' : ''}`;
}
function makeCard() {
  const el = document.createElement('div');
  el.className = 'card';
  el.innerHTML = '<div class="inner"><div class="face back"></div><div class="face front"></div></div>';
  return el;
}
function setCard(el, c, up) {
  const front = el.querySelector('.front');
  const key = c ? c.r + c.s : '';
  if (front.dataset.key !== key) {
    front.dataset.key = key;
    front.innerHTML = cardHtml(c);
    front.classList.toggle('red', !!c && (c.s === '♥' || c.s === '♦'));
  }
  el.classList.toggle('up', !!up && !!c);
}
const sameCard = (a, b) => a && b && a.r === b.r && a.s === b.s;

// ------------------------------------------------------------------ table geometry
// The table is laid out in pixels for the space it has, every time that space changes (window resized, the action
// bar growing on a phone, …): the card size, the seat size, where the seats sit (on an ellipse that keeps every
// seat fully inside the table area, your own seat at the bottom), the felt inside them, and the bets and dealer
// button between each seat and the middle. Nothing is placed in % of the window, so nothing can end up off screen.
const G = { seat: [], bet: [], dealer: [], compact: false, cw: 48, sw: 120 };
let built = null;   // the state the seats were built for (positions depend on its seats and on the space)

// an amount shortened to at most 4 digits: 12.5k, 250k, 2.5M, 9.08M, 487.5M, 1.25B (never "1000M")
function shortNum(n) {
  if (num(n) < 1e4) return fmt(n);
  n = num(n);
  if (huge(n)) return huge(n);
  for (const [d, u] of [...BIG_UNITS, [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k']]) {
    const x = n / d, t = +x.toFixed(x < 10 ? 2 : 1);
    if (t >= 1) return `${t}${u}`;
  }
  return fmt(n);
}
// an amount on the table (stacks, bets, the pot): shortened (2.5M, 487.5M) when the seats are small, at the
// high-stakes tables (so a 5M blind and a 10M blind read alike) and from a billion up; the full number shows on hover
const tableNum = (v) => (G.compact || G.big || v >= 1e9 ? shortNum(v) : fmt(v));
// the amounts in a text from the server ("Raise to 20,000,000") written the same way: "Raise to 20M"
const shortIn = (text) => text.replace(/\d{1,3}(,\d{3})+/g, (m) => tableNum(Number(m.replace(/,/g, ''))));
function setAmount(el, v) {
  el.dataset.v = v;
  el.textContent = tableNum(v);
  el.title = fmt(v);
}

function layout(st = built) {
  if (!st || !seatEls.length) return;
  const w = area.clientWidth, h = area.clientHeight;
  if (!w || !h) return;
  const n = st.seats.length, pad = 6;
  // very short tables (small windows held sideways): the others' face-down cards get smaller and tuck behind
  // their name plate, so the seats take less height
  area.classList.toggle('flat', h < 300 || (n > 6 && h < 360));
  let cw = clamp(Math.min(w / 11, h / 6.4), 22, 64);
  const offsetIdx = st.you >= 0 ? st.you : 0;
  for (let pass = 0; pass < 8; pass++) {
    const sw = clamp(cw * 2.4, w < 380 ? 78 : 92, 150);   // very narrow phones get slimmer seats so 8 still fit
    area.style.setProperty('--card-w', `${cw.toFixed(1)}px`);
    area.style.setProperty('--seat-w', `${Math.round(sw)}px`);
    G.compact = sw < 120 || w < 600;
    area.classList.toggle('compact', G.compact);
    area.classList.toggle('tiny', sw < 86);   // the slimmest seats (very narrow phones)
    let sh = 0;
    seatEls.forEach((e) => { sh = Math.max(sh, e.root.offsetHeight); });
    const rx = Math.max(20, w / 2 - sw / 2 - pad);
    const ry = Math.max(20, h / 2 - sh / 2 - pad);
    const frx = Math.max(30, rx - sw * 0.18);
    const fry = Math.max(30, ry - sh * 0.28);
    Object.assign(G, { w, h, cx: w / 2, cy: h / 2, rx, ry, frx, fry, cw, sw, sh });
    // the middle (street, pot, 5 board cards, result) must fit on the felt and clear the seats above, below and beside
    const fit = Math.min((2 * frx * 0.8) / 5.6, (2 * fry * 0.86 - 70) / 1.4, (2 * (ry - sh / 2 - 6) - 70) / 1.4,
      (rx - sw / 2 - 10) / 2.8);
    if (fit < cw - 0.5 && cw > 22) { cw = Math.max(22, fit); continue; }
    // and every bet needs a clear spot between its seat and the board: if one doesn't, smaller cards
    if (placeSeats(st, n, offsetIdx) < -2 && cw > 26) { cw = Math.max(22, cw * 0.92); continue; }
    break;
  }
  placeSeats(st, n, offsetIdx);
  const { cx, cy, frx, fry } = G;
  cw = G.cw;
  const felt = $('felt');
  Object.assign(felt.style, { left: `${cx - frx}px`, top: `${cy - fry}px`, width: `${2 * frx}px`, height: `${2 * fry}px` });
  felt.classList.toggle('small', fry < 120);
  const boardHw = $('board').offsetWidth / 2 + 4;
  // the deck: on the felt, left of the board (hidden when there's no room; the dealing still flies from there)
  const deck = $('deck');
  const deckX = Math.max(boardHw + cw * 0.6, frx * 0.68);
  const room = deckX + cw * 0.5 <= frx - 4;
  deck.style.left = `${frx - Math.min(deckX, frx - cw * 0.55)}px`;   // the deck sits inside the felt
  deck.style.top = `${fry}px`;
  deck.style.visibility = room ? 'visible' : 'hidden';
  placeDealer(st);
  area.querySelectorAll('[data-v]').forEach((el) => setAmount(el, Number(el.dataset.v)));
}
// puts the seats on their ellipse, each bet between its seat and the middle, and the dealer-button spots; returns
// how much room the most cramped bet has (negative = it had to touch the board or a seat)
function placeSeats(st, n, offsetIdx) {
  const { cx, cy, rx, ry, sw, sh } = G;
  // the middle column (street, pot, board, result), measured: half its width and height around the middle
  const middle = [...document.querySelectorAll('#felt .center > *')];
  const boardHw = $('board').offsetWidth / 2 + 4;
  const boardHh = (middle.reduce((a, e) => a + e.offsetHeight, 0) + 6 * (middle.length - 1)) / 2 + 2;
  const betW = G.compact ? 60 : 80, betH = 24;
  // the seats first: evenly round the ellipse, your own at the bottom
  st.seats.forEach((s, i) => {
    const a = Math.PI / 2 + (((i - offsetIdx + n) % n) * 2 * Math.PI) / Math.max(n, 2);
    G.seat[i] = [cx + rx * Math.cos(a), cy + ry * Math.sin(a)];
  });
  // how far a bet at (bx, by) is from touching things (negative = overlapping by that much)
  const gap = (bx, by, x, y, hw, hh) => Math.max(Math.abs(bx - x) - (hw + betW / 2), Math.abs(by - y) - (hh + betH / 2));
  const room = (i, bx, by) => {
    if (bx - betW / 2 < 0 || bx + betW / 2 > G.w || by - betH / 2 < 0 || by + betH / 2 > G.h) return -Infinity;
    const dBoard = gap(bx, by, cx, cy, boardHw, boardHh);
    let dSeats = Infinity;
    G.seat.forEach(([x, y], j) => { dSeats = Math.min(dSeats, gap(bx, by, x, y, sw / 2, sh / 2) + (j === i ? 6 : 2)); });
    return Math.min(dBoard, dSeats);
  };
  let worst = 0;
  st.seats.forEach((s, i) => {
    const [sx, sy] = G.seat[i];
    // the bet: on the line from the seat to the middle, as near the middle as it can be without touching the board
    // or any seat; failing that just below or above its seat (a side seat on a narrow phone); failing that, the spot
    // with the most room
    const spots = [];
    for (let t = 0.3; t <= 0.96; t += 0.03) spots.push([cx + (sx - cx) * t, cy + (sy - cy) * t]);
    const sign = sy <= cy ? 1 : -1;
    for (const sg of [sign, -sign]) spots.push([sx + (cx - sx) * 0.12, sy + sg * (sh / 2 + betH / 2 + 2)]);
    let best = -Infinity;
    G.bet[i] = spots[0];
    for (const [bx, by] of spots) {
      const r = room(i, bx, by);
      if (r >= 0) { G.bet[i] = [bx, by]; best = 0; break; }
      if (r > best) { best = r; G.bet[i] = [bx, by]; }
    }
    worst = Math.min(worst, best);
    // the dealer button: just off the seat, on the side facing the middle
    const ux = cx - sx, uy = cy - sy, len = Math.hypot(ux, uy) || 1;
    const dx = ux / len, dy = uy / len;
    const edge = Math.min(sw / 2 / Math.max(Math.abs(dx), 1e-3), sh / 2 / Math.max(Math.abs(dy), 1e-3));
    G.dealer[i] = [sx + dx * (edge + 12) - dy * sw * 0.32, sy + dy * (edge + 12) + dx * sw * 0.32];
    const e = seatEls[i];
    if (!e) return;
    e.root.style.left = `${sx}px`; e.root.style.top = `${sy}px`;
    e.bet.style.left = `${G.bet[i][0]}px`; e.bet.style.top = `${G.bet[i][1]}px`;
  });
  return worst;
}
function placeDealer(st) {
  if (st && st.dealer >= 0 && st.dealer < st.seats.length && st.phase !== 'waiting' && G.dealer[st.dealer]) {
    const [x, y] = G.dealer[st.dealer];
    dealerEl.style.left = `${x}px`; dealerEl.style.top = `${y}px`; dealerEl.classList.remove('hidden');
  } else if (dealerEl) dealerEl.classList.add('hidden');
}
// lay out again whenever the table area or a seat changes size (window resized, the action bar grows, the font
// finishes loading and makes the seats taller, …)
const resizer = new ResizeObserver(() => layout());
resizer.observe(area);
if (document.fonts) document.fonts.ready.then(() => layout());

function buildTable(st) {
  layoutKey = `${st.id}|${st.you}|${st.seats.map((s) => s.id).join(',')}`;
  built = st;
  const host = $('seats');
  host.innerHTML = '';
  seatEls = st.seats.map((s, i) => {
    const root = document.createElement('div');
    root.className = 'seat' + (i === st.you ? ' me' : '');
    const hand = document.createElement('div'); hand.className = 'hand';
    const cards = [makeCard(), makeCard()];
    cards.forEach((c) => hand.appendChild(c));
    const plate = document.createElement('div'); plate.className = 'plate';
    plate.innerHTML = `<div class="ring">${avatarHtml(s)}</div><div class="who"><div class="nm">${esc(s.name)}</div>
      <div class="stk"></div></div><div class="tag hidden"></div><span class="streak hidden"></span>`;
    root.append(hand, plate);
    host.appendChild(root);
    resizer.observe(root);
    const bet = document.createElement('div');
    bet.className = 'bet hidden';
    bet.innerHTML = '<div class="chips"></div><span></span>';
    host.appendChild(bet);
    return { root, hand: cards, plate, ring: plate.querySelector('.ring'), stk: plate.querySelector('.stk'),
      tag: plate.querySelector('.tag'), streak: plate.querySelector('.streak'), bet, betChips: bet.querySelector('.chips'),
      betAmt: bet.querySelector('span') };
  });
  dealerEl = document.createElement('div');
  dealerEl.className = 'dealer-btn hidden'; dealerEl.textContent = 'D';
  host.appendChild(dealerEl);
  $('board').innerHTML = '';
  boardEls = [0, 1, 2, 3, 4].map(() => { const c = makeCard(); c.classList.add('slot', 'ghost'); $('board').appendChild(c); return c; });
  layout(st);
}

// draws a state as it is, without animation (used after each animation, and on the first look at a table)
function renderStatic(st, opts = {}) {
  if (!st) return;
  const key = `${st.id}|${st.you}|${st.seats.map((s) => s.id).join(',')}`;
  G.big = st.buy_in >= 1e7;
  if (key !== layoutKey) buildTable(st);
  const between = st.phase === 'between';
  const winners = new Set(st.seats.filter((s) => s.won > 0).map((s) => s.id));
  const winBest = [];
  if (between) for (const s of st.seats) if (winners.has(s.id) && s.best) winBest.push(...s.best);
  st.seats.forEach((s, i) => {
    const e = seatEls[i];
    if (!e) return;
    setAmount(e.stk, s.stack);
    setStreak(e.streak, s.streak || 0);
    e.root.classList.toggle('turn', st.turn === i);
    e.root.classList.toggle('folded', s.in_hand && s.folded);
    e.root.classList.toggle('offline', !s.online);
    e.root.classList.toggle('winner', between && s.won > 0);
    // tag: hand name at showdown, else the last action
    let tag = '', tagCls = '';
    if (between && s.hand) { tag = s.hand; tagCls = 'handname'; }
    else if (s.leaving) tag = 'Leaving';
    else if (s.in_hand && s.all_in && !s.folded) { tag = 'All-in'; tagCls = 'allin'; }
    else if (s.last) { tag = shortIn(s.last); tagCls = /fold/i.test(s.last) ? 'fold' : /raise|bet/i.test(s.last) ? 'raise' : ''; }
    else if (!s.in_hand && st.phase === 'playing') tag = 'Next hand';
    e.tag.textContent = tag; e.tag.className = `tag ${tagCls}` + (tag ? '' : ' hidden');
    // cards
    const showCards = s.in_hand && s.count > 0 && !s.folded && !(opts.hideHole);
    e.hand.forEach((c, k) => {
      c.style.visibility = showCards ? 'visible' : 'hidden';
      const card = s.cards ? s.cards[k] : null;
      setCard(c, card, !!card && !opts.holeDown);
      const best = between && winners.has(s.id) && card && winBest.some((b) => sameCard(b, card));
      c.classList.toggle('win', !!best);
      c.classList.toggle('dim', between && s.hand && !best && winBest.length > 0);
    });
    // bet in front of the seat
    const bet = s.bet || 0;
    e.bet.classList.toggle('hidden', !bet || opts.hideBets);
    if (bet) setChips(e.betChips, bet, 5);
    setAmount(e.betAmt, bet);
  });
  placeDealer(st);
  // board
  boardEls.forEach((c, k) => {
    const card = st.board[k];
    const shown = !!card && !(opts.boardFrom !== undefined && k >= opts.boardFrom);
    c.classList.toggle('ghost', !shown);
    c.classList.toggle('slot', !shown);
    setCard(c, shown ? card : null, shown);
    const best = between && shown && winBest.some((b) => sameCard(b, card));
    c.classList.toggle('win', best);
    c.classList.toggle('dim', between && winBest.length > 0 && shown && !best);
  });
  const bets = st.seats.reduce((a, s) => a + (s.bet || 0), 0);
  const potShown = opts.potOverride !== undefined ? opts.potOverride : st.pot - bets;
  showPot(potShown);
  $('pot').style.visibility = potShown > 0 ? 'visible' : 'hidden';
  $('street').textContent = st.street || (between ? 'SHOWDOWN' : '');
  $('result').textContent = shortIn(st.result || '');
  // waiting message
  const msg = $('tableMsg');
  if (st.phase === 'waiting') {
    msg.classList.remove('hidden');
    msg.textContent = st.seats.length < 2 ? 'Waiting for another player to sit down…'
      : st.hand_no === 0 ? `Waiting for ${st.seats.find((s) => s.id === st.host)?.name || 'the host'} to deal the first hand…`
        : 'Waiting for more players…';
  } else msg.classList.add('hidden');
  renderSide(st);
  renderControls(st);
  // the action bar may have just appeared or grown: if that changed the table's space, lay it out again now
  if (area.clientWidth !== G.w || area.clientHeight !== G.h) layout(st);
}


// the pot in the middle: its chips and amount
function showPot(v) {
  setChips($('potChips'), v, 4);
  setAmount($('potAmount'), v);
}

// The table log, newest line on top. Re-drawn only when it changes; someone scrolled down to older lines stays on
// the same lines when a new one comes in at the top.
function renderLog(lines) {
  const el = $('log');
  const html = [...lines].reverse().map((l) => `<div>${esc(l)}</div>`).join('');
  if (el.dataset.html === html) return;
  const fromTop = el.scrollTop, before = el.scrollHeight;
  el.innerHTML = html;
  el.dataset.html = html;
  if (fromTop > 2) el.scrollTop = fromTop + (el.scrollHeight - before);
}

function renderSide(st) {
  $('crumb').textContent = `${st.name} · blinds ${money(st.sb)}/${money(st.bb)}`;
  const me = st.you >= 0 ? st.seats[st.you] : null;
  $('tableInfo').innerHTML = `<b>${esc(st.name)}</b><br>Buy-in ${money(st.buy_in)} · blinds ${money(st.sb)}/${money(st.bb)}<br>
    ${st.seats.length}/${st.max} seats · ${st.hand_no ? `hand #${st.hand_no}` : 'not started'}
    ${me ? `<br>Your chips: <b>${fmt(me.stack)}</b>` : '<br>You are watching'}`;
  renderLog(st.log);
}

// ------------------------------------------------------------------ controls
let raiseTo = 0;
// amounts on the action buttons: shortened (250k) on narrow screens so the buttons fit on one line
const amt = (v) => (innerWidth < 600 || G.big ? shortNum(v) : fmt(v));
function renderControls(st) {
  const me = st.you >= 0 ? st.seats[st.you] : null;
  const myTurn = me && st.turn === st.you && st.to_call !== undefined;
  $('actRow').classList.toggle('hidden', !myTurn);
  $('waitRow').classList.toggle('hidden', !!myTurn);
  $('myHand').textContent = st.my_hand ? `You have: ${st.my_hand}` : '';
  if (myTurn) {
    const callAll = st.to_call >= me.stack;
    $('callBtn').textContent = st.to_call > 0 ? (callAll ? `Call all-in ${amt(st.to_call)}` : `Call ${amt(st.to_call)}`) : 'Check';
    $('raiseBox').classList.toggle('hidden', !st.can_raise);
    $('raiseBtn').classList.toggle('hidden', !st.can_raise);
    if (st.can_raise) {
      const r = $('raiseRange');
      r.min = st.min_to; r.max = st.max_to; r.step = Math.max(1, Math.floor(st.bb / 2));
      if (!(raiseTo >= st.min_to && raiseTo <= st.max_to)) raiseTo = st.min_to;
      r.value = raiseTo;
      const pot = st.pot;
      const presets = [['Min', st.min_to], ['½ Pot', st.current_bet + Math.floor(pot / 2)], ['Pot', st.current_bet + pot], ['All-in', st.max_to]];
      $('presets').innerHTML = presets.map(([l, v]) => `<button data-v="${clamp(v, st.min_to, st.max_to)}">${l}</button>`).join('');
      $('presets').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { raiseTo = Number(b.dataset.v); sfx.click(); updateRaise(st); }));
      updateRaise(st);
    }
    setActButtons(true);
  } else {
    const seated = !!me;
    const full = st.seats.length >= st.max;
    $('sitBtn').classList.toggle('hidden', seated || full);
    $('sitBtn').textContent = `Sit down · ${money(st.buy_in)}`;
    $('sitBtn').disabled = st.buy_in > balance;
    $('startBtn').classList.toggle('hidden', !(seated && st.host === myId && st.phase === 'waiting' && st.hand_no === 0 && st.seats.length >= 2));
    $('leaveBtn').classList.toggle('hidden', !seated);
    $('backBtn').classList.toggle('hidden', seated);
    let text = '';
    if (st.phase === 'playing' && st.turn >= 0 && st.seats[st.turn]) text = `${st.seats[st.turn].name} is thinking…`;
    else if (st.phase === 'between') text = 'Next hand in a moment…';
    else if (!seated && full) text = 'This table is full. You can watch.';
    $('waitText').textContent = text;
  }
}
function updateRaise(st) {
  $('raiseRange').value = raiseTo;
  $('raiseVal').textContent = amt(raiseTo);
  const allIn = raiseTo >= st.max_to;
  $('raiseBtn').textContent = allIn ? `All-in ${amt(st.max_to)}` : `${st.current_bet ? 'Raise to' : 'Bet'} ${amt(raiseTo)}`;
}
function setActButtons(on) { for (const id of ['foldBtn', 'callBtn', 'raiseBtn']) $(id).disabled = !on; }
$('raiseRange').addEventListener('input', (e) => { raiseTo = Number(e.target.value); if (T) updateRaise(T); });
function act(action, to = 0) { unlock(); setActButtons(false); send({ type: 'act', action, to }); }
$('foldBtn').addEventListener('click', () => act('fold'));
$('callBtn').addEventListener('click', () => act('check_call'));
$('raiseBtn').addEventListener('click', () => {
  if (!T) return;
  if (raiseTo >= T.max_to) act('allin'); else act('raise', raiseTo);
});
$('sitBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'sit' }); });
$('startBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'start' }); });
$('leaveBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'leave' }); });
$('backBtn').addEventListener('click', () => { sfx.click(); send({ type: 'watch', table: null }); });

// ================================================================== animations
// Table states are shown one after another; each runs its animation first (unless many are waiting, then they're
// drawn straight away to catch up).
let queue = Promise.resolve();
let pending = 0;
function enqueue(st) {
  pending++;
  queue = queue.then(() => showState(st)).catch((e) => console.error(e)).finally(() => { pending--; });
}
async function showState(st) {
  if (!st.id) {   // back in the room
    T = null; layoutKey = '';
    $('tableView').classList.add('hidden'); $('room').classList.remove('hidden'); $('crumb').textContent = '';
    return;
  }
  $('room').classList.add('hidden'); $('tableView').classList.remove('hidden');
  const prev = T;
  const sameTable = prev && prev.id === st.id && layoutKey === `${st.id}|${st.you}|${st.seats.map((s) => s.id).join(',')}`;
  // no animation when it can't be seen (the window is in the background: browsers pause animations there, so the
  // table would fall behind), when many states are waiting, or when switching tables
  if (!sameTable || pending > 3 || document.hidden || st.seq <= (prev ? prev.seq : 0)) {
    T = st; renderStatic(st); return;
  }
  try {
    await animate(prev, st);
  } catch (e) { console.error(e); }
  T = st;
  renderStatic(st);
  if (st.event && st.event.kind === 'turn' && st.turn === st.you) sfx.yourTurn();
}

// positions within the table area
function centerOf(el) {
  const a = area.getBoundingClientRect(), r = el.getBoundingClientRect();
  return [r.left - a.left + r.width / 2, r.top - a.top + r.height / 2, r.width, r.height];
}
function flyEl(el, from, to, ms, { rot0 = 0, rot1 = 0, s0 = 1, s1 = 1, o0 = 1, o1 = 1, easing = 'cubic-bezier(.2,.8,.3,1)' } = {}) {
  fly.appendChild(el);
  const w = el.offsetWidth, h = el.offsetHeight;
  const tf = (p, r, s) => `translate(${p[0] - w / 2}px, ${p[1] - h / 2}px) rotate(${r}deg) scale(${s})`;
  const anim = el.animate([{ transform: tf(from, rot0, s0), opacity: o0 }, { transform: tf(to, rot1, s1), opacity: o1 }],
    { duration: ms, easing, fill: 'forwards' });
  // A browser that isn't drawing the page (another tab, a minimised window) may never finish the animation, so
  // don't wait for it longer than it should take: the table must keep up with the game.
  return Promise.race([anim.finished.catch(() => {}), sleep(ms + 250)]).then(() => el.remove());
}
function flyingCard(sizeEl) {
  const c = makeCard();
  const r = sizeEl.getBoundingClientRect();
  c.style.width = `${r.width}px`; c.style.height = `${r.height}px`;
  return c;
}
// a small pile of chips worth `amount`, for flying across the table
function chipPile(amount, max = 3) {
  const el = document.createElement('div');
  el.className = 'chips';
  el.innerHTML = stackHtml(amount, max);
  return el;
}

async function animate(prev, st) {
  const ev = st.event || {};
  switch (ev.kind) {
    case 'deal': return animDeal(prev, st, ev);
    case 'action': return animAction(prev, st, ev);
    case 'street': return animStreet(prev, st, ev);
    case 'collect': return animCollect(prev, st);
    case 'showdown': return animShowdown(prev, st);
    case 'win': return animWin(prev, st, ev);
    default: return undefined;
  }
}

// two cards to every player, one at a time from the deck, then your own cards turn over
async function animDeal(prev, st, ev) {
  renderStatic(st, { hideHole: true, potOverride: 0 });
  const deck = $('deck');
  const [dx, dy] = centerOf(deck);
  const order = [];
  const n = st.seats.length;
  for (let round = 0; round < 2; round++) {
    for (let k = 0; k < n; k++) {
      const i = (ev.first + k) % n;
      if (st.seats[i].in_hand) order.push([i, round]);
    }
  }
  // blinds: chips slide in while the cards go out
  st.seats.forEach((s, i) => {
    if (s.bet) {
      const e = seatEls[i];
      e.bet.classList.add('hidden');
      const [px, py] = centerOf(e.plate), [bx, by] = centerOf(e.bet);
      flyEl(chipPile(s.bet, 2), [px, py], [bx, by], 450).then(() => { e.bet.classList.remove('hidden'); });
      sfx.chips(2);
    }
  });
  const flights = [];
  for (const [i, round] of order) {
    const target = seatEls[i].hand[round];
    target.style.visibility = 'hidden';
    const [tx, ty] = centerOf(target);
    const c = flyingCard(target);
    sfx.deal();
    flights.push(flyEl(c, [dx, dy], [tx, ty], 330, { rot0: -25, rot1: round ? 6 : -6, s0: 0.7 }).then(() => {
      target.style.visibility = 'visible';
      setCard(target, null, false);
    }));
    await sleep(110);
  }
  await Promise.all(flights);
  // your own cards turn over
  if (st.you >= 0 && st.seats[st.you].cards) {
    await sleep(150);
    for (let k = 0; k < 2; k++) {
      setCard(seatEls[st.you].hand[k], st.seats[st.you].cards[k], true);
      sfx.flip();
      await sleep(180);
    }
  }
  await sleep(250);
}

// a player acts: folded cards slide into the middle, bets slide out as chips, a check knocks on the felt
async function animAction(prev, st, ev) {
  const i = ev.seat;
  const e = seatEls[i];
  if (!e) return;
  if (ev.action === 'fold') {
    sfx.fold();
    const [mx, my] = centerOf($('board'));
    await Promise.all(e.hand.map((c, k) => {
      if (c.style.visibility === 'hidden') return null;
      const [x, y] = centerOf(c);
      const f = flyingCard(c);
      c.style.visibility = 'hidden';
      return flyEl(f, [x, y], [mx + (k - 0.5) * 14, my], 420, { rot0: k ? 6 : -6, rot1: 120 + k * 40, s1: 0.6, o1: 0 });
    }));
    return;
  }
  if (ev.action === 'check') {
    sfx.check();
    e.plate.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.06)' }, { transform: 'scale(1)' }], { duration: 260 });
    await sleep(250);
    return;
  }
  // call / raise / all-in: chips from the player to their bet
  const [px, py] = centerOf(e.plate);
  const [bx, by] = centerOf(e.bet.classList.contains('hidden') ? (e.bet.classList.remove('hidden'), e.bet) : e.bet);
  const bigBet = ev.action === 'allin';
  if (bigBet) sfx.allin(); else sfx.chips(ev.action === 'raise' ? 6 : 3);
  const piles = bigBet ? 4 : ev.action === 'raise' ? 2 : 1;
  // what this action put in, shared out over the piles that fly
  const put = Math.max(1, ((st.seats[i] && st.seats[i].bet) || 0) - ((prev && prev.seats[i] && prev.seats[i].bet) || 0));
  const flights = [];
  for (let k = 0; k < piles; k++) {
    flights.push(flyEl(chipPile(Math.max(1, Math.floor(put / piles)), 3), [px, py], [bx + (k - (piles - 1) / 2) * 6, by], 420 + k * 60));
    await sleep(60);
  }
  await Promise.all(flights);
}

// the bets of the round sweep into the pot
async function animCollect(prev) {
  if (!prev) return;
  const [cx, cy] = centerOf($('pot'));
  const flights = [];
  prev.seats.forEach((s, i) => {
    if (!s.bet || !seatEls[i]) return;
    const e = seatEls[i];
    const [bx, by] = centerOf(e.bet);
    e.bet.classList.add('hidden');
    flights.push(flyEl(chipPile(s.bet, 4), [bx, by], [cx, cy], 450, { easing: 'cubic-bezier(.5,0,.2,1)' }));
  });
  if (flights.length) {
    sfx.sweep();
    await Promise.all(flights);
    const pot = $('pot'); pot.style.visibility = 'visible';
    showPot(prev.pot);
    pot.classList.remove('bump'); void pot.offsetWidth; pot.classList.add('bump');
    await sleep(150);
  }
}

// new board cards: they slide out from the deck face down, then turn over one by one
async function animStreet(prev, st, ev) {
  if (ev.collect) await animCollect(prev);
  renderStatic(st, { boardFrom: ev.from, hideBets: true });
  const [dx, dy] = centerOf($('deck'));
  const targets = ev.new.map((_, k) => boardEls[ev.from + k]);
  await Promise.all(targets.map(async (t, k) => {
    await sleep(k * 130);
    const [tx, ty] = centerOf(t);
    sfx.deal();
    await flyEl(flyingCard(t), [dx, dy], [tx, ty], 360, { rot0: -20, s0: 0.7 });
    t.classList.remove('ghost', 'slot');
    setCard(t, null, false);
  }));
  for (let k = 0; k < targets.length; k++) {
    await sleep(k === 0 ? 120 : 220);
    setCard(targets[k], ev.new[k], true);
    sfx.flip();
  }
  await sleep(350);
}

// showdown: everyone still in turns their cards over, one player after another
async function animShowdown(prev, st) {
  await animCollect(prev);
  renderStatic(st, { holeDown: true, hideBets: true });
  st.seats.forEach((s, i) => { if (seatEls[i]) seatEls[i].tag.classList.add('hidden'); });
  sfx.reveal();
  for (let i = 0; i < st.seats.length; i++) {
    const s = st.seats[i];
    if (!s.cards || !s.in_hand || s.folded) continue;
    const e = seatEls[i];
    for (let k = 0; k < 2; k++) {
      setCard(e.hand[k], s.cards[k], true);
      e.hand[k].classList.add('lift');
      sfx.flip();
      await sleep(160);
    }
    e.tag.textContent = s.hand || ''; e.tag.className = 'tag handname pop';
    await sleep(380);
  }
  st.seats.forEach((s, i) => { if (seatEls[i]) seatEls[i].hand.forEach((c) => c.classList.remove('lift')); });
}

// What the announcer says when a pot is won: "Tester 4 wins 250 million with a full house, kings over sevens."
// Everyone else folded: no hand is named. One pot split: both names, the hand and the share. Side pots: each winner
// with everything they won. Chips that only come back because nobody called them aren't announced.
export function winSpeech(st, ev) {
  const name = (i) => speakName(st.seats[i] ? st.seats[i].name : '');
  const pays = (ev.payouts || []).filter((p) => !p.uncalled && p.amount > 0 && st.seats[p.seat]);
  if (!pays.length) return '';
  if (ev.uncontested) return `${name(pays[0].seat)} wins ${speakAmount(pays[0].amount)}. Everyone else folded.`;
  if (new Set(pays.map((p) => p.pot)).size === 1 && pays.length > 1) {
    const each = Math.min(...pays.map((p) => p.amount));
    return `${list(pays.map((p) => name(p.seat)))} split the pot with ${pays[0].hand}, ${speakAmount(each)} each.`;
  }
  const bySeat = new Map();
  for (const p of pays) {
    const w = bySeat.get(p.seat) || { amount: 0, hand: p.hand, split: false };
    w.amount += p.amount; w.split = w.split || p.split;
    bySeat.set(p.seat, w);
  }
  return [...bySeat].map(([i, w]) => `${name(i)} wins ${speakAmount(w.amount)}${w.hand ? ` with ${w.hand}` : ''}`
    + `${w.split ? ', sharing a pot' : ''}.`).join(' ');
}

// the pot goes to the winner(s): a stack of chips slides to each, with "+amount" floating up
async function animWin(prev, st, ev) {
  say(winSpeech(st, ev));
  if (prev && prev.seats.some((s) => s.bet)) await animCollect(prev);
  renderStatic(st, { potOverride: prev ? prev.pot : 0 });
  const [cx, cy] = centerOf($('pot'));
  const total = (ev.payouts || []).reduce((a, p) => a + p.amount, 0);
  const iWon = (ev.payouts || []).some((p) => p.seat === st.you);
  const iPlayed = st.you >= 0 && prev && prev.seats[st.you] && prev.seats[st.you].in_hand;
  if (iWon) sfx.win(total >= st.bb * 30); else if (iPlayed) sfx.lose(); else sfx.sweep();
  const flights = [];
  for (const p of ev.payouts || []) {
    const e = seatEls[p.seat];
    if (!e) continue;
    const [sx, sy] = centerOf(e.plate);
    for (let k = 0; k < 4; k++) {
      flights.push(flyEl(chipPile(Math.max(1, Math.floor(p.amount / 4)), 3), [cx + (k - 1.5) * 5, cy], [sx, sy], 600 + k * 70,
        { easing: 'cubic-bezier(.4,0,.2,1)' }));
    }
    const f = document.createElement('div');
    f.className = 'won-float';
    f.textContent = `+${tableNum(p.amount)}`;
    const [ax, ay] = centerOf(e.root);
    f.style.left = `${ax}px`; f.style.top = `${ay - 30}px`;
    area.appendChild(f);
    setTimeout(() => f.remove(), 1900);
    await sleep(120);
  }
  await Promise.all(flights);
  $('pot').style.visibility = 'hidden';
}

// the turn timer: the ring around the avatar of the player to act runs down
let lastTick = 0;
function timerLoop() {
  if (T && T.phase === 'playing' && T.turn >= 0 && seatEls[T.turn]) {
    const left = Math.max(0, T.deadline - srv());
    const frac = left / (T.turn_time * 1000);
    seatEls.forEach((e, i) => e.ring.style.setProperty('--t', i === T.turn ? frac.toFixed(3) : 0));
    if (T.turn === T.you && left < 5000 && left > 0) {
      const sec = Math.ceil(left / 1000);
      if (sec !== lastTick) { lastTick = sec; sfx.tick(); }
    }
  } else {
    seatEls.forEach((e) => e.ring.style.setProperty('--t', 0));
  }
  requestAnimationFrame(timerLoop);
}
requestAnimationFrame(timerLoop);

boot().catch((e) => { console.error(e); showFatal(`Couldn't start: ${e.message || e}`); });
