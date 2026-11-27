// Raccoon Heist client: every player raids their own 8x8 vault; the server (activity/heist.py) plays every round out
// completely and this page animates its steps. Sounds come from heist-sound.js, sign-in from auth.js.
// Every symbol, raccoon and the Boss is drawn here with canvas paths (no image files), so it stays sharp at any size.
import { signIn, retryAfterExpiredSession, go } from './auth.js';
import { sfx, unlock, isMuted, setMuted } from './heist-sound.js';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// fmt: exact for any size (big balances arrive as strings of digits)
import { fmt, num, minus, BIG_UNITS, huge } from './money.js';
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const BET_STEPS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000, 500000,
  1e6, 2.5e6, 5e6, 1e7, 2.5e7, 5e7, 1e8, 2.5e8, 5e8, 1e9];
const VW = 8, VH = 8;
const COLORS = ['red', 'purple', 'green', 'blue'];
const LOOT = { red: 'ruby', purple: 'amethyst', green: 'emerald', blue: 'sapphire' };
const LOOT_COLOR = { ruby: 'red', amethyst: 'purple', emerald: 'green', sapphire: 'blue' };
const HEX = { red: '#ff5a6e', purple: '#b678ff', green: '#58d68d', blue: '#4fa8ff' };
const DEMO = ['ruby', 'amethyst', 'emerald', 'sapphire', 'junk', 'junk', 'ruby', 'emerald'];

{
  const p = new URLSearchParams(location.search);
  if (p.get('platform') === 'mobile' || p.get('mobile') === '1') document.body.classList.add('discord-mobile');
}

// ------------------------------------------------------------------ state
let W = null;                 // welcome: paytable, settings
let myId = null;
let ws = null, session = null, joinInfo = null, fatal = false, retry = 0;
let balance = 0;              // what the server says
let shownBalance = null;      // what the header shows (held back until a round has been shown)
let bet = 100;
let busy = false;
let turbo = false;
let autoOn = false;
let skipRequested = false;
let players = [];
let feedItems = [];
let waiter = null;
let levels = { red: 1, purple: 1, green: 1, blue: 1 };
try { bet = Number(localStorage.getItem('heist-bet')) || 100; } catch (e) { /* storage blocked */ }
try { turbo = localStorage.getItem('heist-turbo') === '1'; } catch (e) { /* storage blocked */ }
const T = (ms) => ms * (turbo ? 0.5 : 1) * (skipRequested ? 0.25 : 1);
const credits = (b, x, sc) => Math.floor(b * x * sc + 1e-9);

// ------------------------------------------------------------------ sign in & connect
function loading(text) { $('loading').classList.remove('hidden'); $('loadingText').textContent = text; }
function showFatal(text) {
  fatal = true;
  $('loading').classList.remove('hidden');
  $('loading').querySelector('.spinner').classList.add('hidden');
  $('loadingText').textContent = text;
}
async function boot() {
  loading('Picking the lock…');
  const auth = await signIn(loading);
  if (auth.error) { showFatal(auth.error); return; }
  session = auth.session; joinInfo = auth.joinInfo;
  connect();
}
function connect() {
  loading('Sneaking into the vault…');
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/heist`);
  ws.onopen = () => { retry = 0; ws.send(JSON.stringify({ type: 'join', session, ...joinInfo })); };
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    if (fatal) return;
    loading('Connection lost, reconnecting…');
    if (waiter) { waiter(null); waiter = null; }
    setTimeout(connect, Math.min(8000, 1000 * 2 ** retry++));
  };
}
function send(obj) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; } return false; }
setInterval(() => send({ type: 'ping' }), 25000);

function onMessage(m) {
  switch (m.type) {
    case 'welcome':
      W = m; myId = m.you; setupInfo(); setupBuys(); fitBet(); renderBet(); buildCrew(); renderLevels();
      $('meterMax').textContent = String(W.meter_size);
      $('loading').classList.add('hidden');
      break;
    case 'me':
      balance = m.balance;
      if (!busy || shownBalance === null) setShownBalance(balance);
      renderBroke(); renderBuys();
      break;
    case 'round':
      balance = m.balance;
      if (waiter) { const w = waiter; waiter = null; w(m.round); }
      break;
    case 'players': players = m.players; renderPlayers(); break;
    case 'feed_all': feedItems = m.items || []; renderFeed(); break;
    case 'feed': onFeed(m.item); break;
    case 'restart': onRestart(m); break;
    case 'error':
      toast(m.message); sfx.error();
      if (waiter) { const w = waiter; waiter = null; w(null); }
      if (autoOn) setAuto(false);
      break;
    case 'fatal':
      if (m.code === 'session' && retryAfterExpiredSession()) break;
      if (m.code === 'crux' && window.showCrux) window.showCrux(m.until);   // on a /crux break
      showFatal(m.message); try { ws.close(); } catch (e) { /* closed */ }
      break;
    default: break;
  }
}

// ------------------------------------------------------------------ small UI bits
let toastTimer = null;
function toast(text, good = false) {
  const t = $('toast');
  t.textContent = text; t.classList.remove('hidden'); t.classList.toggle('good', good);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add('hidden'), 3200);
}
// big amounts are shortened (247.2M, 1B, 2.5T) where room is tight; the full number is in the tooltip
function shortAmount(n) {
  if (huge(n)) return huge(n);   // the digits, not the number: past 1e308 that's Infinity
  n = num(n);
  for (const [d, u] of [...BIG_UNITS, [1e12, 'T'], [1e9, 'B'], [1e6, 'M']]) {
    const x = n / d, t = +x.toFixed(x >= 100 ? 1 : 2);
    if (t >= 1) return `${t}${u}`;
  }
  if (n >= 1e4) { const x = n / 1e3; return `${+x.toFixed(n >= 1e5 ? 0 : 1)}K`; }
  return fmt(n);
}
// a bet: in full below a million, short from there (2.5M, 1B)
const betText = (v) => (v >= 1e6 ? shortAmount(v) : fmt(v));
// a win or a price: short from a billion, and from a million on phones
const amt = (v) => (v >= 1e9 || (v >= 1e6 && window.innerWidth < 560) ? shortAmount(v) : fmt(v));
function setShownBalance(v, bumpIt = false) {
  shownBalance = v;
  $('balance').textContent = (window.innerWidth < 560 && v >= 1e6) || num(v) >= 1e30 ? shortAmount(v) : fmt(v);
  $('balance').parentElement.title = `${fmt(v)} credits`;
  if (bumpIt) { const b = $('balance').parentElement; b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }
}
function setWinText(text, won = false) { $('winText').textContent = text; $('winBar').classList.toggle('won', won); }
function setLastWin(v) { $('lastWin').textContent = amt(v); $('lastWin').title = `${fmt(v)} credits`; $('lastWin').classList.toggle('on', v > 0); }
function banner(main, sub = '', ms = 1500, kind = '') {
  $('bannerMain').textContent = main; $('bannerSub').textContent = sub;
  const b = $('banner'); b.className = `banner ${kind}`; void b.offsetWidth;
  return sleep(T(ms)).then(() => b.classList.add('hidden'));
}
function setBusy(v) {
  busy = v;
  $('spinBtn').classList.toggle('busy', v);
  $('betUp').disabled = v; $('betDown').disabled = v;
  $('bet').parentElement.classList.toggle('locked', v);
  $('buyBtn').disabled = v;
  renderBroke();
}
function updateMuteBtn() { $('muteBtn').textContent = isMuted() ? '🔇' : '🔊'; }
$('muteBtn').addEventListener('click', () => { unlock(); setMuted(!isMuted()); updateMuteBtn(); });
updateMuteBtn();
$('gamesBtn').addEventListener('click', () => {
  if (busy) { toast('Wait for this round to finish.'); return; }
  go('/');
});
document.addEventListener('pointerdown', () => unlock(), { once: true });

// ------------------------------------------------------------------ bet & controls
function allowedSteps() {
  if (!W) return BET_STEPS;
  const s = BET_STEPS.filter((v) => v >= W.min_bet && (!W.max_bet || v <= W.max_bet));
  return s.length ? s : [W.min_bet];
}
function fitBet() {
  const steps = allowedSteps();
  if (!steps.includes(bet)) bet = steps.reduce((a, b) => (Math.abs(b - bet) < Math.abs(a - bet) ? b : a), steps[0]);
}
function renderBet() {
  $('bet').textContent = betText(bet);
  $('bet').title = `${fmt(bet)} credits`;
  try { localStorage.setItem('heist-bet', String(bet)); } catch (e) { /* storage blocked */ }
  if (W && !busy) renderLevels();
  renderBuys();
}
function stepBet(dir) {
  if (busy) return;
  const steps = allowedSteps();
  const i = clamp(steps.indexOf(bet) + dir, 0, steps.length - 1);
  if (steps[i] !== bet) { bet = steps[i]; sfx.bet(dir > 0); renderBet(); }
}
// hold + or − to keep stepping (faster the longer it's held); tap the bet itself to pick any step at once
function holdToStep(btn, dir) {
  let timer = null;
  const stop = () => { clearTimeout(timer); timer = null; };
  const repeat = (ms) => {
    timer = setTimeout(() => { if (busy || btn.disabled) { stop(); return; } stepBet(dir); repeat(Math.max(60, ms * 0.8)); }, ms);
  };
  btn.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || btn.disabled) return;
    stop(); stepBet(dir); repeat(420);
  });
  for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) btn.addEventListener(ev, stop);
  btn.addEventListener('click', (e) => { if (e.detail === 0) stepBet(dir); });   // Enter / Space on the button
  btn.addEventListener('contextmenu', (e) => e.preventDefault());
}
holdToStep($('betUp'), 1);
holdToStep($('betDown'), -1);
const betModal = document.createElement('div');
betModal.className = 'modal hidden'; betModal.id = 'betModal';
betModal.innerHTML = `<div class="modal-card bet-card"><button class="icon-btn modal-close" id="betClose" aria-label="Close">✕</button>
  <h2>Choose your bet</h2><div class="bet-grid" id="betGrid"></div><p class="buy-note" id="betNote"></p></div>`;
document.body.appendChild(betModal);
const betVal = $('bet').parentElement;
betVal.classList.add('pick');
betVal.setAttribute('role', 'button'); betVal.tabIndex = 0; betVal.title = 'Choose your bet';
function openBetPicker() {
  if (busy || !W) return;
  unlock(); sfx.click();
  const steps = allowedSteps();
  $('betGrid').innerHTML = steps.map((v) => `<button class="bet-opt${v === bet ? ' on' : ''}${v > balance ? ' over' : ''}"
    data-v="${v}" title="${fmt(v)}">${betText(v)}</button>`).join('');
  const buyMax = Object.keys(W.buys || {}).length && W.buy_max_bet;
  $('betNote').textContent = buyMax && steps.some((v) => v > buyMax) ? `Bonus buys go up to a bet of ${betText(buyMax)}.` : '';
  betModal.classList.remove('hidden');
  const on = $('betGrid').querySelector('.on');
  if (on) on.focus({ preventScroll: true });
}
betVal.addEventListener('click', openBetPicker);
betVal.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); openBetPicker(); } });
$('betGrid').addEventListener('click', (e) => {
  const b = e.target.closest('.bet-opt');
  if (!b) return;
  const v = Number(b.dataset.v);
  if (v !== bet && !busy) { sfx.bet(v > bet); bet = v; renderBet(); }
  betModal.classList.add('hidden');
});
$('betClose').addEventListener('click', () => betModal.classList.add('hidden'));
betModal.addEventListener('click', (e) => { if (e.target === betModal) betModal.classList.add('hidden'); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') betModal.classList.add('hidden'); });
$('spinBtn').addEventListener('click', () => {
  unlock();
  if (autoOn && busy) { setAuto(false); return; }
  if (busy) { skipRequested = true; return; }
  doSpin('spin');
});
$('stage').addEventListener('click', (e) => {
  if (busy && !e.target.closest('.bigwin')) skipRequested = true;   // tap the vault to hurry the round up
});
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !e.repeat && $('infoModal').classList.contains('hidden') && $('buyModal').classList.contains('hidden')
    && $('betModal').classList.contains('hidden')) {
    e.preventDefault();
    $('spinBtn').click();
  }
});
function setAuto(on) {
  autoOn = on;
  $('autoBtn').classList.toggle('on', on);
  $('spinBtn').classList.toggle('auto', on);
}
$('autoBtn').addEventListener('click', () => {
  unlock(); sfx.click();
  setAuto(!autoOn);
  if (autoOn && !busy) doSpin('spin');
});
function renderTurbo() { $('turboBtn').classList.toggle('on', turbo); }
$('turboBtn').addEventListener('click', () => {
  turbo = !turbo; sfx.click(); renderTurbo();
  try { localStorage.setItem('heist-turbo', turbo ? '1' : '0'); } catch (e) { /* storage blocked */ }
});
renderTurbo();

const restartState = { done: 0, note: '' };
function renderBroke() {
  if (!W) return;
  const broke = !busy && balance < W.min_bet;
  $('broke').classList.toggle('hidden', !broke);
  if (!broke) { restartState.done = 0; restartState.note = ''; return; }
  const left = W.restart_presses - restartState.done;
  $('restartBtn').textContent = W.restart_presses > 1 && restartState.done > 0
    ? `Press ${left} more time${left === 1 ? '' : 's'}` : `Restart with ${fmt(W.restart_amount)} credits`;
  $('brokeText').textContent = restartState.note;
}
$('restartBtn').addEventListener('click', () => { unlock(); sfx.click(); send({ type: 'restart' }); });
function onRestart(m) {
  restartState.done = m.done || 0;
  restartState.note = '';
  if (m.status === 'ok') { toast(`Back in business! +${fmt(m.amount)} credits`, true); sfx.countEnd(); }
  else if (m.status === 'in_game') restartState.note = 'You still have credits in an unfinished game in this server. Finish it first.';
  else if (m.status === 'paid') {
    const at = new Date(m.until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    restartState.note = `Restarts are locked until ${at} because you sent credits with /pay recently.`;
  }
  renderBroke();
}

// ------------------------------------------------------------------ players & feed
function avatarHtml(p) {
  const initial = esc((p.name || '?').slice(0, 1).toUpperCase());
  return p.avatar ? `<div class="avatar" style="background-image:url('/avatar/${p.id}')"></div>` : `<div class="avatar">${initial}</div>`;
}
function renderPlayers() {
  $('playerCount').textContent = players.length ? `(${players.length})` : '';
  $('players').innerHTML = players.map((p) => {
    const text = p.status === 'spinning' ? 'on a job…' : p.last_win ? `last win ${amt(p.last_win)}` : (p.online ? 'casing the vault' : 'away');
    return `<div class="player${p.id === myId ? ' me' : ''}${p.online ? '' : ' offline'}">${avatarHtml(p)}
      <div class="p-info"><div class="p-name">${esc(p.name)}${p.id === myId ? ' (you)' : ''}</div>
      <div class="p-status">${esc(text)}</div></div></div>`;
  }).join('');
}
function feedHtml(it) {
  const amount = it.amount ? ` <span class="amt" title="${fmt(it.amount)}">${amt(it.amount)}</span>` : '';
  return `<div class="feed-item ${it.kind}"><b>${esc(it.name)}</b> ${esc(it.text)}${amount}</div>`;
}
function renderFeed() {
  $('feed').innerHTML = feedItems.length ? feedItems.map(feedHtml).join('') : '<div class="feed-empty">Nothing yet. Be the first!</div>';
}
function onFeed(item) {
  feedItems.unshift(item); feedItems = feedItems.slice(0, 20); renderFeed();
  if (item.id === myId) return;
  const el = document.createElement('div');
  el.className = item.kind;
  el.innerHTML = `${item.kind === 'win' ? '💰' : '🦝'} <b>${esc(item.name)}</b> ${esc(item.text)}${item.amount ? ` <b>${amt(item.amount)}</b>` : ''}`;
  $('feedPop').prepend(el);
  setTimeout(() => el.remove(), 5200);
  sfx.feed();
}

// ================================================================== art
// Each painter draws one symbol filling a square of side s at (0, 0).
const TAU = Math.PI * 2;
function grad(c, x, y, r, inner, outer) {
  const g = c.createRadialGradient(x - r * 0.35, y - r * 0.4, r * 0.08, x, y, r * 1.05);
  g.addColorStop(0, inner); g.addColorStop(1, outer); return g;
}
function lin(c, x0, y0, x1, y1, stops) {
  const g = c.createLinearGradient(x0, y0, x1, y1);
  stops.forEach(([o, col]) => g.addColorStop(o, col)); return g;
}
function shine(c, x, y, rx, ry, a = 0.45, rot = -0.5) {
  c.save(); c.fillStyle = `rgba(255,255,255,${a})`; c.beginPath(); c.ellipse(x, y, rx, ry, rot, 0, TAU); c.fill(); c.restore();
}
function edge(c, w, col = 'rgba(10, 8, 20, .6)') { c.lineWidth = w; c.strokeStyle = col; c.lineJoin = 'round'; c.stroke(); }
function poly(c, pts) { c.beginPath(); pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y))); c.closePath(); }
function rr(c, x, y, w, h, r) {
  c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
// facet lines from a centre point to each vertex
function facets(c, cx, cy, pts, col, w) {
  c.strokeStyle = col; c.lineWidth = w;
  for (const [x, y] of pts) { c.beginPath(); c.moveTo(cx, cy); c.lineTo(x, y); c.stroke(); }
}
function banderole(c, cx, cy, w, h, text, bg, fg) {
  rr(c, cx - w / 2, cy - h / 2, w, h, h * 0.45);
  c.fillStyle = bg; c.fill(); c.lineWidth = h * 0.12; c.strokeStyle = fg; c.stroke();
  c.fillStyle = fg; c.font = `800 ${h * 0.72}px Manrope, system-ui, sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText(text, cx, cy + h * 0.04);
}

const PAINT = {
  ruby(c, s) {   // round brilliant, seen from above
    const cx = s / 2, cy = s * 0.52, R = s * 0.36;
    const outer = Array.from({ length: 8 }, (_, i) => [cx + Math.cos(i / 8 * TAU + 0.39) * R, cy + Math.sin(i / 8 * TAU + 0.39) * R]);
    poly(c, outer); c.fillStyle = grad(c, cx, cy, R, '#ffb3bd', '#c0182f'); c.fill(); edge(c, s * 0.03);
    const inner = Array.from({ length: 8 }, (_, i) => [cx + Math.cos(i / 8 * TAU + 0.39) * R * 0.5, cy + Math.sin(i / 8 * TAU + 0.39) * R * 0.5]);
    poly(c, inner); c.fillStyle = 'rgba(255, 140, 160, .55)'; c.fill();
    c.lineWidth = s * 0.012; c.strokeStyle = 'rgba(120, 0, 20, .45)'; c.stroke();
    for (let i = 0; i < 8; i++) { c.beginPath(); c.moveTo(...inner[i]); c.lineTo(...outer[i]); c.stroke(); }
    shine(c, cx - R * 0.35, cy - R * 0.4, R * 0.28, R * 0.14, 0.75);
  },
  amethyst(c, s) {   // a tall crystal
    const cx = s / 2;
    const pts = [[cx, s * 0.1], [cx + s * 0.22, s * 0.3], [cx + s * 0.22, s * 0.74], [cx, s * 0.92], [cx - s * 0.22, s * 0.74], [cx - s * 0.22, s * 0.3]];
    poly(c, pts); c.fillStyle = lin(c, cx - s * 0.22, 0, cx + s * 0.22, 0, [[0, '#e3c6ff'], [0.5, '#a35cf2'], [1, '#5a1fa0']]); c.fill(); edge(c, s * 0.03);
    c.beginPath(); c.moveTo(cx, s * 0.1); c.lineTo(cx, s * 0.92); c.moveTo(cx - s * 0.22, s * 0.3); c.lineTo(cx, s * 0.42); c.lineTo(cx + s * 0.22, s * 0.3);
    c.moveTo(cx - s * 0.22, s * 0.74); c.lineTo(cx, s * 0.62); c.lineTo(cx + s * 0.22, s * 0.74);
    c.lineWidth = s * 0.014; c.strokeStyle = 'rgba(60, 10, 110, .5)'; c.stroke();
    shine(c, cx - s * 0.1, s * 0.36, s * 0.05, s * 0.14, 0.6, 0);
  },
  emerald(c, s) {   // step cut
    const x0 = s * 0.18, y0 = s * 0.2, w = s * 0.64, h = s * 0.6, k = s * 0.1;
    const pts = [[x0 + k, y0], [x0 + w - k, y0], [x0 + w, y0 + k], [x0 + w, y0 + h - k], [x0 + w - k, y0 + h], [x0 + k, y0 + h], [x0, y0 + h - k], [x0, y0 + k]];
    poly(c, pts); c.fillStyle = grad(c, s / 2, s / 2, s * 0.4, '#b8ffd5', '#128a4a'); c.fill(); edge(c, s * 0.03);
    const m = s * 0.1;
    rr(c, x0 + m, y0 + m, w - 2 * m, h - 2 * m, s * 0.02); c.fillStyle = 'rgba(160, 255, 200, .35)'; c.fill();
    c.lineWidth = s * 0.014; c.strokeStyle = 'rgba(0, 70, 30, .5)'; c.stroke();
    c.beginPath();
    [[x0, y0 + k, x0 + m, y0 + m], [x0 + k, y0, x0 + m, y0 + m], [x0 + w, y0 + k, x0 + w - m, y0 + m], [x0 + w - k, y0, x0 + w - m, y0 + m],
      [x0, y0 + h - k, x0 + m, y0 + h - m], [x0 + k, y0 + h, x0 + m, y0 + h - m], [x0 + w, y0 + h - k, x0 + w - m, y0 + h - m], [x0 + w - k, y0 + h, x0 + w - m, y0 + h - m]]
      .forEach(([a, b, cc, d]) => { c.moveTo(a, b); c.lineTo(cc, d); });
    c.stroke();
    shine(c, x0 + w * 0.3, y0 + h * 0.3, w * 0.16, h * 0.08, 0.6);
  },
  sapphire(c, s) {   // trillion cut (a rounded triangle)
    const cx = s / 2, cy = s * 0.56, R = s * 0.4;
    const pts = [0, 1, 2].map((i) => [cx + Math.cos(-Math.PI / 2 + i * TAU / 3) * R, cy + Math.sin(-Math.PI / 2 + i * TAU / 3) * R]);
    c.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = pts[i], b = pts[(i + 1) % 3];
      const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      if (i === 0) c.moveTo(a[0], a[1]);
      c.quadraticCurveTo(mx + (mx - cx) * 0.25, my + (my - cy) * 0.25, b[0], b[1]);
    }
    c.closePath();
    c.fillStyle = grad(c, cx, cy, R, '#cde6ff', '#1557b8'); c.fill(); edge(c, s * 0.03);
    const inner = pts.map(([x, y]) => [cx + (x - cx) * 0.45, cy + (y - cy) * 0.45]);
    poly(c, inner); c.fillStyle = 'rgba(170, 215, 255, .45)'; c.fill();
    facets(c, cx, cy, pts, 'rgba(10, 40, 110, .45)', s * 0.014);
    shine(c, cx - R * 0.25, cy - R * 0.35, R * 0.2, R * 0.1, 0.7);
  },
  mask(c, s) {   // the wild: a golden domino mask
    const cx = s / 2, cy = s * 0.42;
    c.beginPath();
    c.moveTo(cx, cy - s * 0.08);
    c.bezierCurveTo(cx + s * 0.12, cy - s * 0.2, cx + s * 0.42, cy - s * 0.2, cx + s * 0.44, cy - s * 0.02);
    c.bezierCurveTo(cx + s * 0.45, cy + s * 0.14, cx + s * 0.26, cy + s * 0.2, cx + s * 0.12, cy + s * 0.12);
    c.quadraticCurveTo(cx, cy + s * 0.05, cx - s * 0.12, cy + s * 0.12);
    c.bezierCurveTo(cx - s * 0.26, cy + s * 0.2, cx - s * 0.45, cy + s * 0.14, cx - s * 0.44, cy - s * 0.02);
    c.bezierCurveTo(cx - s * 0.42, cy - s * 0.2, cx - s * 0.12, cy - s * 0.2, cx, cy - s * 0.08);
    c.fillStyle = lin(c, 0, cy - s * 0.2, 0, cy + s * 0.2, [[0, '#fff3b0'], [0.45, '#f2c14e'], [1, '#9a6a08']]); c.fill(); edge(c, s * 0.03, '#4a3204');
    for (const dx of [-1, 1]) {
      c.beginPath(); c.ellipse(cx + dx * s * 0.2, cy, s * 0.1, s * 0.065, dx * 0.2, 0, TAU); c.fillStyle = '#1a1205'; c.fill();
    }
    c.strokeStyle = '#9a6a08'; c.lineWidth = s * 0.025;
    c.beginPath(); c.moveTo(cx - s * 0.44, cy - s * 0.02); c.lineTo(cx - s * 0.48, cy + s * 0.1); c.moveTo(cx + s * 0.44, cy - s * 0.02); c.lineTo(cx + s * 0.48, cy + s * 0.1); c.stroke();
    shine(c, cx - s * 0.24, cy - s * 0.1, s * 0.08, s * 0.03, 0.8, -0.2);
    banderole(c, cx, s * 0.8, s * 0.6, s * 0.17, 'WILD', '#3a2a08', '#ffe599');
  },
  junk(c, s) {   // a crushed soda can
    const cx = s / 2, cy = s * 0.55;
    c.save(); c.translate(cx, cy); c.rotate(-0.35);
    c.beginPath();
    c.moveTo(-s * 0.16, -s * 0.28); c.lineTo(s * 0.16, -s * 0.28); c.lineTo(s * 0.12, -s * 0.02); c.lineTo(s * 0.18, s * 0.08);
    c.lineTo(s * 0.15, s * 0.28); c.lineTo(-s * 0.15, s * 0.28); c.lineTo(-s * 0.18, s * 0.06); c.lineTo(-s * 0.11, -s * 0.03); c.closePath();
    c.fillStyle = lin(c, -s * 0.18, 0, s * 0.18, 0, [[0, '#5d6680'], [0.4, '#a3abc2'], [1, '#4a5168']]); c.fill(); edge(c, s * 0.025);
    c.fillStyle = 'rgba(80, 200, 190, .5)'; c.fillRect(-s * 0.14, -s * 0.12, s * 0.27, s * 0.1);
    c.beginPath(); c.ellipse(0, -s * 0.28, s * 0.16, s * 0.04, 0, 0, TAU); c.fillStyle = '#c3c9d8'; c.fill(); edge(c, s * 0.018);
    c.strokeStyle = 'rgba(0,0,0,.3)'; c.lineWidth = s * 0.015; c.beginPath(); c.moveTo(-s * 0.11, -s * 0.03); c.lineTo(s * 0.12, -s * 0.02); c.stroke();
    c.restore();
    c.fillStyle = 'rgba(255,255,255,.18)'; c.beginPath(); c.arc(cx + s * 0.25, cy - s * 0.25, s * 0.03, 0, TAU); c.fill();
  },
  stetho(c, s) { paintStetho(c, s, false); },
  gstetho(c, s) { paintStetho(c, s, true); },
  drill(c, s) {
    const cx = s / 2;
    // bit
    c.fillStyle = lin(c, 0, s * 0.3, 0, s * 0.38, [[0, '#e6e9f2'], [1, '#7c8398']]);
    poly(c, [[cx + s * 0.16, s * 0.3], [cx + s * 0.44, s * 0.33], [cx + s * 0.46, s * 0.34], [cx + s * 0.44, s * 0.35], [cx + s * 0.16, s * 0.38]]); c.fill(); edge(c, s * 0.015);
    c.strokeStyle = 'rgba(60, 60, 80, .7)'; c.lineWidth = s * 0.012;
    for (let i = 0; i < 4; i++) { const x = cx + s * (0.2 + i * 0.06); c.beginPath(); c.moveTo(x, s * 0.31); c.lineTo(x + s * 0.03, s * 0.37); c.stroke(); }
    // body
    rr(c, cx - s * 0.32, s * 0.2, s * 0.5, s * 0.28, s * 0.1);
    c.fillStyle = lin(c, 0, s * 0.2, 0, s * 0.48, [[0, '#ffe066'], [0.6, '#f5b301'], [1, '#b37d00']]); c.fill(); edge(c, s * 0.03);
    c.fillStyle = '#2a2d3a'; rr(c, cx + s * 0.08, s * 0.24, s * 0.1, s * 0.2, s * 0.03); c.fill();
    // handle
    poly(c, [[cx - s * 0.2, s * 0.44], [cx + s * 0.02, s * 0.44], [cx - s * 0.04, s * 0.78], [cx - s * 0.26, s * 0.78]]);
    c.fillStyle = '#2a2d3a'; c.fill(); edge(c, s * 0.03);
    rr(c, cx - s * 0.32, s * 0.74, s * 0.32, s * 0.12, s * 0.03); c.fillStyle = '#f5b301'; c.fill(); edge(c, s * 0.025);
    c.fillStyle = '#ff5a6e'; rr(c, cx - s * 0.04, s * 0.48, s * 0.06, s * 0.08, s * 0.02); c.fill();
    shine(c, cx - s * 0.18, s * 0.26, s * 0.1, s * 0.03, 0.6, 0);
  },
  paint(c, s) {   // a paint bomb: a can with a rainbow splat
    const cx = s / 2;
    const splat = [['#ff5a6e', -0.28, 0.2], ['#b678ff', 0.3, 0.16], ['#58d68d', -0.32, 0.6], ['#4fa8ff', 0.3, 0.62]];
    for (const [col, dx, dy] of splat) {
      c.fillStyle = col; c.beginPath(); c.arc(cx + dx * s, dy * s, s * 0.08, 0, TAU); c.fill();
      c.beginPath(); c.arc(cx + dx * s * 1.25, dy * s + (dy < 0.4 ? -1 : 1) * s * 0.06, s * 0.03, 0, TAU); c.fill();
    }
    c.beginPath(); c.ellipse(cx, s * 0.36, s * 0.22, s * 0.07, 0, 0, TAU); c.fillStyle = '#9aa3b8'; c.fill();
    c.beginPath(); c.moveTo(cx - s * 0.22, s * 0.36); c.lineTo(cx - s * 0.2, s * 0.78); c.quadraticCurveTo(cx, s * 0.86, cx + s * 0.2, s * 0.78); c.lineTo(cx + s * 0.22, s * 0.36);
    c.fillStyle = lin(c, cx - s * 0.22, 0, cx + s * 0.22, 0, [[0, '#7c8398'], [0.45, '#dfe3ee'], [1, '#6a7186']]); c.fill(); edge(c, s * 0.03);
    c.save(); c.beginPath(); c.rect(cx - s * 0.23, s * 0.48, s * 0.46, s * 0.18); c.clip();
    COLORS.forEach((col, i) => { c.fillStyle = HEX[col]; c.fillRect(cx - s * 0.23 + i * s * 0.115, s * 0.48, s * 0.116, s * 0.18); });
    c.restore();
    c.beginPath(); c.ellipse(cx, s * 0.36, s * 0.22, s * 0.07, 0, 0, TAU); c.fillStyle = '#ff5a6e'; c.fill(); edge(c, s * 0.02);
    c.fillStyle = '#ff5a6e'; c.beginPath(); c.moveTo(cx + s * 0.1, s * 0.38); c.quadraticCurveTo(cx + s * 0.16, s * 0.5, cx + s * 0.12, s * 0.54); c.quadraticCurveTo(cx + s * 0.08, s * 0.48, cx + s * 0.04, s * 0.4); c.fill();
  },
  cash(c, s) {   // a money bag (its value is drawn on top by the board)
    const cx = s / 2;
    c.beginPath();
    c.moveTo(cx - s * 0.1, s * 0.3);
    c.bezierCurveTo(cx - s * 0.42, s * 0.42, cx - s * 0.38, s * 0.86, cx, s * 0.86);
    c.bezierCurveTo(cx + s * 0.38, s * 0.86, cx + s * 0.42, s * 0.42, cx + s * 0.1, s * 0.3);
    c.closePath();
    c.fillStyle = grad(c, cx, s * 0.6, s * 0.34, '#f3dca4', '#a07a3a'); c.fill(); edge(c, s * 0.03, '#4a3410');
    c.beginPath(); c.moveTo(cx - s * 0.1, s * 0.3); c.lineTo(cx - s * 0.17, s * 0.12); c.quadraticCurveTo(cx, s * 0.2, cx + s * 0.17, s * 0.12); c.lineTo(cx + s * 0.1, s * 0.3);
    c.fillStyle = '#c9a35e'; c.fill(); edge(c, s * 0.025, '#4a3410');
    c.strokeStyle = '#7a1f1f'; c.lineWidth = s * 0.04; c.beginPath(); c.moveTo(cx - s * 0.12, s * 0.3); c.lineTo(cx + s * 0.12, s * 0.3); c.stroke();
    c.fillStyle = '#2e7d32'; c.font = `900 ${s * 0.3}px Manrope, system-ui, sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText('$', cx, s * 0.6);
    shine(c, cx - s * 0.16, s * 0.5, s * 0.05, s * 0.09, 0.35, 0.3);
  },
  hook(c, s) {   // a grappling hook with a coil of rope
    const cx = s / 2;
    c.strokeStyle = '#c9a35e'; c.lineWidth = s * 0.035; c.lineCap = 'round';
    for (let i = 0; i < 3; i++) { c.beginPath(); c.ellipse(cx - s * 0.18, s * 0.66, s * 0.14 - i * 0.03 * s, s * 0.09 - i * 0.02 * s, 0, 0, TAU); c.stroke(); }
    c.beginPath(); c.moveTo(cx - s * 0.08, s * 0.6); c.quadraticCurveTo(cx, s * 0.5, cx + s * 0.06, s * 0.36); c.stroke();
    const steel = lin(c, cx - s * 0.2, 0, cx + s * 0.3, 0, [[0, '#6a7186'], [0.5, '#e6e9f2'], [1, '#5a6178']]);
    c.strokeStyle = steel; c.lineWidth = s * 0.055;
    c.beginPath(); c.moveTo(cx + s * 0.08, s * 0.4); c.lineTo(cx + s * 0.08, s * 0.12); c.stroke();
    for (const [a, len] of [[-2.4, 0.2], [-0.7, 0.2], [Math.PI / 2 + 0.05, 0]]) {
      if (!len) continue;
      c.beginPath(); c.moveTo(cx + s * 0.08, s * 0.14);
      const ex = cx + s * 0.08 + Math.cos(a) * s * len, ey = s * 0.14 + Math.sin(a) * s * len;
      c.quadraticCurveTo(cx + s * 0.08 + Math.cos(a) * s * len * 0.4, s * 0.14 + s * 0.12, ex, ey + s * 0.16);
      c.stroke();
    }
    c.beginPath(); c.moveTo(cx + s * 0.08, s * 0.14); c.quadraticCurveTo(cx + s * 0.1, s * 0.3, cx + s * 0.26, s * 0.24); c.stroke();
    c.beginPath(); c.arc(cx + s * 0.08, s * 0.42, s * 0.04, 0, TAU); c.lineWidth = s * 0.03; c.stroke();
  },
  radio(c, s) {   // walkie-talkie
    const cx = s / 2;
    c.fillStyle = '#2a2d3a'; c.fillRect(cx + s * 0.08, s * 0.06, s * 0.06, s * 0.2); edge(c, s * 0.02);
    rr(c, cx - s * 0.18, s * 0.22, s * 0.36, s * 0.62, s * 0.07);
    c.fillStyle = lin(c, cx - s * 0.18, 0, cx + s * 0.18, 0, [[0, '#262938'], [0.5, '#4a5070'], [1, '#1c1e2a']]); c.fill(); edge(c, s * 0.03);
    rr(c, cx - s * 0.12, s * 0.3, s * 0.24, s * 0.15, s * 0.03); c.fillStyle = '#9ff0c0'; c.fill();
    c.fillStyle = '#1c5e3a'; c.font = `800 ${s * 0.09}px Manrope, system-ui, sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('BOSS', cx, s * 0.38);
    c.fillStyle = '#11131c';
    for (let r = 0; r < 4; r++) c.fillRect(cx - s * 0.1, s * (0.52 + r * 0.06), s * 0.2, s * 0.025);
    c.beginPath(); c.arc(cx - s * 0.08, s * 0.26, s * 0.025, 0, TAU); c.fillStyle = '#ff9f43'; c.fill();
  },
  safe(c, s) {
    const x = s * 0.16, y = s * 0.18, w = s * 0.68, h = s * 0.62;
    rr(c, x, y, w, h, s * 0.06);
    c.fillStyle = lin(c, x, y, x + w, y + h, [[0, '#c4cad8'], [0.5, '#7c8398'], [1, '#474d61']]); c.fill(); edge(c, s * 0.03);
    rr(c, x + s * 0.06, y + s * 0.06, w - s * 0.12, h - s * 0.12, s * 0.04); c.strokeStyle = 'rgba(0,0,0,.35)'; c.lineWidth = s * 0.02; c.stroke();
    const dx = x + w * 0.45, dy = y + h * 0.5;
    c.beginPath(); c.arc(dx, dy, s * 0.13, 0, TAU); c.fillStyle = grad(c, dx, dy, s * 0.13, '#fff3b0', '#a8770c'); c.fill(); edge(c, s * 0.02);
    c.strokeStyle = '#3a2a08'; c.lineWidth = s * 0.015;
    for (let i = 0; i < 12; i++) { const a = i / 12 * TAU; c.beginPath(); c.moveTo(dx + Math.cos(a) * s * 0.09, dy + Math.sin(a) * s * 0.09); c.lineTo(dx + Math.cos(a) * s * 0.12, dy + Math.sin(a) * s * 0.12); c.stroke(); }
    c.beginPath(); c.arc(dx, dy, s * 0.04, 0, TAU); c.fillStyle = '#3a2a08'; c.fill();
    c.fillStyle = '#2a2d3a'; rr(c, x + w * 0.78, y + h * 0.36, s * 0.05, h * 0.28, s * 0.02); c.fill();
    c.fillStyle = '#2a2d3a'; c.fillRect(x + s * 0.06, y + h, s * 0.08, s * 0.05); c.fillRect(x + w - s * 0.14, y + h, s * 0.08, s * 0.05);
  },
  bp(c, s) {   // a rolled-out blueprint
    const x = s * 0.14, y = s * 0.2, w = s * 0.72, h = s * 0.56;
    c.fillStyle = '#1d4f9c'; c.fillRect(x, y, w, h); edge(c, s * 0.02);
    c.strokeStyle = 'rgba(220, 235, 255, .35)'; c.lineWidth = s * 0.008;
    for (let i = 1; i < 6; i++) { c.beginPath(); c.moveTo(x + w * i / 6, y); c.lineTo(x + w * i / 6, y + h); c.stroke(); }
    for (let i = 1; i < 5; i++) { c.beginPath(); c.moveTo(x, y + h * i / 5); c.lineTo(x + w, y + h * i / 5); c.stroke(); }
    c.strokeStyle = '#e8f1ff'; c.lineWidth = s * 0.02;
    c.strokeRect(x + w * 0.14, y + h * 0.18, w * 0.5, h * 0.62);
    c.beginPath(); c.moveTo(x + w * 0.14, y + h * 0.5); c.lineTo(x + w * 0.4, y + h * 0.5); c.lineTo(x + w * 0.4, y + h * 0.8); c.stroke();
    c.beginPath(); c.arc(x + w * 0.78, y + h * 0.4, w * 0.09, 0, TAU); c.stroke();
    c.beginPath(); c.moveTo(x + w * 0.72, y + h * 0.34); c.lineTo(x + w * 0.84, y + h * 0.46); c.moveTo(x + w * 0.84, y + h * 0.34); c.lineTo(x + w * 0.72, y + h * 0.46); c.stroke();
    for (const yy of [y, y + h]) {
      c.beginPath(); c.ellipse(x + w / 2, yy, w * 0.52, s * 0.045, 0, 0, TAU);
      c.fillStyle = lin(c, 0, yy - s * 0.045, 0, yy + s * 0.045, [[0, '#7fb0ff'], [1, '#123d7a']]); c.fill(); edge(c, s * 0.018);
    }
  },
};
function paintStetho(c, s, gold) {
  const cx = s / 2;
  const metal = gold ? lin(c, 0, 0, s, s, [[0, '#fff3b0'], [0.5, '#f2c14e'], [1, '#8a5e06']]) : lin(c, 0, 0, s, s, [[0, '#ffffff'], [0.5, '#b5bccd'], [1, '#5a6178']]);
  c.lineCap = 'round'; c.lineJoin = 'round';
  c.strokeStyle = gold ? '#7a4f00' : '#2a2d3a'; c.lineWidth = s * 0.075;
  c.beginPath(); c.moveTo(cx - s * 0.2, s * 0.12); c.quadraticCurveTo(cx - s * 0.24, s * 0.44, cx, s * 0.48); c.quadraticCurveTo(cx + s * 0.24, s * 0.44, cx + s * 0.2, s * 0.12); c.stroke();
  c.beginPath(); c.moveTo(cx, s * 0.48); c.quadraticCurveTo(cx - s * 0.04, s * 0.62, cx + s * 0.12, s * 0.66); c.stroke();
  c.strokeStyle = metal; c.lineWidth = s * 0.045;
  c.beginPath(); c.moveTo(cx - s * 0.2, s * 0.12); c.quadraticCurveTo(cx - s * 0.24, s * 0.44, cx, s * 0.48); c.quadraticCurveTo(cx + s * 0.24, s * 0.44, cx + s * 0.2, s * 0.12); c.stroke();
  c.beginPath(); c.moveTo(cx, s * 0.48); c.quadraticCurveTo(cx - s * 0.04, s * 0.62, cx + s * 0.12, s * 0.66); c.stroke();
  for (const dx of [-0.2, 0.2]) { c.beginPath(); c.arc(cx + dx * s, s * 0.11, s * 0.04, 0, TAU); c.fillStyle = gold ? '#7a4f00' : '#2a2d3a'; c.fill(); }
  c.beginPath(); c.arc(cx + s * 0.14, s * 0.74, s * 0.13, 0, TAU); c.fillStyle = metal; c.fill(); edge(c, s * 0.03);
  c.beginPath(); c.arc(cx + s * 0.14, s * 0.74, s * 0.07, 0, TAU); c.fillStyle = gold ? 'rgba(255,255,255,.4)' : 'rgba(40,44,60,.35)'; c.fill();
  if (gold) {
    c.fillStyle = '#fffbe0';
    for (const [x, y, r] of [[0.22, 0.3, 0.05], [0.8, 0.48, 0.04], [0.3, 0.84, 0.035]]) {
      c.beginPath(); for (let i = 0; i < 8; i++) { const a = i / 8 * TAU, rad = i % 2 ? r * 0.35 : r; c.lineTo(x * s + Math.cos(a) * rad * s, y * s + Math.sin(a) * rad * s); } c.closePath(); c.fill();
    }
    banderole(c, cx - s * 0.14, s * 0.9, s * 0.42, s * 0.12, 'ALL', '#3a2a08', '#ffe599');
  }
}

// The raccoons: a face with a bandit mask, wearing a beanie and a neckerchief in their colour
const COON_STYLE = {
  red: { hat: '#e23a50', hat2: '#ff8a98', scarf: '#b51f35' },
  purple: { hat: '#8f4fe0', hat2: '#c9a2ff', scarf: '#6a2bb0' },
  green: { hat: '#2fae64', hat2: '#8ff0b8', scarf: '#1f8048' },
  blue: { hat: '#2f7fe0', hat2: '#9ccaff', scarf: '#1d58a8' },
};
function paintRaccoon(c, s, color, boss = false) {
  const cx = s / 2, cy = s * 0.55, r = s * 0.3;
  const st = COON_STYLE[color] || { hat: '#3a2a1a', hat2: '#f2c14e', scarf: '#f2c14e' };
  // team-colour glow behind the face
  c.beginPath(); c.arc(cx, cy, s * 0.46, 0, TAU);
  const halo = c.createRadialGradient(cx, cy, s * 0.2, cx, cy, s * 0.48);
  halo.addColorStop(0, boss ? 'rgba(255, 159, 67, .55)' : `${HEX[color]}88`); halo.addColorStop(1, 'rgba(0,0,0,0)');
  c.fillStyle = halo; c.fill();
  // ears
  for (const dx of [-1, 1]) {
    c.beginPath(); c.ellipse(cx + dx * r * 0.78, cy - r * 0.78, r * 0.3, r * 0.34, dx * 0.5, 0, TAU); c.fillStyle = '#5c5f6e'; c.fill(); edge(c, s * 0.025);
    c.beginPath(); c.ellipse(cx + dx * r * 0.78, cy - r * 0.76, r * 0.16, r * 0.2, dx * 0.5, 0, TAU); c.fillStyle = '#f0d9e2'; c.fill();
  }
  // neckerchief
  c.beginPath(); c.moveTo(cx - r * 0.85, cy + r * 0.62); c.quadraticCurveTo(cx, cy + r * 1.05, cx + r * 0.85, cy + r * 0.62);
  c.lineTo(cx + r * 0.2, cy + r * 1.35); c.lineTo(cx - r * 0.2, cy + r * 1.35); c.closePath();
  c.fillStyle = boss ? '#f2c14e' : st.scarf; c.fill(); edge(c, s * 0.02);
  // head
  c.beginPath(); c.ellipse(cx, cy, r, r * 0.88, 0, 0, TAU); c.fillStyle = grad(c, cx, cy, r, '#b9bccb', '#6d7182'); c.fill(); edge(c, s * 0.03);
  // white cheeks and brow
  c.fillStyle = '#f4f4f8';
  c.beginPath(); c.ellipse(cx, cy + r * 0.38, r * 0.55, r * 0.38, 0, 0, TAU); c.fill();
  c.beginPath(); c.ellipse(cx, cy - r * 0.42, r * 0.5, r * 0.16, 0, 0, TAU); c.fill();
  // the bandit mask
  c.beginPath();
  c.moveTo(cx - r * 0.98, cy - r * 0.02);
  c.bezierCurveTo(cx - r * 0.7, cy - r * 0.4, cx - r * 0.2, cy - r * 0.28, cx, cy - r * 0.12);
  c.bezierCurveTo(cx + r * 0.2, cy - r * 0.28, cx + r * 0.7, cy - r * 0.4, cx + r * 0.98, cy - r * 0.02);
  c.bezierCurveTo(cx + r * 0.75, cy + r * 0.3, cx + r * 0.25, cy + r * 0.18, cx, cy + r * 0.06);
  c.bezierCurveTo(cx - r * 0.25, cy + r * 0.18, cx - r * 0.75, cy + r * 0.3, cx - r * 0.98, cy - r * 0.02);
  c.fillStyle = '#1b1c24'; c.fill();
  // eyes
  for (const dx of [-1, 1]) {
    if (boss) {   // the Boss wears shades
      c.beginPath(); c.ellipse(cx + dx * r * 0.4, cy - r * 0.04, r * 0.26, r * 0.18, 0, 0, TAU); c.fillStyle = '#0b0b10'; c.fill();
      c.strokeStyle = '#f2c14e'; c.lineWidth = s * 0.015; c.stroke();
      shine(c, cx + dx * r * 0.4 - r * 0.08, cy - r * 0.1, r * 0.08, r * 0.04, 0.7, -0.3);
    } else {
      c.beginPath(); c.arc(cx + dx * r * 0.4, cy - r * 0.05, r * 0.17, 0, TAU); c.fillStyle = '#fff'; c.fill();
      c.beginPath(); c.arc(cx + dx * r * 0.4 + r * 0.03, cy - r * 0.03, r * 0.1, 0, TAU); c.fillStyle = '#16121a'; c.fill();
      c.beginPath(); c.arc(cx + dx * r * 0.4 - r * 0.02, cy - r * 0.09, r * 0.04, 0, TAU); c.fillStyle = '#fff'; c.fill();
    }
  }
  if (boss) { c.strokeStyle = '#2a2d3a'; c.lineWidth = s * 0.02; c.beginPath(); c.moveTo(cx - r * 0.14, cy - r * 0.06); c.lineTo(cx + r * 0.14, cy - r * 0.06); c.stroke(); }
  // nose and grin
  c.beginPath(); c.ellipse(cx, cy + r * 0.28, r * 0.13, r * 0.09, 0, 0, TAU); c.fillStyle = '#16121a'; c.fill();
  c.beginPath(); c.moveTo(cx - r * 0.22, cy + r * 0.44); c.quadraticCurveTo(cx, cy + r * 0.62, cx + r * 0.22, cy + r * 0.44);
  c.strokeStyle = '#16121a'; c.lineWidth = s * 0.02; c.stroke();
  if (boss) { c.fillStyle = '#f2c14e'; c.fillRect(cx + r * 0.04, cy + r * 0.48, r * 0.1, r * 0.08); }
  // hat
  if (boss) {   // a fedora with a gold band
    c.beginPath(); c.ellipse(cx, cy - r * 0.72, r * 1.15, r * 0.22, 0, 0, TAU); c.fillStyle = '#2a2230'; c.fill(); edge(c, s * 0.025);
    c.beginPath(); c.moveTo(cx - r * 0.66, cy - r * 0.74); c.bezierCurveTo(cx - r * 0.7, cy - r * 1.4, cx + r * 0.7, cy - r * 1.4, cx + r * 0.66, cy - r * 0.74);
    c.closePath(); c.fillStyle = '#3a3044'; c.fill(); edge(c, s * 0.025);
    c.fillStyle = '#f2c14e'; c.fillRect(cx - r * 0.66, cy - r * 0.92, r * 1.32, r * 0.14);
    c.beginPath(); c.moveTo(cx - r * 0.2, cy - r * 1.24); c.quadraticCurveTo(cx, cy - r * 1.12, cx + r * 0.2, cy - r * 1.24); c.strokeStyle = '#1a1420'; c.lineWidth = s * 0.02; c.stroke();
  } else {      // a knit beanie
    c.beginPath(); c.moveTo(cx - r * 0.86, cy - r * 0.5); c.bezierCurveTo(cx - r * 0.86, cy - r * 1.3, cx + r * 0.86, cy - r * 1.3, cx + r * 0.86, cy - r * 0.5);
    c.closePath(); c.fillStyle = st.hat; c.fill(); edge(c, s * 0.025);
    rr(c, cx - r * 0.92, cy - r * 0.64, r * 1.84, r * 0.26, r * 0.12); c.fillStyle = st.hat2; c.fill(); edge(c, s * 0.02);
    c.strokeStyle = 'rgba(0,0,0,.18)'; c.lineWidth = s * 0.012;
    for (let i = -3; i <= 3; i++) { c.beginPath(); c.moveTo(cx + i * r * 0.24, cy - r * 0.62); c.lineTo(cx + i * r * 0.24, cy - r * 0.4); c.stroke(); }
    c.beginPath(); c.arc(cx, cy - r * 1.12, r * 0.18, 0, TAU); c.fillStyle = st.hat2; c.fill(); edge(c, s * 0.02);
  }
}

// sprites, redrawn whenever the cell size changes
const SPR = {};
let sprPx = 0;
function spriteCanvas(px, paint) {
  const cv = document.createElement('canvas'); cv.width = cv.height = px;
  const c = cv.getContext('2d'); c.translate(px * 0.05, px * 0.05); paint(c, px * 0.9);
  return cv;
}
function buildSprites(px) {
  if (px === sprPx) return;
  sprPx = px;
  for (const k of Object.keys(PAINT)) SPR[k] = spriteCanvas(px, PAINT[k]);
  for (const col of COLORS) SPR[`coon_${col}`] = spriteCanvas(px, (c, s) => paintRaccoon(c, s, col));
  SPR.boss = spriteCanvas(Math.round(px * 1.6), (c, s) => paintRaccoon(c, s, null, true));
}
function spriteKey(sym) { return sym && sym.startsWith('cash:') ? 'cash' : sym; }
function symbolImage(k, px = 96) {
  const cv = document.createElement('canvas'); cv.width = cv.height = px;
  const c = cv.getContext('2d'); c.translate(px * 0.05, px * 0.05);
  if (k.startsWith('coon_')) paintRaccoon(c, px * 0.9, k.slice(5));
  else if (k === 'boss') paintRaccoon(c, px * 0.9, null, true);
  else PAINT[k](c, px * 0.9);
  return cv.toDataURL();
}
const IMG = {};
function img(k) { return IMG[k] || (IMG[k] = symbolImage(k)); }

// ------------------------------------------------------------------ HUD: crew levels, Loot Meter, Blueprints
function buildCrew() {
  $('crew').innerHTML = COLORS.map((col) => `<div class="mate ${col}" id="mate-${col}"><img src="${img(`coon_${col}`)}" alt="">
    <div class="m-info"><div class="m-name">${esc(W.raccoons[col])}</div><div class="pips">${'<i></i>'.repeat(W.levels)}</div>
    <div class="m-pay" id="pay-${col}"></div></div></div>`).join('');
  $('bpIco').style.backgroundImage = `url(${img('bp')})`;
}
function payAt(col, lv) { return credits(bet, W.level_pays[col][lv - 1], 1); }
function renderLevels(changed = []) {
  if (!W || !$('mate-red')) return;
  for (const col of COLORS) {
    const lv = levels[col];
    const pips = $(`mate-${col}`).querySelectorAll('.pips i');
    pips.forEach((p, i) => p.classList.toggle('on', i < lv));
    $(`pay-${col}`).innerHTML = `Lv ${lv} · <b>${amt(payAt(col, lv))}</b>`;
    if (changed.includes(col)) { const m = $(`mate-${col}`); m.classList.remove('up'); void m.offsetWidth; m.classList.add('up'); }
  }
}
function setLevels(lv, sound = true) {
  const changed = COLORS.filter((c) => lv[c] !== levels[c]);
  levels = { ...lv };
  renderLevels(changed);
  if (changed.length && sound) sfx.levelUp();
  return changed;
}
let meterVal = 0;
function setMeter(v, sound = true) {
  if (!W) return;
  if (sound && v > meterVal) { sfx.meter(); const m = $('meter'); m.classList.remove('pulse'); void m.offsetWidth; m.classList.add('pulse'); }
  meterVal = v;
  $('meterNum').textContent = String(v);
  $('meterFill').style.width = `${clamp(v / W.meter_size, 0, 1) * 100}%`;
}
let bpCount = 0;
function setBp(n, inBonus) {
  bpCount = n;
  $('bpNum').textContent = String(n);
  $('bpBox').classList.toggle('lit', !inBonus && n >= 3);
  if (n) { const b = $('bpBox'); b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); }
}
function setFree(left) {
  $('freeBox').classList.toggle('hidden', left === null);
  $('bpBox').classList.toggle('hidden', left !== null);
  if (left !== null) {
    $('freeNum').textContent = String(left);
    const b = $('freeBox'); b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop');
  }
}
function setActive(col) { for (const c of COLORS) $(`mate-${c}`)?.classList.toggle('active', c === col); }

// ------------------------------------------------------------------ info / paytable
function setupInfo() {
  const lvRows = COLORS.map((col) => `<tr><td><img src="${img(LOOT[col])}" alt="">${esc(W.names[LOOT[col]])}</td>
    ${W.level_pays[col].map((x) => `<td>${(Math.round(x * 100) / 100)}x</td>`).join('')}</tr>`).join('');
  const tool = (k, text) => `<div class="pt"><img src="${img(k)}" alt=""><div><b>${esc(W.names[k])}</b><div class="pays">${text}</div></div></div>`;
  const crew = COLORS.map((col) => `<div class="pt"><img src="${img(`coon_${col}`)}" alt=""><div><b>${esc(W.raccoons[col])}</b>
    <div class="pays">grabs ${esc(W.names[LOOT[col]])}s</div></div></div>`).join('');
  $('infoBody').innerHTML = `
    <p>Four raccoons break into an 8×8 vault. Only the middle 6×6 is open at first; the rest is brick wall.
    Every drop, the raccoons land on random spots and go on raids in turn: a raccoon raids when at least <b>${W.min_raid}</b>
    pieces of its own colour's loot (Golden Masks count) are linked to it up, down, left or right. It grabs everything linked to it,
    loot and tools alike, and ends up where its last grab was. Every piece of loot pays its colour's current level.
    Then everything falls, new symbols drop in, and the crew goes again until nobody can raid.</p>
    <div class="paytable">${crew}</div>
    <h3>Loot: what one piece pays at each level (times your bet)</h3>
    <table class="lvtable"><tr><th></th>${Array.from({ length: W.levels }, (_, i) => `<th>Lv ${i + 1}</th>`).join('')}</tr>${lvRows}</table>
    <h3>Tools</h3>
    <div class="paytable">
      ${tool('mask', 'Wild: counts as loot of the raccoon that grabs it.')}
      ${tool('stetho', "The raccoon's colour goes up a level.")}
      ${tool('gstetho', 'Every colour goes up a level.')}
      ${tool('drill', 'Knocks a wall of the vault through: the vault grows (up to 8×8).')}
      ${tool('paint', "Loot and trash around it turn the raccoon's colour.")}
      ${tool('cash', `Pays the amount on the bag (up to ${fmt(W.cash_top)}x).`)}
      ${tool('hook', 'When the raid runs out, the raccoon swings to the nearest loot of its colour and keeps going.')}
      ${tool('radio', `Calls the Boss (see below).`)}
      ${tool('safe', 'Vault Grab after this drop (see below).')}
      ${tool('bp', '3 in one spin start the Night Shift.')}
      ${tool('junk', 'Trash. Nobody wants it.')}
    </div>
    <h3>Scuffles</h3>
    <p>When raccoons end up next to each other they may get into a scuffle (two: ${Math.round(W.scuffle_chance * 100)}% of the time,
    three or four: always, at most ${W.scuffle_max} a drop). Everything around them is knocked loose and collected: loot pays its own colour's level,
    and tools still work, apart from Paint Bombs and Hooks.</p>
    <h3>The Loot Meter</h3>
    <p>Every piece of loot a raccoon grabs fills it. Every <b>${W.meter_size}</b>: <b>Team Up</b>, every colour goes up a level.</p>
    <h3>The Boss</h3>
    <p>A Walkie-Talkie calls the Boss. When nobody can raid any more he crashes into the vault and dashes ${W.boss_dashes} times across a row
    or a column, grabbing everything in it. Every piece of loot he grabs pays the best level of any colour, and he leaves a Golden Mask where he lands.</p>
    <h3>Vault Grab</h3>
    <p>A Safe starts Vault Grab after the drop: ${W.lockers} lockers, ${W.alarms} of them wired to an alarm. The raccoons take turns opening
    lockers and keep what's inside (up to ${fmt(W.locker_top)}x) until the alarms have caught all four.</p>
    <h3>Night Shift</h3>
    <p>Grab 3 Blueprints in one spin for <b>${W.night_drops} free drops</b> (each Blueprint after the third adds one). During the Night Shift the
    vault, every level and the Loot Meter carry over from drop to drop, and every Blueprint grabbed is another free drop.
    ${Object.keys(W.buys).length ? `<b>Big Score</b> (bought only) is a Night Shift in a fully open vault with every colour starting at level ${W.big_level}.` : ''}</p>
    <h3>Everyone in the crew</h3>
    <p>Everybody plays their own vault. Everyone in this server with Raccoon Heist open is in the same crew: the panel shows what the
    others are doing, and big wins (${W.big_win}x and up), Night Shifts and the Boss are announced to everyone.</p>
    <p>Maximum win ${fmt(W.max_win)}x your bet per round. Raccoon Heist returns ${(W.rtp * 100).toFixed(0)}% of what's staked on average,
    on paid spins and every bonus buy alike.${Object.keys(W.buys).length && W.buy_max_bet ? ` Bonus buys go up to a bet of ${betText(W.buy_max_bet)}.` : ''} Space bar spins; tap the vault to speed a round up. Turbo speeds up the animations;
    Auto keeps spinning until you stop it.</p>`;
}
$('infoBtn').addEventListener('click', () => { sfx.click(); $('infoModal').classList.remove('hidden'); });
$('infoClose').addEventListener('click', () => $('infoModal').classList.add('hidden'));
$('infoModal').addEventListener('click', (e) => { if (e.target === $('infoModal')) $('infoModal').classList.add('hidden'); });

// ------------------------------------------------------------------ bonus buys
const BUY_ICON = { boost: 'bp', boss: 'boss', grab: 'safe', night: 'coon_purple', big: 'mask' };
const stakeFor = (mode) => Math.max(1, Math.round(bet * (mode === 'spin' ? 1 : W.buys[mode].price)));
function setupBuys() {
  const has = W && Object.keys(W.buys).length > 0;
  $('buyBtn').classList.toggle('hidden', !has);
  if (!has) return;
  $('buys').innerHTML = Object.entries(W.buys).map(([k, b]) => `<button class="buy" data-mode="${k}">
    <img src="${img(BUY_ICON[k])}" alt=""><div class="b-name">${esc(b.name)}</div><div class="b-text">${esc(b.text)}</div>
    <div class="b-price" id="price-${k}"></div><div class="b-x">${b.price}× bet</div></button>`).join('');
  for (const el of $('buys').querySelectorAll('.buy')) {
    el.addEventListener('click', () => {
      unlock(); sfx.click();
      $('buyModal').classList.add('hidden');
      doSpin(el.dataset.mode);
    });
  }
  renderBuys();
}
// bonus buys go up to W.buy_max_bet: above it the buys are greyed out, with a button to drop the bet to the limit
function buyLimitStep() {
  const s = allowedSteps().filter((v) => v <= W.buy_max_bet);
  return s.length ? s[s.length - 1] : null;
}
function renderBuyLimit() {
  if (!$('buyLimit')) $('buys').insertAdjacentHTML('beforebegin', '<p class="buy-limit hidden" id="buyLimit"></p>');
  const el = $('buyLimit');
  const over = buyTooBig();
  el.classList.toggle('hidden', !over);
  if (!over) return;
  const to = buyLimitStep();
  el.innerHTML = `Bonus buys go up to a bet of <b>${betText(W.buy_max_bet)}</b>.`
    + (to ? ` <button class="buy-limit-btn" id="buyLimitBtn">Bet ${betText(to)}</button>` : '');
  if (to) $('buyLimitBtn').addEventListener('click', () => { sfx.bet(false); bet = to; renderBet(); });
}
function buyTooBig() { return Boolean(W && W.buy_max_bet && bet > W.buy_max_bet); }
function renderBuys() {
  if (!W || !Object.keys(W.buys).length) return;
  $('buyBet').textContent = betText(bet);
  renderBuyLimit();
  for (const k of Object.keys(W.buys)) {
    const el = $(`price-${k}`);
    if (!el) continue;
    const cost = stakeFor(k);
    el.textContent = amt(cost);
    el.title = `${fmt(cost)} credits`;
    el.parentElement.disabled = cost > balance || buyTooBig();
  }
}
$('buyBtn').addEventListener('click', () => {
  unlock(); sfx.click();
  if (busy) return;
  if (autoOn) setAuto(false);
  renderBuys();
  $('buyModal').classList.remove('hidden');
});
$('buyClose').addEventListener('click', () => $('buyModal').classList.add('hidden'));
$('buyModal').addEventListener('click', (e) => { if (e.target === $('buyModal')) $('buyModal').classList.add('hidden'); });

// ================================================================== the board (canvas)
const canvas = $('board');
const cx = canvas.getContext('2d');
const fx = $('fx');
const fxc = fx.getContext('2d');
let dpr = 1;
let CS = 60;                  // cell size in CSS pixels
let boardPx = CS * VW;

function makeCell(sym) { return { sym, dy: 0, sc: 1, a: 1, flash: 0 }; }
const cells = Array.from({ length: VW }, () => Array.from({ length: VH }, () => makeCell(null)));
let open = Array.from({ length: VW }, (_, c) => Array.from({ length: VH }, (_, r) => c >= 1 && c <= 6 && r >= 1 && r <= 6));
for (let c = 0; c < VW; c++) for (let r = 0; r < VH; r++) if (open[c][r]) cells[c][r].sym = DEMO[(c * 3 + r * 5) % DEMO.length];
cells[2][3].sym = 'coon_red'; cells[5][2].sym = 'coon_purple'; cells[3][5].sym = 'coon_green'; cells[4][1].sym = 'coon_blue';
const view = {
  dim: false,
  hl: new Map(),        // "c,r" -> color (cells being grabbed)
  floaters: [],         // [{ x, y, text, color, t0, ms }] in cell units
  actor: null,          // { col, x, y, sc, hop } a raccoon on a raid
  rope: null,           // { x0, y0, x1, y1, a }
  boss: null,           // { x, y, sc, a, rot }
  crumble: [],          // [{ c, r, t0 }] bricks falling away
  dashLine: null,       // { line, index, a }
};
const key = (c, r) => `${c},${r}`;

function resize() {
  dpr = Math.min(2.5, window.devicePixelRatio || 1);
  const play = $('play'), stage = $('stage');
  const cs = getComputedStyle(play);
  const pw = stage.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const ph = stage.clientHeight - $('winBar').offsetHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - 4;
  const framePad = 2 * parseFloat(getComputedStyle($('boardFrame')).paddingLeft || '7');
  const big = window.innerHeight >= 900 && window.innerWidth >= 1100;
  const hudSide = (big ? 180 : 150) + 12;
  const hudTop = 74;
  const land = pw - hudSide >= ph - hudTop;
  play.classList.toggle('land', land);
  play.classList.toggle('port', !land);
  let outer = land ? Math.min(pw - hudSide, ph) : Math.min(pw, ph - hudTop);
  outer = clamp(outer, 160, 980);
  CS = Math.floor((outer - framePad) / VW);
  boardPx = CS * VW;
  canvas.style.width = `${boardPx}px`;
  canvas.style.height = `${boardPx}px`;
  canvas.width = Math.round(boardPx * dpr);
  canvas.height = Math.round(boardPx * dpr);
  buildSprites(Math.round(CS * dpr));
  $('hud').style.width = land ? '' : `${boardPx + framePad}px`;
  if (land) $('hud').style.height = `${boardPx + framePad}px`; else $('hud').style.height = '';
  fx.width = Math.round(stage.clientWidth * dpr); fx.height = Math.round(stage.clientHeight * dpr);
  if (shownBalance !== null) setShownBalance(shownBalance);
}
new ResizeObserver(() => resize()).observe($('stage'));
window.addEventListener('resize', resize);

// ------------------------------------------------------------------ tweens
const tweens = new Set();
const ease = {
  out: (p) => 1 - (1 - p) ** 3,
  in: (p) => p * p,
  land: (p) => { const s = 1.4; const q = p - 1; return q * q * ((s + 1) * q + s) + 1; },
  inout: (p) => (p < 0.5 ? 2 * p * p : 1 - (-2 * p + 2) ** 2 / 2),
  lin: (p) => p,
};
function tween(obj, to, ms, e = ease.out, delay = 0) {
  return new Promise((res) => tweens.add({ obj, to, from: null, t0: performance.now() + delay, ms: Math.max(1, ms), e, res }));
}
function runTweens(now) {
  for (const tw of tweens) {
    if (now < tw.t0) continue;
    if (!tw.from) { tw.from = {}; for (const k in tw.to) tw.from[k] = tw.obj[k]; }
    const p = clamp((now - tw.t0) / tw.ms, 0, 1);
    const v = tw.e(p);
    for (const k in tw.to) tw.obj[k] = tw.from[k] + (tw.to[k] - tw.from[k]) * v;
    if (p >= 1) { tweens.delete(tw); tw.res(); }
  }
}

// ------------------------------------------------------------------ drawing
function roundRect(c, x, y, w, h, r) { rr(c, x, y, w, h, r); }
let brickTile = null, brickPx = 0;
function brickPattern() {
  const px = Math.round(CS * dpr);
  if (brickTile && brickPx === px) return brickTile;
  brickPx = px;
  const cv = document.createElement('canvas'); cv.width = cv.height = px;
  const c = cv.getContext('2d');
  c.fillStyle = '#3d2219'; c.fillRect(0, 0, px, px);
  const rows = 4, bh = px / rows;
  for (let i = 0; i < rows; i++) {
    const off = i % 2 ? px / 4 : 0;
    for (let k = -1; k < 3; k++) {
      const x = off + k * px / 2;
      const g = c.createLinearGradient(0, i * bh, 0, (i + 1) * bh);
      const hue = (((i * 7 + k * 13) % 3) + 3) % 3;
      g.addColorStop(0, ['#a9573a', '#9b4d33', '#b4613f'][hue]); g.addColorStop(1, ['#7a3a25', '#6e3420', '#84432b'][hue]);
      c.fillStyle = g; c.fillRect(x + px * 0.02, i * bh + px * 0.025, px / 2 - px * 0.04, bh - px * 0.05);
    }
  }
  brickTile = cv;
  return cv;
}
let lastFrame = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - lastFrame) / 1000); lastFrame = now;
  runTweens(now);
  const c = cx;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, boardPx, boardPx);
  // vault floor
  const bg = c.createLinearGradient(0, 0, 0, boardPx);
  bg.addColorStop(0, '#2a2f45'); bg.addColorStop(1, '#141724');
  c.fillStyle = bg; c.fillRect(0, 0, boardPx, boardPx);
  for (let col = 0; col < VW; col++) for (let r = 0; r < VH; r++) {
    if (!open[col][r]) continue;
    c.fillStyle = (col + r) % 2 ? 'rgba(255,255,255,.035)' : 'rgba(255,255,255,.06)';
    roundRect(c, col * CS + CS * 0.05, r * CS + CS * 0.05, CS * 0.9, CS * 0.9, CS * 0.14); c.fill();
  }
  // a Boss dash leaves a streak
  if (view.dashLine && view.dashLine.a > 0.01) {
    const d = view.dashLine;
    c.globalAlpha = d.a; c.fillStyle = 'rgba(255, 159, 67, .35)';
    if (d.line === 'row') c.fillRect(0, d.index * CS + CS * 0.1, boardPx, CS * 0.8); else c.fillRect(d.index * CS + CS * 0.1, 0, CS * 0.8, boardPx);
    c.globalAlpha = 1;
  }
  // highlights behind cells being grabbed
  for (const [k, col] of view.hl) {
    const [cc, r] = k.split(',').map(Number);
    c.globalAlpha = 0.45 + 0.2 * Math.sin(now / 120);
    c.fillStyle = col; roundRect(c, cc * CS + CS * 0.05, r * CS + CS * 0.05, CS * 0.9, CS * 0.9, CS * 0.18); c.fill();
    c.globalAlpha = 1;
  }
  // symbols
  for (let col = 0; col < VW; col++) {
    for (let r = 0; r < VH; r++) {
      const cell = cells[col][r];
      if (!cell.sym || cell.a <= 0.01) continue;
      const sp = SPR[spriteKey(cell.sym)];
      if (!sp) continue;
      const lit = view.hl.has(key(col, r));
      const coon = cell.sym.startsWith('coon_');
      const bob = cell.dy === 0 && (coon || cell.sym === 'mask') ? Math.sin(now / 420 + col * 1.3 + r * 0.7) * CS * 0.025 : 0;
      const size = CS * cell.sc * (lit ? 1.06 + 0.04 * Math.sin(now / 110) : 1);
      const x = col * CS + (CS - size) / 2, y = (r + cell.dy) * CS + (CS - size) / 2 + bob;
      c.globalAlpha = cell.a * (view.dim && !lit && !coon ? 0.4 : 1);
      c.drawImage(sp, x, y, size, size);
      if (cell.sym.startsWith('cash:')) cashTag(c, x + size / 2, y + size * 0.86, cell.sym, size);
      if (cell.flash > 0) {
        c.globalAlpha = cell.flash; c.fillStyle = '#ffffff';
        roundRect(c, col * CS + CS * 0.08, (r + cell.dy) * CS + CS * 0.08, CS * 0.84, CS * 0.84, CS * 0.2); c.fill();
      }
      c.globalAlpha = 1;
    }
  }
  // brick walls on top, so new symbols seem to come out from behind them
  const tile = brickPattern();
  for (let col = 0; col < VW; col++) for (let r = 0; r < VH; r++) {
    if (open[col][r]) continue;
    c.drawImage(tile, col * CS, r * CS, CS, CS);
  }
  // bricks falling away when a wall is knocked through
  for (let i = view.crumble.length - 1; i >= 0; i--) {
    const b = view.crumble[i];
    const p = (now - b.t0) / T(700);
    if (p >= 1) { view.crumble.splice(i, 1); continue; }
    c.save(); c.globalAlpha = 1 - p;
    c.translate((b.c + 0.5) * CS + Math.sin(b.c * 3 + b.r) * p * CS * 0.3, (b.r + 0.5) * CS + p * p * CS * 1.5);
    c.rotate(p * (b.c % 2 ? 1 : -1) * 0.8);
    c.drawImage(tile, -CS / 2, -CS / 2, CS, CS);
    c.restore();
  }
  // rope of a Grappling Hook
  if (view.rope && view.rope.a > 0.01) {
    const rp = view.rope;
    c.globalAlpha = rp.a; c.strokeStyle = '#e8c98e'; c.lineWidth = Math.max(2, CS * 0.06); c.lineCap = 'round';
    c.beginPath(); c.moveTo((rp.x0 + 0.5) * CS, (rp.y0 + 0.5) * CS);
    c.quadraticCurveTo(((rp.x0 + rp.x1) / 2 + 0.5) * CS, (Math.min(rp.y0, rp.y1) - 0.6) * CS, (rp.x1 + 0.5) * CS, (rp.y1 + 0.5) * CS); c.stroke();
    c.globalAlpha = 1;
  }
  // a raccoon on a raid
  const a = view.actor;
  if (a) {
    const sp = SPR[`coon_${a.col}`];
    const size = CS * 1.12 * a.sc;
    const hop = Math.sin(clamp(a.hop, 0, 1) * Math.PI) * CS * 0.35;
    c.save(); c.shadowColor = HEX[a.col]; c.shadowBlur = CS * 0.4;
    if (sp) c.drawImage(sp, (a.x + 0.5) * CS - size / 2, (a.y + 0.5) * CS - size / 2 - hop, size, size);
    c.restore();
  }
  // the Boss
  const b = view.boss;
  if (b && b.a > 0.01) {
    const sp = SPR.boss;
    const size = CS * 1.6 * b.sc;
    c.save(); c.globalAlpha = b.a; c.shadowColor = '#ff9f43'; c.shadowBlur = CS * 0.6;
    c.translate((b.x + 0.5) * CS, (b.y + 0.5) * CS); c.rotate(b.rot || 0);
    if (sp) c.drawImage(sp, -size / 2, -size / 2, size, size);
    c.restore();
  }
  // floating pay amounts and notes
  for (let i = view.floaters.length - 1; i >= 0; i--) {
    const f = view.floaters[i];
    const p = (now - f.t0) / f.ms;
    if (p >= 1) { view.floaters.splice(i, 1); continue; }
    const alpha = p < 0.15 ? p / 0.15 : p > 0.7 ? (1 - p) / 0.3 : 1;
    pill(c, (f.x + 0.5) * CS, (f.y + 0.5) * CS - p * CS * 0.6, f.text, alpha, f.color, f.big);
  }
  drawParticles(dt);
  requestAnimationFrame(frame);
}
function cashTag(c, x, y, sym, size) {
  const v = Number(sym.slice(5));
  const text = W && currentRound ? shortAmount(credits(currentRound.bet, v, currentRound.scale)) : `${v}x`;
  const fs = Math.max(9, size * 0.22);
  c.font = `900 ${fs}px Manrope, system-ui, sans-serif`;
  const w = c.measureText(text).width + fs * 0.6, h = fs * 1.25;
  c.fillStyle = 'rgba(10, 30, 14, .9)'; roundRect(c, x - w / 2, y - h / 2, w, h, h / 2); c.fill();
  c.fillStyle = '#9ff0c0'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(text, x, y + fs * 0.05);
}
function pill(c, x, y, text, a, color = '#f2c14e', big = false) {
  const fs = Math.max(11, CS * (big ? 0.42 : 0.3));
  c.font = `800 ${fs}px Manrope, system-ui, sans-serif`;
  const w = c.measureText(text).width + fs * 1.1, h = fs * 1.5;
  c.globalAlpha = a;
  c.fillStyle = 'rgba(12, 14, 26, .92)'; roundRect(c, x - w / 2, y - h / 2, w, h, h / 2); c.fill();
  c.lineWidth = 2; c.strokeStyle = color; c.stroke();
  c.fillStyle = color; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(text, x, y + fs * 0.05);
  c.globalAlpha = 1;
}
function floater(col, r, text, color, ms = 900, big = false) {
  view.floaters.push({ x: col, y: r, text, color, t0: performance.now(), ms: ms * (turbo ? 0.6 : 1), big });
}

// ------------------------------------------------------------------ particles (stage-wide canvas)
const parts = [];
function cellOnStage(col, r) {
  const b = canvas.getBoundingClientRect(), s = $('stage').getBoundingClientRect();
  return [b.left - s.left + (col + 0.5) * CS, b.top - s.top + (r + 0.5) * CS];
}
function sparks(col, r, n = 14, colors = ['#ffffff', '#fff1b8', '#f2c14e']) {
  const [x, y] = cellOnStage(col, r);
  for (let i = 0; i < n; i++) {
    const ang = Math.random() * TAU, v = (0.6 + Math.random()) * CS * 3.2;
    parts.push({ x, y, vx: Math.cos(ang) * v, vy: Math.sin(ang) * v - CS, life: 0.5 + Math.random() * 0.4, age: 0, size: CS * (0.04 + Math.random() * 0.05),
      color: colors[i % colors.length], g: CS * 6, kind: 'dot' });
  }
}
function dust(col, r, n = 10) {
  const [x, y] = cellOnStage(col, r);
  for (let i = 0; i < n; i++) {
    parts.push({ x: x + (Math.random() - 0.5) * CS, y: y + (Math.random() - 0.5) * CS * 0.6, vx: (Math.random() - 0.5) * CS * 2, vy: -CS * (0.3 + Math.random()),
      life: 0.6 + Math.random() * 0.5, age: 0, size: CS * (0.12 + Math.random() * 0.15), color: 'rgba(200, 190, 170, .55)', g: 0, kind: 'puff' });
  }
}
function coinShower(n = 80, dur = 2.5) {
  const w = fx.width / dpr;
  for (let i = 0; i < n; i++) {
    parts.push({ x: Math.random() * w, y: -20 - Math.random() * 200, vx: (Math.random() - 0.5) * 80, vy: 100 + Math.random() * 200,
      life: dur + Math.random(), age: 0, size: 6 + Math.random() * 6, color: Math.random() < 0.5 ? '#ffe599' : '#f2b705', g: 260, kind: 'coin', spin: Math.random() * 6 });
  }
}
function drawParticles(dt) {
  const c = fxc;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, fx.width / dpr, fx.height / dpr);
  for (let i = parts.length - 1; i >= 0; i--) {
    const p = parts[i];
    p.age += dt;
    if (p.age >= p.life) { parts.splice(i, 1); continue; }
    p.vy += p.g * dt; p.x += p.vx * dt; p.y += p.vy * dt;
    const al = 1 - p.age / p.life;
    c.globalAlpha = al;
    if (p.kind === 'coin') {
      const sx = Math.abs(Math.cos(p.age * p.spin));
      c.fillStyle = p.color; c.beginPath(); c.ellipse(p.x, p.y, p.size * sx + 1, p.size, 0, 0, TAU); c.fill();
    } else if (p.kind === 'puff') {
      c.fillStyle = p.color; c.beginPath(); c.arc(p.x, p.y, p.size * (1 + p.age), 0, TAU); c.fill();
    } else {
      c.fillStyle = p.color; c.beginPath(); c.arc(p.x, p.y, p.size, 0, TAU); c.fill();
    }
  }
  c.globalAlpha = 1;
}

// ================================================================== the game flow
let currentRound = null;
// pop animations of grabbed symbols still running: a cell is only reused once its pop is over
let pops = [];
async function settlePops() { const p = pops; pops = []; await Promise.all(p); }
let shownWin = 0;             // credits shown as won so far this round
let xSum = 0;                 // raw x paid so far this round

async function doSpin(mode = 'spin') {
  if (busy || !W) return;
  if (mode !== 'spin' && buyTooBig()) {
    toast(`Bonus buys go up to a bet of ${betText(W.buy_max_bet)}.`); sfx.error(); return;
  }
  const cost = stakeFor(mode);
  if (cost > balance) {
    toast(balance < W.min_bet ? "You're out of credits." : "You don't have enough credits for that.");
    sfx.error(); setAuto(false); return;
  }
  setBusy(true);
  skipRequested = false;
  clearView();
  setWinText(mode === 'spin' ? 'Good luck!' : `${W.buys[mode].name}!`);
  setShownBalance(minus(shownBalance ?? balance, cost));
  sfx.spinStart();
  const answer = new Promise((res) => { waiter = res; });
  if (!send({ type: 'spin', bet, mode })) { waiter = null; setBusy(false); setShownBalance(balance); toast('Not connected.'); return; }
  const out = dropOut();
  const round = await answer;
  await out;
  if (!round) {   // refused: put a board back
    await dropIn(demoGrid(), open);
    setShownBalance(balance);
    setBusy(false);
    return;
  }
  try {
    await playRound(round);
  } catch (e) {
    console.error(e);
    toast('Something went wrong showing that round.');
    $('lockers').classList.add('hidden');
    view.actor = null; view.boss = null;
    const last = round.steps.filter((s) => s.grid).pop();
    if (last) syncTo(last.grid);
    await finishUp(round);
  }
}
function demoGrid() {
  return Array.from({ length: VW }, (_, c) => Array.from({ length: VH }, (_, r) => (open[c][r] ? DEMO[Math.floor(Math.random() * DEMO.length)] : null)));
}
function clearView() {
  view.hl = new Map(); view.dim = false; view.floaters = []; view.actor = null; view.rope = null; view.boss = null; view.dashLine = null;
  setActive(null);
}

// all symbols fall out of the bottom
function dropOut() {
  const jobs = [];
  for (let col = 0; col < VW; col++) for (let r = 0; r < VH; r++) {
    if (cells[col][r].sym) jobs.push(tween(cells[col][r], { dy: VH + 1 }, T(240), ease.in, T(col * 25 + (VH - 1 - r) * 10)));
  }
  return Promise.all(jobs);
}
// a new board drops in from the top, column by column
async function dropIn(grid, openNow) {
  open = openNow.map((col) => col.slice());
  const jobs = [];
  for (let col = 0; col < VW; col++) {
    let any = false;
    for (let r = 0; r < VH; r++) {
      const cl = cells[col][r];
      Object.assign(cl, { sym: grid[col][r], dy: -(VH + 1), sc: 1, a: 1, flash: 0 });
      if (grid[col][r]) { any = true; jobs.push(tween(cl, { dy: 0 }, T(360), ease.land, T(col * 45 + (VH - 1 - r) * 18))); }
      else cl.dy = 0;
    }
    if (any) sleep(T(col * 45 + 280)).then(() => sfx.land(col));
  }
  await Promise.all(jobs);
}
function syncTo(grid) {
  for (let col = 0; col < VW; col++) for (let r = 0; r < VH; r++) {
    Object.assign(cells[col][r], { sym: grid[col][r], dy: 0, sc: 1, a: 1, flash: 0 });
  }
}

async function playRound(round) {
  currentRound = round;
  shownWin = 0; xSum = 0;
  let inBonus = false;
  for (const step of round.steps) {
    switch (step.t) {
      case 'drop': await showDrop(step, inBonus); break;
      case 'raid': await showRaid(step); break;
      case 'scuffle': await showScuffle(step); break;
      case 'teamup': await showTeamUp(step); break;
      case 'fall': await showFall(step); break;
      case 'boss_in': await showBossIn(step); send({ type: 'announce', round: round.id, what: 'boss' }); break;
      case 'dash': await showDash(step); break;
      case 'boss_out': await showBossOut(); break;
      case 'grab': await showVaultGrab(step); break;
      case 'bonus':
        inBonus = true;
        await showBonusStart(step);
        send({ type: 'announce', round: round.id, what: 'bonus' });
        break;
      case 'bonus_end': await showBonusEnd(step); inBonus = false; break;
      default: break;
    }
  }
  await finishUp(round);
}

// pays x (raw) on top of what this round has paid so far and counts the win bar up
async function addWin(x, label = '') {
  if (!x) return;
  xSum += x;
  const r = currentRound;
  const total = Math.min(credits(r.bet, xSum, r.scale), r.payout || Infinity, r.bet * W.max_win);
  const from = shownWin;
  shownWin = total;
  $('winText').textContent = '';
  $('winBar').classList.add('won');
  const el = document.createElement('span');
  $('winText').append(`${label}WIN `, el);
  setLastWin(total);
  await countUp(el, from, total, T(clamp(160 + (total - from) / r.bet * 60, 160, 700)));
}
const payText = (x) => shortAmount(credits(currentRound.bet, x, currentRound.scale));

async function showDrop(step, inBonus) {
  bpCount = 0;
  setBp(0, inBonus);
  if (step.free) setFree(step.free.left);
  else setFree(null);
  setLevels(step.levels, false);
  setMeter(step.meter, false);
  if (inBonus) {
    await dropOut();
    setWinText(`FREE DROP ${step.free.done} · ${step.free.left} left`, true);
  }
  await dropIn(step.grid, step.open);
  await sleep(T(160));
}

// one grab of a raid, scuffle or dash: the symbol pops and its effect happens
async function doGrab(g, color, who) {
  const [col, r] = g.cell;
  const cl = cells[col][r];
  const sym = g.sym;
  if (g.x) {
    floater(col, r, payText(g.x), color || '#f2c14e');
    if (sym.startsWith('cash:')) sfx.coin();
  }
  sparks(col, r, sym in LOOT_COLOR || sym === 'mask' ? 8 : 12, [HEX[LOOT_COLOR[sym]] || '#f2c14e', '#ffffff']);
  pops.push(tween(cl, { sc: 1.3 }, T(80)).then(() => tween(cl, { sc: 0, a: 0 }, T(130), ease.in)).then(() => {
    Object.assign(cl, { sym: null, sc: 1, a: 1 });
  }));
  if (sym === 'junk') sfx.junk();
  if (g.levels) {
    const changed = setLevels(g.levels);
    for (const c of changed) floater(col, r, `${W.raccoons[c]} Lv ${g.levels[c]}`, HEX[c], 1100);
  }
  if (g.wall) await openWall(g.wall, g.open);
  if (g.paint) {
    sfx.paint();
    for (const [pc, pr] of g.paint) {
      const p = cells[pc][pr];
      p.flash = 1; p.sym = LOOT[who]; tween(p, { flash: 0 }, T(380)); sparks(pc, pr, 6, [HEX[who], '#ffffff']);
    }
    await sleep(T(160));
  }
  if (g.boss) {
    sfx.radio();
    floater(col, r, 'BOSS CALLED!', '#ff9f43', 1300, true);
    $('boardFrame').classList.add('boss');
  }
  if (g.safe) { sfx.safe(); floater(col, r, 'VAULT GRAB!', '#f2c14e', 1300, true); }
  if (g.bp) {
    sfx.blueprint();
    setBp(g.bp, g.left !== undefined);
    if (g.left !== undefined) {
      setFree(g.left); sfx.extra(); floater(col, r, '+1 FREE DROP', '#c9bfff', 1300, true);
    } else floater(col, r, `BLUEPRINT ${g.bp}/3`, '#6aa9ff', 1100);
  }
  if (sym === 'mask') sfx.wild();
}

async function openWall(side, openNow) {
  sfx.drill();
  const before = open;
  await sleep(T(350));
  sfx.crumble();
  const f = $('boardFrame'); f.classList.remove('shake'); void f.offsetWidth; f.classList.add('shake');
  const t0 = performance.now();
  for (let c = 0; c < VW; c++) for (let r = 0; r < VH; r++) {
    if (!before[c][r] && openNow[c][r]) { view.crumble.push({ c, r, t0 }); dust(c, r, 4); }
  }
  open = openNow.map((col) => col.slice());
  floaterAtSide(side);
  await sleep(T(400));
}
function floaterAtSide(side) {
  const at = { top: [3.5, 1], bottom: [3.5, 6], left: [1.5, 3.5], right: [5.5, 3.5] }[side];
  floater(at[0], at[1], 'THE VAULT GROWS!', '#f2c14e', 1300, true);
}

async function showRaid(step) {
  const color = step.who;
  const [c0, r0] = step.from;
  setActive(color);
  // lift the raccoon off its cell
  cells[c0][r0].sym = null;
  view.actor = { col: color, x: c0, y: r0, sc: 1, hop: 0 };
  view.dim = true;
  const hl = new Map();
  for (const g of step.grabs) hl.set(key(...g.cell), HEX[color]);
  view.hl = hl;
  await sleep(T(140));
  let n = 0;
  for (const g of step.grabs) {
    const [col, r] = g.cell;
    const a = view.actor;
    const dist = Math.abs(col - a.x) + Math.abs(r - a.y);
    if (g.hook) {
      sfx.hook();
      view.rope = { x0: a.x, y0: a.y, x1: col, y1: r, a: 1 };
      await sleep(T(160));
      a.hop = 0;
      await Promise.all([tween(a, { x: col, y: r }, T(260 + dist * 40), ease.inout), tween(a, { hop: 1 }, T(260 + dist * 40), ease.lin)]);
      tween(view.rope, { a: 0 }, T(250));
    } else {
      a.hop = 0;
      sfx.hop(n);
      const ms = T(dist > 1 ? 150 + dist * 30 : 110);
      await Promise.all([tween(a, { x: col, y: r }, ms, ease.inout), tween(a, { hop: 1 }, ms, ease.lin)]);
    }
    view.hl.delete(key(col, r));
    sfx.grab(n);
    n++;
    await doGrab(g, HEX[color], color);
  }
  // the raccoon settles where its last grab was
  const last = step.grabs[step.grabs.length - 1].cell;
  await settlePops();
  Object.assign(cells[last[0]][last[1]], { sym: `coon_${color}`, sc: 1, a: 1, dy: 0, flash: 0 });
  view.actor = null; view.rope = null;
  view.hl = new Map(); view.dim = false;
  setMeter(step.meter);
  setActive(null);
  await addWin(step.x, `${W.raccoons[color]} grabs ${step.grabs.length} · `);
  await sleep(T(120));
}

async function showScuffle(step) {
  sfx.scuffle();
  for (const col of step.who) {
    for (let c = 0; c < VW; c++) for (let r = 0; r < VH; r++) if (cells[c][r].sym === `coon_${col}`) { dust(c, r, 16); floater(c, r, 'SCUFFLE!', '#ffffff', 900, true); }
  }
  const f = $('boardFrame'); f.classList.remove('shake'); void f.offsetWidth; f.classList.add('shake');
  await sleep(T(450));
  const jobs = step.grabs.map((g, i) => sleep(T(i * 35)).then(() => doGrab(g, '#ffffff', null)));
  await Promise.all(jobs);
  await settlePops();
  await addWin(step.x, 'Scuffle · ');
}

async function showTeamUp(step) {
  sfx.teamUp();
  setMeter(W.meter_size, false);
  await banner('TEAM UP!', 'Every colour goes up a level', 1200);
  setLevels(step.levels, false);
  for (const col of COLORS) for (let c = 0; c < VW; c++) for (let r = 0; r < VH; r++) if (cells[c][r].sym === `coon_${col}`) sparks(c, r, 14, [HEX[col], '#ffffff']);
  setMeter(step.meter, false);
  await sleep(T(250));
}

async function showFall(step) {
  await settlePops();
  const jobs = [];
  let any = false;
  for (let col = 0; col < VW; col++) {
    let moved = false;
    for (let r = 0; r < VH; r++) {
      const src = step.moves[col][r];
      const cl = cells[col][r];
      const sym = step.grid[col][r];
      if (src === null || src === undefined) { Object.assign(cl, { sym: null, dy: 0 }); continue; }
      if (src === r && cl.sym === sym) continue;
      Object.assign(cl, { sym, dy: src - r, sc: 1, a: 1, flash: 0 });
      moved = true;
      jobs.push(tween(cl, { dy: 0 }, T(160 + 55 * Math.sqrt(Math.max(1, r - src))), ease.land, T(col * 16)));
    }
    if (moved) { any = true; sleep(T(col * 16 + 180)).then(() => sfx.land(col)); }
  }
  if (any) sfx.fall();
  await Promise.all(jobs);
  await sleep(T(100));
}

async function showBossIn(step) {
  sfx.boss();
  $('boardFrame').classList.add('boss');
  setWinText('🦝 THE BOSS IS HERE!', true);
  await banner('THE BOSS!', `${step.dashes} dashes through the vault`, 1500, 'boss');
  view.boss = { x: 3.5, y: -2, sc: 1, a: 1, rot: 0 };
}
async function showDash(step) {
  const [lc, lr] = step.land;
  const b = view.boss;
  // he jumps onto the spot he picked
  await tween(b, { x: lc, y: lr }, T(380), ease.inout);
  sfx.stomp();
  const f = $('boardFrame'); f.classList.remove('shake'); void f.offsetWidth; f.classList.add('shake');
  dust(lc, lr, 14);
  view.dashLine = { line: step.line, index: step.index, a: 1 };
  sfx.dash();
  // grabs go outwards from where he landed
  const jobs = step.grabs.map((g) => {
    const d = Math.abs(g.cell[0] - lc) + Math.abs(g.cell[1] - lr);
    return sleep(T(d * 70)).then(() => doGrab(g, '#ff9f43', null));
  });
  await Promise.all(jobs);
  tween(view.dashLine, { a: 0 }, T(400));
  await settlePops();
  // he leaves a Golden Mask behind
  const cl = cells[lc][lr];
  Object.assign(cl, { sym: 'mask', sc: 0.2, a: 1, flash: 1, dy: 0 });
  tween(cl, { sc: 1 }, T(280), ease.land); tween(cl, { flash: 0 }, T(420));
  sfx.wild(); sparks(lc, lr, 16);
  await tween(b, { y: lr - 1.2 }, T(220), ease.out);
  await addWin(step.x, 'The Boss · ');
  await sleep(T(150));
}
async function showBossOut() {
  const b = view.boss;
  if (b) await tween(b, { y: -3, a: 0 }, T(420), ease.in);
  view.boss = null;
  $('boardFrame').classList.remove('boss');
}

// the locker grid is as big as the stage allows: 5 x 4 lockers of 3:4, plus the title, crew and total around them
function fitLockers() {
  const box = $('lockers');
  if (box.classList.contains('hidden')) return;
  const st = $('stage');
  // the overlay fills the stage, so add up what sits around the grid: title, subtitle, crew, total, gaps, padding
  const other = [...box.children].filter((el) => el.id !== 'lkGrid').reduce((a, el) => a + el.offsetHeight, 0) + 4 * 8 + 24;
  const gap = Math.max(5, Math.min(10, st.clientWidth / 100));
  const hAvail = st.clientHeight - Math.max(other, 140) - 8;
  const lockerH = (hAvail - 3 * gap) / 4;
  const w = Math.max(160, Math.min(560, st.clientWidth * 0.94, (lockerH * 3 / 4) * 5 + 4 * gap));
  $('lkGrid').style.width = `${Math.floor(w)}px`;
}
window.addEventListener('resize', fitLockers);

async function showVaultGrab(step) {
  sfx.safe();
  await banner('VAULT GRAB!', 'Open lockers until the alarms catch the crew', 1300);
  const box = $('lockers');
  const r = currentRound;
  $('lkGrid').innerHTML = step.lockers.map((_, i) => `<div class="locker" id="lk-${i}"></div>`).join('');
  $('lkCrew').innerHTML = COLORS.map((col) => `<div id="lkc-${col}" style="background-image:url(${img(`coon_${col}`)})"></div>`).join('');
  $('lkTotal').textContent = '0';
  box.classList.remove('hidden');
  fitLockers();
  await sleep(T(500));
  let total = 0;
  for (const o of step.order) {
    for (const col of COLORS) $(`lkc-${col}`).classList.toggle('turn', col === o.who);
    const el = $(`lk-${o.locker}`);
    el.classList.add('next');
    await sleep(T(320));
    el.classList.remove('next');
    sfx.locker();
    const v = step.lockers[o.locker];
    el.classList.add('open');
    el.innerHTML = `<div class="by" style="background-image:url(${img(`coon_${o.who}`)})"></div>`;
    if (v === 'alarm') {
      el.classList.add('alarm');
      el.insertAdjacentHTML('beforeend', '<span class="val">🚨</span>');
      sfx.alarm();
      $(`lkc-${o.who}`).classList.add('caught');
      await sleep(T(700));
    } else {
      if (v >= 50) el.classList.add('big');
      el.insertAdjacentHTML('beforeend', `<span class="val">${shortAmount(credits(r.bet, v, r.scale))}</span>`);
      sfx.coin();
      total += v;
      $('lkTotal').textContent = amt(credits(r.bet, total, r.scale));
      await sleep(T(260));
    }
  }
  for (const col of COLORS) $(`lkc-${col}`).classList.remove('turn');
  await sleep(T(900));
  box.classList.add('hidden');
  await addWin(step.x, 'Vault Grab · ');
}

async function showBonusStart(step) {
  sfx.night();
  const big = step.kind === 'big';
  $('boardFrame').classList.add(big ? 'big' : 'night');
  setWinText(big ? '💰 THE BIG SCORE!' : '🌙 THE NIGHT SHIFT!', true);
  await banner(big ? 'THE BIG SCORE!' : 'NIGHT SHIFT!', `${step.drops} free drops · levels and the vault carry over`, 2000, 'night');
  coinShower(40, 2);
}
async function showBonusEnd(step) {
  $('boardFrame').classList.remove('night', 'big');
  setFree(null);
  const r = currentRound;
  await banner('SHIFT OVER', `${step.drops} drops · ${amt(credits(r.bet, step.x, r.scale))} won`, 1800, 'night');
}

// counts a number up in an element, with ticking sounds; resolves when done (or skipped)
function countUp(el, from, to, ms, sound = true) {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const step = () => {
      const p = skipRequested ? 1 : clamp((performance.now() - t0) / ms, 0, 1);
      el.textContent = amt(from + (to - from) * (1 - (1 - p) ** 2));
      if (sound && p < 1) sfx.count(p);
      if (p < 1) requestAnimationFrame(step); else resolve();
    };
    step();
  });
}

async function finishUp(round) {
  const pay = round.payout;
  await settlePops();
  // a Vault Grab buy has no board of its own: put one back
  if (!round.steps.some((s) => s.t === 'drop')) await dropIn(demoGrid(), open);
  view.hl = new Map(); view.dim = false;
  $('boardFrame').classList.remove('boss', 'night', 'big');
  setFree(null);
  if (pay > 0) {
    sfx.countEnd();
    if (pay / round.bet >= W.big_win) {
      await bigWin(pay, pay / round.bet);
      send({ type: 'announce', round: round.id, what: 'win' });
    }
    setWinText(`TOTAL WIN ${amt(pay)}`, true);
  } else {
    setWinText('Empty-handed this time');
  }
  setLastWin(pay);
  shownWin = 0;
  // the next spin starts from level 1 again: show what loot pays then
  setLevels({ red: 1, purple: 1, green: 1, blue: 1 }, false);
  setMeter(0, false);
  setBp(0, false);
  setShownBalance(balance, pay > 0);
  send({ type: 'shown', round: round.id });
  skipRequested = false;
  setBusy(false);
  renderBuys();
  if (autoOn) {
    await sleep(pay > round.stake ? 800 : 350);
    if (autoOn && !busy) doSpin('spin');
  }
}

async function bigWin(amount, x) {
  const tier = x >= 50 ? 3 : x >= 20 ? 2 : 1;
  const el = $('bigwin');
  $('bwTitle').textContent = ['', 'BIG WIN!', 'MEGA WIN!', 'EPIC WIN!'][tier];
  $('bwAmount').textContent = '0';
  el.classList.remove('hidden');
  skipRequested = false;
  const skip = () => { skipRequested = true; };
  el.addEventListener('click', skip);
  const secs = sfx.bigWin(tier);
  coinShower(60 + tier * 50, 2 + tier);
  await countUp($('bwAmount'), 0, amount, (turbo ? 0.55 : 1) * secs * 1000 * 0.85, false);
  if (!skipRequested) await sleep(turbo ? 800 : 1400);
  el.classList.add('hidden');
  el.removeEventListener('click', skip);
  skipRequested = false;
}

// ------------------------------------------------------------------ start
resize();
requestAnimationFrame(frame);
boot().catch((e) => showFatal(`Couldn't start: ${e.message || e}`));
