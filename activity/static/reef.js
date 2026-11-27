// Reef Rush client: every player plays their own 7x7 cluster slot; the server (activity/reef.py) plays every spin
// out completely and this page animates its steps. Sounds come from reef-sound.js, sign-in from auth.js.
// Every symbol is drawn here with canvas paths (no image files), so it stays sharp at any size.
import { signIn, retryAfterExpiredSession, go } from './auth.js';
import { sfx, unlock, isMuted, setMuted } from './reef-sound.js';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// fmt: exact for any size (big balances arrive as strings of digits)
import { fmt, num, minus, BIG_UNITS, huge } from './money.js';
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const BET_STEPS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000, 500000,
  1e6, 2.5e6, 5e6, 1e7, 2.5e7, 5e7, 1e8, 2.5e8, 5e8, 1e9];
const COLS = 7, ROWS = 7;
const PAYING = ['shell', 'pearl', 'urchin', 'star', 'clown', 'puffer', 'jelly', 'turtle'];
const COLOR = { shell: '#ff7fa3', pearl: '#e3d6ff', urchin: '#b06bff', star: '#ffb43d', clown: '#ff8a3d', puffer: '#ffd23f',
  jelly: '#ff6fd0', turtle: '#5fe08a', wild: '#4fe0d0', kraken: '#b26bff' };

{
  const p = new URLSearchParams(location.search);
  if (p.get('platform') === 'mobile' || p.get('mobile') === '1') document.body.classList.add('discord-mobile');
}

// ------------------------------------------------------------------ state
let W = null;                 // welcome: paytable, settings
let myId = null;
let ws = null, session = null, joinInfo = null, fatal = false, retry = 0;
let balance = 0;              // what the server says
let shownBalance = null;      // what the header shows (held back until a spin has been shown)
let bet = 100;
let busy = false;
let turbo = false;
let autoOn = false;
let skipRequested = false;
let players = [];
let feedItems = [];
let waiter = null;
try { bet = Number(localStorage.getItem('reef-bet')) || 100; } catch (e) { /* storage blocked */ }
try { turbo = localStorage.getItem('reef-turbo') === '1'; } catch (e) { /* storage blocked */ }
const T = (ms) => ms * (turbo ? 0.55 : 1) * (skipRequested ? 0.25 : 1);
const credits = (stake, x) => Math.floor(stake * x * W.scale + 1e-9);

// ------------------------------------------------------------------ sign in & connect
function loading(text) { $('loading').classList.remove('hidden'); $('loadingText').textContent = text; }
function showFatal(text) {
  fatal = true;
  $('loading').classList.remove('hidden');
  $('loading').querySelector('.spinner').classList.add('hidden');
  $('loadingText').textContent = text;
}
async function boot() {
  loading('Diving in…');
  const auth = await signIn(loading);
  if (auth.error) { showFatal(auth.error); return; }
  session = auth.session; joinInfo = auth.joinInfo;
  connect();
}
function connect() {
  loading('Swimming to the reef…');
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/reef`);
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
      W = m; myId = m.you; setupInfo(); setupBuys(); fitBet(); renderBet(); layoutMeter();
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
  if (busy) { toast('Wait for this spin to finish.'); return; }
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
  try { localStorage.setItem('reef-bet', String(bet)); } catch (e) { /* storage blocked */ }
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
  doSpin();
});
$('stage').addEventListener('click', (e) => {
  if (busy && !e.target.closest('.bigwin')) skipRequested = true;   // tap the reef to hurry the cascade up
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
  if (autoOn && !busy) doSpin();
});
function renderTurbo() { $('turboBtn').classList.toggle('on', turbo); }
$('turboBtn').addEventListener('click', () => {
  turbo = !turbo; sfx.click(); renderTurbo();
  try { localStorage.setItem('reef-turbo', turbo ? '1' : '0'); } catch (e) { /* storage blocked */ }
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
  if (m.status === 'ok') { toast(`Back in the water! +${fmt(m.amount)} credits`, true); sfx.countEnd(); }
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
    const text = p.status === 'spinning' ? 'spinning…' : p.last_win ? `last win ${amt(p.last_win)}` : (p.online ? 'on the reef' : 'away');
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
  el.innerHTML = `${item.kind === 'win' ? '🎉' : '🐙'} <b>${esc(item.name)}</b> ${esc(item.text)}${item.amount ? ` <b>${amt(item.amount)}</b>` : ''}`;
  $('feedPop').prepend(el);
  setTimeout(() => el.remove(), 5200);
  sfx.feed();
}

// ================================================================== symbol art
// Each painter draws one symbol filling a square of side s at (0, 0).
const TAU = Math.PI * 2;
function grad(c, x, y, r, inner, outer) {
  const g = c.createRadialGradient(x - r * 0.35, y - r * 0.4, r * 0.08, x, y, r * 1.05);
  g.addColorStop(0, inner); g.addColorStop(1, outer); return g;
}
function eye(c, x, y, r, iris = '#10202c') {
  c.fillStyle = '#fff'; c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
  c.lineWidth = r * 0.18; c.strokeStyle = 'rgba(0,0,0,.35)'; c.stroke();
  c.fillStyle = iris; c.beginPath(); c.arc(x + r * 0.18, y + r * 0.12, r * 0.56, 0, TAU); c.fill();
  c.fillStyle = '#fff'; c.beginPath(); c.arc(x - r * 0.05, y - r * 0.18, r * 0.2, 0, TAU); c.fill();
}
function shine(c, x, y, rx, ry, a = 0.45) {
  c.save(); c.fillStyle = `rgba(255,255,255,${a})`; c.beginPath(); c.ellipse(x, y, rx, ry, -0.5, 0, TAU); c.fill(); c.restore();
}
function edge(c, w, col = 'rgba(0, 20, 40, .55)') { c.lineWidth = w; c.strokeStyle = col; c.lineJoin = 'round'; c.stroke(); }

const PAINT = {
  shell(c, s) {   // scallop
    const cx = s / 2, by = s * 0.8, R = s * 0.58;
    const a0 = -Math.PI * 0.86, a1 = -Math.PI * 0.14;
    c.beginPath(); c.moveTo(cx, by);
    const bumps = 7;
    for (let i = 0; i <= bumps; i++) {
      const a = a0 + (a1 - a0) * (i / bumps);
      const x = cx + Math.cos(a) * R, y = by + Math.sin(a) * R;
      if (i === 0) c.lineTo(x, y);
      else {
        const am = a0 + (a1 - a0) * ((i - 0.5) / bumps);
        c.quadraticCurveTo(cx + Math.cos(am) * R * 1.09, by + Math.sin(am) * R * 1.09, x, y);
      }
    }
    c.closePath();
    c.fillStyle = grad(c, cx, s * 0.45, s * 0.42, '#ffd1dc', '#e0507a'); c.fill(); edge(c, s * 0.03);
    c.strokeStyle = 'rgba(150, 30, 70, .45)'; c.lineWidth = s * 0.022;
    for (let i = 1; i < bumps; i++) {
      const a = a0 + (a1 - a0) * (i / bumps);
      c.beginPath(); c.moveTo(cx, by); c.lineTo(cx + Math.cos(a) * R * 0.98, by + Math.sin(a) * R * 0.98); c.stroke();
    }
    c.beginPath(); c.moveTo(cx - s * 0.15, by + s * 0.04); c.lineTo(cx + s * 0.15, by + s * 0.04); c.lineTo(cx + s * 0.07, by - s * 0.08);
    c.lineTo(cx - s * 0.07, by - s * 0.08); c.closePath(); c.fillStyle = '#c23c66'; c.fill(); edge(c, s * 0.025);
    shine(c, cx - s * 0.14, s * 0.36, s * 0.08, s * 0.04, 0.5);
  },
  pearl(c, s) {   // a pearl in an open clam
    const cx = s / 2;
    c.beginPath(); c.ellipse(cx, s * 0.6, s * 0.4, s * 0.22, 0, 0, Math.PI); c.lineTo(cx - s * 0.4, s * 0.6);
    c.fillStyle = grad(c, cx, s * 0.62, s * 0.4, '#b9a4ff', '#5b3fb0'); c.fill(); edge(c, s * 0.03);
    c.beginPath(); c.ellipse(cx, s * 0.6, s * 0.4, s * 0.07, 0, 0, TAU); c.fillStyle = '#3a2580'; c.fill();
    c.beginPath(); c.arc(cx, s * 0.42, s * 0.23, 0, TAU);
    c.fillStyle = grad(c, cx, s * 0.42, s * 0.23, '#ffffff', '#c7b8ee'); c.fill(); edge(c, s * 0.025, 'rgba(60, 40, 120, .5)');
    shine(c, cx - s * 0.08, s * 0.34, s * 0.07, s * 0.045, 0.9);
    c.fillStyle = 'rgba(255, 190, 230, .35)'; c.beginPath(); c.arc(cx + s * 0.08, s * 0.5, s * 0.08, 0, TAU); c.fill();
  },
  urchin(c, s) {
    const cx = s / 2, cy = s * 0.52, r = s * 0.25;
    c.fillStyle = '#4a1d80';
    for (let i = 0; i < 20; i++) {
      const a = (i / 20) * TAU + 0.1, w = 0.13;
      c.beginPath();
      c.moveTo(cx + Math.cos(a - w) * r * 0.9, cy + Math.sin(a - w) * r * 0.9);
      c.lineTo(cx + Math.cos(a) * r * (i % 2 ? 1.62 : 1.85), cy + Math.sin(a) * r * (i % 2 ? 1.62 : 1.85));
      c.lineTo(cx + Math.cos(a + w) * r * 0.9, cy + Math.sin(a + w) * r * 0.9); c.closePath(); c.fill();
    }
    c.beginPath(); c.arc(cx, cy, r, 0, TAU); c.fillStyle = grad(c, cx, cy, r, '#e0a8ff', '#6a2bb0'); c.fill(); edge(c, s * 0.025);
    c.fillStyle = 'rgba(255, 220, 255, .55)';
    for (let i = 0; i < 7; i++) { const a = i * 0.9; c.beginPath(); c.arc(cx + Math.cos(a) * r * 0.55, cy + Math.sin(a) * r * 0.55, s * 0.018, 0, TAU); c.fill(); }
    shine(c, cx - r * 0.35, cy - r * 0.4, r * 0.3, r * 0.17, 0.55);
  },
  star(c, s) {   // starfish
    const cx = s / 2, cy = s * 0.53, R = s * 0.4, r = s * 0.17;
    c.beginPath();
    for (let i = 0; i < 10; i++) {
      const a = -Math.PI / 2 + (i / 10) * TAU, rad = i % 2 ? r : R;
      const x = cx + Math.cos(a) * rad, y = cy + Math.sin(a) * rad;
      if (i) c.lineTo(x, y); else c.moveTo(x, y);
    }
    c.closePath();
    c.lineJoin = 'round'; c.lineWidth = s * 0.09; c.strokeStyle = '#e8701c'; c.stroke();
    c.fillStyle = grad(c, cx, cy, R, '#ffe08a', '#f08a1d'); c.fill();
    c.lineWidth = s * 0.012; c.strokeStyle = 'rgba(120, 50, 0, .5)'; c.stroke();
    c.fillStyle = 'rgba(255, 250, 220, .8)';
    for (let i = 0; i < 5; i++) {
      const a = -Math.PI / 2 + (i / 5) * TAU;
      for (const d of [0.35, 0.6]) { c.beginPath(); c.arc(cx + Math.cos(a) * R * d, cy + Math.sin(a) * R * d, s * 0.022, 0, TAU); c.fill(); }
    }
    c.beginPath(); c.arc(cx, cy, s * 0.05, 0, TAU); c.fillStyle = '#ff6f61'; c.fill();
  },
  clown(c, s) {   // clownfish, facing right
    const cx = s * 0.52, cy = s * 0.52, rx = s * 0.3, ry = s * 0.2;
    c.beginPath(); c.moveTo(cx - rx * 0.8, cy); c.lineTo(cx - rx * 1.45, cy - ry * 0.95); c.quadraticCurveTo(cx - rx * 1.2, cy, cx - rx * 1.45, cy + ry * 0.95);
    c.closePath(); c.fillStyle = '#ff7a1f'; c.fill(); edge(c, s * 0.025);
    c.beginPath(); c.moveTo(cx - rx * 0.3, cy - ry * 0.85); c.quadraticCurveTo(cx, cy - ry * 1.9, cx + rx * 0.45, cy - ry * 0.8); c.closePath();
    c.fillStyle = '#ff7a1f'; c.fill(); edge(c, s * 0.025);
    c.save();
    c.beginPath(); c.ellipse(cx, cy, rx, ry, 0, 0, TAU); c.fillStyle = grad(c, cx, cy, rx, '#ffc070', '#f2620f'); c.fill();
    c.clip();
    for (const bx of [-0.5, 0.25]) {
      c.fillStyle = '#10202c'; c.fillRect(cx + rx * bx - s * 0.06, cy - ry, s * 0.12, ry * 2);
      c.fillStyle = '#fff'; c.fillRect(cx + rx * bx - s * 0.04, cy - ry, s * 0.08, ry * 2);
    }
    c.restore();
    c.beginPath(); c.ellipse(cx, cy, rx, ry, 0, 0, TAU); edge(c, s * 0.03);
    c.beginPath(); c.ellipse(cx + rx * 0.05, cy + ry * 0.45, s * 0.07, s * 0.035, 0.6, 0, TAU); c.fillStyle = '#e8560c'; c.fill();
    eye(c, cx + rx * 0.62, cy - ry * 0.18, s * 0.065);
    c.beginPath(); c.arc(cx + rx * 0.86, cy + ry * 0.3, s * 0.03, 0.2, Math.PI - 0.6); c.lineWidth = s * 0.02; c.strokeStyle = '#7a2a00'; c.stroke();
    shine(c, cx - rx * 0.1, cy - ry * 0.55, rx * 0.25, ry * 0.15, 0.4);
  },
  puffer(c, s) {
    const cx = s / 2, cy = s * 0.52, r = s * 0.29;
    c.fillStyle = '#c99a0e';
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * TAU;
      c.beginPath(); c.moveTo(cx + Math.cos(a - 0.12) * r, cy + Math.sin(a - 0.12) * r);
      c.lineTo(cx + Math.cos(a) * r * 1.32, cy + Math.sin(a) * r * 1.32); c.lineTo(cx + Math.cos(a + 0.12) * r, cy + Math.sin(a + 0.12) * r); c.fill();
    }
    c.beginPath(); c.arc(cx, cy, r, 0, TAU); c.fillStyle = grad(c, cx, cy, r, '#fff3a0', '#f0b400'); c.fill(); edge(c, s * 0.03);
    c.beginPath(); c.ellipse(cx, cy + r * 0.45, r * 0.7, r * 0.45, 0, 0, TAU); c.fillStyle = 'rgba(255, 255, 230, .7)'; c.fill();
    c.fillStyle = '#b5820a';
    for (const [dx, dy] of [[-0.5, -0.55], [0.1, -0.7], [0.55, -0.4], [-0.7, -0.1]]) { c.beginPath(); c.arc(cx + dx * r, cy + dy * r, s * 0.022, 0, TAU); c.fill(); }
    eye(c, cx - r * 0.36, cy - r * 0.08, s * 0.085);
    eye(c, cx + r * 0.36, cy - r * 0.08, s * 0.085);
    c.beginPath(); c.ellipse(cx, cy + r * 0.42, s * 0.04, s * 0.03, 0, 0, TAU); c.fillStyle = '#c0392b'; c.fill();
    c.fillStyle = 'rgba(255, 120, 120, .45)';
    c.beginPath(); c.arc(cx - r * 0.62, cy + r * 0.3, s * 0.035, 0, TAU); c.arc(cx + r * 0.62, cy + r * 0.3, s * 0.035, 0, TAU); c.fill();
    shine(c, cx - r * 0.4, cy - r * 0.55, r * 0.22, r * 0.12, 0.6);
  },
  jelly(c, s) {   // jellyfish
    const cx = s / 2, cy = s * 0.42, r = s * 0.3;
    c.lineCap = 'round';
    for (let i = 0; i < 5; i++) {
      const x = cx - r * 0.7 + (i / 4) * r * 1.4;
      c.beginPath(); c.moveTo(x, cy + r * 0.1);
      c.bezierCurveTo(x + s * 0.06, cy + r * 0.6, x - s * 0.06, cy + r * 0.95, x + (i % 2 ? 0.04 : -0.04) * s, cy + r * 1.45);
      c.lineWidth = s * (i % 2 ? 0.03 : 0.04); c.strokeStyle = i % 2 ? 'rgba(255, 170, 230, .85)' : 'rgba(230, 90, 190, .9)'; c.stroke();
    }
    c.beginPath(); c.moveTo(cx - r, cy + r * 0.12); c.arc(cx, cy + r * 0.12, r, Math.PI, 0);
    for (let i = 0; i < 6; i++) {
      const x1 = cx + r - (i + 1) * (2 * r / 6);
      c.quadraticCurveTo(x1 + r / 6, cy + r * 0.38, x1, cy + r * 0.12);
    }
    c.closePath();
    c.fillStyle = grad(c, cx, cy, r, '#ffd6f2', '#e04aae'); c.fill(); edge(c, s * 0.025, 'rgba(110, 10, 80, .55)');
    eye(c, cx - r * 0.35, cy - r * 0.05, s * 0.06);
    eye(c, cx + r * 0.35, cy - r * 0.05, s * 0.06);
    c.beginPath(); c.arc(cx, cy + r * 0.05, s * 0.04, 0.2, Math.PI - 0.2); c.lineWidth = s * 0.02; c.strokeStyle = '#7a1050'; c.stroke();
    shine(c, cx - r * 0.45, cy - r * 0.55, r * 0.25, r * 0.12, 0.6);
  },
  turtle(c, s) {   // sea turtle, seen from above
    const cx = s / 2, cy = s * 0.55;
    c.fillStyle = '#4bbf6a';
    for (const [dx, dy, rot, big] of [[-0.29, -0.15, -0.9, 1], [0.29, -0.15, 0.9, 1], [-0.22, 0.22, 0.6, 0], [0.22, 0.22, -0.6, 0]]) {
      c.beginPath(); c.ellipse(cx + dx * s, cy + dy * s, s * (big ? 0.15 : 0.09), s * 0.06, rot, 0, TAU); c.fill(); edge(c, s * 0.02);
    }
    c.beginPath(); c.ellipse(cx, cy - s * 0.31, s * 0.09, s * 0.1, 0, 0, TAU); c.fillStyle = '#5fd27e'; c.fill(); edge(c, s * 0.02);
    eye(c, cx - s * 0.04, cy - s * 0.33, s * 0.028); eye(c, cx + s * 0.04, cy - s * 0.33, s * 0.028);
    c.beginPath(); c.ellipse(cx, cy, s * 0.24, s * 0.27, 0, 0, TAU);
    c.fillStyle = grad(c, cx, cy, s * 0.27, '#b8f07a', '#2f8a3a'); c.fill(); edge(c, s * 0.03);
    c.save(); c.beginPath(); c.ellipse(cx, cy, s * 0.24, s * 0.27, 0, 0, TAU); c.clip();
    const hex = (x, y, r) => {
      c.beginPath();
      for (let i = 0; i < 6; i++) { const a = (i / 6) * TAU + Math.PI / 6; const px = x + Math.cos(a) * r, py = y + Math.sin(a) * r; if (i) c.lineTo(px, py); else c.moveTo(px, py); }
      c.closePath(); c.fillStyle = 'rgba(255, 230, 140, .35)'; c.fill(); c.lineWidth = s * 0.016; c.strokeStyle = 'rgba(20, 70, 30, .7)'; c.stroke();
    };
    hex(cx, cy, s * 0.085);
    for (let i = 0; i < 6; i++) { const a = (i / 6) * TAU; hex(cx + Math.cos(a) * s * 0.165, cy + Math.sin(a) * s * 0.165, s * 0.07); }
    c.restore();
    shine(c, cx - s * 0.09, cy - s * 0.13, s * 0.07, s * 0.035, 0.45);
  },
  wild(c, s) {   // golden trident in a bubble
    const cx = s / 2, cy = s * 0.47, r = s * 0.4;
    c.beginPath(); c.arc(cx, cy, r, 0, TAU);
    const g = c.createRadialGradient(cx, cy, r * 0.2, cx, cy, r);
    g.addColorStop(0, 'rgba(120, 255, 240, .45)'); g.addColorStop(0.85, 'rgba(30, 170, 190, .55)'); g.addColorStop(1, 'rgba(200, 255, 250, .9)');
    c.fillStyle = g; c.fill(); c.lineWidth = s * 0.025; c.strokeStyle = 'rgba(220, 255, 255, .9)'; c.stroke();
    const gold = c.createLinearGradient(cx - s * 0.2, 0, cx + s * 0.2, 0); gold.addColorStop(0, '#b8860b'); gold.addColorStop(0.5, '#fff1a6'); gold.addColorStop(1, '#c7920f');
    c.fillStyle = gold; c.strokeStyle = '#6b4a00'; c.lineWidth = s * 0.015;
    c.beginPath(); c.rect(cx - s * 0.025, cy - s * 0.18, s * 0.05, s * 0.42); c.fill(); c.stroke();
    c.beginPath();
    c.moveTo(cx - s * 0.17, cy - s * 0.12); c.lineTo(cx - s * 0.17, cy - s * 0.3); c.lineTo(cx - s * 0.12, cy - s * 0.2);
    c.lineTo(cx - s * 0.12, cy - s * 0.08); c.lineTo(cx + s * 0.12, cy - s * 0.08); c.lineTo(cx + s * 0.12, cy - s * 0.2);
    c.lineTo(cx + s * 0.17, cy - s * 0.3); c.lineTo(cx + s * 0.17, cy - s * 0.12); c.quadraticCurveTo(cx, cy + s * 0.02, cx - s * 0.17, cy - s * 0.12);
    c.fill(); c.stroke();
    c.beginPath(); c.moveTo(cx - s * 0.04, cy - s * 0.2); c.lineTo(cx, cy - s * 0.34); c.lineTo(cx + s * 0.04, cy - s * 0.2); c.closePath(); c.fill(); c.stroke();
    banderole(c, cx, s * 0.78, s * 0.62, s * 0.17, 'WILD', '#0b6f80', '#c9fff7');
    shine(c, cx - r * 0.45, cy - r * 0.55, r * 0.22, r * 0.1, 0.7);
  },
};
function banderole(c, cx, cy, w, h, text, bg, fg) {
  c.beginPath(); c.roundRect ? c.roundRect(cx - w / 2, cy - h / 2, w, h, h * 0.45) : c.rect(cx - w / 2, cy - h / 2, w, h);
  c.fillStyle = bg; c.fill(); c.lineWidth = h * 0.12; c.strokeStyle = fg; c.stroke();
  c.fillStyle = fg; c.font = `800 ${h * 0.72}px Manrope, system-ui, sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText(text, cx, cy + h * 0.04);
}
// The Kraken: a giant octopus filling n x n cells (side s)
function paintKraken(c, s) {
  const cx = s / 2;
  c.save();
  c.fillStyle = 'rgba(30, 6, 60, .55)'; c.beginPath(); c.roundRect ? c.roundRect(s * 0.02, s * 0.02, s * 0.96, s * 0.96, s * 0.1) : c.rect(0, 0, s, s); c.fill();
  c.lineCap = 'round';
  for (let i = 0; i < 6; i++) {
    const x0 = s * (0.18 + i * 0.128), dir = i < 3 ? -1 : 1;
    c.beginPath(); c.moveTo(x0, s * 0.55);
    c.bezierCurveTo(x0 + dir * s * 0.02, s * 0.75, x0 - dir * s * 0.1, s * 0.86, x0 + dir * s * 0.05, s * 0.93);
    c.lineWidth = s * 0.075; c.strokeStyle = '#6a2bb0'; c.stroke();
    c.lineWidth = s * 0.045; c.strokeStyle = '#9c5ae8'; c.stroke();
    c.fillStyle = '#f0d9ff';
    for (let k = 0; k < 3; k++) { c.beginPath(); c.arc(x0 + dir * s * (-0.01 - k * 0.012), s * (0.66 + k * 0.08), s * 0.012, 0, TAU); c.fill(); }
  }
  c.beginPath(); c.ellipse(cx, s * 0.4, s * 0.36, s * 0.3, 0, 0, TAU);
  c.fillStyle = grad(c, cx, s * 0.4, s * 0.36, '#d9a6ff', '#5a1fa0'); c.fill(); edge(c, s * 0.018, 'rgba(20, 0, 40, .6)');
  c.fillStyle = 'rgba(255, 220, 255, .35)';
  for (const [dx, dy, r] of [[-0.2, 0.22, 0.03], [0.22, 0.2, 0.025], [-0.05, 0.18, 0.02], [0.12, 0.28, 0.022]]) { c.beginPath(); c.arc(cx + dx * s, dy * s, r * s, 0, TAU); c.fill(); }
  for (const dx of [-0.13, 0.13]) {
    c.beginPath(); c.ellipse(cx + dx * s, s * 0.44, s * 0.085, s * 0.075, 0, 0, TAU); c.fillStyle = '#fff6c0'; c.fill(); edge(c, s * 0.012);
    c.beginPath(); c.ellipse(cx + dx * s + s * 0.01, s * 0.45, s * 0.06, s * 0.055, 0, 0, TAU); c.fillStyle = '#ffc61a'; c.fill();
    c.beginPath(); c.ellipse(cx + dx * s + s * 0.015, s * 0.455, s * 0.016, s * 0.045, 0, 0, TAU); c.fillStyle = '#1a0630'; c.fill();
    c.beginPath(); c.arc(cx + dx * s - s * 0.02, s * 0.42, s * 0.014, 0, TAU); c.fillStyle = '#fff'; c.fill();
  }
  shine(c, cx - s * 0.16, s * 0.22, s * 0.1, s * 0.045, 0.45);
  banderole(c, cx, s * 0.82, s * 0.46, s * 0.11, 'WILD', '#3b1670', '#f0d9ff');
  c.restore();
}

// sprites, redrawn whenever the cell size changes
const SPR = {};
let sprPx = 0;
function buildSprites(px) {
  if (px === sprPx) return;
  sprPx = px;
  for (const k of Object.keys(PAINT)) {
    const cv = document.createElement('canvas'); cv.width = cv.height = px;
    const c = cv.getContext('2d');
    c.translate(px * 0.04, px * 0.04); PAINT[k](c, px * 0.92);
    SPR[k] = cv;
  }
  for (const n of [2, 3]) {
    const cv = document.createElement('canvas'); cv.width = cv.height = px * n;
    paintKraken(cv.getContext('2d'), px * n);
    SPR[`kraken${n}`] = cv;
  }
}
function symbolImage(k, px = 96) {
  const cv = document.createElement('canvas'); cv.width = cv.height = px;
  const c = cv.getContext('2d');
  if (k === 'kraken') paintKraken(c, px); else { c.translate(px * 0.04, px * 0.04); PAINT[k](c, px * 0.92); }
  return cv.toDataURL();
}

// ------------------------------------------------------------------ info / paytable
function setupInfo() {
  const mult = (x) => `${(Math.round(x * 100) / 100).toString()}x`;
  const sizes = (p) => [[0, '5'], [1, '6'], [2, '7'], [3, '8'], [5, '10'], [7, '12'], [10, '15+']]
    .map(([i, lbl]) => `${lbl}: <b>${mult(p[i])}</b>`).join(' · ');
  const order = [...W.high].reverse().concat([...W.low].reverse());
  const pays = order.map((k) => `<div class="pt"><img src="${symbolImage(k)}" alt="">
    <div><b>${esc(W.symbols[k].name)}</b><div class="pays">${sizes(W.symbols[k].pays)}</div></div></div>`).join('');
  const surges = Object.values(W.surges).map((f) => `<li><b>${esc(f.name)}</b>: ${esc(f.text)}</li>`).join('');
  $('infoBody').innerHTML = `
    <p>${W.min_cluster} or more matching symbols touching up, down, left or right make a cluster. Every cluster pays (times your bet,
    by its size), then its symbols vanish, the ones above fall down and new ones drop in. That keeps going for as long as new
    clusters form.</p>
    <div class="paytable">${pays}
      <div class="pt"><img src="${symbolImage('wild')}" alt=""><div><b>Wild</b><div class="pays">Joins any cluster.</div></div></div>
      <div class="pt"><img src="${symbolImage('kraken')}" alt=""><div><b>The Kraken</b><div class="pays">A 3×3 wild. See below.</div></div></div>
    </div>
    <h3>Pearl Wilds</h3>
    <p>A cluster of ${W.pearl_size} or more leaves a wild behind on one of its cells.</p>
    <h3>The Tide Meter</h3>
    <p>Every symbol in a winning cluster adds one charge. The meter starts empty on every spin. When the cascade stops with
    <b>${W.surge_at}</b> charges, a random <b>Surge</b> hits the reef and the cascade carries on:</p><ul>${surges}</ul>
    <p>If it stops again with <b>${W.kraken_at}</b> charges, <b>the Kraken</b> rises: a 3×3 wild on the bottom rows. After it's
    part of a win it shrinks to 2×2, and after another win it splits into 4 ordinary wilds.</p>
    <h3>Bubble Burst</h3>
    <p>When the first drop of a spin wins nothing, bubbles sometimes pop every symbol of one low-paying kind, so new ones drop in.</p>
    ${Object.keys(W.buys).length ? `<h3>Bonus buys</h3>
    <p>BUY skips the wait: a spin that starts with the Tide Meter already charged. ${Object.values(W.buys).map((b) =>
      `<b>${esc(b.name)}</b> costs ${b.price}x your bet and starts at ${b.meter} charges (pays back ${(b.rtp * 100).toFixed(1)}%).`).join(' ')}
    Everything else about the spin is the same as a normal one.${W.buy_max_bet ? ` Bonus buys go up to a bet of ${betText(W.buy_max_bet)}.` : ''}</p>` : ''}
    <h3>Everyone on the reef</h3>
    <p>Everybody plays their own board. Everyone in this server with Reef Rush open is on the same reef: the panel shows what the
    others are doing, and big wins (${W.big_win}x and up) and the Kraken are announced to everyone.</p>
    <p>Maximum win ${fmt(W.max_win)}x your bet per spin. Reef Rush returns ${(W.rtp * 100).toFixed(0)}% of bets on average.
    Space bar spins; tap the board to speed a spin up. Turbo speeds up the animations; Auto keeps spinning until you stop it.</p>`;
}
$('infoBtn').addEventListener('click', () => { sfx.click(); $('infoModal').classList.remove('hidden'); });

// ------------------------------------------------------------------ bonus buys
const BUY_ICON = { surge: 'wild', kraken: 'kraken' };
// a buy never costs less than its price: rounded up, like the server does
const stakeFor = (mode) => Math.max(1, Math.ceil(bet * (mode === 'spin' ? 1 : W.buys[mode].price) - 1e-9));
function setupBuys() {
  const has = W && Object.keys(W.buys || {}).length > 0;
  $('buyBtn').classList.toggle('hidden', !has);
  if (!has) return;
  $('buys').innerHTML = Object.entries(W.buys).map(([k, b]) => `<button class="buy" data-mode="${k}">
    <img src="${symbolImage(BUY_ICON[k] || 'wild')}" alt=""><div class="b-name">${esc(b.name)}</div><div class="b-text">${esc(b.text)}</div>
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
  if (!W || !Object.keys(W.buys || {}).length) return;
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
$('infoClose').addEventListener('click', () => $('infoModal').classList.add('hidden'));
$('infoModal').addEventListener('click', (e) => { if (e.target === $('infoModal')) $('infoModal').classList.add('hidden'); });

// ================================================================== the board (canvas)
const canvas = $('board');
const cx = canvas.getContext('2d');
const fx = $('fx');
const fxc = fx.getContext('2d');
let dpr = 1;
let CS = 60;                  // cell size in CSS pixels
let boardPx = CS * COLS;
const key = (c, r) => `${c},${r}`;

function makeCell(sym) { return { sym, dy: 0, sc: 1, a: 1, flash: 0 }; }
function randomSym() { return PAYING[Math.floor(Math.random() * PAYING.length)]; }
const cells = Array.from({ length: COLS }, () => Array.from({ length: ROWS }, () => makeCell(randomSym())));
const view = {
  hl: new Map(),        // "c,r" -> highlight color (winning clusters)
  dim: false,
  labels: [],           // [{ x, y, text, t0 }] in cell units
  kraken: null,         // { c, size, dy, sc, a }
  swirl: null,          // { t0 } whirlpool backdrop
};

// lay out the board and the Tide Meter for the space there is
function resize() {
  dpr = Math.min(2.5, window.devicePixelRatio || 1);
  // the board, its meter and the win text are centred together in the stage
  const play = $('play'), stage = $('stage');
  const cs = getComputedStyle(play);
  const pw = stage.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const ph = stage.clientHeight - $('winBar').offsetHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - 4;
  const framePad = 2 * parseFloat(getComputedStyle($('boardFrame')).paddingLeft || '6');
  const meterSide = 70;         // room the meter takes beside the board
  const meterTop = 44;          // ... or above it (bar + labels)
  const land = pw - meterSide >= ph - meterTop;
  play.classList.toggle('land', land);
  play.classList.toggle('port', !land);
  let outer = land ? Math.min(pw - meterSide, ph) : Math.min(pw, ph - meterTop);
  outer = clamp(outer, 140, 980);
  CS = Math.floor((outer - framePad) / COLS);
  boardPx = CS * COLS;
  canvas.style.width = `${boardPx}px`;
  canvas.style.height = `${boardPx}px`;
  canvas.width = Math.round(boardPx * dpr);
  canvas.height = Math.round(boardPx * dpr);
  buildSprites(Math.round(CS * dpr));
  const meter = $('meter');
  const len = boardPx + framePad;
  if (land) { meter.style.height = `${len}px`; meter.style.width = ''; } else { meter.style.width = `${len}px`; meter.style.height = ''; }
  layoutMeter();
  const st = $('stage');
  fx.width = Math.round(st.clientWidth * dpr); fx.height = Math.round(st.clientHeight * dpr);
  if (shownBalance !== null) setShownBalance(shownBalance);
}
new ResizeObserver(() => resize()).observe($('stage'));
window.addEventListener('resize', resize);

// ------------------------------------------------------------------ tweens
const tweens = new Set();
const ease = {
  out: (p) => 1 - (1 - p) ** 3,
  in: (p) => p * p,
  land: (p) => { const s = 1.4; const q = p - 1; return q * q * ((s + 1) * q + s) + 1; },   // out-back: a little bounce
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
function roundRect(c, x, y, w, h, r) {
  c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
let lastFrame = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - lastFrame) / 1000); lastFrame = now;
  runTweens(now);
  const c = cx;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, boardPx, boardPx);
  // water and cell slots
  const bg = c.createLinearGradient(0, 0, 0, boardPx);
  bg.addColorStop(0, '#0a3a58'); bg.addColorStop(1, '#03182a');
  c.fillStyle = bg; c.fillRect(0, 0, boardPx, boardPx);
  for (let i = 0; i < 4; i++) {   // light rays
    const x = ((now / 9000 + i * 0.27) % 1.2 - 0.1) * boardPx;
    const g = c.createLinearGradient(x, 0, x + boardPx * 0.12, boardPx);
    g.addColorStop(0, 'rgba(160, 240, 255, .07)'); g.addColorStop(1, 'rgba(160, 240, 255, 0)');
    c.fillStyle = g; c.beginPath(); c.moveTo(x, 0); c.lineTo(x + boardPx * 0.08, 0); c.lineTo(x + boardPx * 0.2, boardPx); c.lineTo(x + boardPx * 0.06, boardPx); c.fill();
  }
  c.fillStyle = 'rgba(120, 220, 240, .055)';
  for (let col = 0; col < COLS; col++) for (let r = 0; r < ROWS; r++) { roundRect(c, col * CS + CS * 0.06, r * CS + CS * 0.06, CS * 0.88, CS * 0.88, CS * 0.2); c.fill(); }
  if (view.swirl) {
    const age = (now - view.swirl.t0) / 1000;
    c.save(); c.translate(boardPx / 2, boardPx / 2); c.rotate(age * 3);
    c.strokeStyle = `rgba(160, 250, 255, ${clamp(0.35 - age * 0.12, 0, 0.35)})`; c.lineWidth = CS * 0.12;
    for (let k = 0; k < 3; k++) { c.beginPath(); c.arc(0, 0, boardPx * (0.15 + k * 0.13), k, k + 2.4); c.stroke(); }
    c.restore();
  }
  // highlights behind winning cells
  for (const [k, col] of view.hl) {
    const [cc, r] = k.split(',').map(Number);
    const cell = cells[cc][r];
    c.globalAlpha = 0.55 + 0.25 * Math.sin(now / 120);
    c.fillStyle = col; roundRect(c, cc * CS + CS * 0.04, (r + cell.dy) * CS + CS * 0.04, CS * 0.92, CS * 0.92, CS * 0.22); c.fill();
    c.globalAlpha = 1;
  }
  // symbols
  for (let col = 0; col < COLS; col++) {
    for (let r = 0; r < ROWS; r++) {
      const cell = cells[col][r];
      if (!cell.sym || cell.sym === 'kraken' || cell.a <= 0.01) continue;
      const img = SPR[cell.sym];
      if (!img) continue;
      const lit = view.hl.has(key(col, r));
      const bob = cell.dy === 0 && !lit ? Math.sin(now / 650 + col * 1.3 + r * 0.7) * CS * 0.018 : 0;
      const size = CS * cell.sc * (lit ? 1.06 + 0.04 * Math.sin(now / 110) : 1);
      const x = col * CS + (CS - size) / 2, y = (r + cell.dy) * CS + (CS - size) / 2 + bob;
      c.globalAlpha = cell.a * (view.dim && !lit ? 0.35 : 1);
      c.drawImage(img, x, y, size, size);
      if (cell.flash > 0) {
        c.globalAlpha = cell.flash; c.fillStyle = '#ffffff';
        roundRect(c, col * CS + CS * 0.08, (r + cell.dy) * CS + CS * 0.08, CS * 0.84, CS * 0.84, CS * 0.25); c.fill();
      }
      c.globalAlpha = 1;
    }
  }
  // the Kraken
  const k = view.kraken;
  if (k && k.a > 0.01) {
    const img = SPR[`kraken${k.size}`];
    const side = k.size * CS * k.sc;
    const x = k.c * CS + (k.size * CS - side) / 2, y = (ROWS - k.size + k.dy) * CS + (k.size * CS - side) / 2;
    c.globalAlpha = k.a * (view.dim && !k.lit ? 0.5 : 1);
    if (k.lit) { c.shadowColor = '#d9a6ff'; c.shadowBlur = CS * 0.5; }
    if (img) c.drawImage(img, x, y, side, side);
    c.shadowBlur = 0; c.globalAlpha = 1;
  }
  // win amounts
  for (const lb of view.labels) {
    const p = clamp((now - lb.t0) / 300, 0, 1);
    pill(c, (lb.x + 0.5) * CS, (lb.y + 0.5) * CS - p * CS * 0.25, lb.text, p);
  }
  drawParticles(dt);
  requestAnimationFrame(frame);
}
function pill(c, x, y, text, a) {
  const fs = Math.max(11, CS * 0.32);
  c.font = `800 ${fs}px Manrope, system-ui, sans-serif`;
  const w = c.measureText(text).width + fs * 1.1, h = fs * 1.5;
  c.globalAlpha = a;
  c.fillStyle = 'rgba(3, 24, 42, .92)'; roundRect(c, x - w / 2, y - h / 2, w, h, h / 2); c.fill();
  c.lineWidth = 2; c.strokeStyle = '#ffe08a'; c.stroke();
  c.fillStyle = '#ffe08a'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(text, x, y + fs * 0.05);
  c.globalAlpha = 1;
}

// ------------------------------------------------------------------ particles (stage-wide canvas)
const parts = [];
function cellOnStage(col, r) {
  const b = canvas.getBoundingClientRect(), s = $('stage').getBoundingClientRect();
  return [b.left - s.left + (col + 0.5) * CS, b.top - s.top + (r + 0.5) * CS];
}
function sparks(col, r, n = 14, colors = ['#ffffff', '#c9fff7', '#4fe0d0']) {
  const [x, y] = cellOnStage(col, r);
  for (let i = 0; i < n; i++) {
    const a = Math.random() * TAU, v = (0.6 + Math.random()) * CS * 3.2;
    parts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - CS, life: 0.5 + Math.random() * 0.4, age: 0, size: CS * (0.04 + Math.random() * 0.05),
      color: colors[i % colors.length], g: CS * 6, kind: 'dot' });
  }
}
function bubbles(col, r, n = 6) {
  const [x, y] = cellOnStage(col, r);
  for (let i = 0; i < n; i++) {
    parts.push({ x: x + (Math.random() - 0.5) * CS * 0.7, y: y + (Math.random() - 0.2) * CS * 0.5, vx: (Math.random() - 0.5) * CS * 0.6,
      vy: -CS * (1.5 + Math.random() * 2), life: 0.8 + Math.random() * 0.6, age: 0, size: CS * (0.06 + Math.random() * 0.1), color: '#c9fff7', g: 0, kind: 'bubble' });
  }
}
function coinShower(n = 80, dur = 2.5) {
  const w = fx.width / dpr;
  for (let i = 0; i < n; i++) {
    parts.push({ x: Math.random() * w, y: -20 - Math.random() * 200, vx: (Math.random() - 0.5) * 80, vy: 100 + Math.random() * 200,
      life: dur + Math.random(), age: 0, size: 6 + Math.random() * 6, color: Math.random() < 0.5 ? '#ffe08a' : '#f2b705', g: 260, kind: 'coin', spin: Math.random() * 6 });
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
    if (p.kind === 'bubble') p.vx += Math.sin(p.age * 8) * CS * 0.08;
    const a = 1 - p.age / p.life;
    c.globalAlpha = a;
    if (p.kind === 'bubble') {
      c.strokeStyle = p.color; c.lineWidth = Math.max(1, p.size * 0.18);
      c.beginPath(); c.arc(p.x, p.y, p.size, 0, TAU); c.stroke();
      c.fillStyle = 'rgba(255,255,255,.6)'; c.beginPath(); c.arc(p.x - p.size * 0.35, p.y - p.size * 0.35, p.size * 0.25, 0, TAU); c.fill();
    } else if (p.kind === 'coin') {
      const sx = Math.abs(Math.cos(p.age * p.spin));
      c.fillStyle = p.color; c.beginPath(); c.ellipse(p.x, p.y, p.size * sx + 1, p.size, 0, 0, TAU); c.fill();
    } else {
      c.fillStyle = p.color; c.beginPath(); c.arc(p.x, p.y, p.size, 0, TAU); c.fill();
    }
  }
  c.globalAlpha = 1;
}

// ------------------------------------------------------------------ Tide Meter
let meterVal = 0;
function layoutMeter() {
  if (!W) return;
  const land = $('play').classList.contains('land');
  for (const [id, at] of [['markSurge', W.surge_at], ['markKraken', W.kraken_at]]) {
    const el = $(id);
    const f = `${(at / W.kraken_at) * 100}%`;
    if (land) { el.style.bottom = f; el.style.left = ''; } else { el.style.left = f; el.style.bottom = ''; }
  }
  setMeter(meterVal, false);
}
function setMeter(v, sound = true) {
  if (!W) return;
  if (sound && v > meterVal) { sfx.meter(); const m = $('meter'); m.classList.remove('pulse'); void m.offsetWidth; m.classList.add('pulse'); }
  const was = meterVal;
  meterVal = v;
  const f = `calc(max(0px, ${clamp(v / W.kraken_at, 0, 1) * 100}% - 4px))`;
  const fill = $('meterFill');
  if ($('play').classList.contains('land')) { fill.style.height = f; fill.style.width = ''; }
  else { fill.style.width = f; fill.style.height = ''; }
  $('meterNum').textContent = String(v);
  $('markSurge').classList.toggle('lit', v >= W.surge_at);
  $('markKraken').classList.toggle('lit', v >= W.kraken_at);
  $('meter').classList.toggle('over', v >= W.surge_at);
  $('boardFrame').classList.toggle('charged', v >= W.surge_at);
  if (sound && ((was < W.surge_at && v >= W.surge_at) || (was < W.kraken_at && v >= W.kraken_at))) sfx.charged();
}

// ================================================================== the game flow
async function doSpin(mode = 'spin') {
  if (busy || !W) return;
  if (mode !== 'spin' && buyTooBig()) {
    toast(`Bonus buys go up to a bet of ${betText(W.buy_max_bet)}.`); sfx.error(); return;
  }
  const cost = stakeFor(mode);
  if (cost > balance) {
    toast(balance < W.min_bet ? "You're out of credits."
      : mode === 'spin' ? "You don't have enough credits for that bet."
        : `You don't have enough credits for that: ${W.buys[mode].name} costs ${amt(cost)}.`);
    sfx.error(); setAuto(false); return;
  }
  setBusy(true);
  skipRequested = false;
  clearView();
  setMeter(0, false);
  $('boardFrame').classList.remove('kraken');
  setWinText(mode === 'spin' ? 'Good luck!' : `${W.buys[mode].name}!`);
  setShownBalance(minus(shownBalance ?? balance, cost));
  sfx.spinStart();
  const answer = new Promise((res) => { waiter = res; });
  if (!send({ type: 'spin', bet, mode })) { waiter = null; setBusy(false); setShownBalance(balance); toast('Not connected.'); return; }
  const out = dropOut();
  const round = await answer;
  await out;
  if (!round) {   // refused: put symbols back
    await dropIn(cells.map((col) => col.map((cl) => cl.sym || randomSym())));
    setShownBalance(balance);
    setBusy(false);
    return;
  }
  try {
    await playRound(round);
  } catch (e) {
    console.error(e);
    toast('Something went wrong showing that spin.');
    syncTo(round.steps.filter((s) => s.grid).pop().grid);
    finishUp(round);
  }
}

function clearView() {
  view.hl = new Map(); view.dim = false; view.labels = []; view.swirl = null;
}

// all symbols fall out of the bottom
function dropOut() {
  const jobs = [];
  for (let col = 0; col < COLS; col++) for (let r = 0; r < ROWS; r++) {
    jobs.push(tween(cells[col][r], { dy: ROWS + 1 }, T(260), ease.in, T(col * 30 + (ROWS - 1 - r) * 12)));
  }
  if (view.kraken) jobs.push(tween(view.kraken, { dy: ROWS + 1 }, T(260), ease.in));
  return Promise.all(jobs).then(() => { view.kraken = null; });
}
// a new grid drops in from the top, column by column
async function dropIn(grid) {
  const jobs = [];
  for (let col = 0; col < COLS; col++) {
    for (let r = 0; r < ROWS; r++) {
      const cl = cells[col][r];
      Object.assign(cl, { sym: grid[col][r], dy: -(ROWS + 1), sc: 1, a: 1, flash: 0 });
      jobs.push(tween(cl, { dy: 0 }, T(380), ease.land, T(col * 55 + (ROWS - 1 - r) * 22)));
    }
    sleep(T(col * 55 + 300)).then(() => sfx.land(col));
  }
  await Promise.all(jobs);
}
function syncTo(grid) {
  for (let col = 0; col < COLS; col++) for (let r = 0; r < ROWS; r++) {
    Object.assign(cells[col][r], { sym: grid[col][r], dy: 0, sc: 1, a: 1, flash: 0 });
  }
}

async function playRound(round) {
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a bonus buy's stake is its price)
  let xSum = 0, cascade = 0;
  for (const step of round.steps) {
    if (step.t === 'drop') {
      if (step.meter) setMeter(step.meter);   // a bonus buy starts with the Tide Meter charged
      await dropIn(step.grid);
    }
    else if (step.t === 'win') {
      cascade++;
      xSum += step.x;
      await showWin(step, stake, cascade, Math.min(credits(stake, xSum), round.payout));
    } else if (step.t === 'fall') await showFall(step);
    else if (step.t === 'surge') await showSurge(step);
    else if (step.t === 'kraken') { await showKraken(step); send({ type: 'announce', round: round.id, what: 'kraken' }); }
    else if (step.t === 'bubble') await showBubble(step);
    if (step.grid && step.t !== 'drop') syncTo(step.grid);
  }
  await finishUp(round);
}

let shownWin = 0;
async function showWin(step, stake, cascade, total) {
  const hl = new Map();
  view.labels = [];
  const now = performance.now();
  const used = new Set();
  for (const cl of step.clusters) {
    for (const [col, r] of cl.cells) {
      const s = cells[col][r].sym;
      if (!hl.has(key(col, r)) || (s !== 'wild' && s !== 'kraken')) hl.set(key(col, r), COLOR[cl.sym]);
    }
    const cx0 = cl.cells.reduce((a, [col]) => a + col, 0) / cl.cells.length;
    const cy0 = cl.cells.reduce((a, [, r]) => a + r, 0) / cl.cells.length;
    // the label goes on the cluster's own symbol nearest its middle, away from the other clusters' labels
    const own = cl.cells.filter(([col, r]) => cells[col][r].sym === cl.sym);
    const spots = (own.length ? own : cl.cells).filter(([col, r]) => !used.has(key(col, r)) && !used.has(key(col - 1, r)) && !used.has(key(col + 1, r)));
    const pool = spots.length ? spots : (own.length ? own : cl.cells);
    const [lc, lr] = pool.reduce((best, cc) => ((cc[0] - cx0) ** 2 + (cc[1] - cy0) ** 2 < (best[0] - cx0) ** 2 + (best[1] - cy0) ** 2 ? cc : best));
    used.add(key(lc, lr));
    view.labels.push({ x: lc, y: lr, text: amt(credits(stake, cl.x)), t0: now });
  }
  view.hl = hl; view.dim = true;
  if (view.kraken) view.kraken.lit = step.clusters.some((cl) => cl.cells.some(([col, r]) => cells[col][r].sym === 'kraken'));
  const big = step.x >= 5;
  sfx.win(cascade, big);
  const desc = step.clusters.length === 1
    ? `${step.clusters[0].size}× ${W.names[step.clusters[0].sym]}`
    : `${step.clusters.length} clusters`;
  $('winText').textContent = '';
  $('winBar').classList.add('won');
  const el = document.createElement('span');
  $('winText').append(`${cascade > 1 ? `Cascade ${cascade} · ` : ''}${desc} · WIN `, el);
  setMeter(step.meter);
  await countUp(el, shownWin, total, T(clamp(300 + (total - shownWin) / stake * 120, 350, 1400)));
  shownWin = total;
  setLastWin(total);
  await sleep(T(320));
  // pop the cluster cells (not the Kraken, not the cells that become Pearl Wilds)
  const pearls = new Set(step.pearls.map(([col, r]) => key(col, r)));
  const jobs = [];
  sfx.pop();
  for (const k of hl.keys()) {
    const [col, r] = k.split(',').map(Number);
    const cl = cells[col][r];
    if (cl.sym === 'kraken' || pearls.has(k)) continue;
    sparks(col, r, 8, [COLOR[cl.sym] || '#fff', '#ffffff']);
    jobs.push(tween(cl, { sc: 1.3 }, T(110)).then(() => tween(cl, { sc: 0, a: 0 }, T(170), ease.in)));
  }
  for (const k of pearls) {
    const [col, r] = k.split(',').map(Number);
    const cl = cells[col][r];
    jobs.push(tween(cl, { sc: 0.4 }, T(150), ease.in).then(() => {
      cl.sym = 'wild'; cl.flash = 1; sfx.wild(); sparks(col, r, 14, ['#ffffff', '#4fe0d0', '#ffe08a']);
      return Promise.all([tween(cl, { sc: 1 }, T(260), ease.land), tween(cl, { flash: 0 }, T(400))]);
    }));
  }
  await Promise.all(jobs);
  view.hl = new Map(); view.dim = false; view.labels = [];
  // the Kraken shrinks or splits after a win it was part of
  const old = view.kraken;
  if (old && old.lit) {
    old.lit = false;
    const nk = step.kraken;
    const oldCells = [];
    for (let col = old.c; col < old.c + old.size; col++) for (let r = ROWS - old.size; r < ROWS; r++) oldCells.push([col, r]);
    if (nk) {
      sfx.thud();
      await tween(old, { sc: 0.82 }, T(220), ease.in);
      view.kraken = { c: nk.c, size: nk.size, dy: 0, sc: 1.2, a: 1 };
      for (const [col, r] of oldCells) {
        const inside = col >= nk.c && col < nk.c + nk.size && r >= ROWS - nk.size;
        cells[col][r].sym = inside ? 'kraken' : null;
      }
      await tween(view.kraken, { sc: 1 }, T(260), ease.land);
    } else {
      sfx.wild();
      await tween(old, { sc: 1.15, a: 0 }, T(300));
      view.kraken = null;
      for (const [col, r] of oldCells) { const cl = cells[col][r]; Object.assign(cl, { sym: 'wild', a: 1, sc: 1, flash: 1 }); tween(cl, { flash: 0 }, T(450)); sparks(col, r, 10); }
      await sleep(T(300));
    }
  }
  // what's been removed is now empty
  for (const k of hl.keys()) {
    const [col, r] = k.split(',').map(Number);
    const cl = cells[col][r];
    if (cl.sym !== 'kraken' && !pearls.has(k)) cl.sym = null;
  }
}

async function showFall(step) {
  const jobs = [];
  let any = false;
  for (let col = 0; col < COLS; col++) {
    let moved = false;
    for (let r = 0; r < ROWS; r++) {
      const src = step.moves[col][r];
      const cl = cells[col][r];
      const sym = step.grid[col][r];
      if (src === r && cl.sym === sym) continue;
      Object.assign(cl, { sym, dy: src - r, sc: 1, a: 1, flash: 0 });
      const dist = r - src;
      moved = true;
      jobs.push(tween(cl, { dy: 0 }, T(170 + 55 * Math.sqrt(dist)), ease.land, T(col * 18)));
    }
    if (moved) { any = true; sleep(T(col * 18 + 200)).then(() => sfx.land(col)); }
  }
  if (any) sfx.fall();
  await Promise.all(jobs);
  await sleep(T(120));
}

const SURGE_BANNER = {
  whirlpool: ['WHIRLPOOL!', 'Symbols swirl into wilds'],
  riptide: ['RIPTIDE!', 'A wild washes out its row and column'],
  undertow: ['UNDERTOW!', 'The low symbols get swept away'],
  shift: ['CORAL SHIFT!', 'One kind turns into another'],
};
async function showSurge(step) {
  const [main, sub] = SURGE_BANNER[step.kind];
  sfx.surge();
  setWinText(`🌊 SURGE: ${main}`, true);
  await banner(main, sub, 1250, 'surge');
  if (step.kind === 'whirlpool') {
    view.swirl = { t0: performance.now() };
    for (const [col, r] of step.cells) {
      const cl = cells[col][r];
      await tween(cl, { sc: 0.2 }, T(140), ease.in);
      cl.sym = 'wild'; cl.flash = 1; sfx.wild(); sparks(col, r, 12);
      tween(cl, { sc: 1 }, T(280), ease.land); tween(cl, { flash: 0 }, T(420));
      await sleep(T(120));
    }
    await sleep(T(450));
    view.swirl = null;
  } else if (step.kind === 'riptide') {
    const [c0, r0] = step.centre;
    const centre = cells[c0][r0];
    await tween(centre, { sc: 0.2 }, T(140), ease.in);
    centre.sym = 'wild'; centre.flash = 1; sfx.wild(); sparks(c0, r0, 18);
    await Promise.all([tween(centre, { sc: 1 }, T(260), ease.land), tween(centre, { flash: 0 }, T(400))]);
    sfx.wash();
    const order = step.cells.slice().sort((a, b) => (Math.abs(a[0] - c0) + Math.abs(a[1] - r0)) - (Math.abs(b[0] - c0) + Math.abs(b[1] - r0)));
    await Promise.all(order.map(([col, r]) => {
      const d = Math.abs(col - c0) + Math.abs(r - r0);
      return sleep(T(d * 70)).then(() => { bubbles(col, r, 4); return tween(cells[col][r], { a: 0, sc: 0.4 }, T(200), ease.in); });
    }));
    for (const [col, r] of step.cells) cells[col][r].sym = null;
  } else if (step.kind === 'undertow') {
    sfx.wash();
    await Promise.all(step.cells.map(([col, r]) => sleep(T(col * 40 + Math.random() * 60)).then(() => {
      bubbles(col, r, 3); return tween(cells[col][r], { a: 0, dy: 0.35 }, T(260), ease.in);
    })));
    for (const [col, r] of step.cells) Object.assign(cells[col][r], { sym: null, dy: 0 });
  } else if (step.kind === 'shift') {
    sfx.shift();
    for (const [col, r] of step.cells) { const cl = cells[col][r]; tween(cl, { flash: 1 }, T(260)); }
    await sleep(T(320));
    for (const [col, r] of step.cells) { const cl = cells[col][r]; cl.sym = step.to; sparks(col, r, 6, [COLOR[step.to], '#ffffff']); tween(cl, { flash: 0 }, T(380)); }
    await sleep(T(450));
  }
}

async function showKraken(step) {
  const k = step.kraken;
  if (autoOn) { /* keep auto going: there is nothing to choose */ }
  sfx.kraken();
  $('boardFrame').classList.add('kraken');
  setWinText('🐙 THE KRAKEN RISES!', true);
  await banner('THE KRAKEN RISES!', 'A giant 3×3 wild', 1500, 'kraken');
  view.kraken = { c: k.c, size: k.size, dy: -(ROWS + 1), sc: 1, a: 1, lit: false };
  for (let col = k.c; col < k.c + k.size; col++) {
    for (let r = ROWS - k.size; r < ROWS; r++) tween(cells[col][r], { a: 0, sc: 0.5 }, T(250), ease.in, T(380));
  }
  await tween(view.kraken, { dy: 0 }, T(650), ease.land);
  sfx.thud();
  const f = $('boardFrame'); f.classList.remove('shake'); void f.offsetWidth; f.classList.add('shake');
  for (let col = k.c; col < k.c + k.size; col++) {
    for (let r = ROWS - k.size; r < ROWS; r++) { Object.assign(cells[col][r], { sym: 'kraken', a: 1, sc: 1 }); sparks(col, r, 8, ['#d9a6ff', '#ffffff', '#b26bff']); }
  }
  await sleep(T(600));
}

async function showBubble(step) {
  sfx.bubble();
  setWinText(`🫧 BUBBLE BURST! Every ${W.names[step.sym]} pops`, true);
  for (const [col, r] of step.cells) bubbles(col, r, 7);
  await Promise.all(step.cells.map(([col, r]) => tween(cells[col][r], { sc: 1.2 }, T(260))));
  sfx.pop();
  await Promise.all(step.cells.map(([col, r]) => tween(cells[col][r], { sc: 0, a: 0 }, T(160), ease.in)));
  for (const [col, r] of step.cells) cells[col][r].sym = null;
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
  view.hl = new Map(); view.dim = false; view.labels = [];
  if (pay > 0) {
    sfx.countEnd();
    const perBet = round.bet ?? round.stake;
    if (pay / perBet >= W.big_win) {
      await bigWin(pay, pay / perBet);
      send({ type: 'announce', round: round.id, what: 'win' });
    }
    setWinText(`${round.cascades > 1 ? `${round.cascades} cascades · ` : ''}TOTAL WIN ${amt(pay)}`, true);
  } else {
    setWinText(round.surge ? 'The tide turned… no win this time' : 'No win this time');
  }
  setLastWin(pay);
  shownWin = 0;
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
