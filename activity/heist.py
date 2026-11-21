"""Raccoon Heist inside the Discord Activity.

Every player plays their own vault. Everyone in the same server who has Raccoon Heist open shares one lobby (voice
call or text channel alike): they see who else is playing and their big wins. The game is utils/heist_engine.py.
The server decides every result; the page only animates what it is sent.

A round has no choices (the Night Shift and Vault Grab play themselves), so it is played out completely the moment
it is paid for, settled straight away and sent to the page with its payout. Until the page says it has finished
showing it ("shown"), the other players see the player as still playing, so the result isn't spoiled.

Websocket /ws/heist. The page sends:
  {"type": "join", "session", "instance_id", "guild_id"}         first message
  {"type": "spin", "bet": int, "mode": "spin"|<buy>}            a paid spin or a bonus buy (costs its price x bet)
  {"type": "announce", "round": int, "what": "bonus"|"boss"|"win"}   the page has shown a bonus, the Boss or a big
                                                                win; the server checks it and tells the others
  {"type": "shown", "round": int}                               the page has finished showing a round
  {"type": "restart"}                                           the bot's /restart when broke
The server sends "welcome", "round" (a played round with its payout and the new balance), "me" (balance),
"players", "feed", "feed_all", "restart", "error" and "fatal".
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web

import config
from utils import heist_engine as E
from utils.economy import Wager
from utils.rng import rng
from activity.fatal import fatal_text

log = logging.getLogger("jaxcasino.activity.heist")

GAME = "raccoonheist"
TABLE_SHUTDOWN = 120         # a lobby with nobody connected is dropped after this
PRESENT_TIMEOUT = 600        # a round still "being shown" after this long counts as shown (bonuses take a while)
MAX_MESSAGES_PER_SECOND = 12
FEED = 20
BIG_WIN = 10                 # times the bet: announced to the other players


def now_ms() -> int:
    return int(time.time() * 1000)


class Rejected(Exception):
    pass


@dataclass
class HeistPlayer:
    user_id: int
    name: str
    avatar_url: str | None
    sockets: set = field(default_factory=set)
    round_id: int = 0
    last_win: int = 0            # what the others see: only updated once the page has shown the round
    last_x: float = 0.0
    settled: dict | None = None  # the last round: {"round", "bet", "stake", "payout", "x", "bonus", "boss"}
    presenting: float | None = None   # time the page started showing the last round (None: shown)
    announced: set = field(default_factory=set)
    restart_presses: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self) -> dict:
        return {"id": str(self.user_id), "name": self.name, "avatar": bool(self.avatar_url),
                "online": bool(self.sockets), "status": "spinning" if self.presenting else "idle",
                "last_win": self.last_win, "last_x": round(self.last_x, 2)}


class Table:
    """The Raccoon Heist lobby of one server."""

    def __init__(self, hub: "HeistHub", guild_id: int):
        self.hub = hub
        self.guild_id = guild_id
        self.players: dict[int, HeistPlayer] = {}
        self.feed: list[dict] = []
        self.empty_since: float | None = None

    @property
    def db(self):
        return self.hub.server.db

    async def send(self, ws: web.WebSocketResponse, data: dict) -> None:
        if ws.closed:
            return
        data["now"] = now_ms()
        try:
            await ws.send_str(json.dumps(data, separators=(",", ":")))
        except (ConnectionResetError, RuntimeError, aiohttp.ClientError):
            pass

    async def to_player(self, p: HeistPlayer, data: dict) -> None:
        await asyncio.gather(*(self.send(ws, dict(data)) for ws in list(p.sockets)))

    async def broadcast(self, data: dict) -> None:
        await asyncio.gather(*(self.send(ws, dict(data)) for p in self.players.values() for ws in list(p.sockets)))

    async def push_players(self) -> None:
        await self.broadcast({"type": "players", "players": [p.public() for p in self.players.values()
                                                             if p.sockets]})

    async def push_me(self, p: HeistPlayer, balance: int | None = None) -> None:
        if balance is None:
            balance = await self.db.get_balance(self.guild_id, p.user_id)
        await self.to_player(p, {"type": "me", "balance": balance})

    async def handle(self, p: HeistPlayer, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "spin":
            bet = msg.get("bet")
            mode = msg.get("mode", "spin")
            if not isinstance(bet, int) or isinstance(bet, bool):
                raise Rejected("Invalid bet.")
            if mode not in E.MODES:
                raise Rejected("Unknown buy.")
            await self.spin(p, bet, mode)
        elif kind == "announce":
            await self.announce(p, msg.get("round"), msg.get("what"))
        elif kind == "shown":
            if p.presenting and msg.get("round") == p.round_id:
                self.done_presenting(p)
                await self.push_players()
        elif kind == "restart":
            await self.restart(p)

    def done_presenting(self, p: HeistPlayer) -> None:
        p.presenting = None
        if p.settled:
            p.last_win, p.last_x = p.settled["payout"], p.settled["x"]

    async def spin(self, p: HeistPlayer, bet: int, mode: str) -> None:
        s = self.hub.server.settings
        if bet < s.min_bet:
            raise Rejected(f"The minimum bet is {s.min_bet:,}.")
        if s.max_bet and bet > s.max_bet:
            raise Rejected(f"The maximum bet is {s.max_bet:,}.")
        if mode != "spin" and not config.HEIST_BUYS:
            raise Rejected("Bonus buys are switched off on this server.")
        if mode != "spin" and config.BUY_MAX_BET and bet > config.BUY_MAX_BET:
            raise Rejected(f"Bonus buys go up to a bet of {config.BUY_MAX_BET:,}.")
        stake = E.stake_for(bet, mode)
        async with p.lock:
            wager = await Wager.create(self.db, self.guild_id, p.user_id, stake, GAME)
            if wager is None:
                raise Rejected("You don't have enough credits for that.")
            try:
                rnd = E.Round(rng, mode)
                pay = E.payout(bet, wager.stake, rnd, self.hub.rtp)
            except Exception:
                await wager.refund()   # never keep a stake the game couldn't play
                raise
            balance = await wager.settle(pay)
            if p.presenting:
                self.done_presenting(p)       # spun again before the last one finished showing
            p.round_id += 1
            p.settled = {"round": p.round_id, "bet": bet, "stake": wager.stake, "payout": pay, "x": pay / bet,
                         "bonus": rnd.bonus_kind, "boss": rnd.bosses > 0}
            p.presenting = time.time()
            await self.to_player(p, {"type": "round", "round": {
                "id": p.round_id, "bet": bet, "stake": wager.stake, "payout": pay,
                "scale": E.scale(mode, self.hub.rtp, bet, wager.stake), **rnd.to_dict()}, "balance": balance})
        log.info("Raccoon Heist (Activity): user %s %s bet %s (paid %s), bonus %s, boss %s, vault grabs %s, won %s",
                 p.user_id, mode, f"{bet:,}", f"{wager.stake:,}", rnd.bonus_kind or "-", rnd.bosses, rnd.grabs,
                 f"{pay:,}")
        await self.push_players()

    async def announce(self, p: HeistPlayer, round_id, what) -> None:
        """The page has just shown a bonus, the Boss or a big win: tell everyone (checked against the real round)."""
        s = p.settled
        if not isinstance(round_id, int) or not s or s["round"] != round_id or (round_id, what) in p.announced:
            return
        item = None
        if what == "bonus" and s["bonus"]:
            item = {"kind": "bonus", "text": "pulled off the Big Score!" if s["bonus"] == "big"
                    else "started the Night Shift!"}
        elif what == "boss" and s["boss"]:
            item = {"kind": "bonus", "text": "called in the Boss!"}
        elif what == "win" and s["x"] >= BIG_WIN:
            label = "EPIC WIN" if s["x"] >= 50 else "MEGA WIN" if s["x"] >= 20 else "BIG WIN"
            item = {"kind": "win", "text": f"{label}!", "amount": s["payout"], "x": round(s["x"], 1)}
        if item is None:
            return
        p.announced.add((round_id, what))
        if len(p.announced) > 50:
            p.announced = set(sorted(p.announced)[-20:])
        item.update(id=str(p.user_id), name=p.name, at=now_ms())
        self.feed.insert(0, item)
        del self.feed[FEED:]
        await self.broadcast({"type": "feed", "item": item})

    async def restart(self, p: HeistPlayer) -> None:
        """The bot's /restart, from inside the Activity (same rules as the other games)."""
        needed = max(1, config.RESTART_PRESSES)
        async with p.lock:
            status, balance, until = await self.db.restart(self.guild_id, p.user_id, grant=False)
            if status == "ok":
                p.restart_presses += 1
                if p.restart_presses < needed:
                    status = "press"
                else:
                    p.restart_presses = 0
                    status, balance, until = await self.db.restart(self.guild_id, p.user_id, grant=True)
            else:
                p.restart_presses = 0
        await self.to_player(p, {"type": "restart", "status": status, "done": p.restart_presses, "needed": needed,
                                 "amount": config.RESTART_AMOUNT, "until": int(until * 1000) if until else 0})
        await self.push_me(p, balance)


class HeistHub:
    """All Raccoon Heist lobbies, one per server (keyed by guild id)."""

    def __init__(self, server):
        self.server = server          # activity.server.ActivityServer: sessions, database, settings, sign-in
        self.rtp = config.HEIST_RTP
        self.tables: dict[int, Table] = {}
        self.janitor: asyncio.Task | None = None

    def start(self) -> None:
        if self.janitor is None:
            self.janitor = asyncio.create_task(self._janitor(), name="heist-janitor")

    async def stop(self) -> None:
        # every round is settled the moment it is played, so there is nothing to finish
        if self.janitor:
            self.janitor.cancel()
            try:
                await self.janitor
            except (asyncio.CancelledError, Exception):
                pass

    async def _janitor(self) -> None:
        while True:
            await asyncio.sleep(30)
            t_now = time.time()
            for gid, t in list(self.tables.items()):
                for p in list(t.players.values()):
                    if p.presenting and (not p.sockets or t_now - p.presenting > PRESENT_TIMEOUT):
                        t.done_presenting(p)
                        await t.push_players()
                    if not p.sockets:
                        t.players.pop(p.user_id, None)
                if t.players:
                    t.empty_since = None
                elif t.empty_since is None:
                    t.empty_since = t_now
                elif t_now - t.empty_since > TABLE_SHUTDOWN:
                    self.tables.pop(gid, None)

    def welcome(self, user_id: int) -> dict:
        s = self.server.settings
        sc = E.scale("spin", self.rtp)
        return {
            "type": "welcome", "you": str(user_id), "vw": E.VW, "vh": E.VH, "colors": list(E.COLORS),
            "loot": E.LOOT, "raccoons": E.RACCOON_NAMES, "names": E.NAMES, "levels": E.LEVELS,
            "level_pays": {c: [round(x * sc, 4) for x in E.LEVEL_PAYS[c]] for c in E.COLORS},
            "min_raid": E.MIN_RAID, "meter_size": E.METER_SIZE, "scuffle_chance": E.SCUFFLE_CHANCE,
            "scuffle_max": E.SCUFFLE_MAX, "boss_dashes": E.BOSS_DASHES, "lockers": E.LOCKERS, "alarms": E.ALARMS,
            "night_drops": E.NIGHT_DROPS, "big_level": E.BIG_SCORE_LEVEL, "boost_bp": E.BOOST_BP,
            "cash_top": max(E.CASH_VALUES), "locker_top": max(E.LOCKER_VALUES),
            "buys": ({k: {**v, "price": E.PRICES[k]} for k, v in E.BUYS.items()} if config.HEIST_BUYS else {}),
            "scale": sc, "rtp": self.rtp, "max_win": E.MAX_WIN, "min_bet": s.min_bet, "max_bet": s.max_bet,
            "buy_max_bet": config.BUY_MAX_BET,
            "restart_amount": config.RESTART_AMOUNT, "restart_presses": max(1, config.RESTART_PRESSES),
            "big_win": BIG_WIN,
        }

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await ws.prepare(request)
        table: Table | None = None
        player: HeistPlayer | None = None
        try:
            first = await ws.receive(timeout=20)
            if first.type != aiohttp.WSMsgType.TEXT:
                return ws
            hello = json.loads(first.data)
            table, player, problem = await self.join(hello, ws)
            if problem:
                await ws.send_str(fatal_text(problem))
                return ws
            window_start, count = time.monotonic(), 0
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                t = time.monotonic()
                if t - window_start > 1:
                    window_start, count = t, 0
                count += 1
                if count > MAX_MESSAGES_PER_SECOND:
                    continue
                try:
                    data = json.loads(msg.data)
                    if not isinstance(data, dict):
                        continue
                    if data.get("type") == "ping":
                        await table.send(ws, {"type": "pong"})
                        continue
                    await table.handle(player, data)
                except Rejected as e:
                    await table.send(ws, {"type": "error", "message": str(e)})
                except (ValueError, TypeError):
                    continue
                except Exception:
                    log.exception("Raccoon Heist: error handling %r from %s", msg.data[:200], player.user_id)
                    await table.send(ws, {"type": "error", "message": "Something went wrong with that."})
        except (asyncio.TimeoutError, ValueError, TypeError):
            pass
        finally:
            if player is not None:
                player.sockets.discard(ws)
                if table is not None:
                    await table.push_players()
        return ws

    async def join(self, hello: dict, ws) -> tuple[Table | None, HeistPlayer | None, str | None]:
        if not config.RACCOON_HEIST_ENABLED:
            return None, None, "Raccoon Heist isn't open yet."
        session, guild_id, member, problem = await self.server.admit(hello, ws)
        if problem:
            return None, None, problem
        table = self.tables.get(guild_id)
        if table is None:
            table = self.tables[guild_id] = Table(self, guild_id)
        self.start()
        p = table.players.get(session.user_id)
        if p is None:
            p = table.players[session.user_id] = HeistPlayer(session.user_id, member["name"],
                                                            member.get("avatar_url"))
        else:
            p.name = member["name"]
        if member.get("avatar_url"):
            self.server.avatars[session.user_id] = member["avatar_url"]
        p.sockets.add(ws)
        await table.send(ws, self.welcome(session.user_id))
        balance = await table.db.get_balance(guild_id, p.user_id)
        await table.send(ws, {"type": "me", "balance": balance})
        await table.send(ws, {"type": "feed_all", "items": table.feed})
        await table.push_players()
        return table, p, None
