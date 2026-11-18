// Live blackjack for the Activity. The server (activity/blackjack.py) runs the tables; this page shows the room,
// the table you're watching or sitting at, and animates what happens: chips slide onto the betting circles, cards
// fly out of the shoe and turn over, splits slide apart, the dealer turns over the hole card, winnings come out of
// the dealer's rack and the cards are swept into the discard tray. Sounds come from blackjack-sound.js, and the
// dealer's voice ("Place your bets, please", "No more bets, thank you", the winners) from dealer-voice.js.
import { signIn, retryAfterExpiredSession, go } from './auth.js';
import { sfx, unlock, isMuted, setMuted } from './blackjack-sound.js';
import { say, hush, voiceOn, setVoiceOn, voiceAvailable, linkMute, speakName, speakAmount, list, useServerVoice } from './dealer-voice.js';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmt = (n) => Math.round(n).toLocaleString('en-US');
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MIN_BETS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000];
// casino chips: value, colour, edge spots, text
const DENOMS = [
  [1, '#f2f2f2', '#3a6fd8', '#222'], [5, '#c8302c', '#fff', '#fff'], [10, '#2b5fc0', '#fff', '#fff'],
  [25, '#178a4c', '#fff', '#fff'], [100, '#1d1d1d', '#e8bd57', '#fff'], [500, '#7b3fb3', '#fff', '#fff'],
  [1000, '#e5a50a', '#5a3400', '#3a2400'], [5000, '#d9468f', '#fff', '#fff'], [10000, '#0f9a9a', '#fff', '#fff'],
  [25000, '#8b5a2b', '#ffe3b0', '#fff'], [100000, '#9aa4b1', '#1d1d1d', '#1d1d1d'], [500000, '#5b1020', '#e8bd57', '#fff'],
  [1000000, '#e8bd57', '#5b1020', '#3a2400'], [2000000, '#1f7a46', '#e8bd57', '#fff'],
  [5000000, '#d0631a', '#fff3d6', '#fff'], [10000000, '#1b2f73', '#e8bd57', '#fff'],
  [25000000, '#2c2c2c', '#c0c7d0', '#fff'], [50000000, '#a3122e', '#f2f2f2', '#fff'],
  [100000000, '#f4f1e8', '#b8860b', '#3a2400'],
];
const RACK = ['#1d1d1d', '#7b3fb3', '#e5a50a', '#178a4c', '#c8302c', '#2b5fc0', '#0f9a9a', '#d9468f', '#1d1d1d'];

{
  const p = new URLSearchParams(location.search);
  if (p.get('platform') === 'mobile' || p.get('mobile') === '1') document.body.classList.add('discord-mobile');
}

let W = null, myId = null, balance = 0, room = { tables: [], players: [] };
let T = null;              // the table being shown (after its animations)
let S = null;              // the table state being animated right now (T once its animation is done)
let ws = null, session = null, joinInfo = null, fatal = false, retry = 0, offset = 0;
let minBet = 100, seatsWanted = 5;
const srv = () => Date.now() + offset;

function shortNum(n) {
  if (n >= 1e9) return `${+(n / 1e9).toFixed(n >= 1e10 ? 1 : 2)}B`;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(n >= 1e8 ? 0 : n >= 1e7 ? 1 : 2)}M`;
  if (n >= 1e3) return `${+(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}k`;
  return fmt(n);
}

// ------------------------------------------------------------------ connection
function loading(text) { $('loading').classList.remove('hidden'); $('loadingText').textContent = text; }
function showFatal(text) {
  fatal = true; loading(text); $('loading').querySelector('.spinner').classList.add('hidden');
}
async function boot() {
  const auth = await signIn(loading);
  if (auth.error) { showFatal(auth.error); return; }
  session = auth.session; joinInfo = auth.joinInfo;
  // local dev server only: lets a test show any table state (used to check the layout at every window size)
  if (auth.cfg && auth.cfg.dev) window.__bj = { show: (st) => enqueue(st) };
  connect();
}
function connect() {
  loading('Joining the blackjack room…');
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/blackjack`);
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
      W = m; myId = m.you; setupForm(); setupInfo(); setupChat(); $('loading').classList.add('hidden');
      if (m.server_voice) useServerVoice(session);
      updateVoiceBtn();
      break;
    case 'me': balance = m.balance; showBalance(); renderForm(); renderRoom(); if (T) renderControls(T); break;
    case 'room': room = m; renderRoom(); break;
    case 'table': pickSeat(m); enqueue(m); break;
    case 'chat': showChat(m); break;
    case 'notice': toast(m.message, true); break;
    case 'error': toast(m.message); sfx.error(); if (T) renderControls(T); break;
    case 'fatal':
      if (m.code === 'session' && retryAfterExpiredSession()) break;
      if (m.code === 'crux' && window.showCrux) window.showCrux(m.until);   // on a /crux break
      showFatal(m.message); try { ws.close(); } catch (e) { /* closed */ }
      break;
    default: break;
  }
}

// the balance in the header, shortened on narrow screens (9.08M) so the header always fits
let shownBalance = null;
function showBalance() {
  const el = $('balance');
  el.textContent = innerWidth < 520 && balance >= 1e6 ? shortNum(balance) : fmt(balance);
  el.parentElement.title = `${fmt(balance)} credits`;
  if (shownBalance !== null && balance > shownBalance) {
    el.parentElement.animate([{ scale: 1 }, { scale: 1.15, offset: 0.4 }, { scale: 1 }], { duration: 500, easing: 'ease' });
  }
  shownBalance = balance;
}
window.addEventListener('resize', showBalance);

let toastTimer = null;
function toast(text, good = false) {
  const t = $('toast'); t.textContent = text; t.classList.remove('hidden'); t.classList.toggle('good', good);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add('hidden'), 3200);
}
function updateMuteBtn() { $('muteBtn').textContent = isMuted() ? '🔇' : '🔊'; }
$('muteBtn').addEventListener('click', () => { unlock(); setMuted(!isMuted()); if (isMuted()) hush(); updateMuteBtn(); });
updateMuteBtn();
// the dealer's voice: on/off on its own, and quiet while the sound is muted
linkMute(isMuted);
function updateVoiceBtn() {
  const b = $('voiceBtn');
  b.classList.toggle('hidden', !voiceAvailable());
  b.classList.toggle('off', !voiceOn());
  b.title = voiceOn() ? "Dealer's voice: on" : "Dealer's voice: off";
}
$('voiceBtn').addEventListener('click', () => {
  unlock(); setVoiceOn(!voiceOn()); updateVoiceBtn();
  if (voiceOn()) say('Hello! I will be your dealer.', { urgent: true });
});
updateVoiceBtn();
document.addEventListener('pointerdown', () => unlock(), { once: true });
$('gamesBtn').addEventListener('click', () => {
  if (T && T.you >= 0) { toast('Leave the table first.'); return; }
  go('/');
});
$('infoBtn').addEventListener('click', () => $('infoModal').classList.remove('hidden'));
$('infoClose').addEventListener('click', () => $('infoModal').classList.add('hidden'));
$('infoModal').addEventListener('click', (e) => { if (e.target === $('infoModal')) $('infoModal').classList.add('hidden'); });

function avatarHtml(p, cls = 'avatar') {
  return p.avatar ? `<div class="${cls}" style="background-image:url('/avatar/${p.id}')"></div>`
    : `<div class="${cls}">${esc((p.name || '?').slice(0, 1).toUpperCase())}</div>`;
}

// ------------------------------------------------------------------ your seats
// A player can sit at more than one seat of a table. st.mine lists them; st.you is the one in focus: the chips you
// tap go there. It's the seat whose turn it is, else the one you last tapped, else your first.
let focus = -1;
function pickSeat(st) {
  if (!st.id) return;
  st.mine = st.mine || (st.you >= 0 ? [st.you] : []);
  if (st.phase === 'playing' && st.turn && st.mine.includes(st.turn.seat)) focus = st.turn.seat;
  if (!st.mine.includes(focus)) focus = st.mine.length ? st.mine[0] : -1;
  st.you = focus;
}
const isMine = (st, i) => !!(st && st.mine && st.mine.includes(i));
// tapping one of your seats while bets are open puts the next chips there
function focusSeat(i, k) {
  if (!T || !isMine(T, i) || T.phase !== 'betting') return;
  sfx.click();
  focus = i;
  for (const st of [T, S]) if (st && st.id === T.id) st.you = i;
  if (k) setTarget(k); else renderStatic(T);
}
// your seats' bets, added up (for the buttons that work on all of them: Clear, Rebet, ×2 and Deal)
function myBets(st) {
  const seats = (st.mine || []).map((i) => st.seats[i]).filter(Boolean);
  return { seats, any: seats.some((s) => s.bet), sides: seats.some((s) => s.side.pp + s.side.t3),
    rebet: seats.filter((s) => s.last_bet && !s.bet && !(s.side.pp + s.side.t3)),
    ready: seats.length > 0 && seats.every((s) => s.ready) };
}

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

// ------------------------------------------------------------------ chips
const denomFor = (v) => [...DENOMS].reverse().find((d) => d[0] <= v) || DENOMS[0];
function chipHtml(v, label = '', style = '') {
  const [, c, e, t] = denomFor(v);
  return `<div class="chip" style="--c:${c};--e:${e};--t:${t};${style}">${label ? `<b>${label}</b>` : ''}</div>`;
}
// the chips that make up an amount, biggest first (at the bottom of a stack), at most `max` of them
function chipsFor(amount, max = 8) {
  const out = [];
  let left = amount;
  for (let k = DENOMS.length - 1; k >= 0 && out.length < max; k--) {
    const v = DENOMS[k][0];
    while (left >= v && out.length < max) { out.push(v); left -= v; }
  }
  return out.length ? out : [DENOMS[0][0]];
}
function stackHtml(amount, max = 8, extra = 0) {
  if (!amount && !extra) return '';
  const chips = [...(amount ? chipsFor(amount, extra ? Math.min(max, 4) : max) : []), ...(extra ? chipsFor(extra, max) : [])];
  const step = chips.length > 6 ? 2.5 : 3;
  return chips.map((v, i) => chipHtml(v, '', `top:${(-i * step).toFixed(1)}px`)).join('');
}
// a small pile of chips worth `amount`, for flying across the table
function chipPile(amount, max = 5) {
  const el = document.createElement('div');
  el.className = 'stack';
  el.style.setProperty('--chip', `${G.chip}px`);
  el.style.width = el.style.height = `${G.chip}px`;
  el.innerHTML = stackHtml(amount, max);
  el.style.transform = 'none';
  el._size = G.chip;
  return el;
}

// ------------------------------------------------------------------ the room
function setupForm() {
  const ok = MIN_BETS.filter((v) => v >= W.min_bet && (!W.max_bet || v <= W.max_bet));
  if (!ok.length) ok.push(W.min_bet);
  if (!ok.includes(minBet)) minBet = ok[0];
  $('seatPick').innerHTML = Array.from({ length: W.max_seats }, (_, k) => `<button data-n="${k + 1}">${k + 1}</button>`).join('');
  $('seatPick').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
    seatsWanted = Number(b.dataset.n); sfx.click(); renderForm();
  }));
  const step = (d) => {
    const i = clamp(ok.indexOf(minBet) + d, 0, ok.length - 1);
    minBet = ok[i]; sfx.click(); renderForm();
  };
  $('minUp').addEventListener('click', () => step(1));
  $('minDown').addEventListener('click', () => step(-1));
  $('createBtn').addEventListener('click', () => {
    unlock(); sfx.click();
    send({ type: 'create', min_bet: minBet, max_players: seatsWanted });
  });
  renderForm();
}
function tableMax(min) {
  const m = min * W.max_mult;
  return Math.max(min, W.max_bet ? Math.min(m, W.max_bet) : m);
}
function renderForm() {
  if (!W) return;
  $('minBet').textContent = fmt(minBet);
  $('limitsHint').textContent = `Bets ${fmt(minBet)} – ${fmt(tableMax(minBet))}` + (minBet > balance ? ' · more than your balance' : '');
  $('seatPick').querySelectorAll('button').forEach((b) => b.classList.toggle('on', Number(b.dataset.n) === seatsWanted));
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
  if (!W) return;
  const list = room.tables;
  syncTables($('tables'), list, (t) => {
    const live = t.phase !== 'betting';
    const full = t.seated >= t.max;
    return `<div class="tstatus ${live ? 'live' : ''}">${live ? `LIVE · round ${t.round}` : 'BETTING'}</div>
      <div class="tname">${esc(t.name)}</div>
      <div class="tmeta">Bets <b>${fmt(t.min_bet)}</b> – <b>${fmt(t.max_bet)}</b> · ${t.seated}/${t.max} seats</div>
      <div class="tplayers">${esc(t.players.join(', '))}</div>
      <div class="tbtns"><button class="btn" data-watch="${t.id}">Watch</button>
      <button class="btn btn-gold" data-sit="${t.id}" ${full ? 'disabled' : ''}>${full ? 'Full' : 'Sit down'}</button></div>`;
  }, '<div class="empty">No tables open yet. Open one and invite the others, or play the dealer alone!</div>');
  $('roomCount').textContent = `(${room.players.length})`;
  $('people').innerHTML = room.players.map((p) => {
    const t = list.find((x) => x.id === p.table);
    return `<div class="person">${avatarHtml(p)}<div><div>${esc(p.name)}${p.id === myId ? ' (you)' : ''}</div>
      <div class="sub">${p.seated && t ? `at ${esc(t.name)}` : 'in the room'}</div></div></div>`;
  }).join('');
}

function setupInfo() {
  $('infoBody').innerHTML = `
    <p>Blackjack against the dealer, together at one table. Everyone plays their own hands against the dealer's, so
    you can all win (or lose) the same round. Open a table (you pick the minimum bet; the maximum is ${W.max_mult}×
    that) or sit down at one, and bet straight from your balance.</p>
    <ul>
      <li>Betting: tap the chips to put them on your circle. The round starts ${W.bet_time} seconds after the first bet,
      or as soon as everyone at the table has pressed <b>Deal</b>. <b>Rebet</b> repeats your last bet, <b>×2</b> doubles
      the one you have out.</li>
      <li>Get closer to 21 than the dealer without going over. Cards 2–10 count their number, J/Q/K count 10, an ace
      counts 1 or 11.</li>
      <li>On your turn: <b>Hit</b> (another card), <b>Stand</b>, <b>Double</b> (double your bet on your first two cards and
      get exactly one more card) or <b>Split</b> two cards of the same value into two hands (up to ${W.max_hands}
      hands; split aces get one card each). You have ${W.turn_time} seconds; if time runs out you stand, and after two
      timeouts in a row you're taken off the table.</li>
      <li>While others are playing you can already pick <b>Hit</b> or <b>Stand</b> for each of your hands still to
      play (on every seat you have): it's taken as soon as that hand's turn comes. Tap it again to take it back. After a hit you choose again.</li>
      <li>${W.decks}-deck shoe. The dealer stands on all 17s. A win pays 1:1, blackjack pays 3:2, a tie is a push (your
      bet comes back). When the dealer has blackjack, the round ends straight away.</li>
      <li>Anyone in the server can watch a table. A seat with no bet for a few rounds in a row is given up.</li>
      <li>You can play more than one seat: tap <b>+ SEAT</b> on any empty one. With <b>⧉ All seats</b> on (it is
      until you switch it off), every chip goes on all your seats at once; switch it off and tap one of your seats to
      put your chips there. <b>Clear</b>, <b>Rebet</b>, <b>×2</b> and <b>Deal</b> work on all your seats; × on a
      seat leaves just that one.</li>
      <li>💬 at the top: quick chats and emotes for everyone at the table.</li>
    </ul>
    <h3>Side bets</h3>
    <p>Next to your betting circle are two small ones for side bets. Pick <b>Pairs</b> or <b>21+3</b> above the chips
    (or tap the small circle) and put chips on it. A side bet goes with a main bet and can be at most as big as it.
    Side bets are paid straight after the deal, whatever happens to your hand: the winnings are stacked on top of your
    side bet and come to you with the rest when the round is paid.</p>
    <table class="pays"><tr><th colspan="2">Perfect Pairs <small>(your first two cards)</small></th></tr>
      ${payRow(W, 'pp', 'perfect', 'same card, e.g. 8♥ 8♥')}${payRow(W, 'pp', 'colored', 'same colour, e.g. 8♥ 8♦')}
      ${payRow(W, 'pp', 'mixed', 'red and black, e.g. 8♥ 8♠')}
      <tr><th colspan="2">21+3 <small>(your first two cards and the dealer's face-up card)</small></th></tr>
      ${payRow(W, 't3', 'suited_trips', 'e.g. 7♣ 7♣ 7♣')}${payRow(W, 't3', 'straight_flush', 'e.g. 5♦ 6♦ 7♦')}
      ${payRow(W, 't3', 'trips', 'e.g. 7♣ 7♥ 7♦')}${payRow(W, 't3', 'straight', 'e.g. 9♠ 10♥ J♣')}
      ${payRow(W, 't3', 'flush', 'e.g. 2♠ 9♠ K♠')}</table>
    <p>Your bet is held safely until the round is paid: if the bot restarts mid-round, you get it back.</p>
    <p>The 🗣 button switches the dealer's voice on or off (it uses your device's own speech voices).</p>`;
}
const payRow = (w, bet, k, eg) => `<tr><td>${w.side_names[k]} <small>${eg}</small></td><td>${w.side_pays[bet][k]}:1</td></tr>`;

// ================================================================== the table
const area = $('tableArea');
const fly = $('flyLayer');
const layer = $('cardsLayer');
let layoutKey = '';
let spotEls = [];          // per seat spot: { root, circle, stack, amt, plate, ring, nm, st, sit }
const cardEls = new Map(); // 'd:0', 's:<seat>:<hand>:<card>' -> card element
const totalEls = new Map();// 't:<seat>:<hand>' -> total badge
const stampEls = new Map();// 'r:<seat>:<hand>' -> result stamp
const stackShown = [];     // per seat: the amount its chip stack shows

function cardHtml(c) {
  if (!c) return '';
  return `<span class="rk">${c.r}</span><span class="st">${c.s}</span><span class="big">${c.s}</span>`;
}
function makeCard() {
  const el = document.createElement('div');
  el.className = 'bcard';
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

// ------------------------------------------------------------------ table geometry
// The table is laid out in pixels for the space it has, every time that space changes: the card size, the half-moon
// felt, the seats on an arc along its curved edge (seat 1, "first base", on the dealer's left = the right of the
// screen), the dealer's cards along the straight edge with the rack, shoe and discard tray, and the printing on the
// felt. If the seats don't fit side by side, everything gets smaller.
const G = { cw: 56, R: 28, sw: 120, chip: 26, seats: [], cx: 0, dealerY: 60, shoe: [0, 0], tray: [0, 0], rack: [0, 0], compact: false };

function layout(st = T) {
  if (!st || !spotEls.length) return;
  const w = area.clientWidth, h = area.clientHeight;
  if (!w || !h) return;
  const n = st.max;
  const plateH = platesHeight();
  let cw = clamp(Math.min(w / Math.min(n * 2.05 + 1.6, 5.2), h / 5.6), 22, 74);
  let best = null;
  for (let pass = 0; pass < 30; pass++) {
    const rail = clamp(cw * 0.26, 8, 18);
    const sw = clamp(cw * 2.1, w < 420 ? 62 : 78, 150);
    const R = cw * 0.52;
    const feltL = rail + 3, feltR = w - rail - 3;
    const frx = (feltR - feltL) / 2, cx = w / 2;
    const feltH = h - rail - 3 - plateH * 0.4;
    const cardH = cw * 1.4, rise = cw * 0.2;
    const handH = cardH + rise * 2;
    const rackH = cw * 0.42 + 11;
    const dealerY = rackH + 8 + cardH / 2;
    const dealerBottom = dealerY + cardH / 2;
    const ry = h - 6 - plateH - R - 6;            // the lowest circle centre (the middle seat)
    const rx = Math.max(10, frx - sw / 2 + 2);
    const need = R + 8 + handH + 18;               // a seat's cards and total, above its circle
    let a0 = Math.max(0.3, Math.asin(clamp(need / Math.max(1, ry), 0, 1)));
    // on a narrow table the end seats come up beside the dealer: keep their cards below the shoe and the tray
    if (n > 1 && rx * Math.cos(a0) - sw / 2 < cw * 3.2) a0 = Math.max(a0, Math.asin(clamp((need + cw * 1.75) / Math.max(1, ry), 0, 1)));
    // The seats go round the curve of the table, evenly spread by angle. On a tall, narrow table (a phone held
    // upright) that crowds the seats at the bottom, so they're then spread evenly along the curve itself instead,
    // which runs them up the long sides.
    const point = (a) => [cx + rx * Math.cos(a), ry * Math.sin(a)];
    const byAngle = () => Array.from({ length: n }, (_, i) => point(n === 1 ? Math.PI / 2 : a0 + (i * (Math.PI - 2 * a0)) / (n - 1)));
    const byLength = () => {
      if (n === 1) return [point(Math.PI / 2)];
      const steps = 240, along = [0];
      let prev = point(a0);
      for (let k = 1; k <= steps; k++) {
        const p = point(a0 + (k * (Math.PI - 2 * a0)) / steps);
        along.push(along[k - 1] + Math.hypot(p[0] - prev[0], p[1] - prev[1]));
        prev = p;
      }
      const total = along[steps];
      return Array.from({ length: n }, (_, i) => {
        const want = (i * total) / (n - 1);
        let k = 0;
        while (k < steps && along[k + 1] < want) k++;
        const f = along[k + 1] > along[k] ? (want - along[k]) / (along[k + 1] - along[k]) : 0;
        return point(a0 + ((k + Math.min(1, f)) * (Math.PI - 2 * a0)) / steps);
      });
    };
    // neighbouring seats (cards, circle and plate) must not overlap: side by side, or one above the other on a
    // tall table
    const clashes = (seats) => seats.some((p, i) => {
      if (!i) return false;
      const [x0, y0] = seats[i - 1], [x1, y1] = p;
      const apartX = Math.abs(x1 - x0) >= sw + 4;
      const apartY = y1 >= y0 ? y1 - need >= y0 + R + 8 + plateH : y0 - need >= y1 + R + 8 + plateH;
      return !apartX && !apartY;
    });
    const midTop = ry - need;
    let seats = byAngle();
    if (clashes(seats)) seats = byLength();
    const fits = !clashes(seats) && midTop >= dealerBottom + 8;
    best = { w, h, cw, R, sw, rail, cx, frx, feltL, feltH, dealerY, dealerBottom, rise, seats, midTop, plateH,
      chip: Math.round(clamp(R * 0.95, 14, 40)), compact: sw < 112 };
    if (!fits && cw > 22.5) { cw = Math.max(22, cw * 0.94); continue; }
    break;
  }
  // the layout that fits (or, on a screen too small for everything, the last and smallest try)
  Object.assign(G, best);
  const { cx, frx, feltL, feltH, rail, R, sw } = G;
  cw = G.cw;
  area.style.setProperty('--card-w', `${cw.toFixed(1)}px`);
  area.style.setProperty('--rail-w', `${rail.toFixed(1)}px`);
  area.classList.toggle('compact', G.compact);
  area.classList.toggle('tiny', sw < 80);
  Object.assign($('felt').style, { left: `${feltL}px`, top: '0px', width: `${2 * frx}px`, height: `${feltH}px` });
  // the dealer's rack, shoe and discard tray
  const rack = $('rack');
  rack.style.left = `${cx}px`; rack.style.top = '0px';
  rack.style.setProperty('--rk', `${Math.round(cw * 0.36)}px`);
  G.rack = [cx, cw * 0.25 + 6];
  // the shoe and the tray sit between the dealer's cards and the end seats (clear of those seats' cards)
  const ends = G.seats.length > 1 ? G.seats[0][0] - cx - G.sw / 2 - cw * 0.8 : frx;
  const side = Math.max(cw * 2.4, Math.min(frx * 0.66, ends));
  G.shoe = [cx + side, cw * 0.95 + 4];
  G.tray = [cx - side, cw * 0.9 + 4];
  Object.assign($('shoe').style, { left: `${G.shoe[0]}px`, top: `${G.shoe[1]}px` });
  Object.assign($('tray').style, { left: `${G.tray[0]}px`, top: `${G.tray[1]}px` });
  // the printing on the felt, on two arcs between the dealer and the players
  const fs = clamp(cw * 0.25, 8, 17);
  const room = G.midTop - G.dealerBottom;
  const svg = $('feltText');
  svg.style.display = room > fs * 3.4 ? '' : 'none';
  svg.setAttribute('width', 2 * frx); svg.setAttribute('height', feltH);
  // a shallow smile across the middle of the felt: its lowest point at `y`, its ends at a 20° angle
  const arc = (y, rxa) => {
    const rya = rxa * 0.42, a = 0.35, cy = y - rya;
    const x0 = frx - rxa * Math.cos(a), x1 = frx + rxa * Math.cos(a), ye = cy + rya * Math.sin(a);
    return `M ${x0.toFixed(1)} ${ye.toFixed(1)} A ${rxa.toFixed(1)} ${rya.toFixed(1)} 0 0 0 ${x1.toFixed(1)} ${ye.toFixed(1)}`;
  };
  const r1 = G.dealerBottom + room * 0.42;
  const rxa = clamp(fs * 13, frx * 0.42, frx * 0.62);
  $('arc1').setAttribute('d', arc(r1, rxa));
  $('arc2').setAttribute('d', arc(r1 + fs * 1.45, rxa * 0.96));
  svg.querySelector('.t1').style.fontSize = `${fs}px`;
  svg.querySelector('.t2').style.fontSize = `${(fs * 0.72).toFixed(1)}px`;
  G.msgY = room > fs * 3.4 ? r1 + fs * 3.1 : G.dealerBottom + room / 2;
  Object.assign($('centerMsg').style, { left: `${cx}px`, top: `${Math.min(G.msgY, G.midTop - 6)}px` });
  // the seats
  st.seats.forEach((s, i) => placeSpot(i));
  for (let i = 0; i < spotEls.length; i++) placeSpot(i);
  renderCards(st, { instant: true });
}
function placeSpot(i) {
  const e = spotEls[i];
  if (!e || !G.seats[i]) return;
  const [x, y] = G.seats[i];
  const { R, sw, chip } = G;
  e.root.style.left = `${x}px`; e.root.style.top = `${y}px`;
  e.circle.style.setProperty('--cr', `${(2 * R).toFixed(1)}px`);
  e.stack.style.setProperty('--chip', `${chip}px`);
  e.stack.style.top = `${-R * 0.12}px`;
  e.amt.style.top = `${R * 0.72}px`;
  e.plate.style.top = `${R + 6}px`;
  e.plate.style.setProperty('--pw', `${Math.round(sw)}px`);
  // the side bet circles: Perfect Pairs on the left, 21+3 on the right, a little lower than the main circle
  for (const k of SIDE) {
    const [dx, dy] = sideOffset(k);
    const s = e.sides[k];
    s.root.style.left = `${dx}px`; s.root.style.top = `${dy}px`;
    s.root.style.setProperty('--sr', `${(R * 0.98).toFixed(1)}px`);
    s.stack.style.setProperty('--chip', `${Math.round(chip * 0.72)}px`);
  }
}
const SIDE = ['pp', 't3'];
const sideOffset = (k) => [(k === 'pp' ? -1 : 1) * G.R * 1.52, G.R * 0.42];
const sideOf = (i, k) => { const [dx, dy] = sideOffset(k); return [G.seats[i][0] + dx, G.seats[i][1] + dy]; };
// lay out again whenever the table area changes size (window resized, the action bar grows, fonts load, …)
const platesHeight = () => Math.max(30, ...spotEls.map((e) => e.plate.offsetHeight || 0));
const resizer = new ResizeObserver(() => {
  // only when the space really changed (a plate appearing, or its text changing, doesn't change it)
  if (area.clientWidth === G.w && area.clientHeight === G.h && platesHeight() === G.plateH) return;
  layout();
});
resizer.observe(area);
if (document.fonts) document.fonts.ready.then(() => layout());

// where each hand of a seat goes: split hands side by side, smaller cards the more hands there are
function handGeom(seat, k) {
  const [sx, sy] = G.seats[seat] || [0, 0];
  k = Math.max(1, k);
  let cwS = G.cw * (k === 1 ? 1 : k === 2 ? 0.82 : 0.66);
  const spread = k === 1 ? 0 : Math.min(cwS * 1.65, (G.sw * 1.35) / k);
  if (k > 1) cwS = Math.min(cwS, spread * 0.86);
  return Array.from({ length: k }, (_, j) => ({ hx: sx + (j - (k - 1) / 2) * spread, baseY: sy - G.R - 6 - cwS * 0.7, cwS }));
}
// How far each card of a hand sits above the one before: a fifth of a card, but less on a long hand that would
// otherwise run off the top of the table (the end seats sit high up), so the top card and its total stay in view.
function handRise(g, m) {
  if (m < 2) return 0;
  const room = g.baseY - g.cwS * 0.7 - 10 - 14;   // the first card's top down to the total's centre, 14px under the edge
  return clamp(room / (m - 1), 0, g.cwS * 0.2);
}
const wobble = (a, b, c) => (((a * 37 + b * 11 + c * 7) % 7) - 3) * 0.9;
// every card on the table, where it goes: [{ key, x, y, w, rot, card, seat, hand, i }]
function cardSpots(st) {
  const out = [];
  const d = st.dealer.cards;
  const step = G.cw * 0.74;
  d.forEach((c, i) => out.push({ key: `d:${i}`, x: G.cx + (i - (d.length - 1) / 2) * step, y: G.dealerY, w: G.cw,
    rot: wobble(i, 1, 9) * 0.5, card: c, seat: -1, hand: 0, i }));
  st.seats.forEach((s, si) => {
    if (!s || !s.hands.length) return;
    const geo = handGeom(si, s.hands.length);
    s.hands.forEach((h, j) => {
      const g = geo[j], m = h.cards.length, rise = handRise(g, m);
      h.cards.forEach((c, i) => {
        const sideways = h.doubled && i === 2;   // a doubled hand's card lies across the others
        out.push({ key: `s:${si}:${j}:${i}`, x: g.hx + (i - (m - 1) / 2) * g.cwS * 0.3 + (sideways ? g.cwS * 0.1 : 0),
          y: g.baseY - i * rise - (sideways ? g.cwS * 0.1 : 0), w: g.cwS,
          rot: sideways ? 90 : wobble(si, j, i), card: c, seat: si, hand: j, i });
      });
    });
  });
  return out;
}
function cardKeys(st) { return cardSpots(st).map((p) => p.key); }

// Everything on the table moves with transforms only (never left/top): the phone can then animate it without
// laying the page out again on every frame. at() is the transform that puts an element's centre at (x, y).
const at = (x, y, rot = 0, scale = 1) => `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-50%, -50%)`
  + (rot ? ` rotate(${rot}deg)` : '') + (scale !== 1 ? ` scale(${scale.toFixed(3)})` : '');
// sets a style only when it changes (writing the same value again can still cost a style pass)
function put(el, prop, value) {
  const k = `_${prop}`;
  if (el[k] === value) return;
  el[k] = value;
  if (prop.startsWith('--')) el.style.setProperty(prop, value); else el.style[prop] = value;
}
// a quick "pop" on a badge, with the Web Animations API (restarting a CSS animation would need a forced layout)
const popEl = (el) => el.animate([{ scale: 1 }, { scale: 1.35, offset: 0.4 }, { scale: 1 }], { duration: 400, easing: 'ease' });
function placeCard(el, p) {
  el._p = p;
  put(el, 'transform', at(p.x, p.y, p.rot));
  put(el, '--w', `${p.w.toFixed(1)}px`);
  put(el, 'zIndex', String(p.i + 1));
}
// puts every card where it belongs. New cards appear in place, or (in `hidden`) stay invisible until flyIn().
function renderCards(st, opts = {}) {
  if (!st) return;
  const spots = cardSpots(st);
  const keep = new Set();
  const turn = st.turn;
  for (const p of spots) {
    keep.add(p.key);
    let el = cardEls.get(p.key);
    const fresh = !el;
    if (fresh) { el = makeCard(); el.classList.add('instant'); cardEls.set(p.key, el); layer.appendChild(el); }
    if (opts.instant) el.classList.add('instant');
    placeCard(el, p);
    const hidden = opts.hidden && opts.hidden.has(p.key);
    el.classList.toggle('gone', !!hidden);
    if (!hidden) setCard(el, p.card, !!p.card);
    else setCard(el, p.card, false);
    const seat = p.seat >= 0 ? st.seats[p.seat] : null;
    const hand = seat ? seat.hands[p.hand] : null;
    el.classList.toggle('turn', !!(turn && hand && turn.seat === p.seat && turn.hand === p.hand));
    const res = hand && hand.result;
    el.classList.toggle('dim', st.phase === 'settle' && (res === 'lose' || res === 'bust') || res === 'bust');
    el.classList.toggle('win', st.phase === 'settle' && (res === 'win' || res === 'blackjack'));
    if (fresh || opts.instant) requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('instant')));
  }
  if (!opts.keepOld) {
    for (const [k, el] of cardEls) if (!keep.has(k)) { el.remove(); cardEls.delete(k); }
  }
  renderBadges(st, opts);
}
// hand totals above every hand (soft hands show both counts) and the result stamps
function renderBadges(st, opts = {}) {
  const want = new Map(), stamps = new Map();
  const hiddenHand = (si, j) => opts.hidden && [...opts.hidden].some((k) => k.startsWith(si < 0 ? 'd:' : `s:${si}:${j}:`));
  st.seats.forEach((s, si) => {
    if (!s || !s.hands.length) return;
    const geo = handGeom(si, s.hands.length);
    s.hands.forEach((h, j) => {
      if (!h.cards.length || hiddenHand(si, j)) return;
      const g = geo[j], m = h.cards.length, rise = handRise(g, m);
      const topY = g.baseY - (m - 1) * rise - g.cwS * 0.7 - 10;
      const bj = h.result === 'blackjack';
      want.set(`t:${si}:${j}`, { x: g.hx, y: topY, text: bj ? 'BJ' : h.soft ? `${h.total - 10}/${h.total}` : String(h.total),
        cls: (bj ? 'bj' : h.total > 21 ? 'bust' : h.total === 21 ? 'n21' : '')
          + (h.pre && st.phase === 'playing' && !h.done ? ` pre-${h.pre}` : '') });
      const showRes = h.result && (st.phase === 'settle' || h.result === 'bust' || h.result === 'blackjack') && !opts.noStamps;
      if (showRes) {
        const label = { blackjack: 'BLACKJACK', win: h.payout ? `WIN` : 'WIN', push: 'PUSH', lose: 'LOSE', bust: 'BUST' }[h.result];
        stamps.set(`r:${si}:${j}`, { x: g.hx, y: g.baseY - (m - 1) * rise / 2, text: label, cls: h.result });
      }
    });
  });
  const sync = (map, wantMap, cls) => {
    for (const [k, el] of map) if (!wantMap.has(k)) { el.remove(); map.delete(k); }
    for (const [k, b] of wantMap) {
      let el = map.get(k);
      const fresh = !el;
      if (fresh) { el = document.createElement('div'); layer.appendChild(el); map.set(k, el); }
      const className = `${cls} ${b.cls}`;
      if (el.textContent !== b.text || fresh) {
        el.textContent = b.text;
        if (cls === 'total' && !fresh) popEl(el);
      }
      if (el.className !== className) el.className = className;
      if (fresh) { el.style.transition = 'none'; requestAnimationFrame(() => requestAnimationFrame(() => { el.style.transition = ''; })); }
      put(el, 'transform', at(b.x, b.y, cls === 'stamp' ? -12 : 0));
    }
  };
  sync(totalEls, want, 'total');
  sync(stampEls, stamps, 'stamp');
  // the dealer's total, right of the dealer's cards
  const dt = $('dealerTotal');
  const d = st.dealer;
  if (d.cards.length && !hiddenHand(-1, 0)) {
    const step = G.cw * 0.74;
    if (dt.classList.contains('hidden')) {   // appearing: straight into place, not sliding in from the corner
      dt.style.transition = 'none';
      requestAnimationFrame(() => requestAnimationFrame(() => { dt.style.transition = ''; }));
    }
    dt.classList.remove('hidden');
    const text = d.blackjack ? 'BJ' : d.soft ? `${d.total - 10}/${d.total}` : String(d.total);
    if (dt.textContent !== text) { dt.textContent = text; popEl(dt); }
    dt.classList.toggle('bust', d.total > 21);
    dt.classList.toggle('bj', !!d.blackjack);
    put(dt, 'transform', at(G.cx + ((d.cards.length - 1) / 2) * step + G.cw * 0.5 + 20, G.dealerY - G.cw * 0.35));
  } else dt.classList.add('hidden');
}

// moves a new card (already in place, invisible) out of the shoe to its spot, then turns it over
async function flyIn(key, { flip = true } = {}) {
  const el = cardEls.get(key);
  if (!el || !el._p) return;
  const p = el._p;
  // the card is already in place (invisible): it flies there from the shoe with the Web Animations API, which starts
  // without a forced layout and runs on the compositor
  el.classList.add('instant');
  el.classList.remove('gone', 'up');
  placeCard(el, p);
  const flight = el.animate([{ transform: at(G.shoe[0], G.shoe[1], -75) }, { transform: at(p.x, p.y, p.rot) }],
    { duration: 420, easing: 'cubic-bezier(.2,.8,.3,1)' });
  sfx.deal();
  await Promise.race([flight.finished.catch(() => {}), sleep(470)]);
  el.classList.remove('instant');
  if (flip && p.card) { setCard(el, p.card, true); sfx.flip(); }
}

// ------------------------------------------------------------------ building and drawing the table
function buildTable(st) {
  layoutKey = `${st.id}|${st.max}`;
  for (const m of [cardEls, totalEls, stampEls]) { for (const el of m.values()) el.remove(); m.clear(); }
  const host = $('spots');
  host.innerHTML = '';
  spotEls = Array.from({ length: st.max }, (_, i) => {
    const root = document.createElement('div');
    root.className = 'spot';
    root.innerHTML = `<div class="circle"></div><div class="stack"></div><div class="bet-amt hidden"></div>
      ${SIDE.map((k) => `<div class="side-spot" data-k="${k}"><span class="lbl">${k === 'pp' ? 'PAIRS' : '21+3'}</span>
        <div class="stack"></div><span class="side-amt hidden"></span></div>`).join('')}
      <button class="sit-btn hidden">SIT</button>
      <div class="plate"><div class="ring"></div><div class="who"><div class="nm"></div><div class="st"></div></div>
        <button class="unsit hidden" title="Leave this seat">×</button><span class="streak hidden"></span></div>`;
    host.appendChild(root);
    const e = { root, circle: root.querySelector('.circle'), stack: root.querySelector('.stack'), amt: root.querySelector('.bet-amt'),
      sit: root.querySelector('.sit-btn'), plate: root.querySelector('.plate'), ring: root.querySelector('.ring'),
      nm: root.querySelector('.nm'), st: root.querySelector('.st'), unsit: root.querySelector('.unsit'),
      streak: root.querySelector('.streak'), id: null, sides: {} };
    for (const k of SIDE) {
      const el = root.querySelector(`.side-spot[data-k="${k}"]`);
      e.sides[k] = { root: el, stack: el.querySelector('.stack'), amt: el.querySelector('.side-amt'), shown: 0 };
      // your own side circles can be tapped to put the next chips on them
      el.addEventListener('click', () => focusSeat(i, k));
    }
    e.circle.addEventListener('click', () => focusSeat(i, 'main'));
    e.plate.addEventListener('click', (ev) => { if (!ev.target.closest('.unsit')) focusSeat(i); });
    e.unsit.addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'leave', seat: i }); });
    e.sit.addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'sit', seat: i }); });
    resizer.observe(e.plate);
    stackShown[i] = 0;
    return e;
  });
  stackShown.length = st.max;
  layout(st);
}

function setSideStack(i, k, amount) {
  const e = spotEls[i] && spotEls[i].sides[k];
  if (!e) return;
  const [bet, won] = Array.isArray(amount) ? amount : [amount, 0];
  const key = `${bet}+${won}`;
  if (e.shown !== key) { e.shown = key; e.stack.innerHTML = stackHtml(bet, 5, won); }
  e.amt.classList.toggle('hidden', !(bet + won));
  e.amt.classList.toggle('won', !!won);
  e.amt.textContent = shortNum(bet + won);
  e.amt.title = fmt(bet + won);
}
// what lies on a side circle: the bet while betting, and a winning side bet with its winnings stacked on top until the
// round is paid (then it all goes to the player with the main bet)
function sideShown(st, s, k) {
  const r = st.phase !== 'settle' && s.side_results && s.side_results.find((x) => x.bet === k && x.win);
  return r ? [r.stake, r.payout - r.stake] : s.side[k];
}

// where the chips you tap go: the main bet or one of the side bets
let target = 'main';
function setTarget(k) {
  target = k;
  $('betTarget').querySelectorAll('button[data-t]').forEach((b) => b.classList.toggle('on', b.dataset.t === k));
  if (T) renderStatic(T);
}
$('betTarget').querySelectorAll('button[data-t]').forEach((b) => b.addEventListener('click', () => { unlock(); sfx.click(); setTarget(b.dataset.t); }));

// Copy bets: a player with two or more seats puts every chip on all of them at once (one message; the server skips a
// seat that can't take it). On unless the player switched it off; that choice is remembered on this device.
let copyBets = true;
try { copyBets = localStorage.getItem('blackjack-copy-bets') !== '0'; } catch (e) { /* storage blocked */ }
const copying = (st) => copyBets && (st.mine || []).length > 1;
$('copyBtn').addEventListener('click', () => {
  unlock(); sfx.click();
  copyBets = !copyBets;
  try { localStorage.setItem('blackjack-copy-bets', copyBets ? '1' : '0'); } catch (e) { /* storage blocked */ }
  toast(copyBets ? 'Chips now go on all your seats.' : 'Chips now go on one seat: tap a seat to pick it.', true);
  if (T) renderStatic(T);
});

function setStack(i, amount) {
  const e = spotEls[i];
  if (!e) return;
  if (stackShown[i] !== amount) {
    stackShown[i] = amount;
    e.stack.innerHTML = stackHtml(amount);
  }
  e.amt.classList.toggle('hidden', !amount);
  e.amt.textContent = G.compact ? shortNum(amount) : fmt(amount);
  e.amt.title = fmt(amount);
}

// draws a state as it is, without animation (used after each animation, and on the first look at a table)
function renderStatic(st, opts = {}) {
  if (!st) return;
  const key = `${st.id}|${st.max}`;
  if (key !== layoutKey) buildTable(st);
  const mine = st.mine || [];
  const more = mine.length < ((W && W.max_each) || 1);
  st.seats.forEach((s, i) => {
    const e = spotEls[i];
    if (!e) return;
    e.root.classList.toggle('vacant', !s);
    e.plate.classList.toggle('hidden', !s);
    e.sit.classList.toggle('hidden', !!s || !more);
    if (!s) e.sit.textContent = mine.length ? '+ SEAT' : 'SIT';
    if (!s) {
      setStack(i, 0); SIDE.forEach((k) => setSideStack(i, k, 0));
      e.root.className = 'spot vacant'; e.id = null; setStreak(e.streak, 0); return;
    }
    if (e.id !== s.id) {
      e.id = s.id;
      e.ring.innerHTML = avatarHtml(s);
    }
    const name = s.name + (mine.includes(i) ? ' (you)' : '');
    if (e.nm.textContent !== name) e.nm.textContent = name;
    setStreak(e.streak, s.streak || 0);
    const turn = st.turn && st.turn.seat === i;
    e.root.classList.toggle('mine', mine.includes(i));
    // the seat(s) your chips go to: all of them while copying bets, else the one in focus
    const allSeats = copying(st) && st.phase === 'betting';
    e.root.classList.toggle('focus', mine.length > 1 && (allSeats ? mine.includes(i) : i === st.you));
    e.unsit.classList.toggle('hidden', !(mine.length > 1 && mine.includes(i) && !s.leaving && (st.phase === 'betting' || !s.hands.length)));
    e.root.classList.toggle('turn', !!turn);
    e.root.classList.toggle('ready', st.phase === 'betting' && s.ready);
    e.root.classList.toggle('offline', !s.online);
    const net = s.won - s.staked;
    e.root.classList.toggle('winner', st.phase === 'settle' && net > 0);
    // the line under the name
    let text = '', cls = '';
    if (s.leaving) text = 'Leaving';
    else if (st.phase === 'betting') { text = s.bet ? (s.ready ? 'Ready ✓' : 'Betting…') : 'No bet'; cls = s.ready ? 'good' : ''; }
    else if (!s.hands.length) text = 'Next round';
    else if (st.phase === 'settle') {
      text = net > 0 ? `+${G.compact ? shortNum(net) : fmt(net)}` : net < 0 ? `−${G.compact ? shortNum(-net) : fmt(-net)}` : 'Push';
      cls = net > 0 ? 'good' : net < 0 ? 'bad' : '';
    } else if (turn) { text = 'Playing…'; cls = 'gold'; }
    else text = s.hands.every((h) => h.done) ? 'Done' : 'Waiting';
    e.st.textContent = text; e.st.className = `st ${cls}`;
    const shown = opts.stacks && opts.stacks[i] !== undefined ? opts.stacks[i] : st.phase === 'settle' ? 0 : s.bet;
    setStack(i, shown);
    for (const k of SIDE) {
      const o = opts.sides && opts.sides[i];
      setSideStack(i, k, o && o[k] !== undefined ? o[k] : sideShown(st, s, k));
      e.sides[k].root.classList.toggle('target', i === st.you && st.phase === 'betting' && target === k);
    }
    e.root.classList.toggle('target-main', i === st.you && st.phase === 'betting' && target === 'main');
  });
  renderCards(st, opts);
  // the discard tray fills up as the shoe empties
  $('trayCards').style.height = `${clamp((1 - st.shoe / st.shoe_size) * 140, 0, 82)}%`;
  renderSide(st);
  renderControls(st);
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
  $('crumb').textContent = `${st.name} · bets ${fmt(st.min_bet)}–${fmt(st.max_bet)}`;
  const me = st.you >= 0 ? st.seats[st.you] : null;
  const seated = st.seats.filter(Boolean).length;
  $('tableInfo').innerHTML = `<b>${esc(st.name)}</b><br>Bets ${fmt(st.min_bet)} – ${fmt(st.max_bet)}<br>
    ${seated}/${st.max} seats · ${st.round ? `round #${st.round}` : 'no rounds yet'}
    ${me ? '' : '<br>You are watching'}`;
  renderLog(st.log);
}

// ------------------------------------------------------------------ controls
const amt = (v) => (innerWidth < 600 ? shortNum(v) : fmt(v));
function chipValues(st) {
  const all = DENOMS.map((d) => d[0]);
  // one chip under the minimum (half of it or more, e.g. 500K at a 1M table) to top a bet up between whole minimums;
  // a bet still has to reach the minimum, the server says so if it doesn't
  const below = all.filter((v) => v < st.min_bet && v * 2 >= st.min_bet).slice(-1);
  const vals = [...below, st.min_bet, ...all.filter((v) => v > st.min_bet && v <= st.max_bet)];
  return vals.slice(0, innerWidth < 420 ? 5 : 6);
}
let chipKey = '';
function renderControls(st) {
  const me = st.you >= 0 ? st.seats[st.you] : null;
  const betting = st.phase === 'betting' && me && !me.leaving;
  const myHand = me && st.turn && st.turn.seat === st.you ? me.hands[st.turn.hand] : null;
  const myTurn = !!(myHand && !myHand.done && st.can_double !== undefined);
  // the three rows share one place in the bar; a row not in use is only made invisible (.off), so the bar keeps its
  // height and the table above isn't laid out again. Watchers never bet, so they don't get those rows at all.
  $('betRow').classList.toggle('hidden', !me);
  $('actRow').classList.toggle('hidden', !me);
  $('betRow').classList.toggle('off', !betting);
  $('actRow').classList.toggle('off', !myTurn);
  $('waitRow').classList.toggle('off', !!(betting || myTurn));
  if (betting) {
    const vals = chipValues(st);
    const k = `${st.id}|${vals.join(',')}`;
    if (k !== chipKey) {
      chipKey = k;
      $('chipPick').innerHTML = vals.map((v) => `<button data-v="${v}" title="${fmt(v)}">${chipHtml(v, shortNum(v))}</button>`).join('');
      $('chipPick').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
        unlock(); sfx.chips(2);
        const amount = Number(b.dataset.v);
        if (T && copying(T)) send({ type: 'bet', amount, spot: target, all: true });
        else send({ type: 'bet', amount, spot: target, seat: T ? T.you : undefined });
      }));
    }
    const all = myBets(st);
    const copy = copying(st);
    $('copyBtn').classList.toggle('hidden', all.seats.length < 2);
    $('copyBtn').classList.toggle('on', copyBets);
    $('copyBtn').setAttribute('aria-pressed', String(copyBets));
    // What a chip of value v puts on a seat, as the server does it: a main bet is cut down to the table maximum (and
    // must reach the minimum); a side bet needs a main bet and is cut down to at most the main bet.
    const room = (s, v) => (target === 'main'
      ? (s.bet + v < st.min_bet ? 0 : Math.max(0, Math.min(v, st.max_bet - s.bet)))
      : (s.bet ? Math.max(0, Math.min(v, s.bet - s.side[target])) : 0));
    const betSeats = copy ? all.seats.filter((s) => !s.leaving) : [me];
    // while copying, a chip is only offered if every seat that takes it can be paid for
    $('chipPick').querySelectorAll('button').forEach((b) => {
      const parts = betSeats.map((s) => room(s, Number(b.dataset.v)));
      b.disabled = !parts.some((x) => x > 0) || parts.reduce((a, x) => a + x, 0) > balance;
    });
    $('betTarget').querySelectorAll('button[data-t]').forEach((b) => {
      if (b.dataset.t !== 'main') b.classList.toggle('dim', !betSeats.some((s) => s.bet));
    });
    const lastAll = all.rebet.reduce((a, s) => a + Math.min(s.last_bet, st.max_bet)
      + (s.last_side ? Math.min(s.last_side.pp, s.last_bet) + Math.min(s.last_side.t3, s.last_bet) : 0), 0);
    const doubling = all.seats.filter((s) => s.bet && s.bet < st.max_bet);
    $('clearBtn').disabled = !all.any && !all.sides;
    $('rebetBtn').disabled = !all.rebet.length || lastAll > balance;
    $('rebetBtn').innerHTML = all.rebet.length ? `Rebet <small>${amt(lastAll)}</small>` : 'Rebet';
    $('x2Btn').disabled = !doubling.length || doubling.reduce((a, s) => a + s.bet + s.side.pp + s.side.t3, 0) > balance;
    $('dealBtn').disabled = !all.any || all.ready;
    $('dealBtn').textContent = all.ready ? 'Ready ✓' : 'Deal';
  } else if (myTurn) {
    const h = me.hands[st.turn.hand];
    $('myTotal').textContent = h ? (me.hands.length > 1 ? `Hand ${st.turn.hand + 1}: ` : '') + (h.soft ? `${h.total - 10}/${h.total}` : h.total) : '';
    $('doubleBtn').disabled = !st.can_double || st.extra > balance;
    $('splitBtn').disabled = !st.can_split || st.extra > balance;
    $('doubleBtn').innerHTML = `Double <small>+${amt(st.extra)}</small>`;
    $('splitBtn').innerHTML = `Split <small>+${amt(st.extra)}</small>`;
    for (const id of ['hitBtn', 'standBtn']) $(id).disabled = false;
  } else {
    const free = st.seats.some((s) => !s);
    $('sitBtn').classList.toggle('hidden', !!me || !free);
    $('leaveBtn').classList.toggle('hidden', !me);
    $('backBtn').classList.toggle('hidden', !!me);
    let text = '';
    const turnSeat = st.turn ? st.seats[st.turn.seat] : null;
    if (st.phase === 'playing' && turnSeat) text = turnSeat.id === myId ? '' : `${turnSeat.name} is playing…`;
    else if (st.phase === 'dealing') text = 'Dealing…';
    else if (st.phase === 'dealer') text = "Dealer's turn…";
    else if (st.phase === 'settle') text = 'Paying out…';
    else if (st.phase === 'betting' && me && me.leaving) text = 'Leaving the table…';
    else if (st.phase === 'betting') text = 'Bets are open';
    if (me && st.phase !== 'betting' && !me.hands.length) text = text ? `${text} You're in from the next round.` : "You're in from the next round.";
    if (!me && !free) text = 'This table is full. You can watch.';
    $('waitText').textContent = text;
    renderPre(st);
  }
}
// Hit or Stand picked before your turn, for every one of your hands still to play (on all your seats, split hands
// too); the server takes each one as soon as that hand's turn comes. The buttons are in the same order as the hands on
// the table, left to right, and a hand with a pick shows it beside its total. Only shown once the turns have started,
// so the totals don't give the cards away before they're dealt on screen.
function handsAhead(st) {
  if (st.phase !== 'playing' || !st.turn) return [];
  const out = [];
  for (const i of st.mine || []) {
    const s = st.seats[i];
    if (!s || s.leaving) continue;
    const geo = handGeom(i, s.hands.length);
    s.hands.forEach((h, j) => {
      if (!h.done && !(st.turn.seat === i && st.turn.hand === j)) out.push({ seat: i, hand: j, h, x: geo[j].hx });
    });
  }
  return out.sort((a, b) => a.x - b.x);
}
let preHands = [];   // the hands the buttons are for, in the order of the groups
// the same hands in the order they'll be played (seats from the dealer's left, a seat's hands in order)
const handsAheadInPlayOrder = () => [...preHands].sort((a, b) => a.seat - b.seat || a.hand - b.hand);
function renderPre(st) {
  preHands = handsAhead(st);
  const row = $('preRow');
  row.classList.toggle('hidden', !preHands.length);
  if (!preHands.length) return;
  const one = preHands.length === 1 && (st.mine || []).length === 1 && st.seats[preHands[0].seat].hands.length === 1;
  // the groups are only rebuilt when the hands change, so a button doesn't lose its focus on every update
  const key = preHands.map((p) => `${p.seat}:${p.hand}`).join(',') + (one ? '|1' : '');
  if (row._key !== key) {
    row._key = key;
    row.innerHTML = preHands.map((p, n) => `<div class="pre-group">
      <span class="pre-label"></span>
      <button class="btn btn-hit" data-n="${n}" data-a="hit" aria-pressed="false">Hit</button>
      <button class="btn btn-stand" data-n="${n}" data-a="stand" aria-pressed="false">Stand</button></div>`).join('');
  }
  row.querySelectorAll('.pre-group').forEach((g, n) => {
    const { h } = preHands[n];
    const total = esc(String(h.soft ? `${h.total - 10}/${h.total}` : h.total));
    g.querySelector('.pre-label').innerHTML = one ? `Your hand <b>${total}</b> · then:` : `<b>${total}</b>`;
    for (const b of g.querySelectorAll('button')) {
      const on = h.pre === b.dataset.a;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    }
  });
}
$('preRow').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-a]');
  const p = b && preHands[Number(b.dataset.n)];
  if (!p) return;
  unlock(); sfx.click();
  send({ type: 'pre', seat: p.seat, hand: p.hand, action: p.h.pre === b.dataset.a ? '' : b.dataset.a });
});
function act(action) {
  unlock();
  for (const id of ['hitBtn', 'standBtn', 'doubleBtn', 'splitBtn']) $(id).disabled = true;
  send({ type: 'act', action });
}
$('hitBtn').addEventListener('click', () => act('hit'));
$('standBtn').addEventListener('click', () => act('stand'));
$('doubleBtn').addEventListener('click', () => act('double'));
$('splitBtn').addEventListener('click', () => act('split'));
$('clearBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'clear' }); });
$('rebetBtn').addEventListener('click', () => { unlock(); sfx.chips(3); send({ type: 'rebet' }); });
$('x2Btn').addEventListener('click', () => { unlock(); sfx.chips(4); send({ type: 'double_bet' }); });
$('dealBtn').addEventListener('click', () => { unlock(); sfx.click(); $('dealBtn').disabled = true; send({ type: 'ready' }); });
$('sitBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'sit' }); });
$('leaveBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'leave' }); });
// while bets are open the betting row takes the place of the one with "Leave table", so it has its own Leave
$('betLeaveBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'leave' }); });
$('backBtn').addEventListener('click', () => { sfx.click(); send({ type: 'watch', table: null }); });
// keyboard: H hit, S stand, D double, P split, Space deal
document.addEventListener('keydown', (e) => {
  if (e.repeat || !T || $('tableView').classList.contains('hidden')) return;
  const k = e.key.toLowerCase();
  const map = { h: 'hitBtn', s: 'standBtn', d: 'doubleBtn', p: 'splitBtn', ' ': 'dealBtn' };
  // before your turn, H and S pick the action for your next hand to play
  const before = { h: 'hit', s: 'stand' }[k] && preHands.length
    ? $('preRow').querySelector(`button[data-n="${preHands.indexOf(handsAheadInPlayOrder()[0])}"][data-a="${{ h: 'hit', s: 'stand' }[k]}"]`) : null;
  for (const b of [map[k] && $(map[k]), before]) {
    if (b && !b.disabled && !b.closest('.hidden, .off')) { e.preventDefault(); b.click(); return; }
  }
});

// ================================================================== quick chat
// Emotes and (Dutch) phrases from a fixed list the server sends; a chat shows as a speech bubble over the player's
// seat, or under the dealer for someone who's only watching. The server limits how often anyone can chat.
const bubbles = new Map();   // user id -> their bubble on the table now
let chatCooling = false;
function setupChat() {
  if (!W.emotes) return;
  $('chatEmotes').innerHTML = W.emotes.map((e, i) => `<button data-id="${i}">${esc(e)}</button>`).join('');
  $('chatPhrases').innerHTML = W.phrases.map((t, i) => `<button data-id="${W.emotes.length + i}">${esc(t)}</button>`).join('');
}
function toggleChat(open = $('chatPanel').classList.contains('hidden')) {
  $('chatPanel').classList.toggle('hidden', !open);
  $('chatBtn').setAttribute('aria-expanded', String(open));
}
$('chatBtn').addEventListener('click', () => { unlock(); sfx.click(); toggleChat(); });
$('chatPanel').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-id]');
  if (!b || chatCooling) return;
  unlock();
  send({ type: 'chat', id: Number(b.dataset.id) });
  toggleChat(false);
  chatCooling = true; $('chatPanel').classList.add('cooling');
  setTimeout(() => { chatCooling = false; $('chatPanel').classList.remove('cooling'); }, 900);
});
document.addEventListener('pointerdown', (e) => {
  if (!$('chatPanel').classList.contains('hidden') && !e.target.closest('#chatPanel, #chatBtn')) toggleChat(false);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') toggleChat(false); });

function showChat(m) {
  if (!T || !W || m.table !== T.id || $('tableView').classList.contains('hidden')) return;
  const text = [...W.emotes, ...W.phrases][m.id];
  if (!text) return;
  const old = bubbles.get(m.from);
  if (old) old.remove();
  const seated = m.seat >= 0 && G.seats[m.seat] && T.seats[m.seat];
  const mine = m.from === myId;
  const el = document.createElement('div');
  el.className = `bubble${m.id < W.emotes.length ? ' emote' : ''}${mine ? ' mine' : ''}${seated ? '' : ' loose'}`;
  el.innerHTML = `${mine ? '' : `<span class="who">${esc(m.name)}</span>`}<span class="say">${esc(text)}</span>`;
  $('chatLayer').appendChild(el);
  // over the top of the player's circle (the tail points at it), or under the dealer's cards
  const [ax, ay] = seated ? [G.seats[m.seat][0], G.seats[m.seat][1] - G.R - 4] : [G.cx, G.dealerY + G.cw * 1.25 + el.offsetHeight + 10];
  const w = el.offsetWidth, h = el.offsetHeight;
  const left = clamp(ax - w / 2, 4, Math.max(4, area.clientWidth - w - 4));
  // out of the way of the bubbles already there: a seat's bubble moves up (its tail still points down at the seat),
  // a watcher's moves down
  let top = Math.max(4, ay - h - 8);
  for (let pass = 0; pass < 4; pass++) {
    const hit = [...bubbles.values()].find((b) => b !== old && b.isConnected && left < b._x + b._w + 4 && b._x < left + w + 4
      && top < b._y + b._h + 4 && b._y < top + h + 4);
    if (!hit) break;
    top = seated ? Math.max(4, hit._y - h - 6) : hit._y + hit._h + 6;
  }
  Object.assign(el, { _x: left, _y: top, _w: w, _h: h });
  el.style.left = `${left.toFixed(0)}px`;
  el.style.top = `${top.toFixed(0)}px`;
  el.style.setProperty('--tail', `${clamp(ax - left, 14, w - 14).toFixed(0)}px`);
  bubbles.set(m.from, el);
  el.addEventListener('animationend', () => { el.remove(); if (bubbles.get(m.from) === el) bubbles.delete(m.from); });
  sfx.pop();
}

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
    T = S = null; layoutKey = '';
    toggleChat(false); $('chatBtn').classList.add('hidden'); $('chatLayer').replaceChildren(); bubbles.clear();
    $('tableView').classList.add('hidden'); $('room').classList.remove('hidden'); $('crumb').textContent = '';
    return;
  }
  $('room').classList.add('hidden'); $('tableView').classList.remove('hidden');
  $('chatBtn').classList.toggle('hidden', !W || !W.emotes);
  const prev = T;
  const sameTable = prev && prev.id === st.id && layoutKey === `${st.id}|${st.max}`;
  // no animation when it can't be seen (the window is in the background: browsers pause animations there, so the
  // table would fall behind), when many states are waiting, or when switching tables
  if (!sameTable || pending > 3 || document.hidden || st.seq <= (prev ? prev.seq : 0)) {
    T = S = st; renderStatic(st, { instant: true }); return;
  }
  S = st;
  try {
    await animate(prev, st);
  } catch (e) { console.error(e); }
  T = st;
  renderStatic(st);
  if (st.event && st.event.kind === 'turn' && st.turn && st.turn.seat === st.you) sfx.yourTurn();
}

// positions within the table area
function flyEl(el, from, to, ms, { arc = 0, s0 = 1, s1 = 1, o0 = 1, o1 = 1, rot = 0, easing = 'cubic-bezier(.2,.8,.3,1)' } = {}) {
  fly.appendChild(el);
  const w = el._size || el.offsetWidth, h = el._size || el.offsetHeight;   // known sizes need no layout
  const tf = (x, y, s, r) => `translate(${x - w / 2}px, ${y - h / 2}px) rotate(${r}deg) scale(${s})`;
  const frames = [{ transform: tf(from[0], from[1], s0, 0), opacity: o0 }];
  if (arc) frames.push({ transform: tf((from[0] + to[0]) / 2, Math.min(from[1], to[1]) - arc, (s0 + s1) / 2 * 1.15, rot / 2), offset: 0.5 });
  frames.push({ transform: tf(to[0], to[1], s1, rot), opacity: o1 });
  const anim = el.animate(frames, { duration: ms, easing, fill: 'forwards' });
  // A browser that isn't drawing the page may never finish the animation, so don't wait for it longer than it should
  // take: the table must keep up with the game.
  return Promise.race([anim.finished.catch(() => {}), sleep(ms + 250)]).then(() => el.remove());
}
// from the table's geometry, not by measuring the page (which would force a layout in the middle of animations)
const plateOf = (i) => [G.seats[i][0], G.seats[i][1] + G.R + 6 + G.plateH / 2];
const circleOf = (i) => [G.seats[i][0], G.seats[i][1] - G.R * 0.12];
function float(i, text, cls = '') {
  const f = document.createElement('div');
  f.className = `won-float ${cls}`;
  f.textContent = text;
  const [x, y] = plateOf(i);
  f.style.left = `${x}px`; f.style.top = `${y - 26}px`;
  area.appendChild(f);
  setTimeout(() => f.remove(), 2100);
}
function banner(text, cls = '') {
  const b = $('banner');
  b.textContent = text; b.className = `banner ${cls}`;
  void b.offsetWidth; b.classList.add('show');
  clearTimeout(banner.t); banner.t = setTimeout(() => b.classList.add('hidden'), 1950);
}
// a shower of gold coins from the dealer's rack to a player (your own wins)
function coinShower(i, n) {
  const [tx, ty] = plateOf(i);
  const [fx, fy] = G.rack;
  for (let k = 0; k < n; k++) {
    const c = document.createElement('div');
    c.className = 'coin';
    c._size = 18;
    const sx = fx + (Math.random() - 0.5) * G.cw * 2, sy = fy + Math.random() * 10;
    setTimeout(() => {
      flyEl(c, [sx, sy], [tx + (Math.random() - 0.5) * 30, ty + (Math.random() - 0.5) * 10], 700 + Math.random() * 400,
        { arc: 40 + Math.random() * 90, rot: (Math.random() - 0.5) * 720, s0: 0.6 + Math.random() * 0.5, s1: 0.7, easing: 'cubic-bezier(.3,.1,.4,1)' })
        .then(() => { if (k % 3 === 0) sfx.coin(); });
    }, k * 35);
  }
}
function pulseRack() { const r = $('rack'); r.classList.remove('bump'); void r.offsetWidth; r.classList.add('bump'); }

async function animate(prev, st) {
  const ev = st.event || {};
  switch (ev.kind) {
    case 'bet': return animBet(prev, st, ev);
    case 'unbet': return animUnbet(prev, st, ev);
    case 'sit': return animSit(prev, st);
    case 'closed': return animClosed(prev, st);
    case 'side': return animSide(prev, st, ev);
    case 'shuffle': return animShuffle();
    case 'deal': return animDeal(prev, st);
    case 'blackjack': return animNaturals(prev, st, ev);
    case 'card': return animCard(prev, st, ev);
    case 'double': return animDouble(prev, st, ev);
    case 'split': return animSplit(prev, st, ev);
    case 'stand': return animStand(prev, st, ev);
    case 'reveal': return animReveal(prev, st, ev);
    case 'dealer_card': return animDealerCard(prev, st, ev);
    case 'settle': return animSettle(prev, st, ev);
    case 'clear': return animClear(prev, st);
    default: return undefined;
  }
}

// chips slide from the player onto their circles (the main bet and/or the side bets)
const spotPos = (i, k) => (k === 'main' ? circleOf(i) : sideOf(i, k));
async function animBet(prev, st, ev) {
  const i = ev.seat;
  if (!spotEls[i] || !st.seats[i]) return;
  const old = prev.seats[i] || { bet: 0, side: { pp: 0, t3: 0 } };
  renderStatic(st, { stacks: { [i]: old.bet }, sides: { [i]: old.side } });
  sfx.chips(3);
  const amounts = ev.amounts || { main: ev.amount };
  await Promise.all(Object.entries(amounts).filter(([, v]) => v > 0).map(([k, v], n) =>
    sleep(n * 70).then(() => flyEl(chipPile(v, 4), plateOf(i), spotPos(i, k), 380).then(() => {
      if (k === 'main') setStack(i, st.seats[i].bet); else setSideStack(i, k, st.seats[i].side[k]);
    }))));
}
async function animUnbet(prev, st, ev) {
  const i = ev.seat;
  if (!spotEls[i]) return;
  const amounts = ev.amounts || { main: ev.amount };
  setStack(i, 0);
  SIDE.forEach((k) => setSideStack(i, k, 0));
  sfx.chips(2);
  await Promise.all(Object.entries(amounts).filter(([, v]) => v > 0).map(([k, v]) =>
    flyEl(chipPile(v, 4), spotPos(i, k), plateOf(i), 380)));
}
// you sat down: the dealer welcomes you
async function animSit(prev, st) {
  const mine = st.you >= 0 && (!prev || prev.you < 0);
  renderStatic(st);
  if (mine && st.phase === 'betting') say(`Welcome, ${speakName(st.seats[st.you].name)}. Place your bets, please.`);
}
// betting closes: the dealer waves over the table
async function animClosed(prev, st) {
  renderStatic(st);
  say('No more bets, thank you.', { urgent: true });
  sfx.reveal();
  banner('NO MORE BETS', 'cool');
  await sleep(500);
}
// the side bets: losing ones go to the rack, winning ones are paid out of it and go to the player
async function animSide(prev, st, ev) {
  const before = {};
  st.seats.forEach((s, i) => { if (s && prev.seats[i]) before[i] = { ...prev.seats[i].side }; });
  renderStatic(st, { sides: before });
  const results = ev.results || [];
  const wins = results.filter((r) => r.win);
  const speech = [];
  for (const r of wins) {
    const s = st.seats[r.seat];
    if (!s) continue;
    speech.push(`${r.name}! ${speakName(s.name)} wins ${speakAmount(r.payout - r.stake)} on ${r.bet === 'pp' ? 'Perfect Pairs' : 'twenty-one plus three'}.`);
  }
  if (speech.length) say(speech.join(' '));
  // the tags over the side circles: what it was, and what it paid
  for (const r of results) {
    if (!spotEls[r.seat]) continue;
    const [x, y] = sideOf(r.seat, r.bet);
    const f = document.createElement('div');
    f.className = `side-tag ${r.win ? 'win' : 'lose'}`;
    f.innerHTML = r.win ? `${esc(r.name)}<b>${r.mult}:1</b>` : 'No win';
    f.style.left = `${x}px`; f.style.top = `${y - G.R * 0.9}px`;
    area.appendChild(f);
    setTimeout(() => f.remove(), 2600);
  }
  const mine = wins.filter((r) => isMine(st, r.seat));
  if (mine.length) {
    const best = mine.reduce((a, r) => (r.mult > a.mult ? r : a));
    banner(best.name.toUpperCase());
    sfx.blackjack();
  } else if (wins.length) sfx.reveal();
  await sleep(500);
  // losing side bets to the rack
  let flights = [];
  for (const r of results.filter((x) => !x.win)) {
    if (!spotEls[r.seat]) continue;
    before[r.seat][r.bet] = 0;
    setSideStack(r.seat, r.bet, 0);
    flights.push(flyEl(chipPile(r.stake, 3), sideOf(r.seat, r.bet), G.rack, 480, { easing: 'cubic-bezier(.5,0,.2,1)' }));
  }
  if (flights.length) { sfx.sweep(); await Promise.all(flights); pulseRack(); }
  if (!wins.length) return;
  // the winnings come out of the rack and are stacked on top of the side bet; they stay there until the round is paid
  flights = wins.filter((r) => spotEls[r.seat]).map((r) =>
    flyEl(chipPile(r.payout - r.stake, 5), G.rack, sideOf(r.seat, r.bet), 560, { arc: 20 })
      .then(() => { setSideStack(r.seat, r.bet, [r.stake, r.payout - r.stake]); float(r.seat, `+${fmt(r.payout - r.stake)}`); }));
  sfx.chips(8);
  await Promise.all(flights);
  if (mine.length) sfx.win(true);
  await sleep(350);
}
async function animShuffle() {
  const shoe = $('shoe');
  banner('SHUFFLING', 'cool');
  sfx.shuffle();
  shoe.classList.remove('shuffle'); void shoe.offsetWidth; shoe.classList.add('shuffle');
  $('trayCards').style.height = '0%';
  await sleep(1600);
  shoe.classList.remove('shuffle');
}
// newly dealt cards, in dealing order: one to every player from the dealer's left, one to the dealer, and again
function newCards(st) {
  const order = (k) => {
    const p = k.split(':');
    return p[0] === 'd' ? [Number(p[1]), 99, 0] : [Number(p[3]), Number(p[1]), Number(p[2])];
  };
  return cardKeys(st).filter((k) => !cardEls.has(k)).sort((a, b) => {
    const x = order(a), y = order(b);
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
  });
}
async function animDeal(prev, st) {
  const fresh = newCards(st);
  renderStatic(st, { hidden: new Set(fresh) });
  const flights = [];
  for (const k of fresh) {
    flights.push(flyIn(k));
    await sleep(260);
  }
  await Promise.all(flights);
  await sleep(150);
}
async function animNaturals(prev, st, ev) {
  renderStatic(st);
  const mineBj = ev.seats.filter((i) => isMine(st, i));
  if (mineBj.length) { banner('BLACKJACK!'); sfx.blackjack(); mineBj.forEach((i) => coinShower(i, 14)); }
  else sfx.reveal();
  const names = [...new Set(ev.seats.filter((i) => st.seats[i]).map((i) => speakName(st.seats[i].name)))];
  if (names.length) say(`Blackjack for ${list(names)}!`);
  await sleep(700);
}
async function animCard(prev, st, ev) {
  const fresh = newCards(st);
  renderStatic(st, { hidden: new Set(fresh), noStamps: true, stacks: { [ev.seat]: prev.seats[ev.seat] ? prev.seats[ev.seat].bet : 0 } });
  for (const k of fresh) await flyIn(k);
  await sleep(120);
  if (ev.bust) await bustAway(prev, st, ev);
}
// a busted hand: the stamp comes down and its chips go to the dealer
async function bustAway(prev, st, ev) {
  const i = ev.seat;
  renderStatic(st, { stacks: { [i]: (prev.seats[i] ? prev.seats[i].bet : 0) + (ev.kind === 'double' ? ev.amount : 0) } });
  sfx.bust();
  if (isMine(st, i)) banner('BUST', 'bad');
  await sleep(450);
  const h = st.seats[i] && st.seats[i].hands[ev.hand];
  setStack(i, st.seats[i] ? st.seats[i].bet : 0);
  sfx.sweep();
  await flyEl(chipPile(h ? h.stake : 1, 4), circleOf(i), G.rack, 520, { easing: 'cubic-bezier(.5,0,.2,1)' });
  pulseRack();
}
async function animDouble(prev, st, ev) {
  const i = ev.seat;
  const before = prev.seats[i] ? prev.seats[i].bet : 0;
  sfx.chips(5);
  await flyEl(chipPile(ev.amount, 4), plateOf(i), circleOf(i), 400);
  setStack(i, before + ev.amount);
  const fresh = newCards(st);
  renderStatic(st, { hidden: new Set(fresh), noStamps: true, stacks: { [i]: before + ev.amount } });
  for (const k of fresh) await flyIn(k);
  await sleep(200);
  if (ev.bust) await bustAway(prev, st, ev);
}
// a split: the second card slides over to make a new hand, chips for it go on the circle, then each hand gets a card
async function animSplit(prev, st, ev) {
  const i = ev.seat, j = ev.hand;
  const before = prev.seats[i] ? prev.seats[i].bet : 0;
  sfx.chips(5);
  await flyEl(chipPile(ev.amount, 4), plateOf(i), circleOf(i), 400);
  // the cards of this seat move up a hand: hand j's second card becomes the first card of hand j + 1
  const moves = [];
  for (const [k, el] of cardEls) {
    const m = k.match(/^s:(\d+):(\d+):(\d+)$/);
    if (!m || Number(m[1]) !== i) continue;
    const h = Number(m[2]), c = Number(m[3]);
    if (h > j) moves.push([k, `s:${i}:${h + 1}:${c}`, el]);
    else if (h === j && c === 1) moves.push([k, `s:${i}:${j + 1}:0`, el]);
  }
  for (const [k] of moves) cardEls.delete(k);
  for (const [, nk, el] of moves) cardEls.set(nk, el);
  const fresh = newCards(st);
  renderStatic(st, { hidden: new Set(fresh), stacks: { [i]: before + ev.amount } });
  sfx.deal();
  await sleep(480);
  for (const k of fresh) await flyIn(k);
  await sleep(150);
}
async function animStand(prev, st, ev) {
  sfx.check();
  const t = totalEls.get(`t:${ev.seat}:${ev.hand}`);
  if (t) popEl(t);
  await sleep(200);
}
// the dealer turns over the hole card
async function animReveal(prev, st, ev) {
  renderStatic(st);
  sfx.flip();
  await sleep(550);
  if (ev.blackjack) { banner('DEALER BLACKJACK', 'bad'); sfx.lose(); say('Dealer has blackjack.'); await sleep(600); }
}
async function animDealerCard(prev, st, ev) {
  const fresh = newCards(st);
  renderStatic(st, { hidden: new Set(fresh) });
  for (const k of fresh) await flyIn(k);
  await sleep(250);
  renderStatic(st);
  if (ev.bust) { banner('DEALER BUSTS!'); sfx.win(false); say('Dealer busts!'); await sleep(400); }
}
// what the dealer says when the round is paid: the dealer's total, then the winners and what each of them won
// (everything they got back this round minus everything they bet, side bets included)
function settleSpeech(st) {
  const d = st.dealer;
  let text = d.blackjack || d.total > 21 ? '' : `Dealer has ${d.total}. `;
  const byPlayer = new Map();
  for (const s of st.seats) {
    if (!s || !s.hands.length) continue;
    const p = byPlayer.get(s.id) || { name: s.name, won: 0, staked: 0 };
    p.won += s.won; p.staked += s.staked;
    byPlayer.set(s.id, p);
  }
  const players = [...byPlayer.values()].map((p) => [p]);
  const winners = players.filter(([s]) => s.won > s.staked);
  if (!players.length) return '';
  if (!winners.length) {
    if (d.total === 21) return "That's 21 for the dealer, thanks for playing.";   // 21 or blackjack, nobody beat it
    const allPush = players.every(([s]) => s.won === s.staked);
    const anyEven = players.some(([s]) => s.won === s.staked);
    return text + (allPush ? "It's a push." : anyEven ? 'No winners this round.' : 'The house wins this round. Better luck next time!');
  }
  // the same words for everyone at the table (names, never "you"), so the server makes each line only once
  const who = ([s]) => speakName(s.name);
  const lines = winners.map(([s]) => `${speakName(s.name)} wins ${speakAmount(s.won - s.staked)}`);
  if (winners.length === 1) return `${text}Congratulations, ${who(winners[0])}! ${lines[0]}.`;
  return `${text}Congratulations to ${list(winners.map(who))}! ${lines.join(', ')}.`;
}
// the payout: stamps on every hand, losing bets go to the rack, winnings come out of it, then everything on the
// circles goes to the players
async function animSettle(prev, st, ev) {
  const stacks = {}, sides = {}, sideWins = [];
  st.seats.forEach((s, i) => {
    const was = prev.seats[i];
    if (!s || !was) return;
    stacks[i] = was.bet;
    sides[i] = {};
    for (const k of SIDE) {
      sides[i][k] = sideShown(prev, was, k);
      if (Array.isArray(sides[i][k])) sideWins.push([i, k, sides[i][k][0] + sides[i][k][1]]);
    }
  });
  renderStatic(st, { stacks, sides });
  const bySeat = {};
  for (const p of ev.payouts || []) (bySeat[p.seat] = bySeat[p.seat] || []).push(p);
  const mine = Object.entries(bySeat).filter(([i]) => isMine(st, Number(i))).flatMap(([, ps]) => ps);
  const myNet = mine.reduce((a, p) => a + p.payout - p.stake, 0);
  if (mine.length) {
    if (mine.some((p) => p.result === 'blackjack' || p.result === 'win')) sfx.reveal();
    else if (mine.every((p) => p.result === 'push')) sfx.push();
  }
  say(settleSpeech(st));
  await sleep(650);
  // 1) the losing bets go to the dealer
  let flights = [];
  for (const [si, ps] of Object.entries(bySeat)) {
    const i = Number(si);
    const lost = ps.filter((p) => p.result === 'lose').reduce((a, p) => a + p.stake, 0);
    if (!lost || !spotEls[i]) continue;
    stacks[i] = Math.max(0, (stacks[i] || 0) - lost);
    setStack(i, stacks[i]);
    flights.push(flyEl(chipPile(lost, 4), circleOf(i), G.rack, 520, { easing: 'cubic-bezier(.5,0,.2,1)' }));
  }
  if (flights.length) { sfx.sweep(); await Promise.all(flights); pulseRack(); await sleep(150); }
  // 2) the winnings come out of the rack
  flights = [];
  for (const [si, ps] of Object.entries(bySeat)) {
    const i = Number(si);
    const profit = ps.filter((p) => p.payout > p.stake).reduce((a, p) => a + p.payout - p.stake, 0);
    if (!profit || !spotEls[i]) continue;
    flights.push(flyEl(chipPile(profit, 5), G.rack, circleOf(i), 560, { arc: 20 }).then(() => {
      stacks[i] = (stacks[i] || 0) + profit; setStack(i, stacks[i]);
    }));
  }
  if (flights.length) { sfx.chips(8); await Promise.all(flights); await sleep(300); }
  // 3) everything left on the circles goes to the players
  flights = [];
  for (const [si, ps] of Object.entries(bySeat)) {
    const i = Number(si);
    if (!spotEls[i]) continue;
    const back = ps.reduce((a, p) => a + p.payout, 0);
    const net = ps.reduce((a, p) => a + p.payout - p.stake, 0);
    if (back) {
      setStack(i, 0);
      flights.push(flyEl(chipPile(back, 5), circleOf(i), plateOf(i), 480));
    }
    if (net > 0) float(i, `+${fmt(net)}`);
    else if (net < 0) float(i, `−${fmt(-net)}`, 'bad');
    else float(i, 'PUSH', 'push');
  }
  for (const [i, k, total] of sideWins) {
    if (!spotEls[i]) continue;
    setSideStack(i, k, 0);
    flights.push(flyEl(chipPile(total, 5), sideOf(i, k), plateOf(i), 480));
  }
  if (mine.length) {
    if (myNet > 0) {
      const big = mine.some((p) => p.result === 'blackjack') || myNet >= st.min_bet * 5;
      sfx.win(big);
      const won = new Set(mine.filter((p) => p.payout > p.stake).map((p) => p.seat));
      won.forEach((i) => coinShower(i, (big ? 30 : 16) / won.size + 4));
      if (big && !mine.some((p) => p.result === 'blackjack')) banner(`+${shortNum(myNet)}`);
    } else if (myNet < 0) sfx.lose();
  }
  await Promise.all(flights);
  await sleep(200);
}
// the cards are swept into the discard tray, face down
async function animClear(prev, st) {
  const [tx, ty] = G.tray;
  let k = 0;
  for (const m of [totalEls, stampEls]) { for (const el of m.values()) el.remove(); m.clear(); }
  $('dealerTotal').classList.add('hidden');
  const els = [...cardEls.values()];
  for (const el of els) {
    el.classList.remove('up', 'turn', 'dim', 'win');
    const scale = (G.cw * 0.8) / (el._p ? el._p.w : G.cw);
    el.animate([{ transform: el.style.transform }, { transform: at(tx, ty, 8 + (k % 5) * 3, scale) }],
      { duration: 420, delay: k * 30, easing: 'cubic-bezier(.2,.8,.3,1)', fill: 'forwards' });
    k++;
  }
  if (els.length) sfx.sweep();
  await sleep(480 + k * 30);
  for (const el of els) el.remove();
  cardEls.clear();
  st.seats.forEach((s, i) => { setStack(i, 0); SIDE.forEach((k) => setSideStack(i, k, 0)); });
  if (st.seats.some(Boolean)) say('Place your bets, please.');
}

// the timers: the ring around the avatar of the player to act runs down, and the bar over the action bar shows the
// betting countdown or your turn's time; the felt says how long betting stays open
let lastTick = 0;
// Runs every frame, so it only writes to the page when something actually changed (each write costs a style pass,
// which on a phone adds up while cards are flying).
const timerEls = { bar: $('timerBar'), fill: $('timerFill'), msg: $('centerMsg') };
let msgShown;
function setMsg(html) {
  if (html === msgShown) return;
  msgShown = html;
  timerEls.msg.classList.toggle('hidden', html === null);
  if (html !== null) timerEls.msg.innerHTML = html;
}
function timerLoop() {
  const { bar, fill } = timerEls;
  const T = S;   // the timers follow the state on screen, even while it's still animating
  let frac = -1, ringSeat = -1;
  if (T && T.deadline && (T.phase === 'betting' || T.phase === 'playing')) {
    const left = Math.max(0, T.deadline - srv());
    const total = (T.phase === 'betting' ? T.bet_time : T.turn_time) * 1000;
    frac = clamp(left / total, 0, 1);
    if (T.phase === 'playing' && T.turn) ringSeat = T.turn.seat;
    const mineTurn = T.phase === 'playing' && T.turn && T.turn.seat === T.you;
    if ((mineTurn || (T.phase === 'betting' && T.you >= 0)) && left < 5000 && left > 0) {
      const sec = Math.ceil(left / 1000);
      if (sec !== lastTick) { lastTick = sec; sfx.tick(); }
    }
    if (T.phase === 'betting') setMsg(`PLACE YOUR BETS<span class="count">${Math.ceil(left / 1000)}</span>`);
  }
  if (T && T.phase === 'betting' && !T.deadline) {
    setMsg(T.you >= 0 ? 'PLACE YOUR BETS' : T.seats.some(Boolean) ? 'WAITING FOR BETS' : 'TAKE A SEAT');
  } else if (!T || T.phase !== 'betting') setMsg(null);
  const showBar = !!(frac >= 0 && T && (T.phase === 'betting' ? T.you >= 0 : T.turn && T.turn.seat === T.you));
  if (bar._shown !== showBar) { bar._shown = showBar; bar.classList.toggle('hidden', !showBar); }
  if (showBar) put(fill, 'transform', `scaleX(${frac.toFixed(3)})`);
  // the ring of the player to act runs down (in 240 small steps over the turn, not a style write every frame)
  spotEls.forEach((e, i) => put(e.ring, '--t', i === ringSeat ? (Math.round(frac * 240) / 240).toFixed(3) : '0'));
  requestAnimationFrame(timerLoop);
}
requestAnimationFrame(timerLoop);

// the dealer's rack: columns of chips
$('rack').innerHTML = RACK.map((c) => `<div class="col" style="--c:${c}"></div>`).join('');

boot().catch((e) => { console.error(e); showFatal(`Couldn't start: ${e.message || e}`); });
