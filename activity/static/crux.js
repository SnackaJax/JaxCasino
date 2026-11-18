// The crux screen: a player who started /crux (a break from the casino) sees this instead of the games, with the
// time left counting down. Every page loads this file; it defines window.showCrux(until), until in unix seconds.
(function () {
  const pad = (n) => String(n).padStart(2, '0');
  function left(s) {
    if (s <= 0) return '0:00';
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    return d ? `${d}d ${pad(h)}:${pad(m)}:${pad(sec)}` : h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }
  const css = `
.crux-screen { position: fixed; inset: 0; z-index: 2147483000; display: grid; place-items: center; padding: 20px;
  background: radial-gradient(ellipse at 50% 30%, #13325a 0%, #071426 70%); color: #e6f0ff;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; text-align: center; }
.crux-card { max-width: 440px; width: 100%; padding: 28px 24px; border-radius: 18px; background: rgba(255,255,255,.05);
  border: 1px solid rgba(160, 200, 255, .25); box-shadow: 0 20px 60px rgba(0,0,0,.45); }
.crux-ice { font-size: 56px; line-height: 1; margin-bottom: 10px; }
.crux-card h1 { margin: 0 0 6px; font-size: 24px; letter-spacing: .5px; }
.crux-card p { margin: 6px 0; color: #b9cbe6; font-size: 14px; line-height: 1.5; }
.crux-left { margin: 18px 0 4px; font-size: 40px; font-weight: 800; font-variant-numeric: tabular-nums; color: #9fd0ff; }
.crux-label { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: #7f9cc4; }
.crux-small { font-size: 12px; color: #7f9cc4; }
.crux-btn { margin-top: 16px; padding: 10px 18px; border-radius: 10px; border: 0; font-weight: 700; cursor: pointer;
  background: #9fd0ff; color: #08203c; }`;
  window.showCrux = function (until) {
    until = Number(until) || 0;
    let el = document.getElementById('cruxScreen');
    if (!el) {
      const style = document.createElement('style');
      style.textContent = css;
      document.head.appendChild(style);
      el = document.createElement('div');
      el.id = 'cruxScreen';
      el.className = 'crux-screen';
      el.setAttribute('role', 'alert');
      document.body.appendChild(el);
    }
    const ends = new Date(until * 1000).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    el.innerHTML = `<div class="crux-card">
      <div class="crux-ice">🧊</div>
      <h1>You have a crux</h1>
      <p>You're taking a break from the casino. The games and the bot's commands are off for you until it ends.</p>
      <div class="crux-label">Time left</div>
      <div class="crux-left" id="cruxLeft">${left(until - Date.now() / 1000)}</div>
      <p class="crux-small">Ends ${ends}</p>
      <p class="crux-small">Only a server admin can end it early.</p>
    </div>`;
    clearInterval(window.__cruxTimer);
    window.__cruxTimer = setInterval(() => {
      const s = until - Date.now() / 1000;
      const out = document.getElementById('cruxLeft');
      if (out) out.textContent = left(s);
      if (s <= 0) {
        clearInterval(window.__cruxTimer);
        el.querySelector('.crux-card').innerHTML = `<div class="crux-ice">✨</div><h1>Your crux is over</h1>
          <p>Welcome back! The casino is open for you again.</p>
          <button class="crux-btn" type="button">Back to the casino</button>`;
        el.querySelector('.crux-btn').addEventListener('click', () => {
          try { window.top.location.reload(); } catch (e) { location.reload(); }
        });
      }
    }, 1000);
  };
})();
