"""Banger Bash inside the Discord Activity.

Every player has their own slot machine. Everyone in the same server who has Banger Bash open shares one lobby,
whether they started the Activity in a voice call or in a text channel (/bangerbash): they see who else is
playing, what they're doing and their big wins. The game is utils/banger_engine.py, the same as the chat version.
The server decides every result; the page only animates what it is sent.

Websocket /ws/banger. The page sends:
  {"type": "join", "session", "instance_id", "guild_id"}      first message
  {"type": "spin", "bet": int, "mode": "spin"|<buy>}         a paid spin or a bonus buy (one round at a time; a
                                                             buy costs its price x the bet, see E.BUYS)
  {"type": "gamble"} / {"type": "take"}                      Grill Bonus: turn up the heat / play the feature
  {"type": "pick", "index": int}                             Hot Potato (0-2) and Picnic Pick (0-14) picks
  {"type": "announce", "round": int, "what": "bonus"|"win"}  the page has shown a bonus trigger or a big win;
                                                             the server checks it and tells the other players
  {"type": "shown", "round": int}                            the page has finished showing a round; until then
                                                             the others see the player as still playing it
  {"type": "restart"}                                        the bot's /restart when broke
The server sends "welcome", "round" (a new spin), "bonus" (the Grill Bonus after a choice), "settled" (the
round's payout and new balance), "me" (balance), "players", "feed", "restart", "error" and "fatal".

A round left waiting for a choice (the player closed the Activity, or walked away) is finished with default
choices after IDLE_FINISH seconds, and every unfinished round is finished and paid when the bot shuts down.
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
from utils import banger_engine as E
from utils.economy import Wager
from utils.rng import rng
from activity.fatal import fatal_text

log = logging.getLogger("jaxcasino.activity.banger")

GAME = "bangerbash"
IDLE_FINISH = 600            # seconds without a choice before an unfinished round finishes itself
TABLE_SHUTDOWN = 120         # a lobby with nobody connected and nothing unfinished is dropped after this
MAX_MESSAGES_PER_SECOND = 12
FEED = 20
BIG_WIN = 10                 # times the bet: announced to the other players


def now_ms() -> int:
    return int(time.time() * 1000)


class Rejected(Exception):
    pass


@dataclass
class SlotPlayer:
    user_id: int
    name: str
    avatar_url: str | None
    sockets: set = field(default_factory=set)
    round: E.Round | None = None
    round_id: int = 0
    bet: int = 0                         # the bet of the round being played (a buy's stake is its price x this)
    wager: Wager | None = None
    last_action: float = field(default_factory=time.time)
    last_win: int = 0
    last_x: float = 0.0
    settled: dict | None = None          # the last finished round, for "announce"
    presenting: tuple | None = None      # (status, feature, round id, since): a paid round the page is still showing
    announced: set = field(default_factory=set)
    revealed: int = 0                    # round id whose Grill Bonus the player's page has shown (see public)
    restart_presses: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def status(self) -> str:
        if self.round is None:
            return "idle"
        b = self.round.bonus
        if b is None:
            return "spinning"
        if b.feature is None:
            return "ladder"
        return b.feature.kind

    def public(self) -> dict:
        b = self.round.bonus if self.round else None
        status, feature = self.status, (E.TIERS[b.tier] if b and not b.lost else None)
        last_win, last_x = self.last_win, self.last_x
        if self.round is not None and b is not None and self.revealed != self.round_id:
            # the spin hit the Grill Bonus, but the player's reels are still turning: it's just a spin until their
            # page shows the bonus (it says so with "announce")
            status, feature = "spinning", None
        if self.round is None and self.presenting:
            # the round is paid but its page is still animating it: don't spoil the result for the others
            status, feature = self.presenting[0], self.presenting[1]
            last_win, last_x = self.shown_win, self.shown_x
        return {"id": str(self.user_id), "name": self.name, "avatar": bool(self.avatar_url),
                "online": bool(self.sockets), "status": status, "feature": feature,
                "last_win": last_win, "last_x": round(last_x, 2)}

    shown_win: int = 0
    shown_x: float = 0.0


class Table:
    """The Banger Bash lobby of one server: its players from every voice call and text channel."""

    def __init__(self, hub: "BangerHub", guild_id: int):
        self.hub = hub
        self.id = guild_id
        self.guild_id = guild_id
        self.players: dict[int, SlotPlayer] = {}
        self.feed: list[dict] = []
        self.empty_since: float | None = None

    @property
    def db(self):
        return self.hub.server.db

    # ------------------------------------------------------------------ sending
    async def send(self, ws: web.WebSocketResponse, data: dict) -> None:
        if ws.closed:
            return
        data["now"] = now_ms()
        try:
            await ws.send_str(json.dumps(data, separators=(",", ":")))
        except (ConnectionResetError, RuntimeError, aiohttp.ClientError):
            pass

    async def to_player(self, p: SlotPlayer, data: dict) -> None:
        await asyncio.gather(*(self.send(ws, dict(data)) for ws in list(p.sockets)))

    async def broadcast(self, data: dict) -> None:
        await asyncio.gather(*(self.send(ws, dict(data)) for p in self.players.values() for ws in list(p.sockets)))

    async def push_players(self) -> None:
        await self.broadcast({"type": "players", "players": [p.public() for p in self.players.values()
                                                             if p.sockets or p.round]})

    async def push_me(self, p: SlotPlayer, balance: int | None = None) -> None:
        if balance is None:
            balance = await self.db.get_balance(self.guild_id, p.user_id)
        await self.to_player(p, {"type": "me", "balance": balance})

    # ------------------------------------------------------------------ the game
    def rtp(self) -> float:
        return self.hub.rtp

    def round_view(self, p: SlotPlayer) -> dict:
        r = p.round
        return {"id": p.round_id, "bet": p.bet, "stake": p.wager.stake if p.wager else 0, "mode": r.mode,
                "spin": r.spin.to_dict(),
                "bonus": r.bonus.to_dict() if r.bonus else None, "done": r.done}

    async def handle(self, p: SlotPlayer, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "spin":
            bet = msg.get("bet")
            mode = msg.get("mode", "spin")
            if not isinstance(bet, int) or isinstance(bet, bool):
                raise Rejected("Invalid bet.")
            if mode not in E.MODES:
                raise Rejected("Unknown buy.")
            await self.spin(p, bet, mode)
        elif kind in ("gamble", "take", "pick"):
            await self.choose(p, kind, msg.get("index"))
        elif kind == "announce":
            await self.announce(p, msg.get("round"), msg.get("what"))
        elif kind == "shown":
            if p.presenting and p.presenting[2] == msg.get("round"):
                self.done_presenting(p)
                await self.push_players()
        elif kind == "restart":
            await self.restart(p)

    async def spin(self, p: SlotPlayer, bet: int, mode: str = "spin") -> None:
        s = self.hub.server.settings
        if bet < s.min_bet:
            raise Rejected(f"The minimum bet is {s.min_bet:,}.")
        if s.max_bet and bet > s.max_bet:
            raise Rejected(f"The maximum bet is {s.max_bet:,}.")
        if mode != "spin" and not config.BANGER_BUYS:
            raise Rejected("Bonus buys are switched off on this server.")
        if mode != "spin" and config.BUY_MAX_BET and bet > config.BUY_MAX_BET:
            raise Rejected(f"Bonus buys go up to a bet of {config.BUY_MAX_BET:,}.")
        stake = E.stake_for(bet, mode)
        async with p.lock:
            if p.round is not None:
                raise Rejected("Finish this round first.")
            wager = await Wager.create(self.db, self.guild_id, p.user_id, stake, GAME)
            if wager is None:
                raise Rejected("You don't have enough credits for that." if mode == "spin" else
                               f"You don't have enough credits for that: {E.BUYS[mode]['name']} costs {stake:,}.")
            p.wager = wager
            p.bet = bet
            try:
                p.round = E.Round(rng, mode)
            except Exception:
                p.wager = None
                await wager.refund()   # never keep a stake the game couldn't play
                raise
            p.round_id += 1
            p.last_action = time.time()
            view = self.round_view(p)
            balance = await self.db.get_balance(self.guild_id, p.user_id)
            await self.to_player(p, {"type": "round", "round": view, "balance": balance})
            if p.round.done:
                await self.settle(p)
        await self.push_players()

    async def choose(self, p: SlotPlayer, kind: str, index) -> None:
        async with p.lock:
            r = p.round
            if r is None or r.bonus is None or r.bonus.done:
                raise Rejected("There's nothing to choose right now.")
            b = r.bonus
            event: dict = {"kind": kind}
            try:
                if kind == "gamble":
                    event["won"] = b.gamble(rng)
                elif kind == "take":
                    b.take(rng)
                else:
                    f = b.feature
                    if f is None or f.done or not isinstance(index, int) or isinstance(index, bool):
                        raise Rejected("You can't pick that.")
                    rnd_before = getattr(f, "round", None)
                    event["index"] = index
                    event["value"] = f.pick(index)
                    if f.kind == "potato":
                        event["row"] = f.rounds[rnd_before]
            except ValueError:
                raise Rejected("You can't do that right now.")
            p.last_action = time.time()
            p.revealed = p.round_id   # choosing means the page is showing the bonus
            await self.to_player(p, {"type": "bonus", "round": p.round_id, "bonus": b.to_dict(), "event": event})
            if r.done:
                await self.settle(p)
        await self.push_players()

    def done_presenting(self, p: SlotPlayer) -> None:
        p.presenting = None
        p.shown_win, p.shown_x = p.last_win, p.last_x

    async def settle(self, p: SlotPlayer) -> None:
        """Pays out a finished round (call with the player's lock held)."""
        r, wager = p.round, p.wager
        b = r.bonus
        p.presenting = (p.status, E.TIERS[b.tier] if b and not b.lost else None, p.round_id, time.time())
        r.finish(rng)
        bet = p.bet or wager.stake
        pay = E.payout(wager.stake, r, self.rtp(), bet)
        balance = await wager.settle(pay)
        p.settled = {"round": p.round_id, "stake": wager.stake, "payout": pay, "x": pay / bet, "mode": r.mode,
                     "bonus": (None if r.bonus is None else "burnt" if r.bonus.lost else E.TIERS[r.bonus.tier])}
        p.last_win, p.last_x = pay, pay / bet
        p.round = None
        p.wager = None
        await self.to_player(p, {"type": "settled", "round": p.settled["round"], "payout": pay, "balance": balance,
                                 "x": p.settled["x"]})
        log.info("Banger Bash (Activity): user %s %s bet %s (paid %s), feature %s, bonus %s, won %s", p.user_id,
                 r.mode, f"{bet:,}", f"{wager.stake:,}", r.spin.feature or "-", p.settled["bonus"] or "-", f"{pay:,}")

    async def finish_idle(self, p: SlotPlayer) -> None:
        async with p.lock:
            if p.round is not None:
                await self.settle(p)
        await self.push_players()

    async def announce(self, p: SlotPlayer, round_id, what) -> None:
        """The page has just shown a bonus trigger or a big win: tell everyone (checked against the real round)."""
        if not isinstance(round_id, int):
            return
        if what == "bonus" and p.round is not None and p.round_id == round_id and p.round.bonus \
                and p.revealed != round_id:
            p.revealed = round_id          # now the player list may show the bonus
            await self.push_players()
        if (round_id, what) in p.announced:
            return
        item = None
        if what == "bonus" and p.round is not None and p.round_id == round_id and p.round.bonus:
            text = (f"lit the Grill Bonus with {p.round.bonus.grills} grills!" if p.round.mode == "spin"
                    else f"bought the {E.BUYS[p.round.mode]['name']}!")
            item = {"kind": "bonus", "text": text}
        elif what == "win" and p.settled and p.settled["round"] == round_id and p.settled["x"] >= BIG_WIN:
            s = p.settled
            label = "EPIC WIN" if s["x"] >= 50 else "MEGA WIN" if s["x"] >= 20 else "BIG WIN"
            how = f" in {E.TIER_NAMES[s['bonus']]}" if s["bonus"] in E.TIER_NAMES else ""
            item = {"kind": "win", "text": f"{label}{how}!", "amount": s["payout"], "x": round(s["x"], 1)}
        if item is None:
            return
        p.announced.add((round_id, what))
        if len(p.announced) > 50:
            p.announced = set(list(p.announced)[-20:])
        item.update(id=str(p.user_id), name=p.name, at=now_ms())
        self.feed.insert(0, item)
        del self.feed[FEED:]
        await self.broadcast({"type": "feed", "item": item})

    async def restart(self, p: SlotPlayer) -> None:
        """The bot's /restart, from inside the Activity (same rules as Spin Frenzy's)."""
        needed = max(1, config.RESTART_PRESSES)
        async with p.lock:
            if p.round is not None:
                raise Rejected("Finish this round first.")
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


class BangerHub:
    """All Banger Bash lobbies, one per server (keyed by guild id, so every Activity instance of a server, in a
    voice call or a text channel, joins the same one)."""

    def __init__(self, server):
        self.server = server          # activity.server.ActivityServer: sessions, database, settings, sign-in
        self.rtp = config.BANGER_RTP
        self.tables: dict[int, Table] = {}   # guild id -> lobby
        self.janitor: asyncio.Task | None = None

    def start(self) -> None:
        if self.janitor is None:
            self.janitor = asyncio.create_task(self._janitor(), name="banger-janitor")

    async def stop(self) -> None:
        if self.janitor:
            self.janitor.cancel()
            try:
                await self.janitor
            except (asyncio.CancelledError, Exception):
                pass
        for t in list(self.tables.values()):
            for p in list(t.players.values()):
                try:
                    await t.finish_idle(p)
                except Exception:
                    log.exception("Banger Bash: couldn't finish the round of user %s on shutdown", p.user_id)

    async def _janitor(self) -> None:
        while True:
            await asyncio.sleep(30)
            t_now = time.time()
            for tid, t in list(self.tables.items()):
                for p in list(t.players.values()):
                    if p.presenting and (not p.sockets or t_now - p.presenting[3] > 300):
                        t.done_presenting(p)
                        await t.push_players()
                    if p.round is not None and t_now - p.last_action > IDLE_FINISH:
                        log.info("Banger Bash: finishing the idle round of user %s", p.user_id)
                        try:
                            await t.finish_idle(p)
                        except Exception:
                            log.exception("Banger Bash: couldn't finish the idle round of user %s", p.user_id)
                    elif not p.sockets and p.round is None:
                        t.players.pop(p.user_id, None)
                if t.players:
                    t.empty_since = None
                elif t.empty_since is None:
                    t.empty_since = t_now
                elif t_now - t.empty_since > TABLE_SHUTDOWN:
                    self.tables.pop(tid, None)

    def welcome(self, user_id: int) -> dict:
        s = self.server.settings
        scale = E.scale(self.rtp)
        return {
            "type": "welcome", "you": str(user_id), "reels": E.REELS, "rows": E.ROWS, "paylines": E.PAYLINES,
            "symbols": {k: {"name": v[0], "pays": [p * scale / E.LINES for p in v[2]]} for k, v in E.SYMBOLS.items()},
            "paying": E.PAYING, "wild": E.WILD, "scatter": E.SCATTER, "mystery": E.MYSTERY,
            "features": {k: {"name": E.FEATURE_NAMES[k], "text": E.FEATURE_TEXT[k]} for k in E.FEATURE_NAMES},
            "tiers": [{"key": k, "name": E.TIER_NAMES[k], "text": E.TIER_TEXT[k], "avg": E.FEATURE_EV[k] * scale}
                      for k in E.TIERS],
            "potato_rounds": E.POTATO_ROUNDS, "picnic_cells": E.PICNIC_CELLS, "picnic_ants": E.PICNIC_ANTS,
            "free_spins": E.FREE_SPINS, "retrigger": E.RETRIGGER_SPINS, "golden_max": E.GOLDEN_MAX,
            "max_gambles": E.MAX_GAMBLES, "feature_chance": E.FEATURE_CHANCE,
            "buys": ({k: {"name": v["name"], "text": v["text"], "grills": v["grills"], "price": E.PRICES[k],
                          "rtp": E.buy_rtp(k, self.rtp)} for k, v in E.BUYS.items()} if config.BANGER_BUYS else {}),
            "scale": scale, "rtp": self.rtp, "max_win": E.MAX_WIN, "min_bet": s.min_bet, "max_bet": s.max_bet,
            "buy_max_bet": config.BUY_MAX_BET,
            "restart_amount": config.RESTART_AMOUNT, "restart_presses": max(1, config.RESTART_PRESSES),
            "big_win": BIG_WIN,
        }

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await ws.prepare(request)
        table: Table | None = None
        player: SlotPlayer | None = None
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
                    log.exception("Banger Bash: error handling %r from %s", msg.data[:200], player.user_id)
                    await table.send(ws, {"type": "error", "message": "Something went wrong with that."})
        except (asyncio.TimeoutError, ValueError, TypeError):
            pass
        finally:
            if player is not None:
                player.sockets.discard(ws)
                if table is not None:
                    await table.push_players()
        return ws

    async def join(self, hello: dict, ws) -> tuple[Table | None, SlotPlayer | None, str | None]:
        session, guild_id, member, problem = await self.server.admit(hello, ws)
        if problem:
            return None, None, problem
        # one lobby per server: the voice-call and text-channel Activities of a server share it
        table = self.tables.get(guild_id)
        if table is None:
            table = self.tables[guild_id] = Table(self, guild_id)
        self.start()
        p = table.players.get(session.user_id)
        if p is None:
            p = table.players[session.user_id] = SlotPlayer(session.user_id, member["name"], member.get("avatar_url"))
        else:
            p.name = member["name"]
        if member.get("avatar_url"):
            self.server.avatars[session.user_id] = member["avatar_url"]
        p.sockets.add(ws)
        await table.send(ws, self.welcome(session.user_id))
        balance = await table.db.get_balance(guild_id, p.user_id)
        await table.send(ws, {"type": "me", "balance": balance})
        if p.round is not None:   # back after closing the Activity mid-round: carry on where it was
            await table.send(ws, {"type": "round", "round": table.round_view(p), "balance": balance,
                                  "resume": True})
        await table.send(ws, {"type": "feed_all", "items": table.feed})
        await table.push_players()
        return table, p, None
