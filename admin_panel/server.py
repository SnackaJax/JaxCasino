"""The admin panel: a local website for the bot's owner with stats, every player's balance and balance editing.

Runs inside the bot process (cogs/adminpanel.py) so it shares the bot's database connection and lock: an edit made
here can't collide with a bet. The page itself is a React app built into admin_panel/static (its source is in
admin_panel/web, see the README there).

Security: it listens on ADMIN_PANEL_HOST (127.0.0.1 by default) and every API call needs a signed session cookie,
which you get by entering ADMIN_PANEL_PASSWORD or by signing in with Discord as the bot's owner (or someone in
ADMIN_PANEL_USERS). The cookie is SameSite=Strict and HttpOnly, and changes must be JSON
from the panel's own origin, so another website open in your browser can't make changes through it.

Routes
  GET  /api/session                  whether you're signed in
  POST /api/login, /api/logout       {password}
  GET  /api/auth/discord/start       sends you to Discord to sign in; Discord sends you back to /api/auth/discord
  GET  /api/guilds                   servers with players
  GET  /api/overview?guild=&period=  stats for the dashboard (period: 24h, 7d, 30d, all)
  GET  /api/circulation?guild=&from=&to=   credits held by all players over that time (unix times), in detail
  GET  /api/insights?guild=&period=  the overview's extra charts: house result, players, going broke, heatmap,
                                     where credits came from, bet sizes, wealth spread and records
  GET  /api/games?guild=&period=     every game's numbers, payback over time against its expected payback
  GET  /api/players?guild=           every player with balance and totals
  GET  /api/players/{id}?guild=      one player's stats, games and admin edits
  GET  /api/players/{id}/history?guild=&from=&to=   their balance over that time (unix times)
  GET  /api/players/{id}/insights?guild=   when they play, net per day, game mix, streaks, going broke, payments
  POST /api/players/{id}/balance     {guild, action: set|add|remove, amount, note?}; nothing is posted in Discord
  POST /api/players/{id}/crux        {guild, duration}: puts the player on a crux (like /crux), e.g. "3d" or "1d12h"
  POST /api/players/{id}/crux/remove {guild}: ends the player's /crux (break from the casino) early
  GET  /api/edits?guild=             the latest balance edits made in the panel
  GET  /api/activity?guild=&before=&after=   games and other credit movements, newest first
  GET  /api/settings?guild=          the server's game switches (GAME_SETTINGS), e.g. {"bj_double_side": false}
  POST /api/settings                 {guild, key, value}: changes one of them; the games pick it up straight away
  everything else                    the page (admin_panel/static)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlencode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import aiohttp
from aiohttp import web

from admin_panel import insights, queries
from database import Database
from utils.crux import MAX_SECONDS, MIN_SECONDS, describe, parse_duration

log = logging.getLogger("jaxcasino.adminpanel")

HERE = Path(__file__).parent
STATIC = HERE / "static"
COOKIE = "jaxcasino_admin"
STATE_COOKIE = "jaxcasino_admin_state"   # ties Discord's answer to the sign-in this browser started
DISCORD = "https://discord.com"
DISCORD_CALLBACK = "/api/auth/discord"
SESSION_DAYS = 30
MAX_AMOUNT = 10**15
MAX_NOTE = 200
# Game switches per server, saved in the settings table (database.get_setting). Each: (default, label for the log).
# bj_double_side: blackjack in the Activity pays side bet wins out, then doubles them (activity/blackjack.py).
GAME_SETTINGS: dict[str, tuple[Any, str]] = {"bj_double_side": (False, "Blackjack double side bets")}
LOGIN_FAILS = 5            # wrong passwords allowed per LOGIN_WINDOW before logins pause
LOGIN_WINDOW = 60


class Directory(Protocol):
    """Who's who: names and avatars come from Discord, the database only knows IDs."""
    def guilds(self) -> dict[int, dict]: ...                           # id -> {name, icon}
    def member(self, guild_id: int, user_id: int) -> dict | None: ...  # {name, avatar, in_server}
    async def crux_started(self, guild_id: int, user_id: int, until: float) -> None: ...  # closes their games
    def client_id(self) -> int | None: ...                             # the bot's application id (Discord sign-in)
    async def owner_ids(self) -> set[int]: ...                         # the bot's owner and team (Discord sign-in)
    async def setting_changed(self, guild_id: int, key: str, value: Any) -> None: ...  # tells the running games




@dataclass
class AdminPanel:
    db: Database
    password: str
    directory: Directory
    discord_secret: str = ""                                  # DISCORD_CLIENT_SECRET: Discord sign-in needs it
    discord_users: frozenset[int] = frozenset()               # ADMIN_PANEL_USERS: let in besides the owner
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
            open_paths = ("/api/session", "/api/login", "/api/auth/discord/start", DISCORD_CALLBACK)
            if path not in open_paths and not self.valid_session(request.cookies.get(COOKIE)):
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
        out = {"authenticated": ok, "discord": self.discord_ready()}
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

    # ------------------------------------------------------------ Discord sign-in
    def discord_ready(self) -> bool:
        client_id = getattr(self.directory, "client_id", None)   # test servers may not have one
        return bool(self.discord_secret and client_id and client_id())

    @staticmethod
    def callback_url(request: web.Request) -> str:
        """Where Discord sends you back: this panel, at the address you opened it on. Discord only accepts it when
        it's listed under OAuth2 -> Redirects in the Developer Portal."""
        return f"{request.scheme}://{request.host}{DISCORD_CALLBACK}"

    def signed_in(self, location: str = "/") -> web.Response:
        resp = web.HTTPFound(location)
        resp.set_cookie(COOKIE, self.new_session(), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="Strict", path="/")
        resp.del_cookie(STATE_COOKIE, path=DISCORD_CALLBACK)
        return resp

    @staticmethod
    def sign_in_failed(reason: str) -> web.Response:
        resp = web.HTTPFound(f"/?signin={reason}")
        resp.del_cookie(STATE_COOKIE, path=DISCORD_CALLBACK)
        return resp

    async def discord_start(self, request: web.Request) -> web.Response:
        if not self.discord_ready():
            return self.sign_in_failed("unavailable")
        state = secrets.token_urlsafe(24)
        query = urlencode({"client_id": str(self.directory.client_id()), "response_type": "code", "scope": "identify",
                           "redirect_uri": self.callback_url(request), "state": state, "prompt": "none"})
        resp = web.HTTPFound(f"{DISCORD}/oauth2/authorize?{query}")
        # Lax, not Strict: the browser has to send it along when Discord sends you back here
        resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="Lax", path=DISCORD_CALLBACK)
        return resp

    async def discord_callback(self, request: web.Request) -> web.Response:
        q = request.query
        if q.get("error"):
            return self.sign_in_failed("cancelled")
        state, expected = q.get("state", ""), request.cookies.get(STATE_COOKIE, "")
        if not state or not expected or not hmac.compare_digest(state, expected):
            return self.sign_in_failed("expired")
        if not self.discord_ready() or not q.get("code"):
            return self.sign_in_failed("failed")
        form = {"client_id": str(self.directory.client_id()), "client_secret": self.discord_secret,
                "grant_type": "authorization_code", "code": q["code"], "redirect_uri": self.callback_url(request)}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
                async with http.post(f"{DISCORD}/api/v10/oauth2/token", data=form) as r:
                    token = await r.json(content_type=None)
                    if r.status != 200 or not isinstance(token, dict) or "access_token" not in token:
                        log.warning("Admin panel: Discord sign-in, code exchange failed (%s): %s", r.status, token)
                        return self.sign_in_failed("failed")
                headers = {"Authorization": f"Bearer {token['access_token']}"}
                async with http.get(f"{DISCORD}/api/v10/users/@me", headers=headers) as r:
                    user = await r.json(content_type=None)
                    if r.status != 200 or not isinstance(user, dict):
                        return self.sign_in_failed("failed")
            uid = int(user["id"])
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as e:
            log.warning("Admin panel: Discord sign-in, no usable answer from Discord: %r", e)
            return self.sign_in_failed("failed")
        name = user.get("global_name") or user.get("username") or uid
        allowed = uid in self.discord_users
        if not allowed:
            try:
                allowed = uid in await self.directory.owner_ids()
            except Exception:
                log.exception("Admin panel: couldn't look up the bot's owner")
        if not allowed:
            log.warning("Admin panel: %s (%s) tried to sign in with Discord from %s, not allowed", name, uid,
                        request.remote)
            return self.sign_in_failed("denied")
        log.info("Admin panel: %s (%s) signed in with Discord from %s", name, uid, request.remote)
        return self.signed_in()

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
        data = await queries.overview(self.db, gid, self.period_arg(request))
        recent = await queries.edits(self.db, gid, 8)
        for e in recent:
            e.update(self.who(gid, e["user_id"]))
        data["edits"] = recent
        return web.json_response(data)

    def period_arg(self, request: web.Request) -> str:
        period = request.query.get("period", "24h")
        if period not in queries.PERIODS:
            raise web.HTTPBadRequest(text="unknown period")
        return period

    def people(self, gid: int, data) -> dict[str, dict]:
        """Names and avatars for every user_id anywhere in `data`."""
        found: dict[str, dict] = {}

        def walk(x):
            if isinstance(x, dict):
                uid = x.get("user_id")
                if isinstance(uid, str) and uid not in found:
                    found[uid] = self.who(gid, uid)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(data)
        return found

    async def insights(self, request: web.Request) -> web.Response:
        gid = self.guild_arg(request)
        data = await insights.overview_extras(self.db, gid, self.period_arg(request))
        data["people"] = self.people(gid, data)
        return web.json_response(data)

    async def games(self, request: web.Request) -> web.Response:
        gid = self.guild_arg(request)
        data = await insights.games(self.db, gid, self.period_arg(request))
        data["people"] = self.people(gid, data)
        return web.json_response(data)

    async def player_insights(self, request: web.Request) -> web.Response:
        gid, uid = self.guild_arg(request), self.user_arg(request)
        if not await queries.player_exists(self.db, gid, uid):
            raise web.HTTPNotFound(text="no such player")
        data = await insights.player_extras(self.db, gid, uid)
        data["people"] = self.people(gid, data)
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

    def window_arg(self, request: web.Request) -> tuple[float, float]:
        """?from=&to= as unix times, clipped to now (from missing or 0 = from the start)."""
        now = time.time()
        try:
            start = max(0.0, float(request.query.get("from") or 0))
            end = min(now, float(request.query.get("to") or now))
        except ValueError:
            raise web.HTTPBadRequest(text="from and to are unix times")
        if end <= start:
            raise web.HTTPBadRequest(text="to must be after from")
        return start, end

    async def circulation(self, request: web.Request) -> web.Response:
        """The overview's "credits in circulation" line for a stretch you zoomed into."""
        gid = self.guild_arg(request)
        start, end = self.window_arg(request)
        points = await self.db.circulation_history(gid, start, buckets=400, until=end)
        return web.json_response({"from": start, "to": end, "points": [[ts, total] for ts, total in points]})

    async def player_history(self, request: web.Request) -> web.Response:
        gid, uid = self.guild_arg(request), self.user_arg(request)
        if not await queries.player_exists(self.db, gid, uid):
            raise web.HTTPNotFound(text="no such player")
        start, end = self.window_arg(request)
        points = await queries.balance_window(self.db, gid, uid, start, end)
        return web.json_response({"from": start, "to": end, "points": points})

    async def give_crux(self, request: web.Request) -> web.Response:
        """Puts a player on a crux from the panel, the same break /crux gives. {guild, duration}. A running crux
        gets the new end time, longer or shorter."""
        uid = self.user_arg(request)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="bad JSON")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="bad request")
        gid = str(body.get("guild", ""))
        if not gid.isdigit():
            raise web.HTTPBadRequest(text="guild is missing")
        seconds = parse_duration(str(body.get("duration", "")))
        if seconds is None:
            return web.json_response({"error": "Can't read that duration. Try 2h, 3d, 1w or 1d12h."}, status=400)
        if not MIN_SECONDS <= seconds <= MAX_SECONDS:
            return web.json_response({"error": "A crux lasts from 10 minutes up to a year."}, status=400)
        if not await queries.player_exists(self.db, int(gid), uid):
            raise web.HTTPNotFound(text="no such player")
        until = time.time() + seconds
        await self.db.set_crux(int(gid), uid, until)
        log.info("Admin panel: crux for %s in %s, %s", uid, gid, describe(seconds))
        try:
            await self.directory.crux_started(int(gid), uid, until)
        except Exception:
            log.exception("Couldn't close %s's casino games for their crux", uid)
        return web.json_response({"ok": True, "until": until})

    async def remove_crux(self, request: web.Request) -> web.Response:
        """Ends a player's /crux early (only an admin can). {guild}"""
        uid = self.user_arg(request)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="bad JSON")
        gid = str(body.get("guild", "")) if isinstance(body, dict) else ""
        if not gid.isdigit():
            raise web.HTTPBadRequest(text="guild is missing")
        if not await self.db.remove_crux(int(gid), uid):
            return web.json_response({"error": "That player doesn't have a crux (any more)."}, status=409)
        log.info("Admin panel: ended the crux of %s in %s", uid, gid)
        return web.json_response({"ok": True})

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


    # ------------------------------------------------------------ game settings
    async def settings(self, request: web.Request) -> web.Response:
        gid = self.guild_arg(request)
        return web.json_response({k: await self.db.get_setting(gid, k, default)
                                  for k, (default, _) in GAME_SETTINGS.items()})

    async def change_setting(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="bad JSON")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="bad request")
        gid = str(body.get("guild", ""))
        if not gid.isdigit():
            raise web.HTTPBadRequest(text="guild is missing")
        key = body.get("key")
        if key not in GAME_SETTINGS:
            raise web.HTTPBadRequest(text="no such setting")
        default, label = GAME_SETTINGS[key]
        value = body.get("value")
        if type(value) is not type(default):
            raise web.HTTPBadRequest(text="wrong kind of value")
        await self.db.set_setting(int(gid), key, value)
        log.info("Admin panel: %s set to %r in %s", label, value, gid)
        changed = getattr(self.directory, "setting_changed", None)
        if changed is not None:
            try:
                await changed(int(gid), key, value)
            except Exception:
                log.exception("Couldn't pass %s on to the running games", key)
        return web.json_response({"key": key, "value": value})

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
        r.add_get("/api/auth/discord/start", self.discord_start)
        r.add_get(DISCORD_CALLBACK, self.discord_callback)
        r.add_get("/api/guilds", self.guilds)
        r.add_get("/api/overview", self.overview)
        r.add_get("/api/circulation", self.circulation)
        r.add_get("/api/players", self.players)
        r.add_get("/api/players/{user_id}", self.player)
        r.add_get("/api/players/{user_id}/history", self.player_history)
        r.add_get("/api/players/{user_id}/insights", self.player_insights)
        r.add_get("/api/insights", self.insights)
        r.add_get("/api/games", self.games)
        r.add_post("/api/players/{user_id}/balance", self.edit_balance)
        r.add_post("/api/players/{user_id}/crux", self.give_crux)
        r.add_post("/api/players/{user_id}/crux/remove", self.remove_crux)
        r.add_get("/api/edits", self.edits)
        r.add_get("/api/activity", self.activity)
        r.add_get("/api/settings", self.settings)
        r.add_post("/api/settings", self.change_setting)
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

