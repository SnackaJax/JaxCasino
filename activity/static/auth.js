// Sign-in shared by every page of the Activity (the game menu, Spin Frenzy and Banger Bash).
//
// Only the menu page (lobby.html, the Activity's first page) talks to Discord. The Discord SDK addresses its
// handshake to document.referrer, which is only Discord while the Activity is still on its first page: after a
// navigation the referrer is our own menu page, the handshake goes nowhere and the page hangs. So the menu never
// navigates away; it shows each game in a full-size frame inside itself, and the games get their session from the
// menu (window.parent, same origin) instead of from Discord. Opened on their own (local dev server, or an old
// link), the game pages sign in themselves the same way the menu does.
// In the local dev server (python -m activity.devserver) ?user=<n>&name=<name> signs in instead of Discord.
import { DiscordSDK } from './vendor/discord-sdk/index.mjs';

const RETRY_KEY = 'gamblebot-reauth';

// The menu page that is showing this game, if any.
function shell() {
  try {
    if (window.parent !== window && window.parent.__gamblebot) return window.parent.__gamblebot;
  } catch (e) { /* not our page */ }
  return null;
}

// ------------------------------------------------------------------ the top page: talks to Discord
let sdk = null;
let current = null;   // Promise of { cfg, session, joinInfo } or { cfg, error }

async function signInTop(onStatus) {
  const params = new URLSearchParams(location.search);
  const cfg = await fetch('/api/config').then((r) => r.json());
  if (params.get('frame_id')) {
    if (!sdk) {
      onStatus('Connecting to Discord…');
      sdk = new DiscordSDK(cfg.client_id, { disableConsoleLogOverride: true });
      await sdk.ready();
    }
    const joinInfo = { instance_id: sdk.instanceId, guild_id: sdk.guildId, channel_id: sdk.channelId };
    onStatus('Signing in with Discord…');
    const { code } = await sdk.commands.authorize({
      client_id: cfg.client_id, response_type: 'code', state: '', prompt: 'none', scope: ['identify'],
    });
    const res = await fetch('/api/token', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
    });
    const tok = await res.json();
    if (!res.ok) throw new Error(tok.error || 'Sign-in failed.');
    try { await sdk.commands.authenticate({ access_token: tok.access_token }); } catch (e) { /* not needed to play */ }
    return { cfg, session: tok.session, joinInfo };
  }
  if (cfg.dev) {
    const q = new URLSearchParams({ user: params.get('user') || '1', name: params.get('name') || '' });
    const d = await fetch('/api/dev-login?' + q).then((r) => r.json());
    return { cfg, session: d.session, joinInfo: { instance_id: d.instance_id, guild_id: d.guild_id } };
  }
  return { cfg, error: 'Open the games from a Discord voice call: press the 🚀 Activities button and pick the bot.' };
}

// Called by the menu page: makes it the place games get their session from.
export function becomeShell(handlers) {
  window.__gamblebot = {
    signIn: () => signIn(),
    // the bot restarted and forgot the session: sign in again with the Discord connection we already have
    refresh: () => { current = signInTop(() => {}); return current; },
    ...handlers,
  };
}

// Returns { cfg, session, joinInfo } or { cfg, error }.
export async function signIn(onStatus = () => {}) {
  const s = shell();
  if (s) {
    onStatus('Signing in…');
    return s.signIn();
  }
  if (!current) current = signInTop(onStatus).catch((e) => { current = null; throw e; });
  return current;
}

// The server forgot our session (the bot restarted): sign in again once, instead of showing an error.
export function retryAfterExpiredSession() {
  try {
    const last = Number(sessionStorage.getItem(RETRY_KEY) || 0);
    if (Date.now() - last < 30000) return false;
    sessionStorage.setItem(RETRY_KEY, String(Date.now()));
  } catch (e) { return false; }
  const s = shell();
  if (s) {
    s.refresh().then(() => location.reload(), () => location.reload());
  } else {
    location.reload();
  }
  return true;
}

// Back to the game menu (or, outside the menu, to another page of the Activity).
export function go(path) {
  const s = shell();
  if (s && path === '/') { s.menu(); return; }
  location.href = path + location.search;
}
