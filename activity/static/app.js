// Spin Frenzy client. The server decides everything; this page signs in, shows the shared round and sends bets.
// Colors and fonts come from theme.css (see loadTheme), sounds from sound.js.
import { signIn, retryAfterExpiredSession, go } from './auth.js';
import { sfx, unlock, isMuted, setMuted } from './sound.js';

const $ = (id) => document.getElementById(id);
const TAU = Math.PI * 2;
const SHORT = { '1': '1', '2': '2', '5': '5', '10': '10', coin: 'COIN', grid: 'GRID', drop: 'DROP', frenzy: 'FRENZY' };
const CHIP_VALUES = [10, 50, 100, 500, 1000, 5000, 25000, 100000];

let W = null;            // welcome: static game data
let S = null;            // latest shared state
let ME = { balance: 0, bets: {}, last_bets: {}, payout: 0 };
let myId = null;
let offset = 0;          // server clock - local clock (ms)
let chip = 100;
let ws = null, session = null, joinInfo = null, fatal = false, retry = 0;
const srv = () => Date.now() + offset;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const easeOut = (p) => 1 - Math.pow(1 - p, 4);
const fmt = (n) => Math.round(n).toLocaleString('en-US');
function short(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'k';
  return String(n);
}
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Discord passes platform=mobile to Activities on phones, where its own bar covers the top of the page.
// ?mobile=1 does the same when testing in a normal browser.
{
  const p = new URLSearchParams(location.search);
  if (p.get('platform') === 'mobile' || p.get('mobile') === '1') document.body.classList.add('discord-mobile');
}

// ------------------------------------------------------------------ theme (from theme.css)
const T = {};
function loadTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name, fallback = '#888') => cs.getPropertyValue(`--${name}`).trim() || fallback;
  T.spot = {};
  for (const s of Object.keys(SHORT)) T.spot[s] = v(`spot-${s}`);
  T.title = { coin: v('title-coin'), grid: v('title-grid'), drop: v('title-drop'), frenzy: v('title-frenzy') };
  T.pointers = [v('pointer-1'), v('pointer-2'), v('pointer-3')];
  T.frenzy = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`frenzy-${i}`));
  T.chips = Object.fromEntries(CHIP_VALUES.map((c) => [c, v(`chip-${c}`, '#475569')]));
  for (const k of ['wheel-rim', 'wheel-rim-dark', 'wheel-label', 'wheel-light-on', 'wheel-light-off', 'wheel-hub',
    'wheel-hub-dark', 'wheel-hub-ring', 'wheel-pointer', 'wheel-win-outline', 'wheel-rim-edge', 'wheel-separator',
    'wheel-light-glow', 'wheel-hub-text-1', 'wheel-hub-text-2', 'accent', 'accent2', 'peg', 'drop-slot',
    'drop-double', 'drop-high', 'drop-closed', 'drop-landed', 'drop-ball', 'drop-flash', 'frenzy-double', 'frenzy-triple',
    'frenzy-rim', 'frenzy-hub', 'text', 'bg']) T[k] = v(k);
  T.font = v('font-display', 'sans-serif');
  T.weight = v('display-weight', '900');
  T.italic = v('display-style', 'normal') === 'italic' ? 'italic ' : '';
}
const font = (px, weight = T.weight) => `${weight} ${Math.round(px)}px ${T.font}`;

// ------------------------------------------------------------------ sign in & connect
function loading(text) { $('loading').classList.remove('hidden'); $('loadingText').textContent = text; }
function showFatal(text) {
  fatal = true;
  $('loading').classList.remove('hidden');
  $('loading').querySelector('.spinner').classList.add('hidden');
  $('loadingText').textContent = text;
}

async function boot() {
  const auth = await signIn(loading);
  if (auth.error) { showFatal(auth.error); return; }
  session = auth.session;
  joinInfo = auth.joinInfo;
  connect();
}

function connect() {
  loading('Joining the game…');
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
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
      W = m; myId = m.you; setupBoard(); setupInfo();
      $('loading').classList.add('hidden');
      break;
    case 'state': S = m; onState(); render(); break;
    case 'me': ME = m; render(); break;
    case 'error':
      toast(m.message); sfx.error();
      if (auto.on && Date.now() - auto.sentAt < 3000) stopAuto('Auto-repeat stopped: your bets were refused.');
      break;
    case 'restart': onRestart(m); break;
    case 'fatal':
      if (m.code === 'session' && retryAfterExpiredSession()) break;
      if (m.code === 'crux' && window.showCrux) window.showCrux(m.until);   // on a /crux break
      showFatal(m.message); try { ws.close(); } catch (e) { /* closed */ } break;
  }
}

let toastTimer = null;
function toast(text, good = false) {
  const t = $('toast');
  t.textContent = text; t.classList.remove('hidden');
  t.classList.toggle('good', good);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add('hidden'), 3200);
}

// ------------------------------------------------------------------ sound controls
function updateMuteBtn() { $('muteBtn').textContent = isMuted() ? '🔇' : '🔊'; }
$('muteBtn').addEventListener('click', () => { unlock(); setMuted(!isMuted()); updateMuteBtn(); });
$('gamesBtn').addEventListener('click', () => go('/'));
updateMuteBtn();
// browsers only allow audio after the first tap/click/key
for (const ev of ['pointerdown', 'keydown', 'touchstart']) window.addEventListener(ev, unlock, { passive: true });

// ------------------------------------------------------------------ auto-repeat
// When on, your last round's bets are placed again as each betting window opens (if you haven't bet yet).
// It switches itself off when you can't afford them or the server refuses them. Always off when the page opens.
const auto = { on: false, round: -1, sentAt: 0 };
function setAuto(on) {
  auto.on = on;
  $('autoBtn').classList.toggle('on', on);
  $('autoBtn').textContent = on ? '🔁 Auto ON' : '🔁 Auto';
}
function stopAuto(message) {
  if (!auto.on) return;
  setAuto(false);
  if (message) toast(message);
}
$('autoBtn').addEventListener('click', () => {
  sfx.pop();
  if (auto.on) { setAuto(false); return; }
  // can be switched on before betting: it then repeats whatever you bet this round, from the next round on
  setAuto(true);
  auto.round = -1;
  autoRepeat();   // if betting is open right now and you haven't bet, do it straight away
});

function autoRepeat() {
  if (!auto.on || !S || !W || S.phase !== 'betting' || auto.round === S.round) return;
  if (Object.keys(ME.bets || {}).length) { auto.round = S.round; return; }   // already bet this round
  const last = ME.last_bets || {};
  const total = Object.values(last).reduce((a, b) => a + b, 0);
  if (!total) return;                                    // wait for the 'me' update with last round's bets
  if (S.ends_at - srv() < 800) return;                   // too late in this window
  auto.round = S.round;
  if (total > ME.balance) { stopAuto(`Auto-repeat stopped: you need ${fmt(total)} credits for your bets.`); return; }
  auto.sentAt = Date.now();
  sfx.chip();
  send({ type: 'repeat' });
}

// ------------------------------------------------------------------ restart when broke (same rules as /restart)
const restartState = { done: 0, note: '' };
$('restartBtn').addEventListener('click', () => { sfx.chip(); send({ type: 'restart' }); });

function onRestart(m) {
  restartState.done = m.done || 0;
  restartState.note = '';
  if (m.status === 'ok') {
    toast(`Back in the game! +${fmt(m.amount)} credits`, true);
    sfx.win(false);
  } else if (m.status === 'in_game') {
    restartState.note = 'You still have credits in an unfinished game in this server. Finish it first.';
  } else if (m.status === 'paid') {
    const at = new Date(m.until - offset).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    restartState.note = `Restarts are locked until ${at} because you sent credits with /pay recently.`;
  }
  render();
}

function renderBroke() {
  const betsIn = Object.keys(ME.bets || {}).length > 0;
  const broke = ME.balance < W.min_bet && !betsIn;
  $('broke').classList.toggle('hidden', !broke);
  if (!broke) { restartState.done = 0; restartState.note = ''; return; }
  const left = W.restart_presses - restartState.done;
  $('restartBtn').textContent = W.restart_presses > 1 && restartState.done > 0
    ? `Press ${left} more time${left === 1 ? '' : 's'}` : `Restart with ${fmt(W.restart_amount)} credits`;
  $('brokeText').textContent = restartState.note;
}

// ------------------------------------------------------------------ board & controls
function setupBoard() {
  const board = $('board');
  board.innerHTML = '';
  for (const spot of W.spots) {
    const isBonus = !(spot in W.numbers);
    const el = document.createElement('div');
    el.className = 'tile' + (isBonus ? ' bonus' : '');
    el.style.setProperty('--c', `var(--spot-${spot})`);
    el.dataset.spot = spot;
    el.innerHTML = `<div class="tile-label">${esc(W.names[spot])}</div>
      <div class="tile-pay">${isBonus ? 'Bonus' : `pays ${W.numbers[spot]}:1`}</div>
      <div class="tile-mine"></div><div class="tile-all"></div>`;
    el.addEventListener('click', () => {
      if (!S || S.phase !== 'betting') { toast('Betting is closed. Wait for the next round.'); sfx.error(); return; }
      if (ME.balance < chip) { toast("You don't have enough credits for that chip."); sfx.error(); return; }
      sfx.chip();
      send({ type: 'bet', spot, amount: chip });
    });
    board.appendChild(el);
  }
  const chips = $('chips');
  chips.innerHTML = '';
  const values = CHIP_VALUES.filter((v) => v >= W.min_bet && (!W.max_bet || v <= W.max_bet));
  if (!values.length) values.push(W.min_bet);
  chip = values.includes(100) ? 100 : values[0];
  for (const v of values) {
    const c = document.createElement('div');
    c.className = 'chip' + (v === chip ? ' sel' : '');
    c.style.background = T.chips[v] || '#475569';
    c.textContent = short(v);
    c.addEventListener('click', () => {
      chip = v;
      sfx.pop();
      chips.querySelectorAll('.chip').forEach((x) => x.classList.toggle('sel', x === c));
    });
    chips.appendChild(c);
  }
  $('clearBtn').onclick = () => { sfx.clear(); send({ type: 'clear' }); };
  $('doubleBtn').onclick = () => { sfx.chip(); send({ type: 'double' }); };
  $('repeatBtn').onclick = () => { sfx.chip(); send({ type: 'repeat' }); };
}

function setupInfo() {
  const i = W.info;
  $('infoBody').innerHTML = `
    <p>Everyone in this call shares one wheel. Bet with your server credits while the timer runs, then the
    <b>Boost Reel</b> spins and the wheel decides the round.</p>
    <h3>The wheel</h3>
    <table><tr><th>Spot</th><th>Wins</th><th>Pays</th><th>Pays back*</th></tr>
    ${W.spots.map((s) => `<tr><td>${esc(W.names[s])}</td><td>${(i.chances[s] * 100).toFixed(1)}%</td>
      <td>${s in W.numbers ? `${W.numbers[s]} to 1` : 'Bonus round'}</td>
      <td>${Math.round(i.returns[s] * 100)}%</td></tr>`).join('')}</table>
    <p style="font-size:12px;color:var(--muted)">* On average over many rounds: 100% means you'd break even.</p>
    <h3>Boost Reel</h3>
    <p>Before each spin the reel may land on one spot with a multiplier (${i.boost_mults[0]}x to
    ${i.boost_mults[i.boost_mults.length - 1]}x). If the wheel stops on that spot, its winnings are multiplied.
    For a bonus, the multiplier applies to the bonus result.</p>
    <h3>Bonus rounds</h3>
    <p><b>Coin Toss</b>: gold and silver each get a multiplier (${i.coin[0]}x to ${i.coin[1]}x), then the coin is flipped.</p>
    <p><b>Treasure Grid</b>: ${W.grid_cells} multipliers (${i.grid[0]}x to ${i.grid[1]}x) are shown, hidden and shuffled.
    Pick a chest; whatever is behind it is yours. Every game has ${i.grid[3]} or ${i.grid[4]} big chests worth
    ${i.grid[2]}x or more (shown in gold before the shuffle).</p>
    <p><b>Peg Drop</b>: a ball bounces down to a slot (${i.drop[0]}x to ${i.drop[1]}x). DOUBLE doubles every slot
    and drops again (up to ${i.drop[2]} times). Every board has 1 or 2 big slots worth ${i.drop[3]}x or more on its
    outer edges; like on a real plinko board the edges are hard to reach: the ball lands in each edge slot
    ${(i.drop[4] * 100).toFixed(1)}% of the time.</p>
    <p><b>Frenzy Wheel</b>: pick a pointer (Ruby, Jade or Sapphire). DOUBLE and TRIPLE multiply your pointer's
    result and the wheel spins again (up to ${i.frenzy[2]} times). Values ${i.frenzy[0]}x to ${i.frenzy[1]}x.</p>
    <h3>Payouts</h3>
    <p>A win pays your bet back plus bet × result. The "Pays back" column above shows what each spot returns over time.
    ${W.max_win_mult ? `A single bet can win at most ${fmt(W.max_win_mult)}x.` : ''}
    Minimum chip ${fmt(W.min_bet)}${W.max_bet ? `, at most ${fmt(W.max_bet)} per round` : ''}.
    If you leave mid-round, your bets still play out and are paid to your balance.</p>`;
  $('infoBtn').onclick = () => $('infoModal').classList.remove('hidden');
  $('infoClose').onclick = () => $('infoModal').classList.add('hidden');
  $('infoModal').onclick = (e) => { if (e.target.id === 'infoModal') $('infoModal').classList.add('hidden'); };
}

// ------------------------------------------------------------------ rendering (DOM)
const REEL_STOP = 2500;   // ms into the boost phase when the reel stops
function boostRevealed() {
  if (!S || !S.boost) return false;
  return S.phase !== 'boost' || srv() > S.starts_at + REEL_STOP;
}
function inBonus(kind) { return S && S.bonus && S.bonus.kind === kind && (S.phase === 'bonus' || S.phase === 'payout'); }
function iAmIn() { return !!(S && S.bonus && S.bonus.players.includes(myId)); }

function avatarHtml(p, cls = '') {
  const initial = esc((p.name || '?').trim().charAt(0).toUpperCase() || '?');
  // the fallback letter is read from data-i, so a name starting with a quote can't break the onerror script
  if (p.avatar) return `<div class="avatar ${cls}"><img src="/avatar/${p.id}" alt="" data-i="${initial}" onerror="this.replaceWith(document.createTextNode(this.dataset.i))"></div>`;
  return `<div class="avatar ${cls}">${initial}</div>`;
}

function render() {
  if (!W || !S) return;
  $('balance').textContent = fmt(ME.balance);
  const myTotal = Object.values(ME.bets || {}).reduce((a, b) => a + b, 0);
  $('myTotal').textContent = fmt(myTotal);
  const betting = S.phase === 'betting';
  $('clearBtn').disabled = !betting || !myTotal;
  $('doubleBtn').disabled = !betting || !myTotal;
  $('repeatBtn').disabled = !betting || myTotal > 0 || !Object.keys(ME.last_bets || {}).length;
  renderBroke();
  if (auto.on && ME.balance < W.min_bet && !myTotal) stopAuto();
  autoRepeat();

  // board
  const totals = {};
  for (const p of S.players) for (const [s, a] of Object.entries(p.bets)) totals[s] = (totals[s] || 0) + a;
  const showWin = ['result', 'bonus', 'payout'].includes(S.phase) && S.result;
  for (const el of $('board').children) {
    const spot = el.dataset.spot;
    const mine = (ME.bets || {})[spot] || 0;
    el.querySelector('.tile-mine').innerHTML = mine ? `<div class="chip-stack">${short(mine)}</div>` : '';
    el.querySelector('.tile-all').textContent = totals[spot] ? `all: ${short(totals[spot])}` : '';
    el.classList.toggle('locked', !betting);
    el.classList.toggle('won', !!showWin && S.result === spot);
    let badge = el.querySelector('.tile-boost');
    const boosted = boostRevealed() && S.boost.spot === spot;
    if (boosted && !badge) {
      badge = document.createElement('div'); badge.className = 'tile-boost'; el.appendChild(badge);
    }
    if (badge) { if (boosted) badge.textContent = `×${S.boost.mult}`; else badge.remove(); }
  }

  // history
  $('history').innerHTML = S.history.map((h) => `<div class="hist" style="background:${T.spot[h.spot]}">
    ${esc(SHORT[h.spot])}${h.boost > 1 ? `<span class="hb">×${h.boost}</span>` : ''}</div>`).join('');

  // players
  const online = S.players.filter((p) => p.online).length;
  const players = [...S.players].sort((a, b) => (b.id === myId) - (a.id === myId) || b.total - a.total || b.payout - a.payout);
  $('playerCount').textContent = `(${online})`;
  $('headPlayers').textContent = `👥 ${online}`;
  $('players').innerHTML = players.map((p) => {
    const bets = Object.entries(p.bets).map(([s, a]) => `<i style="background:${T.spot[s]}">${esc(SHORT[s])} ${short(a)}</i>`).join('');
    const amt = S.phase === 'payout' && p.payout ? `<div class="p-amt win">+${short(p.payout)}</div>`
      : p.total ? `<div class="p-amt">${short(p.total)}</div>` : '';
    return `<div class="player${p.id === myId ? ' me' : ''}${p.online ? '' : ' offline'}">${avatarHtml(p)}
      <div class="p-info"><div class="p-name">${esc(p.name)}</div><div class="p-bets">${bets}</div></div>${amt}</div>`;
  }).join('');
  const showWinners = S.winners && S.winners.length && S.phase === 'payout';
  $('winnersTitle').classList.toggle('hidden', !showWinners);
  $('winners').innerHTML = showWinners ? S.winners.slice(0, 6).map((w) =>
    `<div class="winner"><span>${esc(w.name)}</span><b>+${short(w.payout)}</b></div>`).join('') : '';

  renderScenes();
  renderBanner();
  spawnWinFeed();
}

// winners of the round fade in at the top left, glide down and fade out, biggest win first
let feedRound = -1;
function spawnWinFeed() {
  if (S.phase !== 'payout' || feedRound === S.round) return;
  feedRound = S.round;
  if (!S.winners || !S.winners.length || srv() - S.starts_at > 2500) return;
  const feed = $('winFeed');
  // start below the Boost Reel / bonus title when they reach into the top-left corner
  let top = 8;
  for (const el of [$('boostReel'), $('bonusHead')]) {
    const b = boxOf(el);
    if (b && b.x0 < 240 && b.y0 < 140) top = Math.max(top, b.y1 + 6);
  }
  feed.style.setProperty('--feed-top', `${top}px`);
  const people = Object.fromEntries(S.players.map((p) => [p.id, p]));
  S.winners.slice(0, 12).forEach((w, i) => {
    const el = document.createElement('div');
    el.className = 'feed-item' + (i === 0 ? ' top' : '') + (w.id === myId ? ' me' : '');
    el.style.animationDelay = `${i * 0.65}s`;
    el.innerHTML = `${avatarHtml(people[w.id] || { id: w.id, name: w.name })}`
      + `<span class="feed-name">${i === 0 ? '👑 ' : ''}${esc(w.name)}</span><span class="feed-amount">+${short(w.payout)}</span>`;
    el.addEventListener('animationend', () => el.remove());
    feed.appendChild(el);
  });
}

function renderBanner() {
  const b = $('banner'), main = $('bannerMain'), sub = $('bannerSub');
  let text = null, subText = '', win = false;
  const bonus = S.bonus;
  if (S.phase === 'waiting') { text = 'Waiting for players…'; }
  else if (S.phase === 'result' && S.result) {
    const boost = S.boost && S.boost.spot === S.result ? S.boost.mult : 1;
    if (S.result in W.numbers) {
      text = `${S.result} WINS!`;
      subText = boost > 1 ? `Boosted ×${boost}: pays ${W.numbers[S.result] * boost} to 1` : `pays ${W.numbers[S.result]} to 1`;
    } else {
      text = `${W.names[S.result].toUpperCase()}!`;
      subText = (ME.bets || {})[S.result] ? "You're in the bonus!" : 'Bonus round: watch along';
      if (boost > 1) subText += ` · Boosted ×${boost}`;
    }
  } else if (S.phase === 'bonus' && bonus) {
    const boost = bonus.boost || 1;
    const x = (v) => boost > 1 ? `${fmt(v)}x × ${boost} = ${fmt(v * boost)}x` : `${fmt(v)}x`;
    if (bonus.kind === 'coin' && bonus.stage === 'done') {
      text = `${bonus.winner.toUpperCase()} · ${x(bonus.value)}`;
    } else if (bonus.kind === 'grid' && bonus.stage === 'reveal' && srv() > S.starts_at + 1200) {
      const pick = bonus.picks[myId];
      if (pick !== undefined) { text = `You found ${x(bonus.values[pick])}`; win = true; }
    } else if (bonus.kind === 'drop' && bonus.stage === 'done') {
      text = x(bonus.value);
    } else if (bonus.kind === 'frenzy' && bonus.stage === 'done') {
      const p = bonus.picks[myId];
      if (p !== undefined) { text = `${W.frenzy_pointers[p]}: ${x(bonus.results[p])}`; win = true; }
      else text = bonus.results.map((r, i) => `${W.frenzy_pointers[i]} ${fmt(r)}x`).join(' · ');
    }
  } else if (S.phase === 'payout' && ME.payout > 0) {
    text = `YOU WON ${fmt(ME.payout)}`; win = true; subText = 'added to your balance';
  }
  if (!text) { b.classList.add('hidden'); b.dataset.key = ''; return; }
  const key = S.round + S.phase + (bonus ? bonus.stage : '') + text;
  if (b.dataset.key !== key) {   // restart the pop animation when the content changes
    b.dataset.key = key;
    b.classList.add('hidden'); void b.offsetWidth;
  }
  main.textContent = text; sub.textContent = subText;
  b.classList.toggle('win', win);
  b.classList.toggle('low', !!(bonus && (S.phase === 'bonus' || S.phase === 'payout')));
  b.classList.remove('hidden');
}

// bonus scenes in the DOM (coin & grid), plus the header and the pointer picker
let gridRound = -1, coinRound = -1, coinFlipRound = -1;
function renderScenes() {
  const bonus = S.bonus;
  const active = bonus && (S.phase === 'bonus' || S.phase === 'payout');
  $('boostReel').classList.toggle('hidden', !!active);
  $('bonusHead').classList.toggle('hidden', !active);
  $('coinScene').classList.toggle('hidden', !inBonus('coin'));
  $('gridScene').classList.toggle('hidden', !inBonus('grid'));
  $('pointerPick').classList.toggle('hidden', !(inBonus('frenzy') && bonus.stage === 'pick' && iAmIn()));
  if (!active) return;

  $('bonusTitle').textContent = W.names[bonus.kind].toUpperCase();
  $('bonusTitle').style.color = T.title[bonus.kind];
  const n = bonus.players.length;
  let sub = iAmIn() ? "You're in!" : n ? `${n} player${n === 1 ? '' : 's'} in · you're watching` : 'Nobody bet on this bonus';
  if (bonus.boost > 1) sub += ` · Boost ×${bonus.boost}`;
  const stageText = {
    grid: { show: 'Remember the prizes…', shuffle: 'Hiding and shuffling…', pick: iAmIn() ? 'Pick a chest!' : 'Players are picking…', reveal: 'Revealing!' },
    frenzy: { intro: 'Get ready…', pick: iAmIn() ? 'Pick your pointer!' : 'Players are picking pointers…', spin: '', done: '' },
    drop: { board: 'Here comes the ball…', drop: '', done: '' },
    coin: { reveal: 'Setting the sides…', flip: 'Flipping!', done: '' },
  }[bonus.kind][bonus.stage];
  if (stageText && n) sub += ` · ${stageText}`;
  $('bonusSub').textContent = sub;
  // the scenes start below the title, however tall it ends up at this window size
  const head = $('bonusHead');
  $('stage').style.setProperty('--head-h', `${head.offsetTop + head.offsetHeight + 6}px`);

  if (bonus.kind === 'coin') renderCoin(bonus);
  if (bonus.kind === 'grid') renderGrid(bonus);
  if (bonus.kind === 'frenzy') renderPointerPick(bonus);
}

function renderCoin(b) {
  const coin = $('coin');
  if (coinRound !== S.round) {
    coinRound = S.round;
    coin.classList.remove('flipping'); coin.style.transform = 'rotateY(0deg)';
    $('coinGold').textContent = '?'; $('coinSilver').textContent = '?';
  }
  if (b.sides) {
    $('coinGold').textContent = `${b.sides.gold}x`;
    $('coinSilver').textContent = `${b.sides.silver}x`;
  }
  const decided = b.winner && (b.stage === 'done' || S.phase === 'payout' || srv() > S.starts_at + 4300);
  $('coinGoldBox').classList.toggle('win', !!decided && b.winner === 'gold');
  $('coinSilverBox').classList.toggle('win', !!decided && b.winner === 'silver');
  $('coinGoldBox').classList.toggle('lose', !!decided && b.winner !== 'gold');
  $('coinSilverBox').classList.toggle('lose', !!decided && b.winner !== 'silver');
  if (b.winner && coinFlipRound !== S.round) {
    coinFlipRound = S.round;
    const end = `rotateY(${360 * 7 + (b.winner === 'silver' ? 180 : 0)}deg)`;
    if (b.stage === 'flip' && srv() < S.starts_at + 1000) {
      coin.classList.add('flipping');
      void coin.offsetWidth;   // commit the start position so the transition runs
      coin.style.transform = end;
    } else {
      coin.classList.remove('flipping'); coin.style.transform = end;
    }
  }
}

function renderGrid(b) {
  const grid = $('grid');
  if (gridRound !== S.round || grid.children.length !== b.cells) {
    gridRound = S.round;
    grid.innerHTML = '';
    for (let i = 0; i < b.cells; i++) {
      const c = document.createElement('div');
      c.className = 'cell';
      c.innerHTML = '<div class="cell-inner"><div class="cell-front"></div><div class="cell-back">💰</div></div><div class="cell-picks"></div>';
      c.addEventListener('click', () => {
        if (S.bonus && S.bonus.kind === 'grid' && S.bonus.stage === 'pick' && iAmIn()) {
          sfx.chip(); send({ type: 'pick_cell', cell: i });
        }
      });
      grid.appendChild(c);
    }
  }
  const byCell = {};
  for (const [uid, cell] of Object.entries(b.picks || {})) (byCell[cell] = byCell[cell] || []).push(uid);
  const people = Object.fromEntries(S.players.map((p) => [p.id, p]));
  const reveal = b.stage === 'reveal' || S.phase === 'payout';
  [...grid.children].forEach((c, i) => {
    const front = c.querySelector('.cell-front');
    let value = null;
    if (b.stage === 'show') value = b.shown[i];
    if (reveal && b.values) value = b.values[i];
    if (value !== null) {
      front.textContent = `${value}x`;
      front.classList.toggle('jackpot', value >= W.info.grid[2]);   // the big chests
    }
    c.classList.toggle('down', b.stage === 'shuffle' || b.stage === 'pick');
    c.classList.toggle('shuffle', b.stage === 'shuffle');
    c.classList.toggle('pickable', b.stage === 'pick' && iAmIn());
    c.classList.toggle('mine', b.picks && b.picks[myId] === i);
    c.classList.toggle('dim', reveal && !byCell[i]);
    c.querySelector('.cell-picks').innerHTML = (byCell[i] || []).slice(0, 4)
      .map((uid) => avatarHtml(people[uid] || { id: uid, name: '?' })).join('');
  });
}

function renderPointerPick(b) {
  const box = $('pointerPick');
  if (!box.children.length) {
    W.frenzy_pointers.forEach((name, i) => {
      const btn = document.createElement('button');
      btn.className = 'ptr-btn'; btn.style.background = T.pointers[i]; btn.dataset.i = i;
      btn.addEventListener('click', () => { sfx.chip(); send({ type: 'pick_pointer', pointer: i }); });
      box.appendChild(btn);
    });
  }
  const counts = [0, 0, 0];
  for (const p of Object.values(b.picks || {})) counts[p]++;
  [...box.children].forEach((btn, i) => {
    btn.textContent = `${W.frenzy_pointers[i]}${counts[i] ? ` (${counts[i]})` : ''}`;
    btn.classList.toggle('sel', b.picks && b.picks[myId] === i);
  });
}

// ------------------------------------------------------------------ state changes: animations & sounds
const mainWheel = { angle: 0, anim: null, round: -1, lastSeg: -1, segments: 51 };
const frenzyWheel = { angle: 0, anim: null, key: '', lastSeg: -1, segments: 48 };
let boostTimer = null, lastSoundKey = '', coinLandTimer = null;

function startSpin(w, stop, n, startAt, durMs, turns, onDone) {
  const seg = TAU / n;
  const target = -(stop + 0.5) * seg + (Math.random() - 0.5) * 0.6 * seg;
  const delta = (((target - w.angle) % TAU) + TAU) % TAU;
  w.segments = n;
  w.anim = { from: w.angle, to: w.angle + turns * TAU + delta, start: startAt, dur: durMs, onDone };
}
function settleAngle(w, stop, n) { w.anim = null; w.angle = -(stop + 0.5) * (TAU / n); }

function onState() {
  const n = W.wheel.length;
  if (S.phase === 'spin' && mainWheel.round !== S.round) {
    mainWheel.round = S.round;
    startSpin(mainWheel, S.stop, n, S.starts_at, 6800, 6, () => sfx.land());
  } else if (['result', 'bonus', 'payout'].includes(S.phase) && mainWheel.round !== S.round && S.stop !== null) {
    mainWheel.round = S.round; settleAngle(mainWheel, S.stop, n);
  }
  if (S.bonus && S.bonus.kind === 'frenzy' && S.phase === 'bonus') {
    const b = S.bonus;
    if (b.stage === 'spin') {
      const key = `${S.round}:${b.spin_index}`;
      if (frenzyWheel.key !== key) {
        frenzyWheel.key = key;
        const boosted = b.spin.landed.some((seg) => seg !== null && typeof b.segments[seg] !== 'number');
        if (srv() - S.starts_at < 5000) {
          startSpin(frenzyWheel, b.spin.stop, b.segments.length, S.starts_at, 6000, 4,
            () => (boosted ? sfx.boost() : sfx.land()));
        } else settleAngle(frenzyWheel, b.spin.stop, b.segments.length);
      }
    } else if (b.stage === 'intro' && frenzyWheel.key !== `${S.round}:intro`) {
      frenzyWheel.key = `${S.round}:intro`; frenzyWheel.anim = null; frenzyWheel.angle = 0;
    }
  }
  // Boost Reel
  clearInterval(boostTimer);
  const reel = $('boostReel'), spotEl = $('boostSpot'), multEl = $('boostMult');
  reel.classList.remove('hit', 'miss');
  if (S.phase === 'boost' && srv() < S.starts_at + REEL_STOP) {
    const mults = W.info.boost_mults;
    let k = 0;
    boostTimer = setInterval(() => {
      if (srv() > S.starts_at + REEL_STOP) {
        clearInterval(boostTimer); showBoost();
        if (S.boost && S.boost.spot) sfx.reelHit(); else sfx.reelMiss();
        render(); return;
      }
      const s = W.spots[Math.floor(Math.random() * W.spots.length)];
      spotEl.textContent = W.names[s]; spotEl.style.color = T.spot[s];
      multEl.textContent = `${mults[Math.floor(Math.random() * mults.length)]}x`;
      if (k++ % 2 === 0) sfx.reelTick();
    }, 70);
  } else if (S.boost && S.phase !== 'betting') {
    showBoost();
  } else {
    spotEl.textContent = '–'; spotEl.style.color = ''; multEl.textContent = '–';
  }
  phaseSounds();
}

// one-off sounds when a phase or bonus stage starts (skipped when joining halfway through one)
function phaseSounds() {
  const b = S.bonus;
  const key = `${S.round}|${S.phase}|${b ? b.stage : ''}|${b ? (b.drop_index ?? '') : ''}|${b ? (b.spin_index ?? '') : ''}`;
  if (key === lastSoundKey) return;
  lastSoundKey = key;
  if (srv() - S.starts_at > 1500) return;
  if (S.phase === 'boost') sfx.closed();
  else if (S.phase === 'result' && S.result && !(S.result in W.numbers)) sfx.bonus();
  else if (S.phase === 'payout' && ME.payout > 0) {
    const bet = Object.values(ME.last_bets || {}).reduce((a, c) => a + c, 0) || 1;
    sfx.win(ME.payout >= bet * 10);
  } else if (S.phase === 'bonus' && b) {
    if (b.kind === 'coin' && b.stage === 'reveal') { sfx.pop(); setTimeout(() => sfx.pop(), 250); }
    if (b.kind === 'coin' && b.stage === 'flip') {
      sfx.whoosh();
      clearTimeout(coinLandTimer); coinLandTimer = setTimeout(() => sfx.land(), 4200);
    }
    if (b.kind === 'grid' && b.stage === 'show') sfx.pop();
    if (b.kind === 'grid' && b.stage === 'shuffle') sfx.shuffle();
    if (b.kind === 'grid' && b.stage === 'reveal') { for (let i = 0; i < 5; i++) setTimeout(() => sfx.pop(), i * 90); setTimeout(() => sfx.land(), 600); }
    if (b.kind === 'frenzy' && b.stage === 'intro') sfx.whoosh();
    if (b.kind === 'drop' && b.stage === 'board') sfx.whoosh();
  }
}

function showBoost() {
  const reel = $('boostReel'), spotEl = $('boostSpot'), multEl = $('boostMult');
  if (S.boost && S.boost.spot) {
    spotEl.textContent = W.names[S.boost.spot]; spotEl.style.color = T.spot[S.boost.spot];
    multEl.textContent = `${S.boost.mult}x`; reel.classList.add('hit');
  } else {
    spotEl.textContent = 'NO BOOST'; spotEl.style.color = ''; multEl.textContent = '–'; reel.classList.add('miss');
  }
}

// ------------------------------------------------------------------ canvas
const canvas = $('canvas');
const ctx = canvas.getContext('2d');
let cw = 0, ch = 0, lastFrame = performance.now();
function resize() {
  const r = $('stage').getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  cw = r.width; ch = r.height;
  canvas.width = Math.max(1, Math.round(cw * dpr)); canvas.height = Math.max(1, Math.round(ch * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', resize);
new ResizeObserver(resize).observe($('stage'));

// moves a wheel along its spin, ticking whenever a segment passes the top pointer
function stepAnim(w) {
  if (!w.anim) return;
  const p = clamp((srv() - w.anim.start) / w.anim.dur, 0, 1);
  w.angle = w.anim.from + (w.anim.to - w.anim.from) * easeOut(p);
  const seg = TAU / w.segments;
  const atPointer = Math.floor((((-w.angle) % TAU) + TAU) % TAU / seg);
  if (atPointer !== w.lastSeg) { w.lastSeg = atPointer; if (p < 1) sfx.tick(0.9 + 0.2 * (1 - p)); }
  if (p >= 1) {
    w.angle = w.anim.to % TAU;
    const done = w.anim.onDone;
    w.anim = null;
    if (done && srv() - (w.lastDoneAt || 0) > 500) { w.lastDoneAt = srv(); done(); }
  }
}

function frame(t) {
  const dt = Math.min(0.1, (t - lastFrame) / 1000);
  lastFrame = t;
  ctx.clearRect(0, 0, cw, ch);
  if (W && S && cw > 0 && ch > 0) {
    if (inBonus('drop')) drawDrop();
    else if (inBonus('frenzy')) drawFrenzy();
    else if (!inBonus('coin') && !inBonus('grid')) {
      if (!mainWheel.anim && ['waiting', 'betting', 'boost'].includes(S.phase)) mainWheel.angle += dt * 0.12;
      stepAnim(mainWheel);
      drawMainWheel(t);
    }
    updateTimer();
  }
  requestAnimationFrame(frame);
}

// the stage-relative box of an element (for keeping the wheel clear of the Boost Reel and titles)
function boxOf(el) {
  if (el.classList.contains('hidden')) return null;
  const s = $('stage').getBoundingClientRect(), r = el.getBoundingClientRect();
  return { x0: r.left - s.left, y0: r.top - s.top, x1: r.right - s.left, y1: r.bottom - s.top };
}
function distToBox(x, y, b) {
  const dx = Math.max(b.x0 - x, 0, x - b.x1), dy = Math.max(b.y0 - y, 0, y - b.y1);
  return Math.hypot(dx, dy);
}

function drawPointer(x, y, angle, color, size) {
  ctx.save();
  ctx.translate(x, y); ctx.rotate(angle);
  ctx.beginPath();
  ctx.moveTo(0, size); ctx.lineTo(-size * 0.7, -size * 0.6); ctx.lineTo(size * 0.7, -size * 0.6); ctx.closePath();
  ctx.fillStyle = color; ctx.shadowColor = 'rgba(0,0,0,.6)'; ctx.shadowBlur = 8; ctx.fill();
  ctx.lineWidth = 2; ctx.strokeStyle = '#fff'; ctx.stroke();
  ctx.restore();
}

function drawMainWheel(t) {
  // Two ways to fit the wheel: centred below the Boost Reel, or centred in the stage and just clear of it.
  // Whichever gives the bigger wheel wins, so it fills short-wide and tall-narrow windows alike.
  const reel = boxOf($('boostReel'));
  const pad = 10, rim = 1.08;
  let cx = cw / 2, cy, R;
  const below = reel ? reel.y1 + 6 : pad;
  const rA = Math.min(cw / 2 - pad, (ch - below) / 2 - pad) / rim;
  let rB = Math.min(cw / 2 - pad, ch / 2 - pad) / rim;
  if (reel) rB = Math.min(rB, distToBox(cx, ch / 2, reel) / (rim + 0.03));
  if (rA >= rB) { R = rA; cy = below + (ch - below) / 2; } else { R = rB; cy = ch / 2; }
  R = Math.max(20, R);
  const n = W.wheel.length, seg = TAU / n;
  const settled = !mainWheel.anim && ['result', 'bonus', 'payout'].includes(S.phase);
  ctx.save();
  ctx.translate(cx, cy);
  const rimG = ctx.createRadialGradient(0, 0, R * 0.95, 0, 0, R * rim);
  rimG.addColorStop(0, T['wheel-rim']); rimG.addColorStop(1, T['wheel-rim-dark']);
  ctx.beginPath(); ctx.arc(0, 0, R * rim, 0, TAU); ctx.fillStyle = rimG; ctx.fill();
  ctx.lineWidth = Math.max(1, R * 0.008); ctx.strokeStyle = T['wheel-rim-edge']; ctx.stroke();
  for (let i = 0; i < n; i++) {
    const a0 = -Math.PI / 2 + mainWheel.angle + i * seg;
    const spot = W.wheel[i];
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, R, a0, a0 + seg); ctx.closePath();
    const g = ctx.createRadialGradient(0, 0, R * 0.25, 0, 0, R);
    g.addColorStop(0, shade(T.spot[spot], -0.35)); g.addColorStop(1, T.spot[spot]);
    ctx.fillStyle = g; ctx.fill();
    ctx.strokeStyle = T['wheel-separator']; ctx.lineWidth = 1; ctx.stroke();
    const isNum = spot in W.numbers;
    const fs = R * (isNum ? 0.085 : 0.05);
    if (fs >= 6) {
      ctx.save();
      ctx.rotate(a0 + seg / 2);
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.font = font(fs);
      ctx.fillStyle = T['wheel-label']; ctx.shadowColor = 'rgba(0,0,0,.6)'; ctx.shadowBlur = 3;
      ctx.fillText(SHORT[spot], R * 0.94, 0);
      ctx.restore();
    }
  }
  if (settled && S.stop !== null) {
    const a0 = -Math.PI / 2 + mainWheel.angle + S.stop * seg;
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, R, a0, a0 + seg); ctx.closePath();
    ctx.lineWidth = 4; ctx.globalAlpha = 0.6 + 0.4 * Math.sin(t / 150);
    ctx.strokeStyle = T['wheel-win-outline']; ctx.stroke(); ctx.globalAlpha = 1;
  }
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + mainWheel.angle + i * seg;
    const on = (Math.floor(t / 250) + i) % 3 !== 0;   // marquee chase: two bulbs on, one off
    ctx.beginPath(); ctx.arc(Math.cos(a) * R * 1.045, Math.sin(a) * R * 1.045, Math.max(1.8, R * 0.016), 0, TAU);
    ctx.fillStyle = on ? T['wheel-light-on'] : T['wheel-light-off'];
    if (on) { ctx.shadowColor = T['wheel-light-glow']; ctx.shadowBlur = Math.max(4, R * 0.03); }
    ctx.fill(); ctx.shadowBlur = 0;
  }
  const hub = ctx.createRadialGradient(-R * 0.05, -R * 0.05, 0, 0, 0, R * 0.24);
  hub.addColorStop(0, T['wheel-hub']); hub.addColorStop(1, T['wheel-hub-dark']);
  ctx.beginPath(); ctx.arc(0, 0, R * 0.24, 0, TAU); ctx.fillStyle = hub; ctx.fill();
  ctx.lineWidth = Math.max(1.5, R * 0.012); ctx.strokeStyle = T['wheel-hub-ring']; ctx.stroke();
  if (R > 60) {
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = T.italic + font(R * 0.07);
    ctx.fillStyle = T['wheel-hub-text-1']; ctx.fillText('SPIN', 0, -R * 0.045);
    ctx.fillStyle = T['wheel-hub-text-2']; ctx.fillText('FRENZY', 0, R * 0.045);
  }
  ctx.restore();
  drawPointer(cx, cy - R * 1.02, 0, T['wheel-pointer'], Math.max(9, R * 0.07));
}

function shade(color, amt) {
  const m = /^#([0-9a-f]{6})$/i.exec(color);
  if (!m) return color;
  const n = parseInt(m[1], 16);
  const f = (c) => clamp(Math.round(amt < 0 ? c * (1 + amt) : c + (255 - c) * amt), 0, 255);
  return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
}

function headBottom() {
  const h = boxOf($('bonusHead'));
  return h ? h.y1 + 6 : 60;
}

// ---------------- Peg Drop
const DROP_ANIM = 3800;
const dropSound = { key: '', row: -1, landed: false };
function drawDrop() {
  const b = S.bonus;
  const rows = b.rows, slots = b.slots.length;
  const top = headBottom() + 12;
  const narrow = (cw - 16) / slots < 30;   // slots too thin for flat labels: write them sideways
  const slotH = narrow ? clamp(ch * 0.13, 34, 58) : clamp(ch * 0.09, 24, 46);
  const slotY = ch - slotH - 10;
  const dy = Math.max(6, (slotY - top - 16) / (rows - 1));
  const dx = Math.min((cw - 16) / slots, dy * 1.9);
  const cx = cw / 2, pegR = Math.max(2, dx * 0.09), ballR = Math.max(4, dx * 0.2);
  ctx.fillStyle = T.peg;
  for (let r = 0; r < rows; r++) for (let j = 0; j <= r; j++) {
    ctx.beginPath(); ctx.arc(cx + (j - r / 2) * dx, top + r * dy, pegR, 0, TAU); ctx.fill();
  }
  const d = b.drop;
  const tIn = srv() - S.starts_at;
  let factor = b.factor || 1;
  let flash = false;
  if (b.stage === 'drop' && d && d.double && tIn > DROP_ANIM) { factor = d.factor_before * 2; flash = true; }
  if (b.stage === 'done' || S.phase === 'payout') factor = b.factor;
  const closed = !!(d && d.closed);
  const finished = b.stage === 'done' || S.phase === 'payout';
  const landed = (b.stage === 'drop' && d && tIn > DROP_ANIM) ? d.slot : finished ? b.final_slot : -1;
  for (let k = 0; k < slots; k++) {
    const x = cx + (k - (slots - 1) / 2) * dx;
    const s = b.slots[k];
    const isD = s === 'double';
    const big = !isD && s >= W.info.drop[3];   // the big edge slots
    ctx.fillStyle = k === landed ? T['drop-landed'] : isD ? (closed ? T['drop-closed'] : T['drop-double'])
      : big ? T['drop-high'] : T['drop-slot'];
    roundRect(x - dx / 2 + 1.5, slotY, dx - 3, slotH, 6); ctx.fill();
    ctx.fillStyle = k === landed ? '#111' : '#fff';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const label = isD ? (closed ? '—' : '×2') : short(s * factor);
    if (narrow) {
      ctx.save();
      ctx.translate(x, slotY + slotH / 2); ctx.rotate(-Math.PI / 2);
      ctx.font = font(Math.min(dx * 0.62, slotH * 0.3, 16));
      ctx.fillText(label, 0, 0);
      ctx.restore();
    } else {
      ctx.font = font(Math.min(dx * 0.34, slotH * 0.42, 18));
      ctx.fillText(label, x, slotY + slotH / 2);
    }
  }
  if (flash) {
    ctx.globalAlpha = 0.25 + 0.2 * Math.sin(performance.now() / 80);
    ctx.fillStyle = T['drop-flash']; ctx.fillRect(0, slotY - 6, cw, slotH + 12);
    ctx.globalAlpha = 1;
    ctx.font = font(clamp(cw * 0.06, 18, 40)); ctx.fillStyle = T['drop-flash'];
    ctx.fillText(`DOUBLE! ×${factor}`, cx, top + rows * dy * 0.45);
  }
  // sounds for this drop: a tick per peg, then the landing
  const soundKey = `${S.round}:${b.drop_index}`;
  if (dropSound.key !== soundKey) { dropSound.key = soundKey; dropSound.row = -1; dropSound.landed = tIn > DROP_ANIM; }
  if (b.stage === 'drop' && d) {
    const pts = [[cx, top - dy * 1.2]];
    let k = 0;
    for (let r = 0; r < d.path.length; r++) {
      pts.push([cx + (k - r / 2) * dx, top + r * dy - pegR - ballR]);
      k += d.path[r];
    }
    pts.push([cx + (k - d.path.length / 2) * dx, slotY + slotH * 0.35]);
    const p = clamp(tIn / DROP_ANIM, 0, 1) * (pts.length - 1);
    const i = Math.min(pts.length - 2, Math.floor(p)), f = p - i;
    if (i > dropSound.row && i > 0 && i < pts.length - 1) { dropSound.row = i; sfx.peg(0.8 + i * 0.03); }
    if (!dropSound.landed && tIn > DROP_ANIM) { dropSound.landed = true; if (d.double) sfx.boost(); else sfx.land(); }
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    const hop = i === 0 ? 0 : dy * 0.45;
    const x = x0 + (x1 - x0) * f, y = y0 + (y1 - y0) * f - hop * Math.sin(Math.PI * f);
    const g = ctx.createRadialGradient(x - ballR * 0.3, y - ballR * 0.3, 1, x, y, ballR);
    g.addColorStop(0, '#ffffff'); g.addColorStop(1, T['drop-ball']);
    ctx.beginPath(); ctx.arc(x, y, ballR, 0, TAU); ctx.fillStyle = g; ctx.fill();
  }
  if (factor > 1 && !flash) {
    ctx.textAlign = 'right'; ctx.font = font(16); ctx.fillStyle = T['drop-flash'];
    ctx.fillText(`Board ×${factor}`, cw - 10, top);
  }
}
function roundRect(x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// ---------------- Frenzy Wheel
function drawFrenzy() {
  const b = S.bonus;
  stepAnim(frenzyWheel);
  const n = b.segments.length, seg = TAU / n;
  // Fit the wheel plus its three pointer labels (one above, two lower left/right) into the space below the title.
  const top = headBottom();
  const pickBar = !$('pointerPick').classList.contains('hidden') ? 58 : 6;
  const avail = ch - top - pickBar;
  // E = centre -> label distance. Vertically the wheel needs E above the centre (top label) and about 0.8 E below
  // (its own rim, which reaches further down than the two lower labels).
  const E = Math.max(40, Math.min((cw / 2 - 46) / 0.866, (avail - 34) / 1.8));
  const L = clamp(E * 0.22, 20, 48);
  const R = (E - L) / 1.1;
  const cx = cw / 2, cy = top + E + 16;
  ctx.save(); ctx.translate(cx, cy);
  ctx.beginPath(); ctx.arc(0, 0, R * 1.07, 0, TAU); ctx.fillStyle = T['frenzy-rim']; ctx.fill();
  for (let i = 0; i < n; i++) {
    const a0 = -Math.PI / 2 + frenzyWheel.angle + i * seg;
    const s = b.segments[i];
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, R, a0, a0 + seg); ctx.closePath();
    ctx.fillStyle = s === 'double' ? T['frenzy-double'] : s === 'triple' ? T['frenzy-triple'] : T.frenzy[i % T.frenzy.length];
    ctx.fill(); ctx.strokeStyle = 'rgba(0,0,0,.3)'; ctx.lineWidth = 1; ctx.stroke();
    if (R * 0.06 >= 6) {
      ctx.save(); ctx.rotate(a0 + seg / 2);
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.font = font(R * 0.06);
      ctx.fillStyle = typeof s === 'number' ? '#fff' : '#1f1300';
      ctx.fillText(s === 'double' ? '×2' : s === 'triple' ? '×3' : String(s), R * 0.95, 0);
      ctx.restore();
    }
  }
  ctx.beginPath(); ctx.arc(0, 0, R * 0.2, 0, TAU); ctx.fillStyle = T['frenzy-hub']; ctx.fill();
  ctx.lineWidth = 3; ctx.strokeStyle = T.title.frenzy; ctx.stroke();
  ctx.restore();

  const spinning = !!frenzyWheel.anim;
  const factors = b.stage === 'spin' ? (spinning ? (b.factors_before || b.factors) : b.factors_after) : (b.factors || [1, 1, 1]);
  const results = b.stage === 'spin' ? (spinning ? (b.results_before || []) : b.results) : b.stage === 'done' ? b.results : [];
  const pickers = [[], [], []];
  const people = Object.fromEntries(S.players.map((p) => [p.id, p]));
  for (const [uid, ptr] of Object.entries(b.picks || {})) pickers[ptr].push(people[uid] || { name: '?' });
  const fs = clamp(R * 0.1, 10, 16);
  for (let p = 0; p < 3; p++) {
    const a = -Math.PI / 2 + p * TAU / 3;
    drawPointer(cx + Math.cos(a) * R * 1.1, cy + Math.sin(a) * R * 1.1, a - Math.PI / 2, T.pointers[p], Math.max(9, R * 0.08));
    // top label centred above its pointer; the lower ones grow outward so they don't cover their pointers
    const align = p === 0 ? 'center' : p === 1 ? 'left' : 'right';
    const lx = cx + Math.cos(a) * E + (p === 0 ? 0 : p === 1 ? -L * 0.4 : L * 0.4), ly = cy + Math.sin(a) * E;
    ctx.textAlign = align; ctx.textBaseline = 'middle';
    ctx.font = font(fs, 800); ctx.fillStyle = T.pointers[p];
    const mine = b.picks && b.picks[myId] === p;
    ctx.fillText(`${W.frenzy_pointers[p]}${mine ? ' (you)' : ''}`, lx, ly - fs * 0.8);
    let line = factors && factors[p] > 1 ? `×${factors[p]}` : '';
    if (results && results[p] != null) line = `${fmt(results[p])}x`;
    ctx.font = font(fs * 1.25); ctx.fillStyle = T.text;
    if (line) ctx.fillText(line, lx, ly + fs * 0.5);
    const shown = Math.min(6, pickers[p].length);
    const startX = align === 'center' ? lx - (shown - 1) * 9 : align === 'left' ? lx + 8 : lx - 8 - (shown - 1) * 18;
    pickers[p].slice(0, 6).forEach((pp, i) => {
      const ax = startX + i * 18, ay = ly + fs * 1.9;
      ctx.beginPath(); ctx.arc(ax, ay, 8, 0, TAU); ctx.fillStyle = T.pointers[p]; ctx.fill();
      ctx.textAlign = 'center'; ctx.font = font(9, 800); ctx.fillStyle = '#fff';
      ctx.fillText((pp.name || '?').charAt(0).toUpperCase(), ax, ay + 0.5);
    });
  }
}

// ------------------------------------------------------------------ timer bar
const STAGE_LABEL = {
  boost: 'NO MORE BETS · BOOST REEL', spin: 'SPINNING…', result: 'RESULT', payout: 'PAYING OUT',
  waiting: 'WAITING FOR PLAYERS',
};
let lastCountdown = 0;
function updateTimer() {
  const total = Math.max(1, S.ends_at - S.starts_at);
  const left = clamp(S.ends_at - srv(), 0, total);
  const fill = $('timerFill'), text = $('timerText');
  if (S.phase === 'betting') {
    const secs = Math.ceil(left / 1000);
    fill.style.width = `${(left / total) * 100}%`;
    text.textContent = `PLACE YOUR BETS · ${secs}s`;
    if (secs <= 3 && secs > 0 && secs !== lastCountdown) { lastCountdown = secs; sfx.countdown(); }
  } else {
    lastCountdown = 0;
    fill.style.width = `${(1 - left / total) * 100}%`;
    let label = STAGE_LABEL[S.phase] || '';
    if (S.phase === 'bonus' && S.bonus) {
      label = W.names[S.bonus.kind].toUpperCase();
      if (S.bonus.stage === 'pick') label += ` · PICK · ${Math.ceil(left / 1000)}s`;
    }
    text.textContent = label;
  }
}

// keep time-based DOM bits (banner reveal delays, coin result) fresh between server messages
setInterval(() => { if (W && S && S.phase === 'bonus') { renderScenes(); renderBanner(); } }, 250);

loadTheme();
resize();
requestAnimationFrame(frame);
boot().catch((e) => { console.error(e); showFatal(`Couldn't start: ${e.message || e}`); });
