"""Web server for the bot's Discord Activity: a game menu with Spin Frenzy, Banger Bash, poker, blackjack and more.

Runs inside the bot process (see cogs/spinfrenzy.py) so it shares the bot's database and credits.

Routes
  GET  /                  the game menu (static/lobby.html)
  GET  /spinfrenzy        the Spin Frenzy page (static/index.html)
  GET  /bangerbash        the Banger Bash page (static/banger.html); its game server is activity/banger.py
  GET  /ws/banger         the Banger Bash connection (every player has their own slot)
  GET  /reefrush          the Reef Rush page (static/reef.html); its game server is activity/reef.py. Only
                          while REEF_RUSH_ENABLED=1 in .env (404 otherwise)
  GET  /ws/reef           the Reef Rush connection (every player has their own slot)
  GET  /raccoonheist      the Raccoon Heist page (static/heist.html); its game server is activity/heist.py. Only
                          while RACCOON_HEIST_ENABLED=1 in .env (404 otherwise)
  GET  /ws/heist          the Raccoon Heist connection (every player has their own vault)
  GET  /poker             the poker page (static/poker.html); its game server is activity/poker.py
  GET  /ws/poker          the poker connection (one poker room per server, with its tables)
  GET  /blackjack         the live blackjack page (static/blackjack.html); its game server is activity/blackjack.py
  GET  /ws/blackjack      the blackjack connection (one blackjack room per server, with its tables)
  GET  /static/...        scripts, styles and the vendored Discord Embedded App SDK
  GET  /api/config        public settings the page needs (the application's client id)
  POST /api/token         exchanges the OAuth2 code from the SDK for an access token and a game session
  POST /api/start-game    the game the player picked in /casino, for the menu to open straight away
  POST /api/voice         a line for the blackjack dealer to say, as a WAV made on this machine (activity/voice.py)
  GET  /avatar/{user_id}  avatar of a player who is in a game (Discord's CDN is blocked inside Activities)
  GET  /ws                the live game: one shared round loop per Activity instance

Every Activity instance (everyone in the same voice call who opened the game) shares one wheel. A round is:
betting -> Boost Reel -> spin -> result -> bonus round (when a bonus segment wins) -> payouts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp
from aiohttp import web

import config
from activity import engine
from activity.banger import BangerHub
from activity.blackjack import BlackjackHub
from activity.heist import HeistHub
from activity.poker import PokerHub
from activity.reef import ReefHub
from activity.voice import DealerVoice
from utils.economy import Wager
from utils.rng import rng

log = logging.getLogger("jaxcasino.activity")

STATIC = Path(__file__).parent / "static"
API = "https://discord.com/api/v10"
GAME = "spinfrenzy"   # name used in the game log, /stats and /serverstats

SESSION_TTL = 12 * 60 * 60
START_GAME_TTL = 120         # how long a "/casino game:..." launch waits for the menu to open that game
GAME_PAGES = {"spinfrenzy": "/spinfrenzy", "bangerbash": "/bangerbash", "poker": "/poker",
              "blackjack": "/blackjack",
              **({"reefrush": "/reefrush"} if config.REEF_RUSH_ENABLED else {}),
              **({"raccoonheist": "/raccoonheist"} if config.RACCOON_HEIST_ENABLED else {})}
IDLE_SHUTDOWN = 120          # an instance with nobody connected stops after this many seconds
MAX_MESSAGES_PER_SECOND = 15
HISTORY = 24

# Checking an Activity instance with Discord (see verified_guild)
INSTANCE_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")   # Discord's instance ids, e.g. i-123-gc-456-789
INSTANCE_LOOKUP_TRIES = 3    # a 404 is asked again after 1s and 2s: a brand-new instance can take a moment to show up
INSTANCE_CACHE_TTL = 10 * 60
# Sign-ins (/api/token). Every one makes the bot call Discord, and Discord temporarily bans an IP address that sends
# it too many invalid requests, so made-up codes mustn't be passed on unlimited.
TOKEN_LIMIT_PER_ADDRESS = 10   # per minute, from one address
TOKEN_LIMIT_TOTAL = 60         # per minute, from everyone together
MAX_SOCKETS_PER_USER = 8       # open game connections per player, over every game and device

# Phase lengths in seconds
T_BOOST = 3.6
T_SPIN = 7.5
T_RESULT = 3.0
T_PAYOUT = 5.0
T_COIN = {"reveal": 3.5, "flip": 5.0, "done": 3.5}
T_GRID = {"show": 3.5, "shuffle": 3.0, "pick": 10.0, "reveal": 6.0}
T_DROP_BOARD, T_DROP_EACH, T_DROP_DOUBLE, T_DROP_DONE = 3.5, 5.0, 2.0, 3.5
T_FRENZY = {"intro": 3.0, "pick": 9.0, "spin": 7.5, "done": 5.0}

ResolveMember = Callable[[int, int], Awaitable["dict | None"]]


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Settings:
    rtp: float = 0.97
    bonus_weight: float = 1.5
    bonus_rtp: float = 1.0
    bet_seconds: int = 15
    max_win_mult: int = 10_000
    min_bet: int = 10
    max_bet: int = 0


@dataclass
class Session:
    user_id: int
    username: str
    expires: float


@dataclass
class Player:
    user_id: int
    name: str
    avatar_url: str | None
    sockets: set = field(default_factory=set)
    bets: dict[str, int] = field(default_factory=dict)
    last_bets: dict[str, int] = field(default_factory=dict)
    wager: Wager | None = None
    grid_pick: int | None = None
    pointer: int | None = None
    payout: int = 0
    restart_presses: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def total(self) -> int:
        return sum(self.bets.values())

    def public(self) -> dict:
        return {"id": str(self.user_id), "name": self.name, "avatar": bool(self.avatar_url),
                "bets": self.bets, "total": self.total, "payout": self.payout,
                "online": bool(self.sockets)}


class BetRejected(Exception):
    pass


class Instance:
    """One Activity instance: a shared wheel, its players and its round loop."""

    def __init__(self, server: "ActivityServer", instance_id: str, guild_id: int):
        self.server = server
        self.id = instance_id
        self.guild_id = guild_id
        self.players: dict[int, Player] = {}
        self.round = 0
        self.phase = "waiting"
        self.starts_at = 0
        self.ends_at = 0
        self.betting_open = False
        self.boost: engine.Boost | None = None
        self.stop: int | None = None
        self.result: str | None = None
        self.bonus: dict | None = None        # public view of the running bonus round
        self.history: list[dict] = []
        self.winners: list[dict] = []
        self.task: asyncio.Task | None = None
        self.empty_since: float | None = None

    # ------------------------------------------------------------------ connections
    @property
    def online(self) -> list[Player]:
        return [p for p in self.players.values() if p.sockets]

    def ensure_running(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name=f"spinfrenzy-{self.id}")

    async def send(self, ws: web.WebSocketResponse, data: dict) -> None:
        if ws.closed:
            return
        data["now"] = now_ms()
        try:
            await ws.send_str(json.dumps(data, separators=(",", ":")))
        except (ConnectionResetError, RuntimeError, aiohttp.ClientError):
            pass

    async def broadcast(self, data: dict) -> None:
        sockets = [ws for p in self.players.values() for ws in p.sockets]
        await asyncio.gather(*(self.send(ws, dict(data)) for ws in sockets))

    def state(self) -> dict:
        return {
            "type": "state", "round": self.round, "phase": self.phase, "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "boost": self.boost.to_dict() if self.boost else None, "stop": self.stop, "result": self.result,
            "bonus": self.bonus, "history": self.history, "winners": self.winners,
            "players": [p.public() for p in self.players.values() if p.sockets or p.total or p.payout],
        }

    async def push_state(self) -> None:
        await self.broadcast(self.state())

    async def push_me(self, player: Player, balance: int | None = None) -> None:
        if balance is None:
            balance = await self.server.db.get_balance(self.guild_id, player.user_id)
        data = {"type": "me", "balance": balance, "bets": player.bets, "last_bets": player.last_bets,
                "payout": player.payout, "grid_pick": player.grid_pick, "pointer": player.pointer}
        await asyncio.gather(*(self.send(ws, dict(data)) for ws in player.sockets))

    async def set_phase(self, phase: str, seconds: float) -> None:
        self.phase = phase
        self.starts_at = now_ms()
        self.ends_at = self.starts_at + int(seconds * 1000)
        await self.push_state()

    async def wait(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))

    # ------------------------------------------------------------------ bets
    async def add_bets(self, player: Player, additions: dict[str, int]) -> None:
        s = self.server.settings
        additions = {k: v for k, v in additions.items() if v > 0}
        if not additions:
            return
        for spot, amount in additions.items():
            if spot not in engine.SPOTS:
                raise BetRejected("That isn't a spot on the board.")
            if amount < s.min_bet:
                raise BetRejected(f"The minimum bet is {s.min_bet:,}.")
        extra = sum(additions.values())
        async with player.lock:
            if not self.betting_open:
                raise BetRejected("Betting is closed for this round.")
            if s.max_bet and player.total + extra > s.max_bet:
                raise BetRejected(f"The most you can bet in one round is {s.max_bet:,}.")
            if player.wager is None:
                wager = await Wager.create(self.server.db, self.guild_id, player.user_id, extra, GAME)
                if wager is None:
                    raise BetRejected("You don't have enough credits for that.")
                player.wager = wager
            elif not await player.wager.add_stake(extra):
                raise BetRejected("You don't have enough credits for that.")
            for spot, amount in additions.items():
                player.bets[spot] = player.bets.get(spot, 0) + amount
        await self.push_me(player)
        await self.push_state()

    async def clear_bets(self, player: Player) -> None:
        async with player.lock:
            if not self.betting_open:
                raise BetRejected("Betting is closed for this round.")
            if player.wager is None:
                return
            await player.wager.refund()
            player.wager = None
            player.bets = {}
        await self.push_me(player)
        await self.push_state()

    async def restart(self, player: Player) -> None:
        """The bot's /restart, from inside the Activity: same amount, presses and rules (database.restart)."""
        db = self.server.db
        needed = max(1, config.RESTART_PRESSES)
        async with player.lock:
            if player.wager is not None:
                raise BetRejected("You still have bets in this round. Wait for it to finish.")
            status, balance, until = await db.restart(self.guild_id, player.user_id, grant=False)
            if status == "ok":
                player.restart_presses += 1
                if player.restart_presses < needed:
                    status = "press"
                else:
                    player.restart_presses = 0
                    status, balance, until = await db.restart(self.guild_id, player.user_id, grant=True)
                    if status == "ok":
                        log.info("Spin Frenzy: user %s restarted with %s credits (balance %s)", player.user_id,
                                 f"{config.RESTART_AMOUNT:,}", f"{balance:,}")
            else:
                player.restart_presses = 0
        reply = {"type": "restart", "status": status, "done": player.restart_presses, "needed": needed,
                 "amount": config.RESTART_AMOUNT, "until": int(until * 1000) if until else 0}
        await asyncio.gather(*(self.send(ws, dict(reply)) for ws in player.sockets))
        await self.push_me(player, balance)

    async def handle(self, player: Player, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "bet":
            amount = msg.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool):
                raise BetRejected("Invalid amount.")
            await self.add_bets(player, {str(msg.get("spot")): amount})
        elif kind == "clear":
            await self.clear_bets(player)
        elif kind == "double":
            await self.add_bets(player, dict(player.bets))
        elif kind == "repeat":
            if player.bets:
                raise BetRejected("Clear your bets first, or use Double.")
            await self.add_bets(player, dict(player.last_bets))
        elif kind == "restart":
            await self.restart(player)
        elif kind == "pick_cell":
            cell = msg.get("cell")
            if (self.bonus and self.bonus.get("kind") == "grid" and self.bonus.get("stage") == "pick"
                    and player.bets.get("grid") and isinstance(cell, int) and 0 <= cell < engine.GRID_CELLS):
                player.grid_pick = cell
                self.bonus["picks"][str(player.user_id)] = cell
                await self.push_me(player)
                await self.push_state()
        elif kind == "pick_pointer":
            ptr = msg.get("pointer")
            if (self.bonus and self.bonus.get("kind") == "frenzy" and self.bonus.get("stage") == "pick"
                    and player.bets.get("frenzy") and isinstance(ptr, int) and 0 <= ptr < engine.FRENZY_POINTERS):
                player.pointer = ptr
                self.bonus["picks"][str(player.user_id)] = ptr
                await self.push_me(player)
                await self.push_state()

    # ------------------------------------------------------------------ the round loop
    async def run(self) -> None:
        log.info("Spin Frenzy instance %s started (guild %s)", self.id, self.guild_id)
        try:
            while True:
                if not self.online:
                    self.phase = "waiting"
                    self.empty_since = self.empty_since or time.time()
                    if time.time() - self.empty_since > IDLE_SHUTDOWN:
                        break
                    await asyncio.sleep(1)
                    continue
                self.empty_since = None
                try:
                    await self.play_round()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Spin Frenzy round %s in instance %s failed; refunding its bets", self.round, self.id)
                    await self.refund_all()
                    await self.broadcast({"type": "error", "message": "Something went wrong, so this round's "
                                                                       "bets were refunded."})
                    await asyncio.sleep(3)
        finally:
            await self.refund_all()
            self.server.instances.pop(self.id, None)
            log.info("Spin Frenzy instance %s stopped", self.id)

    async def refund_all(self) -> None:
        for p in self.players.values():
            async with p.lock:
                if p.wager and not p.wager.settled:
                    await p.wager.refund()
                p.wager = None
                p.bets = {}

    def bettors(self, spot: str) -> list[Player]:
        return [p for p in self.players.values() if p.bets.get(spot)]

    async def play_round(self) -> None:
        s = self.server.settings
        self.round += 1
        self.boost = self.stop = self.result = self.bonus = None
        self.winners = []
        for p in list(self.players.values()):
            p.payout = 0
            p.grid_pick = p.pointer = None
            if not p.sockets and not p.wager:
                del self.players[p.user_id]
        self.betting_open = True
        await self.set_phase("betting", s.bet_seconds)
        for p in self.online:
            await self.push_me(p)
        await self.wait(s.bet_seconds)

        # close betting, and let any bet that is being processed right now finish first
        self.betting_open = False
        for p in list(self.players.values()):
            async with p.lock:
                pass

        self.boost = engine.roll_boost(self.server.chances)
        await self.set_phase("boost", T_BOOST)
        await self.wait(T_BOOST)

        self.stop = engine.spin_wheel()
        self.result = engine.WHEEL[self.stop]
        await self.set_phase("spin", T_SPIN)
        await self.wait(T_SPIN)

        await self.set_phase("result", T_RESULT)
        await self.wait(T_RESULT)

        values: dict[int, float] = {}   # per player: winnings multiple of the winning spot (before boost)
        spot = self.result
        if spot in engine.NUMBERS:
            for p in self.bettors(spot):
                values[p.user_id] = engine.NUMBERS[spot]
        elif spot == "coin":
            v = await self.coin_toss()
            for p in self.bettors(spot):
                values[p.user_id] = v
        elif spot == "grid":
            values = await self.treasure_grid()
        elif spot == "drop":
            v = await self.peg_drop()
            for p in self.bettors(spot):
                values[p.user_id] = v
        else:
            values = await self.frenzy_wheel()

        staked: dict[str, int] = {}
        for p in self.players.values():
            if p.wager is not None:
                for k, amount in p.bets.items():
                    staked[k] = staked.get(k, 0) + amount
        await self.settle(values)
        boost_text = (f"{self.boost.spot} x{self.boost.mult}" + (" (hit)" if self.boost.spot == spot else "")
                      if self.boost.spot else "missed")
        bonus_text = ""
        if spot in engine.BONUSES and values:
            vals = sorted(set(values.values()))
            bonus_text = " value " + "/".join(f"{v:g}x" for v in vals)
        log.info("Spin Frenzy round %s (guild %s): landed %s%s, boost %s, staked %s, paid %s, bets %s",
                 self.round, self.guild_id, spot, bonus_text, boost_text, f"{sum(staked.values()):,}",
                 f"{sum(w['payout'] for w in self.winners):,}",
                 ", ".join(f"{k} {v:,}" for k, v in staked.items()) or "none")
        self.history.insert(0, {"spot": spot, "boost": self.boost.mult if self.boost.spot == spot else 1})
        del self.history[HISTORY:]
        await self.set_phase("payout", T_PAYOUT)
        await self.wait(T_PAYOUT)

    async def settle(self, values: dict[int, float]) -> None:
        s = self.server.settings
        boost = self.boost.mult if self.boost and self.boost.spot == self.result else 1
        self.winners = []
        for p in list(self.players.values()):
            async with p.lock:
                if p.wager is None:
                    continue
                stake = p.bets.get(self.result, 0)
                pay = engine.payout(stake, values[p.user_id], boost, s.max_win_mult) if stake else 0
                total = p.total
                balance = await p.wager.settle(pay)
                p.payout = pay
                p.last_bets = dict(p.bets)
                p.bets = {}
                p.wager = None
            if pay:
                self.winners.append({"id": str(p.user_id), "name": p.name, "payout": pay, "profit": pay - total})
            await self.push_me(p, balance)
        self.winners.sort(key=lambda w: -w["payout"])

    # ------------------------------------------------------------------ bonus rounds
    def _boost_for(self, spot: str) -> int:
        return self.boost.mult if self.boost and self.boost.spot == spot else 1

    def _participants(self, spot: str) -> list[str]:
        return [str(p.user_id) for p in self.bettors(spot)]

    async def bonus_stage(self, stage: str, seconds: float, **extra) -> None:
        self.bonus.update(stage=stage, **extra)
        await self.set_phase("bonus", seconds)
        await self.wait(seconds)

    async def coin_toss(self) -> int:
        game = engine.CoinToss.play()
        self.bonus = {"kind": "coin", "boost": self._boost_for("coin"), "players": self._participants("coin")}
        await self.bonus_stage("reveal", T_COIN["reveal"], sides=game.sides)
        await self.bonus_stage("flip", T_COIN["flip"], winner=game.winner)
        await self.bonus_stage("done", T_COIN["done"], value=game.value())
        return game.value()

    async def treasure_grid(self) -> dict[int, float]:
        game = engine.TreasureGrid.deal()
        self.bonus = {"kind": "grid", "boost": self._boost_for("grid"), "players": self._participants("grid"),
                      "cells": engine.GRID_CELLS, "picks": {}}
        await self.bonus_stage("show", T_GRID["show"], shown=game.shown_values())
        await self.bonus_stage("shuffle", T_GRID["shuffle"])
        await self.bonus_stage("pick", T_GRID["pick"])
        # anyone who didn't pick gets a random cell
        for p in self.bettors("grid"):
            if p.grid_pick is None:
                p.grid_pick = rng.randrange(engine.GRID_CELLS)
                self.bonus["picks"][str(p.user_id)] = p.grid_pick
        await self.bonus_stage("reveal", T_GRID["reveal"], values=game.cells)
        return {p.user_id: game.cells[p.grid_pick] for p in self.bettors("grid")}

    async def peg_drop(self) -> int:
        game = engine.PegDrop.play()
        self.bonus = {"kind": "drop", "boost": self._boost_for("drop"), "players": self._participants("drop"),
                      "slots": game.slots, "rows": engine.DROP_ROWS, "factor": 1, "drop": None, "drop_index": -1}
        await self.bonus_stage("board", T_DROP_BOARD)
        for i, d in enumerate(game.drops):
            self.bonus["factor"] = d["factor_before"]
            await self.bonus_stage("drop", T_DROP_EACH + (T_DROP_DOUBLE if d["double"] else 0),
                                   drop=d, drop_index=i)
        await self.bonus_stage("done", T_DROP_DONE, factor=game.factor, value=game.final,
                               final_slot=game.drops[-1]["slot"])
        return game.final

    async def frenzy_wheel(self) -> dict[int, float]:
        game = engine.FrenzyWheel.deal()
        self.bonus = {"kind": "frenzy", "boost": self._boost_for("frenzy"), "players": self._participants("frenzy"),
                      "segments": game.segments, "pointers": engine.POINTER_NAMES, "picks": {},
                      "spin": None, "spin_index": -1, "factors": [1] * engine.FRENZY_POINTERS,
                      "results": [None] * engine.FRENZY_POINTERS}
        await self.bonus_stage("intro", T_FRENZY["intro"])
        await self.bonus_stage("pick", T_FRENZY["pick"])
        for p in self.bettors("frenzy"):
            if p.pointer is None:
                p.pointer = rng.randrange(engine.FRENZY_POINTERS)
                self.bonus["picks"][str(p.user_id)] = p.pointer
        game.play()
        results: list = [None] * engine.FRENZY_POINTERS
        for i, spin in enumerate(game.spins):
            # results become known as each pointer settles on a value
            for ptr, seg in enumerate(spin["landed"]):
                if seg is not None and isinstance(game.segments[seg], int):
                    results[ptr] = game.results[ptr]
            self.bonus["spin"] = spin
            self.bonus["spin_index"] = i
            await self.bonus_stage("spin", T_FRENZY["spin"], factors_after=spin["factors"], results=list(results))
        await self.bonus_stage("done", T_FRENZY["done"], results=game.results, factors=game.factors)
        return {p.user_id: game.results[p.pointer] for p in self.bettors("frenzy")}


class ActivityServer:
    def __init__(self, db, settings: Settings, resolve_member: ResolveMember, client_secret: str,
                 bot_token: str, client_id: Callable[[], int | None], dev_guild_id: int | None = None):
        self.db = db
        self.settings = settings
        self.chances = engine.configure(settings.rtp, settings.bonus_weight, settings.bonus_rtp)
        log.info("Spin Frenzy payback per spot: %s", ", ".join(
            f"{s} {engine.spot_rtp(s, self.chances):.1%}" for s in engine.SPOTS))
        self.resolve_member = resolve_member
        self.client_secret = client_secret
        self.bot_token = bot_token
        self.client_id = client_id
        self.dev_guild_id = dev_guild_id   # only set by activity/devserver.py: allows logins without Discord
        self.sessions: dict[str, Session] = {}
        self.instances: dict[str, Instance] = {}
        self.avatars: dict[int, str] = {}
        self.avatar_cache: dict[int, tuple[bytes, str]] = {}
        self.http: aiohttp.ClientSession | None = None
        self.runner: web.AppRunner | None = None
        self.banger = BangerHub(self)
        self.poker = PokerHub(self)
        self.blackjack = BlackjackHub(self)
        self.voice = DealerVoice(config.DEALER_VOICE_MODEL, config.DEALER_VOICE_NAME)
        self.reef = ReefHub(self)
        self.heist = HeistHub(self)
        self.start_with: dict[int, tuple[str, float]] = {}   # user id -> (game page, expiry): see want_game
        self.instance_guilds: dict[str, tuple[int | None, float]] = {}   # instance id -> (guild id, expiry)
        self.token_calls: dict[str, deque] = {}   # address ("" = everyone) -> times of recent sign-ins
        self.user_sockets: dict[int, weakref.WeakSet] = {}   # user id -> their open game connections

    # ------------------------------------------------------------------ lifecycle
    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024, middlewares=[self.no_cache])
        app.add_routes([
            web.get("/", self.lobby),
            web.get("/spinfrenzy", self.index),
            web.get("/bangerbash", self.banger_page),
            web.get("/ws/banger", self.banger.websocket),
            web.get("/reefrush", self.reef_page),
            web.get("/ws/reef", self.reef.websocket),
            web.get("/raccoonheist", self.heist_page),
            web.get("/ws/heist", self.heist.websocket),
            web.get("/poker", self.poker_page),
            web.get("/ws/poker", self.poker.websocket),
            web.get("/blackjack", self.blackjack_page),
            web.get("/ws/blackjack", self.blackjack.websocket),
            web.get("/api/config", self.api_config),
            web.post("/api/token", self.api_token),
            web.post("/api/start-game", self.api_start_game),
            web.post("/api/voice", self.api_voice),
            web.get("/avatar/{user_id}", self.avatar),
            web.get("/ws", self.websocket),
            web.static("/static", STATIC),
        ])
        if self.dev_guild_id:
            app.add_routes([web.get("/api/dev-login", self.dev_login)])
        return app

    async def start(self, host: str, port: int) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, host, port).start()
        await self.voice.start()
        log.info("Spin Frenzy Activity server listening on http://%s:%s", host, port)

    async def stop(self) -> None:
        await self.banger.stop()
        await self.poker.stop()
        await self.blackjack.stop()
        await self.reef.stop()
        await self.heist.stop()
        for inst in list(self.instances.values()):
            if inst.task:
                inst.task.cancel()
        for inst in list(self.instances.values()):
            if inst.task:
                try:
                    await inst.task
                except (asyncio.CancelledError, Exception):
                    pass
            await inst.refund_all()
        if self.runner:
            await self.runner.cleanup()
        if self.http:
            await self.http.close()

    @web.middleware
    async def no_cache(self, request: web.Request, handler):
        resp = await handler(request)
        if not request.path.startswith("/avatar/") and not request.path.startswith("/static/vendor/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ------------------------------------------------------------------ http
    async def lobby(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "lobby.html")

    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "index.html")

    async def banger_page(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "banger.html")

    async def reef_page(self, request: web.Request) -> web.StreamResponse:
        if not config.REEF_RUSH_ENABLED:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / "reef.html")

    async def heist_page(self, request: web.Request) -> web.StreamResponse:
        if not config.RACCOON_HEIST_ENABLED:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / "heist.html")

    async def poker_page(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "poker.html")

    async def blackjack_page(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "blackjack.html")

    async def api_config(self, request: web.Request) -> web.Response:
        s = self.settings
        return web.json_response({"client_id": str(self.client_id() or ""), "dev": bool(self.dev_guild_id),
                                  "min_bet": s.min_bet, "max_bet": s.max_bet, "games": list(GAME_PAGES)})

    def sign_in_allowed(self, request: web.Request) -> bool:
        """Counts a sign-in against the per-address and overall limits (TOKEN_LIMIT_*)."""
        # Behind a Cloudflare tunnel or a proxy every request comes from the tunnel, so the visitor's address is in a
        # header. A header can be made up by a caller, which is why there's an overall limit as well.
        address = (request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0]
                   or request.remote or "?").strip()[:64]
        now = time.monotonic()
        windows = []
        for key, limit in ((address, TOKEN_LIMIT_PER_ADDRESS), ("", TOKEN_LIMIT_TOTAL)):
            calls = self.token_calls.setdefault(key, deque())
            while calls and now - calls[0] > 60:
                calls.popleft()
            if len(calls) >= limit:
                return False
            windows.append(calls)
        for calls in windows:
            calls.append(now)
        if len(self.token_calls) > 10_000:   # forget addresses that haven't signed in for a minute
            for key in [k for k, v in self.token_calls.items() if k and (not v or now - v[-1] > 60)]:
                del self.token_calls[key]
        return True

    async def api_token(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            code = str(body["code"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "missing code"}, status=400)
        if not self.sign_in_allowed(request):
            log.warning("Activity: too many sign-ins, refused one (from %s)", request.remote)
            return web.json_response({"error": "Too many sign-ins right now. Wait a minute and reopen the Activity."},
                                     status=429)
        client_id = self.client_id()
        if not client_id or not self.client_secret:
            log.error("Spin Frenzy: DISCORD_CLIENT_SECRET is not set, so nobody can sign in to the Activity")
            return web.json_response({"error": "The bot owner hasn't finished setting up the Activity "
                                               "(DISCORD_CLIENT_SECRET is missing)."}, status=500)
        form = {"client_id": str(client_id), "client_secret": self.client_secret,
                "grant_type": "authorization_code", "code": code}
        try:
            async with self.http.post(f"{API}/oauth2/token", data=form) as r:
                token = await r.json(content_type=None)
                if r.status != 200 or not isinstance(token, dict) or "access_token" not in token:
                    log.warning("Spin Frenzy: OAuth code exchange failed (%s): %s", r.status, token)
                    return web.json_response({"error": "Discord sign-in failed. Try reopening the Activity."},
                                             status=401)
            access = token["access_token"]
            async with self.http.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {access}"}) as r:
                if r.status != 200:
                    return web.json_response({"error": "Couldn't read your Discord account."}, status=401)
                user = await r.json()
            session = self.new_session(int(user["id"]), user.get("global_name") or user["username"])
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as e:
            # Discord unreachable, or an answer that isn't the JSON it should be (an outage page, for example)
            log.warning("Spin Frenzy: sign-in failed, Discord gave no usable answer: %r", e)
            return web.json_response({"error": "Couldn't reach Discord to sign you in. Try again in a moment."},
                                     status=502)
        return web.json_response({"access_token": access, "session": session})

    async def api_voice(self, request: web.Request) -> web.Response:
        """The blackjack dealer says a line: {"session", "text"} -> audio/wav. Signed-in players only, short lines,
        and a limit per player, so this can't be used as a free speech service."""
        try:
            body = await request.json()
            session = self.sessions.get(str(body["session"]))
            text = str(body["text"]).strip()
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "bad request"}, status=400)
        if session is None or session.expires < time.time():
            return web.json_response({"error": "session expired"}, status=401)
        if not text or len(text) > 240:
            return web.json_response({"error": "bad text"}, status=400)
        if not self.voice.allowed(session.user_id):
            return web.json_response({"error": "slow down"}, status=429)
        wav = await self.voice.say(text)
        if wav is None:
            return web.json_response({"error": "no voice"}, status=503)
        return web.Response(body=wav, content_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})

    def want_game(self, user_id: int, game: str) -> None:
        """/casino launched the Activity for this player with a game picked: the menu opens that game straight
        away when it signs them in, if that happens within START_GAME_TTL seconds."""
        if game in GAME_PAGES:
            self.start_with[user_id] = (game, time.time() + START_GAME_TTL)

    async def api_start_game(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            session = self.sessions.get(str(body["session"]))
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "missing session"}, status=400)
        if session is None:
            return web.json_response({"game": None})
        game, expires = self.start_with.pop(session.user_id, (None, 0))
        return web.json_response({"game": GAME_PAGES[game] if game and expires > time.time() else None})

    async def dev_login(self, request: web.Request) -> web.Response:
        """Local testing without Discord (devserver.py only): ?user=<any number>&name=<name>."""
        try:
            uid = int(request.query.get("user", "1"))
        except ValueError:
            return web.json_response({"error": "user must be a number"}, status=400)
        name = request.query.get("name") or f"Tester {uid}"
        return web.json_response({"session": self.new_session(uid, name[:32]), "guild_id": str(self.dev_guild_id),
                                  "instance_id": "dev"})

    def new_session(self, user_id: int, username: str) -> str:
        t = time.time()
        for k in [k for k, s in self.sessions.items() if s.expires < t]:
            del self.sessions[k]
        token = secrets.token_urlsafe(32)
        self.sessions[token] = Session(user_id, username, t + SESSION_TTL)
        return token

    async def avatar(self, request: web.Request) -> web.StreamResponse:
        try:
            uid = int(request.match_info["user_id"])
        except ValueError:
            raise web.HTTPNotFound()
        if uid not in self.avatar_cache:
            url = self.avatars.get(uid)
            if not url:
                raise web.HTTPNotFound()
            async with self.http.get(url) as r:
                if r.status != 200:
                    raise web.HTTPNotFound()
                if len(self.avatar_cache) > 500:
                    self.avatar_cache.clear()
                self.avatar_cache[uid] = (await r.read(), r.headers.get("Content-Type", "image/png"))
        body, ctype = self.avatar_cache[uid]
        return web.Response(body=body, content_type=ctype.split(";")[0],
                            headers={"Cache-Control": "public, max-age=3600"})

    async def verified_guild(self, instance_id: str, claimed_guild: int | None,
                             user_id: int) -> tuple[int | None, str | None]:
        """The guild of an Activity instance, checked with Discord: (guild id, problem).

        If Discord says the instance doesn't exist (404, asked INSTANCE_LOOKUP_TRIES times because a brand-new
        instance can take a moment to show up), the player is refused: otherwise a page could make up an instance
        for any server it likes. If Discord can't be asked at all (network trouble, a Discord error), the page's
        guild id is used, so an outage doesn't lock everyone out. Answers are remembered for INSTANCE_CACHE_TTL."""
        now = time.time()
        cached = self.instance_guilds.get(instance_id)
        if cached and cached[1] > now:
            return cached[0], None
        app_id = self.client_id()
        if not (app_id and self.bot_token and self.http):
            return claimed_guild, None
        url = f"{API}/applications/{app_id}/activity-instances/{instance_id}"
        for attempt in range(INSTANCE_LOOKUP_TRIES):
            if attempt:
                await asyncio.sleep(attempt)
            try:
                async with self.http.get(url, headers={"Authorization": f"Bot {self.bot_token}"}) as r:
                    if r.status == 200:
                        data = await r.json()
                        gid = (data.get("location") or {}).get("guild_id")
                        gid = int(gid) if gid else None
                        if str(user_id) not in [str(u) for u in data.get("users", [])]:
                            log.debug("User %s not (yet) listed in instance %s", user_id, instance_id)
                        if len(self.instance_guilds) > 2000:
                            self.instance_guilds = {k: v for k, v in self.instance_guilds.items() if v[1] > now}
                        self.instance_guilds[instance_id] = (gid, now + INSTANCE_CACHE_TTL)
                        return gid, None
                    if r.status != 404:
                        log.warning("Instance lookup for %s returned %s; using the page's guild id", instance_id,
                                    r.status)
                        return claimed_guild, None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                log.warning("Instance lookup for %s failed; using the page's guild id", instance_id, exc_info=True)
                return claimed_guild, None
            log.debug("Instance lookup for %s returned 404 (try %s of %s)", instance_id, attempt + 1,
                      INSTANCE_LOOKUP_TRIES)
        log.warning("Activity instance %s (user %s) is unknown to Discord; refused", instance_id, user_id)
        return None, "Discord doesn't know this game session. Close the Activity and open it again."

    # ------------------------------------------------------------------ websocket
    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await ws.prepare(request)
        inst: Instance | None = None
        player: Player | None = None
        try:
            first = await ws.receive(timeout=20)
            if first.type != aiohttp.WSMsgType.TEXT:
                return ws
            hello = json.loads(first.data)
            inst, player, problem = await self.join(hello, ws)
            if problem:
                await ws.send_str(json.dumps({"type": "fatal", "message": problem,
                                              "code": "session" if "session" in problem.lower() else ""}))
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
                        await inst.send(ws, {"type": "pong"})
                        continue
                    await inst.handle(player, data)
                except BetRejected as e:
                    await inst.send(ws, {"type": "error", "message": str(e)})
                except (ValueError, TypeError):
                    continue
                except Exception:
                    log.exception("Spin Frenzy: error handling %r from %s", msg.data[:200], player.user_id)
                    await inst.send(ws, {"type": "error", "message": "Something went wrong with that."})
        except (asyncio.TimeoutError, ValueError, TypeError):
            pass
        finally:
            if player is not None:
                player.sockets.discard(ws)
                if inst is not None:
                    await inst.push_state()
        return ws

    async def admit(self, hello: dict, ws: web.WebSocketResponse) -> tuple[Session | None, int | None, dict | None,
                                                                         str | None]:
        """Checks a page's first websocket message: (session, guild id, member, problem). Every game's connection
        comes through here, so this is also where a player's open connections are counted."""
        if not isinstance(hello, dict) or hello.get("type") != "join":
            return None, None, None, "Bad hello."
        session = self.sessions.get(str(hello.get("session", "")))
        if session is None or session.expires < time.time():
            return None, None, None, "Your session expired. Close and reopen the Activity."
        instance_id = str(hello.get("instance_id") or "")
        if not INSTANCE_ID.fullmatch(instance_id):
            return None, None, None, "Missing instance."
        open_now = self.user_sockets.setdefault(session.user_id, weakref.WeakSet())
        for old in [w for w in open_now if w.closed]:
            open_now.discard(old)
        if len(open_now) >= MAX_SOCKETS_PER_USER:
            return None, None, None, "You have the casino open in too many places. Close some of them and try again."
        try:
            claimed = int(hello.get("guild_id") or 0) or None
        except (TypeError, ValueError):
            claimed = None
        if self.dev_guild_id:
            guild_id, problem = self.dev_guild_id, None
        else:
            guild_id, problem = await self.verified_guild(instance_id, claimed, session.user_id)
        if problem:
            return None, None, None, problem
        if not guild_id:
            return None, None, None, "The games only work in a server's voice channel, not in DMs."
        member = await self.resolve_member(guild_id, session.user_id)
        if member is None:
            return None, None, None, "You need to be a member of this server (and the bot must be in it) to play."
        open_now.add(ws)
        return session, guild_id, member, None

    async def join(self, hello: dict, ws) -> tuple[Instance | None, Player | None, str | None]:
        session, guild_id, member, problem = await self.admit(hello, ws)
        if problem:
            return None, None, problem
        instance_id = str(hello.get("instance_id"))[:100]

        inst = self.instances.get(instance_id)
        if inst is not None and inst.guild_id != guild_id:
            return None, None, "This game belongs to a different server."
        if inst is None:
            inst = self.instances[instance_id] = Instance(self, instance_id, guild_id)
        player = inst.players.get(session.user_id)
        if player is None:
            player = inst.players[session.user_id] = Player(session.user_id, member["name"], member.get("avatar_url"))
        else:
            player.name = member["name"]
        if member.get("avatar_url"):
            self.avatars[session.user_id] = member["avatar_url"]
        player.sockets.add(ws)
        inst.ensure_running()
        s = self.settings
        await inst.send(ws, {
            "type": "welcome", "you": str(session.user_id), "wheel": engine.WHEEL, "spots": engine.SPOTS,
            "names": engine.SPOT_NAMES, "numbers": engine.NUMBERS, "min_bet": s.min_bet, "max_bet": s.max_bet,
            "bet_seconds": s.bet_seconds, "max_win_mult": s.max_win_mult, "rtp": s.rtp,
            "restart_amount": config.RESTART_AMOUNT, "restart_presses": max(1, config.RESTART_PRESSES),
            "grid_cells": engine.GRID_CELLS, "frenzy_pointers": engine.POINTER_NAMES,
            "info": {
                "segments": engine.SEGMENT_COUNTS,
                "chances": {s: round(engine.land_chance(s), 4) for s in engine.SPOTS},
                "returns": {s: round(engine.spot_rtp(s, self.chances), 4) for s in engine.SPOTS},
                "boost_mults": list(engine.BOOST_MULTS),
                "coin": [min(engine.COIN_LOW), max(engine.COIN_HIGH)],
                # [lowest, highest, smallest big prize, fewest big prizes per game, most big prizes per game]
                "grid": [min(engine.GRID_VALUES), max(engine.GRID_HIGH), min(engine.GRID_HIGH),
                         min(engine.GRID_HIGH_COUNT), max(engine.GRID_HIGH_COUNT)],
                # [lowest, highest, most doubles, smallest big prize, chance of each edge slot]
                "drop": [min(engine.DROP_VALUES), max(engine.DROP_HIGH), engine.DROP_MAX_DOUBLES,
                         min(engine.DROP_HIGH), engine.DROP_EDGE_CHANCE],
                "frenzy": [min(engine.FRENZY_VALUES), max(engine.FRENZY_VALUES), engine.FRENZY_MAX_BOOSTS],
            },
        })
        await inst.send(ws, inst.state())
        await inst.push_me(player)
        await inst.push_state()
        return inst, player, None
