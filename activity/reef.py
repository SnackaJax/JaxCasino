"""Reef Rush inside the Discord Activity.

Every player plays their own 7x7 cluster slot. Everyone in the same server who has Reef Rush open shares one
lobby (voice call or text channel alike): they see who else is playing and their big wins. The game is
utils/reef_engine.py. The server decides every result; the page only animates what it is sent.

Reef Rush has no choices: a spin is played out completely the moment it is paid for, so it is settled straight
away and sent to the page with its payout. Until the page says it has finished showing it ("shown"), the other
players see the player as still spinning, so the result isn't spoiled.

Websocket /ws/reef. The page sends:
  {"type": "join", "session", "instance_id", "guild_id"}      first message
  {"type": "spin", "bet": int, "mode": "spin"|<buy>}         a paid spin or a bonus buy (a buy costs its price x
                                                             the bet, see E.BUYS)
  {"type": "announce", "round": int, "what": "kraken"|"win"} the page has shown the Kraken or a big win;
                                                             the server checks it and tells the other players
  {"type": "shown", "round": int}                            the page has finished showing a round
  {"type": "restart"}                                        the bot's /restart when broke
The server sends "welcome", "round" (a played spin with its payout and the new balance), "me" (balance),
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
from utils import reef_engine as E
from utils.economy import Wager
from utils.rng import rng
from activity.fatal import fatal_text

log = logging.getLogger("jaxcasino.activity.reef")

GAME = "reefrush"
TABLE_SHUTDOWN = 120         # a lobby with nobody connected is dropped after this
PRESENT_TIMEOUT = 300        # a round still "being shown" after this long counts as shown
MAX_MESSAGES_PER_SECOND = 12
FEED = 20
BIG_WIN = 10                 # times the bet: announced to the other players


def now_ms() -> int:
    return int(time.time() * 1000)


class Rejected(Exception):
    pass


@dataclass
class ReefPlayer:
    user_id: int
    name: str
    avatar_url: str | None
    sockets: set = field(default_factory=set)
    round_id: int = 0
    last_win: int = 0            # what the others see: only updated once the page has shown the round
    last_x: float = 0.0
    settled: dict | None = None  # the last round: {"round", "stake", "payout", "x", "kraken"}
    presenting: float | None = None   # time the page started showing the last round (None: shown)
    announced: set = field(default_factory=set)
    restart_presses: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self) -> dict:
        return {"id": str(self.user_id), "name": self.name, "avatar": bool(self.avatar_url),
                "online": bool(self.sockets), "status": "spinning" if self.presenting else "idle",
                "last_win": self.last_win, "last_x": round(self.last_x, 2)}


class Table:
    """The Reef Rush lobby of one server."""

    def __init__(self, hub: "ReefHub", guild_id: int):
        self.hub = hub
        self.guild_id = guild_id
        self.players: dict[int, ReefPlayer] = {}
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

    async def to_player(self, p: ReefPlayer, data: dict) -> None:
        await asyncio.gather(*(self.send(ws, dict(data)) for ws in list(p.sockets)))

    async def broadcast(self, data: dict) -> None:
        await asyncio.gather(*(self.send(ws, dict(data)) for p in self.players.values() for ws in list(p.sockets)))

    async def push_players(self) -> None:
        await self.broadcast({"type": "players", "players": [p.public() for p in self.players.values()
                                                             if p.sockets]})

    async def push_me(self, p: ReefPlayer, balance: int | None = None) -> None:
        if balance is None:
            balance = await self.db.get_balance(self.guild_id, p.user_id)
        await self.to_player(p, {"type": "me", "balance": balance})

    async def handle(self, p: ReefPlayer, msg: dict) -> None:
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

    def done_presenting(self, p: ReefPlayer) -> None:
        p.presenting = None
        if p.settled:
            p.last_win, p.last_x = p.settled["payout"], p.settled["x"]

    async def spin(self, p: ReefPlayer, bet: int, mode: str = "spin") -> None:
        s = self.hub.server.settings
        if bet < s.min_bet:
            raise Rejected(f"The minimum bet is {s.min_bet:,}.")
        if s.max_bet and bet > s.max_bet:
            raise Rejected(f"The maximum bet is {s.max_bet:,}.")
        if mode != "spin" and not config.REEF_BUYS:
            raise Rejected("Bonus buys are switched off on this server.")
        if mode != "spin" and config.BUY_MAX_BET and bet > config.BUY_MAX_BET:
            raise Rejected(f"Bonus buys go up to a bet of {config.BUY_MAX_BET:,}.")
        stake = E.stake_for(bet, mode)
        async with p.lock:
            wager = await Wager.create(self.db, self.guild_id, p.user_id, stake, GAME)
            if wager is None:
                raise Rejected("You don't have enough credits for that." if mode == "spin" else
                               f"You don't have enough credits for that: {E.BUYS[mode]['name']} costs {stake:,}.")
            try:
                rnd = E.Round(rng, mode)
                pay = E.payout(wager.stake, rnd, self.hub.rtp, bet)
            except Exception:
                await wager.refund()   # never keep a stake the game couldn't play
                raise
            balance = await wager.settle(pay)
            if p.presenting:
                self.done_presenting(p)       # spun again before the last one finished showing
            p.round_id += 1
            p.settled = {"round": p.round_id, "stake": wager.stake, "payout": pay, "x": pay / bet,
                         "kraken": rnd.kraken_landed}
            p.presenting = time.time()
            await self.to_player(p, {"type": "round", "round": {"id": p.round_id, "bet": bet, "stake": wager.stake,
                                                                 "payout": pay, **rnd.to_dict()},
                                     "balance": balance})
        log.info("Reef Rush (Activity): user %s %s bet %s (paid %s), %s cascades, surge %s, kraken %s, won %s",
                 p.user_id, mode, f"{bet:,}", f"{wager.stake:,}", rnd.cascades, rnd.surge or "-",
                 "yes" if rnd.kraken_landed else "-", f"{pay:,}")
        await self.push_players()

    async def announce(self, p: ReefPlayer, round_id, what) -> None:
        """The page has just shown the Kraken or a big win: tell everyone (checked against the real round)."""
        s = p.settled
        if not isinstance(round_id, int) or not s or s["round"] != round_id or (round_id, what) in p.announced:
            return
        item = None
        if what == "kraken" and s["kraken"]:
            item = {"kind": "bonus", "text": "woke the Kraken!"}
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

    async def restart(self, p: ReefPlayer) -> None:
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


class ReefHub:
    """All Reef Rush lobbies, one per server (keyed by guild id)."""

    def __init__(self, server):
        self.server = server          # activity.server.ActivityServer: sessions, database, settings, sign-in
        self.rtp = config.REEF_RTP
        self.tables: dict[int, Table] = {}
        self.janitor: asyncio.Task | None = None

    def start(self) -> None:
        if self.janitor is None:
            self.janitor = asyncio.create_task(self._janitor(), name="reef-janitor")

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
        scale = E.scale(self.rtp)
        return {
            "type": "welcome", "you": str(user_id), "cols": E.COLS, "rows": E.ROWS, "min_cluster": E.MIN_CLUSTER,
            "symbols": {k: {"name": E.NAMES[k], "pays": [round(x * scale, 4) for x in E.PAYS[k]]}
                        for k in E.PAYING},
            "low": E.LOW, "high": E.HIGH, "names": E.NAMES,
            "surges": {k: {"name": E.SURGE_NAMES[k], "text": E.SURGE_TEXT[k]} for k in E.SURGES},
            "surge_at": E.SURGE_AT, "kraken_at": E.KRAKEN_AT, "pearl_size": E.PEARL_SIZE,
            "buys": ({k: {**v, "price": E.PRICES[k], "meter": E.BUY_METER[k], "rtp": E.buy_rtp(k, self.rtp)}
                      for k, v in E.BUYS.items()} if config.REEF_BUYS else {}),
            "scale": scale, "rtp": self.rtp, "max_win": E.MAX_WIN, "min_bet": s.min_bet, "max_bet": s.max_bet,
            "buy_max_bet": config.BUY_MAX_BET,
            "restart_amount": config.RESTART_AMOUNT, "restart_presses": max(1, config.RESTART_PRESSES),
            "big_win": BIG_WIN,
        }

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await ws.prepare(request)
        table: Table | None = None
        player: ReefPlayer | None = None
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
                    log.exception("Reef Rush: error handling %r from %s", msg.data[:200], player.user_id)
                    await table.send(ws, {"type": "error", "message": "Something went wrong with that."})
        except (asyncio.TimeoutError, ValueError, TypeError):
            pass
        finally:
            if player is not None:
                player.sockets.discard(ws)
                if table is not None:
                    await table.push_players()
        return ws

    async def join(self, hello: dict, ws) -> tuple[Table | None, ReefPlayer | None, str | None]:
        if not config.REEF_RUSH_ENABLED:
            return None, None, "Reef Rush isn't open yet."
        session, guild_id, member, problem = await self.server.admit(hello, ws)
        if problem:
            return None, None, problem
        table = self.tables.get(guild_id)
        if table is None:
            table = self.tables[guild_id] = Table(self, guild_id)
        self.start()
        p = table.players.get(session.user_id)
        if p is None:
            p = table.players[session.user_id] = ReefPlayer(session.user_id, member["name"],
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
