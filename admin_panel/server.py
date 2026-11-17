"""The admin panel: a local website for the bot's owner with stats, every player's balance and balance editing.

Runs inside the bot process (cogs/adminpanel.py) so it shares the bot's database connection and lock: an edit made
here can't collide with a bet. The page itself is a React app built into admin_panel/static (its source is in
admin_panel/web, see the README there).

Security: it listens on ADMIN_PANEL_HOST (127.0.0.1 by default) and every API call needs a signed session cookie,
which you get by entering ADMIN_PANEL_PASSWORD. The cookie is SameSite=Strict and HttpOnly, and changes must be JSON
from the panel's own origin, so another website open in your browser can't make changes through it.

Routes
  GET  /api/session                  whether you're signed in
  POST /api/login, /api/logout       {password}
  GET  /api/guilds                   servers with players
  GET  /api/overview?guild=&period=  stats for the dashboard (period: 24h, 7d, 30d, all)
  GET  /api/players?guild=           every player with balance and totals
  GET  /api/players/{id}?guild=      one player's history, games and admin edits
  POST /api/players/{id}/balance     {guild, action: set|add|remove, amount, note?}; nothing is posted in Discord
  GET  /api/edits?guild=             the latest balance edits made in the panel
  GET  /api/activity?guild=&before=&after=   games and other credit movements, newest first
  everything else                    the page (admin_panel/static)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from aiohttp import web

from admin_panel import queries
from database import Database

log = logging.getLogger("jaxcasino.adminpanel")

HERE = Path(__file__).parent
STATIC = HERE / "static"
COOKIE = "jaxcasino_admin"
SESSION_DAYS = 30
MAX_AMOUNT = 10**15
MAX_NOTE = 200
LOGIN_FAILS = 5            # wrong passwords allowed per LOGIN_WINDOW before logins pause
LOGIN_WINDOW = 60


class Directory(Protocol):
    """Who's who: names and avatars come from Discord, the database only knows IDs."""
    def guilds(self) -> dict[int, dict]: ...                           # id -> {name, icon}
    def member(self, guild_id: int, user_id: int) -> dict | None: ...  # {name, avatar, in_server}




@dataclass
class AdminPanel:
    db: Database
    password: str
    directory: Directory
    runner: web.AppRunner | None = None
    _fails: list[float] = field(default_factory=list)

    def __post_init__(self):
        self._key = hmac.new(self.password.encode(), b"jaxcasino-admin-panel-session", hashlib.sha256).digest()


    # ------------------------------------------------------------ sessions
    def _sign(self, expires: int) -> str:
        return hmac.new(self._key, str(expires).encode(), hashlib.sha256).hexdigest()

    def new_session(self) -> str:
        expires = int(time.time()) + SESSION_DAYS * 86400
        return f"{expires}.{self._sign(expires)}"

    def valid_session(self, value: str | None) -> bool:
        if not value or "." not in value:
            return False
        expires, sig = value.split(".", 1)
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(sig, self._sign(int(expires)))

    # ------------------------------------------------------------ helpers
    def who(self, guild_id: int, user_id: int | str | None) -> dict:
        if user_id is None:
            return {"name": None, "avatar": None, "in_server": False}
        m = self.directory.member(guild_id, int(user_id))
        return m or {"name": None, "avatar": None, "in_server": False}

    @staticmethod
    def guild_arg(request: web.Request) -> int:
        raw = request.query.get("guild", "")
        if not raw.isdigit():
            raise web.HTTPBadRequest(text="guild is missing")
        return int(raw)

    # ------------------------------------------------------------ middleware
    @web.middleware
    async def guard(self, request: web.Request, handler):
        path = request.path
        if path.startswith("/api/"):
            if request.method not in ("GET", "HEAD"):
                # Changes only as JSON from this page: a form or script on another website can't send JSON here
                # without the browser asking first (and we never say yes), and its Origin wouldn't match.
                if request.content_type != "application/json":
                    return web.json_response({"error": "JSON only"}, status=415)
                origin = request.headers.get("Origin")
                if origin and origin.split("://", 1)[-1] != request.host:
                    return web.json_response({"error": "wrong origin"}, status=403)
            if path not in ("/api/session", "/api/login") and not self.valid_session(request.cookies.get(COOKIE)):
                return web.json_response({"error": "not signed in"}, status=401)
            try:
                resp = await handler(request)
            except web.HTTPException as e:
                if e.status >= 400:
                    return web.json_response({"error": e.text or e.reason}, status=e.status)
                raise
            resp.headers["Cache-Control"] = "no-store"
        else:
            resp = await handler(request)
        if resp.prepared:     # a websocket: its headers are already sent
            return resp
        # framing only by the panel itself; a route may set its own CSP
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers.setdefault("Content-Security-Policy", (
            "default-src 'self'; img-src 'self' data: https://cdn.discordapp.com; font-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'self'"))
        return resp

    # ------------------------------------------------------------ auth routes
    async def session(self, request: web.Request) -> web.Response:
        ok = self.valid_session(request.cookies.get(COOKIE))
        out = {"authenticated": ok}
        return web.json_response(out)

    async def login(self, request: web.Request) -> web.Response:
        now = time.time()
        self._fails = [t for t in self._fails if now - t < LOGIN_WINDOW]
        if len(self._fails) >= LOGIN_FAILS:
            return web.json_response({"error": "Too many wrong passwords. Wait a minute and try again."},
                                     status=429)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="bad JSON")
        given = str(body.get("password", "")) if isinstance(body, dict) else ""
        if not hmac.compare_digest(given.encode(), self.password.encode()):
            self._fails.append(now)
            log.warning("Admin panel: wrong password from %s", request.remote)
            return web.json_response({"error": "Wrong password."}, status=401)
        resp = web.json_response({"authenticated": True})
        resp.set_cookie(COOKIE, self.new_session(), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="Strict", path="/")
        log.info("Admin panel: signed in from %s", request.remote)
        return resp

    async def logout(self, request: web.Request) -> web.Response:
        resp = web.json_response({"authenticated": False})
        resp.del_cookie(COOKIE, path="/")
        return resp

    # ------------------------------------------------------------ data routes
    async def guilds(self, request: web.Request) -> web.Response:
        known = self.directory.guilds()
        counts = await queries.guild_ids(self.db)
        out = []
        for gid in {*known, *counts}:
            g = known.get(gid, {})
            out.append({"id": str(gid), "name": g.get("name") or f"Server {gid}", "icon": g.get("icon"),
                        "players": counts.get(gid, 0), "bot_in_server": gid in known})
        out.sort(key=lambda g: (-g["players"], g["name"].lower()))
        return web.json_response(out)

    async def overview(self, request: web.Request) -> web.Response:
        gid = self.guild_arg(request)
        period = request.query.get("period", "30d")
        if period not in queries.PERIODS:
            raise web.HTTPBadRequest(text="unknown period")
        data = await queries.overview(self.db, gid, period)
        recent = await queries.edits(self.db, gid, 8)
        for e in recent:
            e.update(self.who(gid, e["user_id"]))
        data["edits"] = recent
        return web.json_response(data)

    async def players(self, request: web.Request) -> web.Response:
        gid = self.guild_arg(request)
        rows = await queries.players(self.db, gid)
        for r in rows:
            r.update(self.who(gid, r["id"]))
        return web.json_response(rows)

    def user_arg(self, request: web.Request) -> int:
        raw = request.match_info["user_id"]
        if not raw.isdigit():
            raise web.HTTPBadRequest(text="bad player id")
        return int(raw)

    async def player(self, request: web.Request) -> web.Response:
        gid, uid = self.guild_arg(request), self.user_arg(request)
        if not await queries.player_exists(self.db, gid, uid):
            raise web.HTTPNotFound(text="no such player")
        data = await queries.player_detail(self.db, gid, uid)
        data["player"] = {"id": str(uid), **self.who(gid, uid)}
        return web.json_response(data)

    async def edit_balance(self, request: web.Request) -> web.Response:
        uid = self.user_arg(request)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="bad JSON")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="bad request")
        gid = str(body.get("guild", ""))
        action = body.get("action")
        amount = body.get("amount")
        note = body.get("note") or None
        if not gid.isdigit():
            raise web.HTTPBadRequest(text="guild is missing")
        gid = int(gid)
        if action not in ("set", "add", "remove"):
            raise web.HTTPBadRequest(text="action must be set, add or remove")
        if not isinstance(amount, int) or isinstance(amount, bool) or not 0 <= amount <= MAX_AMOUNT:
            raise web.HTTPBadRequest(text=f"amount must be a whole number from 0 to {MAX_AMOUNT:,}")
        if action != "set" and amount == 0:
            raise web.HTTPBadRequest(text="amount must be more than 0")
        if note is not None:
            note = str(note).strip()[:MAX_NOTE] or None
        if not await queries.player_exists(self.db, gid, uid):
            raise web.HTTPNotFound(text="no such player")
        before, after = await self.db.admin_edit(gid, uid, action, amount, note)
        log.info("Admin panel: %s %s for %s in %s: %s -> %s%s", action, amount, uid, gid, before, after,
                 f" ({note})" if note else "")
        return web.json_response({"before": before, "after": after})

    async def edits(self, request: web.Request) -> web.Response:
        gid = self.guild_arg(request)
        rows = await queries.edits(self.db, gid, min(int(request.query.get("limit", "50") or 50), 500))
        for r in rows:
            r.update(self.who(gid, r["user_id"]))
        return web.json_response(rows)

    async def activity(self, request: web.Request) -> web.Response:
        gid = self.guild_arg(request)

        def num(key):
            raw = request.query.get(key)
            try:
                return float(raw) if raw else None
            except ValueError:
                raise web.HTTPBadRequest(text=f"bad {key}")
        limit = min(int(request.query.get("limit", "100") or 100), 500)
        rows = await queries.activity(self.db, gid, num("before"), num("after"), limit)
        names: dict[str, dict] = {}
        for r in rows:
            for key in ("user_id", "other_id"):
                uid = r[key]
                if uid is not None and uid not in names:
                    names[uid] = self.who(gid, uid)
        return web.json_response({"rows": rows, "people": names})


    # ------------------------------------------------------------ page
    async def page(self, request: web.Request) -> web.StreamResponse:
        rel = request.match_info.get("tail", "")
        if rel:
            target = (STATIC / rel).resolve()
            if STATIC.resolve() in target.parents and target.is_file():
                resp = web.FileResponse(target)
                if rel.startswith("assets/"):   # file names carry a content hash
                    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return resp
        index = STATIC / "index.html"
        if not index.is_file():
            return web.Response(text="The admin panel's page hasn't been built. See admin_panel/web/README.md.",
                                status=503)
        resp = web.FileResponse(index)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ------------------------------------------------------------ server
    def app(self) -> web.Application:
        app = web.Application(middlewares=[self.guard], client_max_size=64 * 1024)
        r = app.router
        r.add_get("/api/session", self.session)
        r.add_post("/api/login", self.login)
        r.add_post("/api/logout", self.logout)
        r.add_get("/api/guilds", self.guilds)
        r.add_get("/api/overview", self.overview)
        r.add_get("/api/players", self.players)
        r.add_get("/api/players/{user_id}", self.player)
        r.add_post("/api/players/{user_id}/balance", self.edit_balance)
        r.add_get("/api/edits", self.edits)
        r.add_get("/api/activity", self.activity)
        r.add_route("*", "/api/{tail:.*}", self._api_404)
        r.add_get("/{tail:.*}", self.page)
        return app

    async def _api_404(self, request: web.Request) -> web.Response:
        raise web.HTTPNotFound(text="no such API route")

    async def start(self, host: str, port: int) -> None:
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, host, port).start()
        log.info("Admin panel running on http://%s:%s", "127.0.0.1" if host in ("0.0.0.0", "::") else host, port)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

