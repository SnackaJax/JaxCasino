// Banger Bash client: every player spins their own slot; the server (activity/banger.py) decides every result
// and this page animates it. Sounds come from banger-sound.js, sign-in from auth.js.
import { signIn, retryAfterExpiredSession, go } from './auth.js';
import { sfx, unlock, isMuted, setMuted } from './banger-sound.js';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmt = (n) => Math.round(n).toLocaleString('en-US');
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const BET_STEPS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000, 500000,
  1e6, 2.5e6, 5e6, 1e7, 2.5e7, 5e7, 1e8, 2.5e8, 5e8, 1e9];
const LINE_COLORS = ['#ff5a5a', '#5ac8ff', '#78f078', '#ffc83c', '#ff78dc', '#a082ff', '#ff9646', '#50f0d2', '#e6e65a', '#ff6e96',
  '#6eaaff', '#beff6e', '#ffaa78', '#c878ff', '#6effaa', '#ffe696', '#96dcff', '#ff8cbe', '#b4c878', '#f0aaff'];
const HIGH_BG = { king: ['#5a420e', '#be9628'], hotdog: ['#601a16', '#b43c32'], brat: ['#563212', '#aa6e32'] };

{
  const p = new URLSearchParams(location.search);
  if (p.get('platform') === 'mobile' || p.get('mobile') === '1') document.body.classList.add('discord-mobile');
}

// ------------------------------------------------------------------ state
let W = null;                 // welcome: paytable, settings
let myId = null;
let ws = null, session = null, joinInfo = null, fatal = false, retry = 0;
let balance = 0;              // what the server says
let shownBalance = null;      // what the header shows (held back until a win has been shown)
let bet = 100;
let busy = false;             // a round is being played or shown
let flow = 0;                 // bumps when a round's presentation must stop (reconnect)
let turbo = false;
let autoOn = false;
let players = [];
let feedItems = [];
let resumeSeen = false;
const settled = new Map();    // round id -> settled message
let waiters = { round: null, bonus: null, settled: null };
try { bet = Number(localStorage.getItem('banger-bet')) || 100; } catch (e) { /* storage blocked */ }
try { turbo = localStorage.getItem('banger-turbo') === '1'; } catch (e) { /* storage blocked */ }
const T = (ms) => (turbo ? ms * 0.5 : ms);

class Aborted extends Error {}
function guard(token) { if (token !== flow) throw new Aborted(); }
const credits = (stake, x) => Math.floor(stake * x * W.scale + 1e-9);

// ------------------------------------------------------------------ sprites
const IMG = {};
const BLUR = {};
const BIG = {};
function loadImg(src) {
  return new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = src; });
}
async function loadSprites() {
  const keys = ['ketchup', 'mustard', 'pickle', 'onion', 'chorizo', 'brat', 'hotdog', 'king', 'wild', 'grill', 'bun'];
  await Promise.all(keys.flatMap((k) => [
    loadImg(`/static/banger/${k}.png`).then((i) => { IMG[k] = i; }),
    loadImg(`/static/banger/${k}_blur.png`).then((i) => { BLUR[k] = i; }),
    ...(['wild', 'grill', 'bun'].includes(k) ? [] : [loadImg(`/static/banger/${k}_big.png`).then((i) => { BIG[k] = i; })]),
  ]));
}

// ------------------------------------------------------------------ sign in & connect
function loading(text) { $('loading').classList.remove('hidden'); $('loadingText').textContent = text; }
function showFatal(text) {
  fatal = true;
  $('loading').classList.remove('hidden');
  $('loading').querySelector('.spinner').classList.add('hidden');
  $('loadingText').textContent = text;
}
async function boot() {
  loading('Lighting the grill…');
  const [auth] = await Promise.all([signIn(loading), loadSprites()]);
  if (auth.error) { showFatal(auth.error); return; }
  session = auth.session; joinInfo = auth.joinInfo;
  connect();
}
function connect() {
  loading('Joining the barbecue…');
  resumeSeen = false;
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/banger`);
  ws.onopen = () => { retry = 0; ws.send(JSON.stringify({ type: 'join', session, ...joinInfo })); };
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    if (fatal) return;
    loading('Connection lost, reconnecting…');
    // whatever was waiting for the server won't get an answer on this connection
    for (const k of Object.keys(waiters)) { if (waiters[k]) { waiters[k].resolve(null); waiters[k] = null; } }
    setTimeout(connect, Math.min(8000, 1000 * 2 ** retry++));
  };
}
function send(obj) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; } return false; }
setInterval(() => send({ type: 'ping' }), 25000);

function waitFor(kind, extra = {}) {
  return new Promise((resolve) => { waiters[kind] = { resolve, ...extra }; });
}
function resolveWaiter(kind, value) {
  const w = waiters[kind];
  if (w) { waiters[kind] = null; w.resolve(value); return true; }
  return false;
}

function onMessage(m) {
  switch (m.type) {
    case 'welcome':
      W = m; myId = m.you; setupInfo(); setupBuys(); fitBet(); renderBet();
      $('loading').classList.add('hidden');
      break;
    case 'me':
      balance = m.balance;
      if (!busy || shownBalance === null) setShownBalance(balance);
      renderBroke(); renderBuys();
      break;
    case 'round':
      balance = m.balance;
      if (m.round && m.resume) {
        resumeSeen = true;
        resolveWaiter('round', null);
        resumeRound(m.round);
      } else if (!resolveWaiter('round', m)) { /* nobody asked: ignore */ }
      break;
    case 'bonus': resolveWaiter('bonus', m); break;
    case 'settled':
      balance = m.balance;
      settled.set(m.round, m);
      if (waiters.settled && waiters.settled.round === m.round) resolveWaiter('settled', m);
      break;
    case 'players': players = m.players; renderPlayers(); break;
    case 'feed_all':
      feedItems = m.items || []; renderFeed();
      // after a reconnect: if our round finished meanwhile, the server sends no round; stop showing the old one
      if (busy && !resumeSeen) abortFlow();
      break;
    case 'feed': onFeed(m.item); break;
    case 'restart': onRestart(m); break;
    case 'error':
      toast(m.message); sfx.error();
      resolveWaiter('round', null); resolveWaiter('bonus', null);
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
// big balances are shortened on phones (247.2M) so the header fits; the full number is in the tooltip
function shortAmount(n) {
  for (const [d, u] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M']]) {
    const x = n / d, t = +x.toFixed(x >= 100 ? 1 : 2);
    if (t >= 1) return `${t}${u}`;
  }
  return fmt(n);
}
// a bet: in full below a million, short from there (2.5M, 1B)
const betText = (v) => (v >= 1e6 ? shortAmount(v) : fmt(v));
// a win or a price: short from a billion, and from a million on phones
const amt = (v) => (v >= 1e9 || (v >= 1e6 && window.innerWidth < 520) ? shortAmount(v) : fmt(v));
function setShownBalance(v, bumpIt = false) {
  shownBalance = v;
  $('balance').textContent = window.innerWidth < 520 && v >= 1e6 ? shortAmount(v) : fmt(v);
  $('balance').parentElement.title = `${fmt(v)} credits`;
  if (bumpIt) { const b = $('balance').parentElement; b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }
}
function setWinText(text, won = false) { $('winText').textContent = text; $('winBar').classList.toggle('won', won); }
function setLastWin(v) { $('lastWin').textContent = amt(v); $('lastWin').title = `${fmt(v)} credits`; $('lastWin').classList.toggle('on', v > 0); }
function banner(main, sub = '', ms = 1600) {
  $('bannerMain').textContent = main; $('bannerSub').textContent = sub;
  const b = $('banner'); b.classList.add('hidden'); void b.offsetWidth; b.classList.remove('hidden');
  return sleep(T(ms)).then(() => b.classList.add('hidden'));
}
function hideScenes() { for (const id of ['ladderScene', 'potatoScene', 'picnicScene', 'goldenScene']) $(id).classList.add('hidden'); }
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
  if (busy) { toast('Finish this round first.'); return; }
  go('/');
});
document.addEventListener('pointerdown', () => unlock(), { once: true });

// ------------------------------------------------------------------ bet & controls
function allowedSteps() {
  if (!W) return BET_STEPS;
  return BET_STEPS.filter((v) => v >= W.min_bet && (!W.max_bet || v <= W.max_bet));
}
function fitBet() {
  const steps = allowedSteps();
  if (!steps.includes(bet)) bet = steps.reduce((a, b) => (Math.abs(b - bet) < Math.abs(a - bet) ? b : a), steps[0]);
}
function renderBet() {
  $('bet').textContent = betText(bet);
  $('bet').title = `${fmt(bet)} credits`;
  try { localStorage.setItem('banger-bet', String(bet)); } catch (e) { /* storage blocked */ }
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
  try { localStorage.setItem('banger-turbo', turbo ? '1' : '0'); } catch (e) { /* storage blocked */ }
});
renderTurbo();

// broke: the same restart as /restart
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
  if (m.status === 'ok') { toast(`Back in the game! +${fmt(m.amount)} credits`, true); sfx.win(1); }
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
function statusText(p) {
  const tier = p.feature && W ? (W.tiers.find((t) => t.key === p.feature) || {}).name : null;
  switch (p.status) {
    case 'spinning': return ['spinning…', false];
    case 'ladder': return [`🔥 in the Grill Bonus${tier ? ` (${tier})` : ''}`, true];
    case 'idle': return [p.last_win ? `last win ${amt(p.last_win)}` : (p.online ? 'at the grill' : 'away'), false];
    default: return [`🔥 playing ${tier || 'a bonus'}`, true];
  }
}
function renderPlayers() {
  $('playerCount').textContent = players.length ? `(${players.length})` : '';
  $('players').innerHTML = players.map((p) => {
    const [text, hot] = statusText(p);
    return `<div class="player${p.id === myId ? ' me' : ''}${p.online ? '' : ' offline'}${hot ? ' hot' : ''}">${avatarHtml(p)}
      <div class="p-info"><div class="p-name">${esc(p.name)}${p.id === myId ? ' (you)' : ''}</div>
      <div class="p-status${hot ? ' hot' : ''}">${esc(text)}</div></div></div>`;
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
  el.innerHTML = `${item.kind === 'win' ? '🎉' : '🔥'} <b>${esc(item.name)}</b> ${esc(item.text)}${item.amount ? ` <b>${amt(item.amount)}</b>` : ''}`;
  $('feedPop').prepend(el);
  setTimeout(() => el.remove(), 5200);
  sfx.feed();
}

// ------------------------------------------------------------------ info / paytable
function setupInfo() {
  const sym = (k) => W.symbols[k];
  const order = ['king', 'hotdog', 'brat', 'chorizo', 'onion', 'pickle', 'mustard', 'ketchup'];
  const mult = (x) => `${(Math.round(x * 100) / 100).toString()}x`;
  const pays = order.map((k) => `<div class="pt"><img src="/static/banger/${k}.png" alt="">
    <div><b>${esc(sym(k).name)}</b><div class="pays">${sym(k).pays.map((p, i) => `${i + 3}: <b>${mult(p)}</b>`).join(' · ')}</div></div></div>`).join('');
  const feats = Object.values(W.features).map((f) => `<li><b>${esc(f.name)}</b>: ${esc(f.text)}</li>`).join('');
  const tiers = W.tiers.map((t, i) => `<li><b>${i + 1}. ${esc(t.name)}</b>: ${esc(t.text)} (average ${Math.round(t.avg)}x your bet)</li>`).join('');
  $('infoBody').innerHTML = `
    <p>Your bet covers all 20 lines. A line pays for 3 to 5 matching symbols in a row from the leftmost reel. Pays are
    times your total bet; wins on different lines add up.</p>
    <div class="paytable">${pays}
      <div class="pt"><img src="/static/banger/wild.png" alt=""><div><b>Flame Wild</b><div class="pays">Reels 2–5. Stands in for everything but the grill.</div></div></div>
      <div class="pt"><img src="/static/banger/grill.png" alt=""><div><b>Grill</b><div class="pays">3 or more anywhere start the Grill Bonus.</div></div></div>
    </div>
    <h3>Random features (about 1 spin in ${Math.round(1 / W.feature_chance)})</h3><ul>${feats}</ul>
    <h3>The Grill Bonus</h3>
    <p>The Grill Wheel picks one of five features. 4 or 5 grills skip the lowest rungs.</p><ul>${tiers}</ul>
    <p><b>Turn up the heat:</b> before playing, you can gamble up to ${W.max_gambles} times. Win and your feature climbs a
    rung; lose and it drops one (from Hot Potato, the bonus burns). The odds are set so a gamble is worth exactly what you
    risk, on average.</p>
    ${Object.keys(W.buys).length ? `<h3>Bonus buys</h3>
    <p>BUY skips the wait: one spin that is sure to land the grills, straight into the Grill Bonus (turning up the heat
    works as usual). ${Object.values(W.buys).map((b) => `<b>${esc(b.name)}</b> (${b.grills} grills) costs ${b.price}x your bet and
    pays back ${(b.rtp * 100).toFixed(1)}%.`).join(' ')}${W.buy_max_bet ? ` Bonus buys go up to a bet of ${betText(W.buy_max_bet)}.` : ''}</p>` : ''}
    <h3>Everyone at the barbecue</h3>
    <p>Everybody plays their own machine. Everyone in this server with Banger Bash open is at the same barbecue, whether they
    started it in a voice call or with /bangerbash in a text channel. The panel on the right shows what the others are doing,
    and big wins (${W.big_win}x and up) and bonus triggers are announced to everyone.</p>
    <p>Maximum win ${fmt(W.max_win)}x your bet per spin. Banger Bash returns ${(W.rtp * 100).toFixed(0)}% of bets on average.
    Space bar spins. Turbo speeds up the animations; Auto keeps spinning until you stop it or a bonus starts.</p>`;
}
$('infoBtn').addEventListener('click', () => { sfx.click(); $('infoModal').classList.remove('hidden'); });

// ------------------------------------------------------------------ bonus buys
// a buy never costs less than its price: rounded up, like the server does
const stakeFor = (mode) => Math.max(1, Math.ceil(bet * (mode === 'spin' ? 1 : W.buys[mode].price) - 1e-9));
function setupBuys() {
  const has = W && Object.keys(W.buys || {}).length > 0;
  $('buyBtn').classList.toggle('hidden', !has);
  if (!has) return;
  $('buys').innerHTML = Object.entries(W.buys).map(([k, b]) => `<button class="buy" data-mode="${k}">
    <div class="b-icons">${'<img src="/static/banger/grill.png" alt="">'.repeat(b.grills)}</div>
    <div class="b-name">${esc(b.name)}</div><div class="b-text">${esc(b.text)}</div>
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

// ================================================================== the reels (canvas)
const CW = 140, CH = 124, REELS = 5, ROWS = 3;
const RW = CW * REELS, RH = CH * ROWS;
const canvas = $('reels');
const cx = canvas.getContext('2d');
const fx = $('fx');
const fxc = fx.getContext('2d');
let dpr = 1;
const FILLER = ['ketchup', 'mustard', 'pickle', 'onion', 'chorizo', 'brat', 'hotdog', 'king', 'wild', 'ketchup', 'mustard', 'pickle',
  'onion', 'chorizo', 'brat', 'ketchup', 'mustard', 'pickle', 'onion', 'grill', 'hotdog', 'chorizo', 'brat', 'king'];
function randomFiller() { const a = FILLER.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }
function randomGrid() {
  return Array.from({ length: REELS }, (_, r) => Array.from({ length: ROWS }, () => {
    const pool = ['ketchup', 'mustard', 'pickle', 'onion', 'chorizo', 'brat', 'hotdog', 'king'];
    return pool[Math.floor(Math.random() * pool.length)];
  }));
}

// what the reels show (when still) and the effects drawn on top
const view = {
  grid: randomGrid(),
  lit: new Set(),        // "r,row" cells glowing orange (just turned wild)
  links: [],             // Twin Links cells
  mega: null,            // { reel, size, sym, t0 }
  win: new Set(),        // highlighted cells
  dim: false,
  lines: [],             // [{ line, label }]
  grills: new Set(),
  tease: -1,             // reel being teased (near miss)
  flash: new Map(),      // "r,row" -> time a flash started (cells changing)
  bunReveal: null,       // { cells:Set, sym, t0 }
  free: false,
};
const reels = Array.from({ length: REELS }, () => ({ mode: 'still', o: 0, speed: 0, filler: randomFiller(), final: null, t0: 0, dur: 0, S: 0, done: null }));
const key = (r, row) => `${r},${row}`;

function resize() {
  dpr = Math.min(2, window.devicePixelRatio || 1);
  const wrap = $('reelsWrap');
  const pad = window.innerWidth < 520 ? 12 : 20;   // the wooden frame's padding on each side, plus a little air
  const availW = wrap.clientWidth - pad - 4, availH = wrap.clientHeight - pad - 4;
  const s = Math.max(0.2, Math.min(availW / RW, availH / RH));
  const cssW = Math.floor(RW * s), cssH = Math.floor(RH * s);
  canvas.style.width = `${cssW}px`;
  canvas.style.height = `${cssH}px`;
  canvas.width = Math.max(1, Math.round(cssW * dpr));
  canvas.height = Math.max(1, Math.round(cssH * dpr));
  const st = $('stage');
  fx.width = st.clientWidth * dpr; fx.height = st.clientHeight * dpr;
  if (shownBalance !== null) setShownBalance(shownBalance);   // long or short form depends on the width
}
new ResizeObserver(resize).observe($('stage'));
window.addEventListener('resize', resize);

function roundRect(c, x, y, w, h, r) {
  c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}

// one reel's symbol at strip position k while it moves
function stripSym(rl, k) {
  if (rl.mode === 'stopping' && k >= 0 && k < ROWS) return rl.final[k];
  const n = rl.filler.length;
  return rl.filler[((k % n) + n) % n];
}

function drawSymbol(sym, x, y, w = CW, h = CH, blur = false, scale = 1) {
  const img = (blur ? BLUR[sym] : IMG[sym]) || IMG[sym];
  if (!img) return;
  const sw = w * scale, sh = h * scale;
  cx.drawImage(img, x + (w - sw) / 2, y + (h - sh) / 2, sw, sh);
}

function drawCellBg(r, row, sym, now) {
  const x = r * CW, y = row * CH;
  let fill = '#38241a', edge = '#5c3e28', lw = 2;
  if (HIGH_BG[sym]) [fill, edge] = HIGH_BG[sym];
  const k = key(r, row);
  if (view.lit.has(k)) { fill = '#6e280e'; edge = '#ff821e'; lw = 4; }
  if (view.links.some(([a, b]) => a === r && b === row)) { fill = '#6e280e'; edge = '#ffc828'; lw = 4; }
  roundRect(cx, x + 4, y + 4, CW - 8, CH - 8, 12);
  cx.fillStyle = fill; cx.fill();
  cx.lineWidth = lw; cx.strokeStyle = edge; cx.stroke();
}

let lastFrame = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - lastFrame) / 1000);
  lastFrame = now;
  const s = canvas.width / RW;
  cx.setTransform(s, 0, 0, s, 0, 0);
  cx.clearRect(0, 0, RW, RH);
  cx.fillStyle = view.free ? '#2a120c' : '#1e1410';
  cx.fillRect(0, 0, RW, RH);
  let anySpinning = false;
  const megaCells = new Set();
  if (view.mega) for (let r = view.mega.reel; r < view.mega.reel + 3; r++) for (let row = 0; row < 3; row++) megaCells.add(key(r, row));

  for (let r = 0; r < REELS; r++) {
    const rl = reels[r];
    cx.save();
    cx.beginPath(); cx.rect(r * CW, 0, CW, RH); cx.clip();
    // Move the reel before drawing it, so the frame in which it lands is already drawn as a stopped reel with its
    // real symbols. (Moving it inside the drawing code drew that one frame with filler symbols: a visible flash.)
    if (rl.mode === 'spinning') {
      rl.o -= rl.speed * dt;
    } else if (rl.mode === 'stopping') {   // ease from S down to 0 with a little bounce
      const p = clamp((now - rl.t0) / rl.dur, 0, 1);
      const c1 = 1.1, c3 = c1 + 1;
      const e = 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2);
      rl.o = rl.S * (1 - e);
      if (p >= 1) {
        rl.mode = 'still'; rl.o = 0;
        view.grid[r] = rl.final.slice();
        if (rl.done) { const d = rl.done; rl.done = null; d(); }
      }
    }
    if (rl.mode === 'still') {
      for (let row = 0; row < ROWS; row++) {
        if (megaCells.has(key(r, row))) continue;
        let sym = view.grid[r][row];
        drawCellBg(r, row, sym, now);
        let scale = 1;
        const k = key(r, row);
        if (view.win.has(k)) scale = 1 + 0.07 * Math.sin(now / 110);
        if (view.bunReveal && view.bunReveal.cells.has(k)) {
          const p = (now - view.bunReveal.t0) / T(700);
          if (p < 0.5) { sym = 'bun'; scale = 1 + 0.1 * Math.sin(now / 40) * (1 - p); } else { sym = view.bunReveal.sym; scale = Math.min(1.2, 0.6 + p * 0.6); }
        }
        drawSymbol(sym, r * CW, row * CH, CW, CH, false, scale);
        const fl = view.flash.get(k);
        if (fl !== undefined) {
          const p = (now - fl) / 450;
          if (p < 1) { cx.globalAlpha = 1 - p; cx.fillStyle = '#fff6c8'; roundRect(cx, r * CW + 4, row * CH + 4, CW - 8, CH - 8, 12); cx.fill(); cx.globalAlpha = 1; }
        }
        if (view.dim && !view.win.has(k) && !view.lit.has(k)) {
          cx.fillStyle = 'rgba(12,6,4,0.62)'; cx.fillRect(r * CW, row * CH, CW, CH);
        }
      }
    } else {
      anySpinning = true;
      for (let row = 0; row < ROWS; row++) drawCellBg(r, row, '', now);
      const speedNow = rl.mode === 'spinning' ? rl.speed : rl.S / (rl.dur / 1000) * 3 * (1 - clamp((now - rl.t0) / rl.dur, 0, 1));
      const blurry = speedNow > 7;
      const base = Math.floor(rl.o);
      for (let k = base - 1; k <= base + ROWS; k++) {
        const y = (k - rl.o) * CH;
        if (y > RH || y < -CH) continue;
        drawSymbol(stripSym(rl, k), r * CW, y, CW, CH, blurry);
      }
    }
    cx.restore();
    if (r > 0 && !(view.mega && r > view.mega.reel && r < view.mega.reel + 3)) {
      cx.fillStyle = '#140c08'; cx.fillRect(r * CW - 1, 0, 2, RH);
    }
  }
  if (anySpinning) sfx.spinTick(turbo ? 1.6 : 1);

  // Mega Banger: one giant symbol over three reels
  if (view.mega) {
    const { reel, sym, t0 } = view.mega;
    const p = clamp((now - t0) / 500, 0, 1);
    const x = reel * CW, w = CW * 3;
    cx.save();
    cx.shadowColor = 'rgba(255,200,60,0.9)'; cx.shadowBlur = 30;
    roundRect(cx, x + 4, 4, w - 8, RH - 8, 16);
    cx.fillStyle = (HIGH_BG[sym] || ['#38241a'])[0]; cx.fill();
    cx.lineWidth = 4; cx.strokeStyle = '#ffdc6e'; cx.stroke();
    cx.restore();
    const img = BIG[sym] || IMG[sym];
    const sc = (0.6 + 0.4 * (1 - Math.pow(1 - p, 3))) * (view.win.size ? 1 + 0.03 * Math.sin(now / 130) : 1);
    if (img) cx.drawImage(img, x + (w - w * sc) / 2, (RH - RH * sc) / 2, w * sc, RH * sc);
    cx.font = '800 15px Manrope, sans-serif'; cx.textAlign = 'center'; cx.textBaseline = 'middle';
    pillAt(x + w / 2, 22, 'MEGA BANGER', '#ffc828', '#3c1e00');
  }

  // glowing cells: grills that triggered something, wilds that just lit
  const glowCells = (cells, color, width = 5) => {
    cx.save(); cx.shadowColor = color; cx.shadowBlur = 18; cx.lineWidth = width; cx.strokeStyle = color;
    for (const k of cells) { const [r, row] = k.split(',').map(Number); roundRect(cx, r * CW + 4, row * CH + 4, CW - 8, CH - 8, 12); cx.stroke(); }
    cx.restore();
  };
  if (view.grills.size) glowCells(view.grills, `rgba(255,170,60,${0.6 + 0.4 * Math.sin(now / 120)})`, 6);
  if (view.lit.size) glowCells(view.lit, '#ff821e', 4);

  // Twin Links chain
  if (view.links.length === 2 && reels.every((rl) => rl.mode === 'still')) {
    const [[r1, row], [r2]] = view.links;
    const y = row * CH + 18;
    const x1 = r1 * CW + CW / 2, x2 = r2 * CW + CW / 2;
    cx.save(); cx.strokeStyle = '#ffc828'; cx.lineWidth = 3; cx.shadowColor = '#ffc828'; cx.shadowBlur = 10;
    for (let i = 0; i < 7; i++) {
      const lx = x1 + (x2 - x1) * (i + 0.5) / 7;
      cx.beginPath(); cx.ellipse(lx, y + Math.sin(now / 200 + i) * 1.5, 9, 6, 0, 0, Math.PI * 2); cx.stroke();
    }
    cx.restore();
    cx.font = '800 13px Manrope, sans-serif'; cx.textAlign = 'center'; cx.textBaseline = 'middle';
    pillAt((x1 + x2) / 2, y, '2x LINK', '#ffc828', '#3c1e00');
  }

  // paylines
  for (const ln of view.lines) {
    const pts = W.paylines[ln.line].map((row, r) => [r * CW + CW / 2, row * CH + CH / 2]);
    const col = LINE_COLORS[ln.line % LINE_COLORS.length];
    cx.save();
    cx.lineJoin = 'round'; cx.lineCap = 'round';
    cx.shadowColor = col; cx.shadowBlur = 14;
    cx.strokeStyle = 'rgba(20,8,4,0.85)'; cx.lineWidth = 9; strokePath(pts);
    cx.strokeStyle = col; cx.lineWidth = 5; strokePath(pts);
    cx.restore();
    if (ln.label) {
      const [lx, ly] = pts[Math.min(2, pts.length - 1)];
      cx.font = '800 17px Manrope, sans-serif'; cx.textAlign = 'center'; cx.textBaseline = 'middle';
      pillAt(lx, ly, ln.label, col, '#1a0a04');
    }
  }
  // winning cell outlines
  if (view.win.size) {
    cx.save(); cx.lineWidth = 3; cx.strokeStyle = `rgba(255,245,200,${0.7 + 0.3 * Math.sin(now / 100)})`;
    for (const k of view.win) {
      if (megaCells.has(k)) continue;
      const [r, row] = k.split(',').map(Number); roundRect(cx, r * CW + 6, row * CH + 6, CW - 12, CH - 12, 11); cx.stroke();
    }
    cx.restore();
  }
  // near miss: the reel that keeps spinning glows
  if (view.tease >= 0) {
    const x = view.tease * CW;
    cx.save(); cx.shadowColor = '#ffb020'; cx.shadowBlur = 24; cx.lineWidth = 5;
    cx.strokeStyle = `rgba(255,190,40,${0.6 + 0.4 * Math.sin(now / 60)})`;
    roundRect(cx, x + 3, 3, CW - 6, RH - 6, 14); cx.stroke(); cx.restore();
  }

  drawParticles(dt);
  requestAnimationFrame(frame);
}
function strokePath(pts) { cx.beginPath(); pts.forEach(([x, y], i) => (i ? cx.lineTo(x, y) : cx.moveTo(x, y))); cx.stroke(); }
function pillAt(x, y, text, bg, fg) {
  const w = cx.measureText(text).width + 24, h = 26;
  roundRect(cx, x - w / 2, y - h / 2, w, h, h / 2);
  cx.fillStyle = bg; cx.fill(); cx.lineWidth = 2; cx.strokeStyle = '#fff'; cx.stroke();
  cx.fillStyle = fg; cx.fillText(text, x, y + 1);
}

// ------------------------------------------------------------------ particles (on the stage-wide fx canvas)
const parts = [];
function cellOnStage(r, row) {
  const cr = canvas.getBoundingClientRect(), sr = $('stage').getBoundingClientRect();
  const s = cr.width / RW;
  return [cr.left - sr.left + (r + 0.5) * CW * s, cr.top - sr.top + (row + 0.5) * CH * s, s];
}
function sparks(r, row, n = 26, colors = ['#ffdd55', '#ff9a2a', '#ff5a1e']) {
  const [x, y, s] = cellOnStage(r, row);
  for (let i = 0; i < n; i++) {
    const a = Math.random() * Math.PI * 2, v = (80 + Math.random() * 260) * s;
    parts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 120 * s, g: 380 * s, life: 0.6 + Math.random() * 0.6, age: 0,
      size: (2 + Math.random() * 4) * s, color: colors[i % colors.length], kind: 'spark' });
  }
}
function smoke(r, row, n = 10) {
  const [x, y, s] = cellOnStage(r, row);
  for (let i = 0; i < n; i++) {
    parts.push({ x: x + (Math.random() - 0.5) * 60 * s, y, vx: (Math.random() - 0.5) * 30 * s, vy: -(40 + Math.random() * 60) * s, g: -10 * s,
      life: 1.2 + Math.random() * 0.8, age: 0, size: (14 + Math.random() * 20) * s, color: 'rgba(160,160,170,', kind: 'smoke' });
  }
}
function coinShower(n = 80, dur = 2.5) {
  const w = fx.width / dpr;
  for (let i = 0; i < n; i++) {
    parts.push({ x: Math.random() * w, y: -20 - Math.random() * 200, vx: (Math.random() - 0.5) * 80, vy: 80 + Math.random() * 160, g: 420,
      life: dur + Math.random(), age: -Math.random() * dur * 0.7, size: 8 + Math.random() * 8, spin: Math.random() * 6, kind: 'coin' });
  }
}
function drawParticles(dt) {
  // clear in raw pixels: with the zoom below 100% (dpr < 1) a clear in scaled units missed the right and bottom
  // edges, and every coin and spark that passed there stayed on screen as a trail
  fxc.setTransform(1, 0, 0, 1, 0, 0);
  fxc.clearRect(0, 0, fx.width, fx.height);
  fxc.setTransform(dpr, 0, 0, dpr, 0, 0);
  for (let i = parts.length - 1; i >= 0; i--) {
    const p = parts[i];
    p.age += dt;
    if (p.age < 0) continue;
    if (p.age > p.life) { parts.splice(i, 1); continue; }
    p.vy += p.g * dt; p.x += p.vx * dt; p.y += p.vy * dt;
    const a = 1 - p.age / p.life;
    if (p.kind === 'spark') {
      fxc.globalAlpha = a; fxc.fillStyle = p.color;
      fxc.beginPath(); fxc.arc(p.x, p.y, p.size * a + 0.5, 0, Math.PI * 2); fxc.fill();
    } else if (p.kind === 'smoke') {
      fxc.globalAlpha = 1; fxc.fillStyle = `${p.color}${0.35 * a})`;
      fxc.beginPath(); fxc.arc(p.x, p.y, p.size * (1.6 - a), 0, Math.PI * 2); fxc.fill();
    } else if (p.kind === 'coin') {
      const sx = Math.abs(Math.cos(p.age * p.spin * 2));
      fxc.globalAlpha = Math.min(1, a * 2);
      fxc.fillStyle = '#f2b705'; fxc.strokeStyle = '#8a5a00'; fxc.lineWidth = 1.5;
      fxc.beginPath(); fxc.ellipse(p.x, p.y, p.size * sx + 1, p.size, 0, 0, Math.PI * 2); fxc.fill(); fxc.stroke();
      fxc.fillStyle = '#fff3a6'; fxc.beginPath(); fxc.ellipse(p.x - p.size * 0.25 * sx, p.y - p.size * 0.3, p.size * 0.3 * sx, p.size * 0.25, 0, 0, Math.PI * 2); fxc.fill();
    }
  }
  fxc.globalAlpha = 1;
}

// ------------------------------------------------------------------ reel control
function clearEffects() {
  Object.assign(view, { lit: new Set(), links: [], mega: null, win: new Set(), dim: false, lines: [], grills: new Set(), tease: -1, bunReveal: null });
  view.flash.clear();
}
function startReels(which = [0, 1, 2, 3, 4]) {
  for (const r of which) {
    const rl = reels[r];
    rl.mode = 'spinning'; rl.speed = turbo ? 30 : 22; rl.o = 1000 + Math.random() * 10; rl.filler = randomFiller();
  }
}
function stopReel(r, final, durMs) {
  const rl = reels[r];
  return new Promise((resolve) => {
    rl.final = final; rl.mode = 'stopping'; rl.t0 = performance.now(); rl.dur = durMs;
    rl.S = Math.max(2.2, (rl.speed * durMs / 1000) / 4.1);   // matches the spinning speed as it starts to slow
    rl.done = resolve;
  });
}
function countGrills(grid, upTo) {
  let n = 0;
  for (let r = 0; r < upTo; r++) for (let row = 0; row < ROWS; row++) if (grid[r][row] === 'grill') n++;
  return n;
}
// stops the spinning reels on `grid`, left to right, teasing the last reels when a bonus is close
async function landReels(grid, token, which = [0, 1, 2, 3, 4], startedAt = performance.now()) {
  const minSpin = T(which.length === 1 ? 450 : 650);
  const wait = minSpin - (performance.now() - startedAt);
  if (wait > 0) await sleep(wait);
  guard(token);
  // reels start slowing one after another (overlapping), like a real machine
  const pending = [];
  for (const r of which) {
    const near = which.length > 1 && r >= 2 && countGrills(grid, r) >= 2;
    if (near) {
      await Promise.all(pending);
      guard(token);
      view.tease = r;
      sfx.tease(T(1100) / 1000);
      await sleep(T(1100));
      guard(token);
    }
    const landed = stopReel(r, grid[r], T(near ? 480 : 330)).then(() => {
      if (token !== flow) return;
      if (view.tease === r) view.tease = -1;
      const hasGrill = grid[r].includes('grill');
      sfx.reelStop(r, hasGrill);
      if (hasGrill) {
        sfx.grill(countGrills(grid, r + 1));
        grid[r].forEach((s, row) => { if (s === 'grill') sparks(r, row, 14, ['#ffcc66', '#ff9a2a']); });
      }
    });
    pending.push(landed);
    if (near) await landed;
    else await sleep(T(skipRequested ? 30 : 170));
  }
  await Promise.all(pending);
  guard(token);
}

// ================================================================== the game flow
let skipRequested = false;
let cycleTimer = null;
function stopCycle() { if (cycleTimer) { clearInterval(cycleTimer); cycleTimer = null; } }

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
  const token = ++flow;
  setBusy(true);
  skipRequested = false;
  stopCycle(); clearEffects(); hideScenes();
  setWinText(mode === 'spin' ? 'Good luck!' : `${W.buys[mode].name}!`);
  setShownBalance((shownBalance ?? balance) - cost);
  sfx.spinStart();
  const startedAt = performance.now();
  startReels();
  const answer = waitFor('round');
  if (!send({ type: 'spin', bet, mode })) resolveWaiter('round', null);
  const m = await answer;
  if (token !== flow) return;
  if (!m) {   // refused (not enough credits, …): put the reels back
    await landReels(view.grid.map((c) => c.slice()), token).catch(() => {});
    setShownBalance(balance);
    setBusy(false);
    return;
  }
  try {
    await playRound(m.round, token, startedAt);
  } catch (e) {
    if (!(e instanceof Aborted)) { console.error(e); toast('Something went wrong showing that spin.'); }
    if (token === flow) { setBusy(false); setShownBalance(balance); }
  }
}

function abortFlow() {
  flow++;
  for (const rl of reels) if (rl.mode !== 'still') { rl.mode = 'still'; rl.o = 0; }
  clearEffects(); hideScenes(); exitFreeMode();
  $('bigwin').classList.add('hidden');
  setBusy(false);
  setShownBalance(balance);
}

async function resumeRound(round) {
  abortFlow();
  const token = flow;
  setBusy(true);
  view.grid = round.spin.grid.map((c) => c.slice());
  try {
    await playRound(round, token, 0, true);
  } catch (e) {
    if (!(e instanceof Aborted)) { console.error(e); }
    if (token === flow) { setBusy(false); setShownBalance(balance); }
  }
}

async function playRound(round, token, startedAt, resumed = false) {
  const spin = round.spin;
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a buy's stake is its price)
  const pre = { charcoal: ['EXTRA CHARCOAL!', 'At least 2 grills are coming'], sure: ['SURE SIZZLE!', 'This spin is going to win'], mega: ['MEGA BANGER!', 'Something big is coming'] }[spin.feature];
  if (!resumed) {
    if (pre) { sfx.feature(spin.feature); banner(pre[0], pre[1], 1200); }
    await landReels(spin.landed.map((c) => c.slice()), token, [0, 1, 2, 3, 4], startedAt);
    guard(token);
    await applyFeature(spin, token);
  } else {
    showSpinStatic(spin);
  }
  guard(token);
  let won = 0;
  if (spin.wins.length) {
    won = credits(stake, spin.x);
    await showWins(spin, stake, 0, token, round.bonus ? 'quick' : 'full');
  } else if (!round.bonus) {
    setWinText(spin.feature === 'respin' ? 'So close! Better luck next spin.' : 'No win this time');
  }
  guard(token);
  if (round.bonus) {
    if (autoOn) setAuto(false);
    await playBonus(round, token, won, resumed);
  }
  guard(token);
  await finishRound(round, token);
}

function showSpinStatic(spin) {
  clearEffects();
  view.grid = spin.grid.map((c) => c.slice());
  if (spin.feature === 'mega') view.mega = { reel: 1, size: 3, sym: spin.detail.symbol, t0: 0 };
  if (spin.links && spin.links.length) view.links = spin.links;
}

async function applyFeature(spin, token) {
  const f = spin.feature;
  const d = spin.detail || {};
  if (f === 'flame') {
    sfx.feature('flame');
    setWinText('🔥 FLAME WILDS!', true);
    banner('FLAME WILDS!', 'The grill flares up', 1100);
    await sleep(T(500));
    for (const [r, row] of d.cells) {
      guard(token);
      sparks(r, row); sfx.ignite();
      view.grid[r][row] = 'wild'; view.lit.add(key(r, row)); view.flash.set(key(r, row), performance.now());
      await sleep(T(240));
    }
    await sleep(T(400));
  } else if (f === 'hot') {
    sfx.feature('hot');
    setWinText('🔥 HOT REELS!', true);
    banner('HOT REELS!', d.reels.length > 1 ? 'Two reels go up in flames' : 'A whole reel goes up in flames', 1100);
    await sleep(T(500));
    for (const r of d.reels) {
      guard(token);
      for (let row = 0; row < ROWS; row++) {
        sparks(r, row, 18); view.grid[r][row] = 'wild'; view.lit.add(key(r, row)); view.flash.set(key(r, row), performance.now());
      }
      sfx.ignite();
      await sleep(T(350));
    }
    await sleep(T(300));
  } else if (f === 'mystery') {
    sfx.feature('mystery');
    setWinText('🍔 MYSTERY BUNS!', true);
    banner('MYSTERY BUNS!', "What's inside?", 1000);
    await sleep(T(900));
    guard(token);
    const cells = new Set(d.cells.map(([r, row]) => key(r, row)));
    view.bunReveal = { cells, sym: d.symbol, t0: performance.now() };
    await sleep(T(350));
    sfx.reveal();
    for (const [r, row] of d.cells) { sparks(r, row, 10, ['#fff3a6', '#ffc828']); }
    await sleep(T(400));
    for (const [r, row] of d.cells) view.grid[r][row] = d.symbol;
    view.bunReveal = null;
    await sleep(T(250));
  } else if (f === 'mega') {
    sfx.feature('mega');
    view.mega = { reel: 1, size: 3, sym: d.symbol, t0: performance.now() };
    for (let r = 1; r < 4; r++) for (let row = 0; row < 3; row++) if (Math.random() < 0.4) sparks(r, row, 8, ['#ffe15a', '#ffc828']);
    await sleep(T(700));
  } else if (f === 'respin') {
    const won = spin.wins.length > 0;
    sfx.feature('respin');
    setWinText('🍽️ SECOND HELPING!', true);
    banner('SECOND HELPING!', 'One reel spins again', 900);
    await sleep(T(700));
    guard(token);
    startReels([d.reel]);
    await landReels(spin.grid.map((c) => c.slice()), token, [d.reel], performance.now());
    if (!won) await sleep(T(200));
  } else if (f === 'charcoal' || f === 'sure') {
    // already announced as the reels started
  }
  view.grid = spin.grid.map((c) => c.slice());
  if (spin.links && spin.links.length) { view.links = spin.links; sfx.link(); }
  const grills = spin.grills || [];
  if (grills.length >= 3) view.grills = new Set(grills.map(([r, row]) => key(r, row)));
}

// counts a number up in an element, with ticking sounds; resolves when done (or skipped)
function countUp(el, from, to, ms, sound = true) {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const step = () => {
      const p = skipRequested ? 1 : clamp((performance.now() - t0) / ms, 0, 1);
      const v = from + (to - from) * (1 - Math.pow(1 - p, 2));
      el.textContent = amt(v);
      if (sound && p < 1) sfx.count(p);
      if (p < 1) requestAnimationFrame(step); else resolve();
    };
    step();
  });
}

// shows the line wins of one spin: all lines together with the counter, then (mode 'full') cycles them
async function showWins(spin, stake, before, token, mode = 'full') {
  const total = credits(stake, spin.x);
  const all = new Set();
  for (const w of spin.wins) for (let r = 0; r < w.count; r++) all.add(key(r, W.paylines[w.line][r]));
  if (view.mega) for (let r = 1; r < 4; r++) for (let row = 0; row < 3; row++) if ([...all].some((k) => k === key(r, row))) all.add(key(r, row));
  view.win = all; view.dim = true;
  view.lines = spin.wins.map((w) => ({ line: w.line }));
  const x = spin.x;
  const level = x >= 5 ? 2 : x >= 1.5 ? 1 : 0;
  sfx.win(level);
  for (const k of all) { const [r, row] = k.split(',').map(Number); if (Math.random() < (level ? 0.6 : 0.3)) sparks(r, row, 6 + level * 6, ['#fff3a6', '#ffc828', '#ffe15a']); }
  const mult = spin.mult > 1 ? ` (×${spin.mult})` : '';
  const desc = spin.wins.length === 1
    ? `${spin.wins[0].count}× ${W.symbols[spin.wins[0].symbol].name}${spin.wins[0].mult > 1 ? ' · 2x link' : ''}`
    : `${spin.wins.length} winning lines`;
  $('winText').textContent = '';
  $('winBar').classList.add('won');
  const el = document.createElement('span');
  $('winText').append(`${desc}${mult} · WIN `, el);
  const ms = T(clamp(400 + x * 220, 500, 2600));
  await countUp(el, 0, total, ms);
  if (total > 0) sfx.countEnd();
  setLastWin(before + total);
  guard(token);
  await sleep(T(mode === 'quick' ? 500 : 700));
  guard(token);
  if (mode === 'full' && spin.wins.length > 1 && !autoOn) startCycle(spin, stake);
}

// after a round: keep showing each winning line in turn until the next spin
function startCycle(spin, stake) {
  stopCycle();
  let i = 0;
  const show = () => {
    const w = spin.wins[i % spin.wins.length];
    const cells = new Set();
    for (let r = 0; r < w.count; r++) cells.add(key(r, W.paylines[w.line][r]));
    view.win = cells; view.dim = true;
    view.lines = [{ line: w.line, label: amt(credits(stake, w.x * spin.mult)) }];
    i++;
  };
  show();
  cycleTimer = setInterval(show, 1300);
}

async function finishRound(round, token) {
  view.grills = new Set();
  let s = settled.get(round.id);
  if (!s) {
    const wait = waitFor('settled', { round: round.id });
    s = settled.get(round.id) || await wait;
  }
  guard(token);
  if (!s) { abortFlow(); return; }
  settled.delete(round.id);
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a buy's stake is its price)
  if (s.payout > 0 && s.x >= W.big_win) {
    await bigWin(s.payout, s.x, token);
    send({ type: 'announce', round: round.id, what: 'win' });
  }
  setLastWin(s.payout);
  if (s.payout > 0 && round.bonus) setWinText(`TOTAL WIN ${amt(s.payout)}`, true);
  setShownBalance(s.balance, s.payout > 0);
  send({ type: 'shown', round: round.id });
  setBusy(false);
  if (autoOn) {
    await sleep(T(s.payout > stake ? 700 : 300));
    if (autoOn && !busy && token === flow) doSpin('spin');
  }
}

async function bigWin(amount, x, token) {
  const tier = x >= 50 ? 3 : x >= 20 ? 2 : 1;
  const el = $('bigwin');
  $('bwTitle').textContent = ['', 'BIG WIN!', 'MEGA WIN!', 'EPIC WIN!'][tier];
  $('bwAmount').textContent = '0';
  el.classList.remove('hidden');
  skipRequested = false;
  const skip = () => { skipRequested = true; };
  el.addEventListener('click', skip, { once: true });
  const secs = sfx.bigWin(tier);
  coinShower(60 + tier * 50, 2 + tier);
  await countUp($('bwAmount'), 0, amount, T(secs * 1000 * 0.85), false);
  guard(token);
  if (!skipRequested) await sleep(T(1400));
  el.classList.add('hidden');
  el.removeEventListener('click', skip);
  skipRequested = false;
}

// ================================================================== the Grill Bonus
const tierName = (i) => W.tiers[i].name;

async function playBonus(round, token, baseWon, resumed) {
  let b = round.bonus;
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a buy's stake is its price)
  // The bonus is on screen from here on: let the server show it in the player list and announce it. (Until now
  // the others just see this player spinning. Sent again after a reconnect; the server announces only once.)
  send({ type: 'announce', round: round.id, what: 'bonus' });
  if (!resumed || (b.feature === null && b.gambles.length === 0)) {
    sfx.bonusTrigger();
    for (const k of view.grills) { const [r, row] = k.split(',').map(Number); sparks(r, row, 30); smoke(r, row, 6); }
    coinShower(40, 1.5);
    await banner(`${b.grills} GRILLS!`, 'The Grill Bonus is lit!', 2000);
    guard(token);
  }
  // the ladder: choose, and maybe turn up the heat
  if (!b.feature && !b.lost) {
    showLadder(b, stake);
    if (!resumed || b.gambles.length === 0) await chaseTo(b.awarded, token);
    for (;;) {
      guard(token);
      showLadder(b, stake);
      const choice = await ladderChoice(b);
      guard(token);
      setLadderButtons(false);
      const reply = waitFor('bonus');
      if (!send({ type: choice })) resolveWaiter('bonus', null);
      const m = await reply;
      guard(token);
      if (!m || m.round !== round.id) continue;
      if (choice === 'gamble') {
        const g = m.bonus.gambles[m.bonus.gambles.length - 1];
        await animateGamble(g, token);
        b = m.bonus;
        if (b.lost) break;
        continue;
      }
      b = m.bonus;
      break;
    }
  }
  guard(token);
  $('ladderScene').classList.add('hidden');
  if (b.lost) {
    sfx.burnt();
    await banner('THE GRILL BURNT OUT!', 'Better luck next time', 2000);
    return;
  }
  const f = b.feature;
  setWinText(`🔥 ${tierName(b.tier).toUpperCase()}`, true);
  let won = 0;
  if (f.kind === 'potato') won = await playPotato(round, b, token);
  else if (f.kind === 'picnic') won = await playPicnic(round, b, token);
  else if (f.kind === 'golden') won = await playGolden(round, f, token);
  else won = await playFreeSpins(round, f, token, baseWon);
  guard(token);
  hideScenes();
  setLastWin(baseWon + won);
  setWinText(`${tierName(b.tier)} won ${amt(won)}`, won > 0);
  sfx.countEnd();
  await banner(`${tierName(b.tier).toUpperCase()} WON`, amt(won), 1800);
}

function showLadder(b, stake) {
  hideScenes();
  $('ladderScene').classList.remove('hidden');
  const n = W.tiers.length;
  const canGamble = b.can_gamble;
  $('rungs').innerHTML = W.tiers.map((t, i) => i).reverse().map((i) => {
    const t = W.tiers[i];
    const on = i === b.tier && !b.lost;
    const next = canGamble && i === b.tier + 1;
    const tag = on ? 'YOUR FEATURE' : next ? `${Math.round(b.chance * 100)}% TO CLIMB` : '';
    return `<div class="rung${on ? ' on' : ''}${next ? ' next' : ''}" data-i="${i}"><div class="num">${i + 1}</div>
      <div><div class="rn">${esc(t.name)}</div><div class="rt">${esc(t.text)} · avg ${amt(stake * t.avg)}</div></div>
      ${tag ? `<div class="tag">${tag}</div>` : ''}</div>`;
  }).join('');
  const bars = 12;
  const lit = Math.round(((b.tier + 1) / n) * bars);
  $('thermo').innerHTML = Array.from({ length: bars }, (_, i) => {
    const t = i / (bars - 1);
    const col = `rgb(255,${Math.round(220 - 170 * t)},${Math.round(60 - 40 * t)})`;
    return `<i class="${i < lit ? 'on' : ''}" style="${i < lit ? `background:${col};color:${col}` : ''}"></i>`;
  }).join('');
  const left = W.max_gambles - b.gambles.length;
  if (canGamble) {
    const up = tierName(b.tier + 1);
    const down = b.tier > 0 ? tierName(b.tier - 1) : 'nothing (the bonus burns)';
    $('ladderNote').textContent = `${Math.round(b.chance * 100)}% to climb to ${up}, otherwise you drop to ${down}. ${left} gamble${left === 1 ? '' : 's'} left.`;
  } else {
    $('ladderNote').textContent = b.tier === n - 1 ? 'The top of the grill! Take your feature.' : 'No more gambles. Take your feature!';
  }
  $('takeBtn').textContent = `🍽️ Take ${tierName(b.tier)}`;
  $('heatBtn').classList.toggle('hidden', !canGamble);
  $('heatBtn').textContent = canGamble ? `🔥 Turn up the heat (${Math.round(b.chance * 100)}%)` : '';
  setLadderButtons(true);
}
function setLadderButtons(on) { $('takeBtn').disabled = !on; $('heatBtn').disabled = !on; }
function ladderChoice(b) {
  return new Promise((resolve) => {
    const take = () => { cleanup(); sfx.click(); resolve('take'); };
    const heat = () => { cleanup(); sfx.click(); resolve('gamble'); };
    const cleanup = () => { $('takeBtn').removeEventListener('click', take); $('heatBtn').removeEventListener('click', heat); };
    $('takeBtn').addEventListener('click', take);
    if (b.can_gamble) $('heatBtn').addEventListener('click', heat);
  });
}
function lightRung(i) {
  for (const el of $('rungs').children) el.classList.toggle('flash', Number(el.dataset.i) === i);
}
// the Grill Wheel: a light runs up and down the rungs and settles on the awarded feature
async function chaseTo(target, token) {
  const n = W.tiers.length;
  const steps = 8 + target + n;
  let pos = 0, dir = 1;
  const seq = [];
  for (let i = 0; i < steps; i++) { seq.push(pos); if (pos + dir >= n || pos + dir < 0) dir = -dir; pos += dir; }
  while (seq[seq.length - 1] !== target) { if (pos + dir >= n || pos + dir < 0) dir = -dir; pos += dir; seq.push(pos); }
  setLadderButtons(false);
  for (let i = 0; i < seq.length; i++) {
    guard(token);
    lightRung(seq[i]); sfx.chase(seq[i]);
    await sleep(T(55 + Math.pow(i / seq.length, 3) * 320));
  }
  lightRung(-1);
  sfx.award();
}
async function animateGamble(g, token) {
  setLadderButtons(false);
  const a = g.from, b = Math.min(W.tiers.length - 1, g.from + 1);
  for (let i = 0; i < 14; i++) {
    guard(token);
    lightRung(i % 2 ? a : b); sfx.chase(i);
    await sleep(T(70 + i * 18));
  }
  lightRung(-1);
  if (g.won) { sfx.heatUp(); toastScene(`HEAT RISES! ${tierName(g.to)}`); }
  else if (g.lost) { /* the caller shows the burnt banner */ }
  else { sfx.heatDown(); toastScene(`Dropped to ${tierName(g.to)}`); }
  await sleep(T(700));
}
function toastScene(text) { $('ladderNote').textContent = text; }

// ---- Hot Potato
async function playPotato(round, b, token) {
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a buy's stake is its price)
  let f = b.feature;
  $('potatoScene').classList.remove('hidden');
  $('potatoTotal').textContent = `TOTAL ${amt(credits(stake, f.total))}`;
  while (!f.done) {
    guard(token);
    drawSausages(null, null, stake);
    $('potatoRound').textContent = `ROUND ${f.round + 1} / ${W.potato_rounds}`;
    $('potatoMsg').textContent = f.round === 0 ? 'Round 1 is safe: pick any sausage!' : 'One of these three is burnt!';
    const i = await pickFrom('#sausages .sausage');
    guard(token);
    sfx.pick();
    const reply = waitFor('bonus');
    if (!send({ type: 'pick', index: i })) resolveWaiter('bonus', null);
    const m = await reply;
    guard(token);
    if (!m) continue;
    const ev = m.event;
    drawSausages(ev.row, i, stake);
    const v = ev.row[i];
    const box = $('sausages').children[i];
    if (v === null) { sfx.burnt(); burstAt(box, ['#555', '#333', '#ff5a1e']); }
    else { sfx.prize(Math.min(5, Math.log2(v))); burstAt(box, ['#fff3a6', '#ffc828', '#ffe15a']); }
    f = m.bonus.feature;
    $('potatoTotal').textContent = `TOTAL ${amt(credits(stake, f.total))}`;
    $('potatoTotal').classList.remove('pop'); void $('potatoTotal').offsetWidth; $('potatoTotal').classList.add('pop');
    await sleep(T(1500));
  }
  return credits(stake, f.total);
}
function drawSausages(row, pick, stake) {
  $('sausages').innerHTML = [0, 1, 2].map((i) => {
    let cls = 'sausage', tag = 'ABC'[i];
    if (row) {
      const v = row[i];
      if (v === null) { cls += ' burnt'; tag = 'BURNT!'; } else { cls += ' prize'; tag = `+${amt(credits(stake, v))}`; }
      if (i === pick) cls += ' picked'; else cls += ' faded';
    }
    return `<button class="${cls}" data-i="${i}" ${row ? 'disabled' : ''}><img src="/static/banger/brat.png" alt="Sausage ${'ABC'[i]}"><span class="tag">${tag}</span></button>`;
  }).join('');
}
function pickFrom(selector) {
  return new Promise((resolve) => {
    const els = [...document.querySelectorAll(selector)];
    const onClick = (e) => {
      const el = e.currentTarget;
      if (el.disabled) return;
      els.forEach((x) => { x.removeEventListener('click', onClick); x.disabled = true; });
      resolve(Number(el.dataset.i));
    };
    els.forEach((x) => { if (!x.disabled) x.addEventListener('click', onClick); });
  });
}
function burstAt(el, colors) {
  const er = el.getBoundingClientRect(), sr = $('stage').getBoundingClientRect();
  const x = er.left - sr.left + er.width / 2, y = er.top - sr.top + er.height / 2;
  for (let i = 0; i < 36; i++) {
    const a = Math.random() * Math.PI * 2, v = 80 + Math.random() * 260;
    parts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 120, g: 380, life: 0.7 + Math.random() * 0.6, age: 0,
      size: 2 + Math.random() * 4, color: colors[i % colors.length], kind: 'spark' });
  }
}

// ---- Picnic Pick
async function playPicnic(round, b, token) {
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a buy's stake is its price)
  let f = b.feature;
  $('picnicScene').classList.remove('hidden');
  drawHampers(f, stake, null);
  while (!f.done) {
    guard(token);
    const i = await pickFrom('#hampers .hamper');
    guard(token);
    sfx.pick();
    const reply = waitFor('bonus');
    if (!send({ type: 'pick', index: i })) resolveWaiter('bonus', null);
    const m = await reply;
    guard(token);
    if (!m) { drawHampers(f, stake, null); continue; }
    f = m.bonus.feature;
    drawHampers(f, stake, i);
    const v = m.event.value;
    const box = $('hampers').children[i];
    if (v === 'ants') { sfx.ants(); burstAt(box, ['#2a1a10', '#6e1a10', '#ff5a1e']); }
    else if (v === 'double') { sfx.double(); burstAt(box, ['#9ff08a', '#fff3a6']); }
    else { sfx.prize(Math.min(5, Math.log2(v + 1))); burstAt(box, ['#fff3a6', '#ffc828']); }
    await sleep(T(f.done ? 1600 : 350));
  }
  return credits(stake, f.total);
}
function drawHampers(f, stake, last) {
  const opened = f.opened || {};
  const all = f.all;
  $('hampers').innerHTML = Array.from({ length: W.picnic_cells }, (_, i) => {
    const mine = String(i) in opened;
    const v = mine ? opened[String(i)] : all ? all[i] : undefined;
    if (v === undefined) return `<button class="hamper" data-i="${i}" ${f.done ? 'disabled' : ''}><span class="num">${i + 1}</span><div class="basket"></div></button>`;
    const kind = v === 'ants' ? 'ants' : v === 'double' ? 'double' : 'prize';
    const text = v === 'ants' ? '🐜 ANTS!' : v === 'double' ? '×2 DOUBLE' : amt(credits(stake, v));
    return `<button class="hamper open ${kind}${mine ? '' : ' unpicked'}" data-i="${i}" disabled style="${i === last ? '' : 'animation:none'}">${text}</button>`;
  }).join('');
  $('picnicDouble').textContent = f.doubled ? 'DOUBLED ×2' : '1 DOUBLER HIDDEN';
  $('picnicDouble').className = `pill${f.doubled ? ' gold' : ''}`;
  $('picnicTotal').textContent = `TOTAL ${amt(credits(stake, f.total))}`;
  $('picnicMsg').textContent = f.done ? (Object.values(opened).includes('ants') ? 'The ants found the picnic!' : 'Every hamper opened!')
    : `Find the prizes · avoid the ${W.picnic_ants} ant nests`;
}

// ---- Golden Grill
async function playGolden(round, f, token) {
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a buy's stake is its price)
  $('goldenScene').classList.remove('hidden');
  const g = $('goldenScene').querySelector('.golden');
  g.classList.remove('cool');
  let value = f.start;
  const show = () => {
    $('goldenValue').textContent = `${value}x`;
    $('goldenWin').textContent = `WIN ${amt(credits(stake, value))}`;
    $('goldenGlow').style.transform = `scale(${1 + Math.min(1, value / W.golden_max) * 0.5})`;
  };
  $('goldenMax').textContent = `MAX ${W.golden_max}x`;
  $('goldenMsg').textContent = 'Turning up the heat…';
  show();
  sfx.step(value);
  await sleep(T(1200));
  for (const step of f.steps) {
    guard(token);
    value = Math.min(W.golden_max, value + step);
    show();
    const el = $('goldenValue'); el.classList.remove('pop'); void el.offsetWidth; el.classList.add('pop');
    const st = $('goldenStep'); st.textContent = `+${step}x`; st.classList.remove('show'); void st.offsetWidth; st.classList.add('show');
    sfx.step(value);
    burstAt($('goldenValue'), ['#ffe15a', '#ffb020', '#ff5a1e']);
    await sleep(T(skipRequested ? 150 : 850));
  }
  guard(token);
  g.classList.add('cool');
  sfx.cool();
  $('goldenMsg').textContent = `The grill cooled at ${value}x!`;
  await sleep(T(1600));
  return credits(stake, f.total);
}

// ---- free spins: Grill Spins and Twin Links
function enterFreeMode(name) {
  view.free = true;
  $('reelsFrame').classList.add('free');
  $('modeLabel').textContent = name.toUpperCase();
}
function exitFreeMode() {
  view.free = false;
  $('reelsFrame').classList.remove('free');
  $('modeLabel').textContent = '';
  $('freeInfo').classList.add('hidden'); $('multInfo').classList.add('hidden');
}
async function playFreeSpins(round, f, token, baseWon) {
  const stake = round.bet ?? round.stake;   // pays are multiples of the bet (a buy's stake is its price)
  const name = f.kind === 'grillspins' ? tierName(2) : tierName(3);
  hideScenes();
  enterFreeMode(name);
  clearEffects();
  sfx.freeStart();
  await banner(name.toUpperCase(), `${W.free_spins[f.kind]} free spins`, 1700);
  let total = W.free_spins[f.kind];
  let won = 0;
  const showMult = f.kind === 'grillspins';
  for (let i = 0; i < f.spins.length; i++) {
    guard(token);
    const s = f.spins[i];
    $('freeInfo').textContent = `SPIN ${i + 1} / ${total}`; $('freeInfo').classList.remove('hidden');
    if (showMult) { $('multInfo').textContent = `MULTIPLIER ${s.mult}x`; $('multInfo').classList.remove('hidden'); }
    clearEffects();
    sfx.spinStart();
    const t0 = performance.now();
    startReels();
    await landReels(s.grid.map((c) => c.slice()), token, [0, 1, 2, 3, 4], t0 - T(250));
    guard(token);
    view.grid = s.grid.map((c) => c.slice());
    if (s.links && s.links.length) {
      view.links = s.links; sfx.link();
      for (const [r, row] of s.links) sparks(r, row, 12, ['#ffe15a', '#ffc828']);
      await sleep(T(250));
    }
    if (s.wins.length) {
      await showWins(s, stake, baseWon + won, token, 'quick');
      won += credits(stake, s.x);
      setLastWin(baseWon + won);
      if (showMult) {
        await sleep(T(150));
        $('multInfo').textContent = `MULTIPLIER ${s.mult + 1}x`;
        const el = $('multInfo'); el.classList.remove('pop'); void el.offsetWidth; el.classList.add('pop');
        sfx.multUp();
      }
    } else {
      await sleep(T(350));
    }
    if ((s.grills || []).length >= 3 && total < f.awarded) {
      view.grills = new Set(s.grills.map(([r, row]) => key(r, row)));
      total = Math.min(f.awarded, total + W.retrigger);
      sfx.bonusTrigger();
      await banner(`+${W.retrigger} FREE SPINS!`, '3 more grills', 1500);
    }
    setWinText(`${name} · won ${amt(won)}`, won > 0);
    await sleep(T(250));
  }
  guard(token);
  await sleep(T(400));
  exitFreeMode();
  clearEffects();
  view.grid = round.spin.grid.map((c) => c.slice());
  return credits(stake, f.total);
}

// ------------------------------------------------------------------ go
resize();
requestAnimationFrame(frame);
renderBet();
boot().catch((e) => { console.error(e); showFatal(`Couldn't start: ${e.message || e}`); });
