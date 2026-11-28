"""Multiplayer Texas Hold'em (no-limit) inside the Discord Activity.

The rules are the same as /poker in chat (cogs/poker.py): players buy in with credits, the buy-in is held in
escrow (game "poker"), side pots and split pots are handled, and players are cashed out when they leave. Every
server has one poker room; anyone in it (from any voice call or text channel) sees its tables, can watch one,
open a new one or sit down.

Websocket /ws/poker. The page sends:
  {"type": "join", "session", "instance_id", "guild_id"}     first message
  {"type": "create", "buy_in": int, "max_players": int}     open a table and sit at it
  {"type": "watch", "table": id | null}                     look at a table (or back to the room list)
  {"type": "sit"} / {"type": "leave"} / {"type": "start"}   at the table you're watching
  {"type": "act", "action": "fold"|"check_call"|"raise"|"allin", "to": int}
The server sends "welcome", "room" (the list of tables), "table" (the table you're watching, with your own cards
and an "event" describing what just happened, for the page's animations), "me" (balance), "error" and "fatal".

The server paces a hand so the page can animate it: a short pause after the deal and after each new board card,
before the next player's turn starts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web

import config
from utils.cards import Card, new_deck
from utils.hand_eval import best_hand, hand_name
from utils.rng import rng
from activity.fatal import fatal_text
from utils.bignum import mul_floor
from utils import bignum

log = logging.getLogger("jaxcasino.activity.poker")

GAME = "poker"
LOG_KEEP = 100   # table log lines kept
LOG_SENT = 60    # ...and sent to the page, which scrolls through them
TURN_TIME = 30            # seconds a player has to act
NEXT_HAND_DELAY = 9       # seconds between hands (the showdown and the pot sliding to the winner play meanwhile)
MAX_TIMEOUTS = 2          # consecutive timeouts before a player is removed
MAX_TABLES = 8            # per server
# the buy-ins a table can have (the blinds are a 50th and a 100th of it); the high-stakes ones at the end
BUY_INS = (100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000,
           100_000_000, 250_000_000, 500_000_000)
MAX_MESSAGES_PER_SECOND = 12
DEAL_PAUSE = 2.2          # seconds for the page to deal the cards before the first turn
STREET_PAUSE = 1.3        # seconds for the page to show new board cards
RUNOUT_PAUSE = 2.0        # between streets when everyone is all-in
STREET_NAMES = {"preflop": "PRE-FLOP", "flop": "FLOP", "turn": "TURN", "river": "RIVER"}


def now_ms() -> int:
    return int(time.time() * 1000)


def card(c: Card) -> dict:
    return {"r": c.rank_name, "s": c.suit}


class Rejected(Exception):
    pass


RANK_WORDS = {2: ("two", "twos"), 3: ("three", "threes"), 4: ("four", "fours"), 5: ("five", "fives"),
              6: ("six", "sixes"), 7: ("seven", "sevens"), 8: ("eight", "eights"), 9: ("nine", "nines"),
              10: ("ten", "tens"), 11: ("jack", "jacks"), 12: ("queen", "queens"), 13: ("king", "kings"),
              14: ("ace", "aces")}


def spoken_hand(score) -> str:
    """A hand as the announcer says it: "a full house, kings over sevens", "two pair, aces and eights"."""
    kind, ranks = score
    one = lambda r: RANK_WORDS[r][0]
    many = lambda r: RANK_WORDS[r][1]
    if kind == 8:
        return "a royal flush" if ranks[0] == 14 else f"a straight flush, {one(ranks[0])} high"
    if kind == 7:
        return f"four {many(ranks[0])}"
    if kind == 6:
        return f"a full house, {many(ranks[0])} over {many(ranks[1])}"
    if kind == 5:
        return f"a flush, {one(ranks[0])} high"
    if kind == 4:
        return f"a straight, {one(ranks[0])} high"
    if kind == 3:
        return f"three {many(ranks[0])}"
    if kind == 2:
        return f"two pair, {many(ranks[0])} and {many(ranks[1])}"
    if kind == 1:
        return f"a pair of {many(ranks[0])}"
    return f"{one(ranks[0])} high"


def buy_ins(settings) -> list[int]:
    """The buy-ins a table can be opened with on this bot (none under the minimum bet, and at least 20)."""
    low = max(20, settings.min_bet)
    return [v for v in BUY_INS if v >= low] or [low]


@dataclass
class Seat:
    user_id: int
    name: str
    avatar: bool
    stack: int
    escrow_id: int
    bought_in: int = 0
    hole: list = field(default_factory=list)
    bet: int = 0           # chips put in this betting round
    committed: int = 0     # chips put in this hand
    in_hand: bool = False
    folded: bool = False
    all_in: bool = False
    acted: bool = False
    leaving: bool = False
    timeouts: int = 0
    last: str = ""         # last action this hand, shown on the seat ("Check", "Raise 400", …)
    streak: int = 0        # hands won in a row at this seat: won more than put in; even leaves it, less ends it

    def reset_for_hand(self) -> None:
        self.hole = []
        self.bet = self.committed = 0
        self.in_hand = True
        self.folded = self.all_in = self.acted = False
        self.last = ""

    def put(self, amount: int) -> int:
        amount = min(amount, self.stack)
        self.stack -= amount
        self.bet += amount
        self.committed += amount
        if self.stack == 0:
            self.all_in = True
        return amount


class Table:
    def __init__(self, room: "Room", table_id: int, host_id: int, host_name: str, buy_in: int, max_players: int):
        self.room = room
        self.id = table_id
        self.host_id = host_id
        self.name = f"{host_name}'s table"
        self.buy_in = buy_in
        self.bb = max(2, buy_in // 50)
        self.sb = max(1, self.bb // 2)
        self.max_players = max_players
        self.seats: list[Seat] = []
        self.phase = "waiting"    # waiting | playing | between | closed
        self.street = ""
        self.deck: list[Card] = []
        self.board: list[Card] = []
        self.dealer = -1
        self.turn = -1
        self.current_bet = 0
        self.min_raise = self.bb
        self.hand_no = 0
        self.log: list[str] = []
        self.result = ""
        self.reveal: dict[int, dict] = {}   # user id -> {"hand": name, "best": [cards]} at showdown
        self.won: dict[int, int] = {}       # user id -> chips won last hand
        self.event: dict = {}
        self.seq = 0
        self.lock = asyncio.Lock()
        self.turn_token = 0
        self.deadline = 0.0
        self.timer: asyncio.Task | None = None
        self.next_task: asyncio.Task | None = None

    @property
    def db(self):
        return self.room.hub.server.db

    @property
    def guild_id(self) -> int:
        return self.room.guild_id

    # ---------------------------------------------------------------- helpers
    def seat_of(self, user_id: int) -> Seat | None:
        return next((s for s in self.seats if s.user_id == user_id), None)

    @property
    def pot(self) -> int:
        return sum(s.committed for s in self.seats)

    @staticmethod
    def can_act(s: Seat) -> bool:
        return s.in_hand and not s.folded and not s.all_in

    def live(self) -> list[Seat]:
        return [s for s in self.seats if s.in_hand and not s.folded]

    def next_from(self, i: int, pred) -> int:
        n = len(self.seats)
        for k in range(1, n + 1):
            j = (i + k) % n
            if pred(self.seats[j]):
                return j
        return -1

    def note(self, text: str) -> None:
        self.log.append(text)
        self.log = self.log[-LOG_KEEP:]

    def index_of(self, seat: Seat) -> int:
        return self.seats.index(seat)

    # ---------------------------------------------------------------- what the page sees
    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "buy_in": self.buy_in, "sb": self.sb, "bb": self.bb,
                "seated": len(self.seats), "max": self.max_players, "phase": self.phase, "hand": self.hand_no,
                "players": [s.name for s in self.seats]}

    def view(self, user_id: int) -> dict:
        playing = self.phase in ("playing", "between")
        me = self.seat_of(user_id)
        seats = []
        for i, s in enumerate(self.seats):
            info = {"id": str(s.user_id), "name": s.name, "avatar": s.avatar, "stack": s.stack, "streak": s.streak,
                    "bet": s.bet if self.phase == "playing" else 0, "in_hand": playing and s.in_hand,
                    "folded": s.folded, "all_in": s.all_in, "leaving": s.leaving, "last": s.last,
                    "won": self.won.get(s.user_id, 0) if self.phase == "between" else 0,
                    "cards": None, "count": 0, "hand": None, "best": None, "online": s.user_id in self.room.viewers}
            if playing and s.in_hand and s.hole:
                info["count"] = len(s.hole)
                shown = self.reveal.get(s.user_id) if self.phase == "between" else None
                if shown or s.user_id == user_id:
                    info["cards"] = [card(c) for c in s.hole]
                if shown:
                    info["hand"], info["best"] = shown["hand"], shown["best"]
            seats.append(info)
        you = self.index_of(me) if me else -1
        out = {"type": "table", "id": self.id, "name": self.name, "host": str(self.host_id), "buy_in": self.buy_in,
               "sb": self.sb, "bb": self.bb, "max": self.max_players, "phase": self.phase,
               "street": STREET_NAMES.get(self.street, "") if self.phase == "playing" else "",
               "hand_no": self.hand_no, "board": [card(c) for c in self.board] if playing else [],
               "pot": self.pot if playing else 0, "current_bet": self.current_bet, "dealer": self.dealer,
               "turn": self.turn if self.phase == "playing" else -1, "deadline": int(self.deadline * 1000),
               "seats": seats, "you": you, "log": self.log[-LOG_SENT:], "result": self.result if self.phase == "between"
               else "", "event": self.event, "seq": self.seq, "turn_time": TURN_TIME, "rake": config.POKER_RAKE}
        if me and self.phase == "playing" and me.in_hand and not me.folded and me.hole and len(self.board) >= 3:
            out["my_hand"] = hand_name(best_hand(me.hole + self.board)[0])
        if me and self.phase == "playing" and self.turn >= 0 and self.seats[self.turn] is me:
            to_call = max(0, self.current_bet - me.bet)
            max_to = me.bet + me.stack
            out["to_call"] = min(to_call, me.stack)
            out["min_to"] = min(self.current_bet + self.min_raise, max_to)
            out["max_to"] = max_to
            out["can_raise"] = me.stack > to_call
        return out

    async def push(self, event: dict | None = None) -> None:
        """Sends everyone watching this table its new state (each with their own cards), and the room list."""
        self.seq += 1
        self.event = event or {}
        await self.room.push_table(self)
        await self.room.push_room()

    # ---------------------------------------------------------------- seating
    async def add_player(self, user_id: int, name: str, avatar: bool) -> None:
        if self.phase == "closed":
            raise Rejected("This table is closed.")
        if self.seat_of(user_id):
            raise Rejected("You're already at this table.")
        if self.room.table_of(user_id):
            raise Rejected("You're already sitting at another table. Leave it first.")
        if len(self.seats) >= self.max_players:
            raise Rejected("This table is full.")
        if not await self.db.try_debit(self.guild_id, user_id, self.buy_in):
            bal = await self.db.get_balance(self.guild_id, user_id)
            raise Rejected(f"The buy-in is {self.buy_in:,} but you only have {bal:,}.")
        escrow_id = await self.db.open_escrow(self.guild_id, user_id, self.buy_in, GAME)
        self.seats.append(Seat(user_id, name, avatar, self.buy_in, escrow_id, bought_in=self.buy_in))
        self.note(f"{name} sat down with {self.buy_in:,}")
        await self.room.push_me(user_id)

    async def cash_out(self, seat: Seat) -> None:
        """Returns the seat's stack to their balance and removes them from the table."""
        await self.db.settle_escrow(seat.escrow_id, self.guild_id, seat.user_id, seat.stack)
        await self.db.record_game(self.guild_id, seat.user_id, seat.bought_in, seat.stack, GAME)
        log.info("Poker (Activity): user %s left table %s with %s (bought in %s)", seat.user_id, self.id,
                 f"{seat.stack:,}", f"{seat.bought_in:,}")
        self.seats.remove(seat)
        if seat.user_id == self.host_id and self.seats:
            self.host_id = self.seats[0].user_id
        await self.room.push_me(seat.user_id)

    async def request_leave(self, seat: Seat) -> str:
        if self.phase == "playing" and seat.in_hand:
            seat.leaving = True
            if not seat.folded:
                if self.turn >= 0 and self.seats[self.turn] is seat:
                    self.apply(seat, "fold")
                    await self.push({"kind": "action", "seat": self.index_of(seat), "action": "fold"})
                    await self.advance()
                else:
                    seat.folded = True
                    seat.last = "Fold"
                    self.note(f"{seat.name} folds and leaves")
                    await self.push({"kind": "action", "seat": self.index_of(seat), "action": "fold"})
                    if len(self.live()) == 1:
                        await self.win_uncontested(self.live()[0])
            return "You folded and will be cashed out when this hand ends."
        if self.phase == "playing":
            seat.leaving = True
            await self.push()
            return "You'll be cashed out when this hand ends."
        stack = seat.stack
        await self.cash_out(seat)
        self.note(f"{seat.name} left with {stack:,}")
        if not self.seats:
            await self.close()
        else:
            await self.push()
        return f"You left the table and got {stack:,} back."

    # ---------------------------------------------------------------- hand flow
    async def start_hand(self) -> None:
        self.next_task = None
        old_ids = [s.user_id for s in self.seats]
        old_dealer = old_ids[self.dealer] if 0 <= self.dealer < len(old_ids) else None
        for s in list(self.seats):
            if s.leaving or s.stack <= 0:
                if s.stack <= 0 and not s.leaving:
                    self.note(f"{s.name} is out of chips")
                await self.cash_out(s)
        if not self.seats:
            await self.close()
            return
        if len(self.seats) < 2:
            self.phase = "waiting"
            self.board = []
            for s in self.seats:
                s.in_hand = False
                s.bet = s.committed = 0
            await self.push()
            return
        if old_dealer is None:
            self.dealer = rng.randrange(len(self.seats))
        else:
            start = old_ids.index(old_dealer)
            ids = [s.user_id for s in self.seats]
            self.dealer = 0
            for k in range(1, len(old_ids) + 1):
                uid = old_ids[(start + k) % len(old_ids)]
                if uid in ids:
                    self.dealer = ids.index(uid)
                    break
        self.phase = "playing"
        self.hand_no += 1
        self.street = "preflop"
        self.deck = new_deck()
        self.board = []
        self.result = ""
        self.reveal = {}
        self.won = {}
        for s in self.seats:
            s.reset_for_hand()
        for _ in range(2):
            for s in self.seats:
                s.hole.append(self.deck.pop())
        n = len(self.seats)
        if n == 2:
            sb_i, bb_i = self.dealer, (self.dealer + 1) % n
        else:
            sb_i = (self.dealer + 1) % n
            bb_i = (sb_i + 1) % n
        sb_paid = self.seats[sb_i].put(self.sb)
        bb_paid = self.seats[bb_i].put(self.bb)
        self.seats[sb_i].last = f"SB {sb_paid:,}"
        self.seats[bb_i].last = f"BB {bb_paid:,}"
        self.note(f"— Hand #{self.hand_no} —")
        self.note(f"{self.seats[sb_i].name} posts small blind {sb_paid:,}")
        self.note(f"{self.seats[bb_i].name} posts big blind {bb_paid:,}")
        self.current_bet = self.bb
        self.min_raise = self.bb
        self.turn = -1
        # the deal: the page flies two cards to every seat, starting left of the dealer
        await self.push({"kind": "deal", "hand": self.hand_no, "first": (self.dealer + 1) % n})
        await asyncio.sleep(DEAL_PAUSE)
        self.turn = bb_i
        await self.advance()

    def apply(self, seat: Seat, action: str, raise_to: int = 0) -> dict:
        """Applies an action for the seat whose turn it is. Returns the event for the page; raises Rejected."""
        i = self.index_of(seat)
        to_call = self.current_bet - seat.bet
        if action == "fold":
            seat.folded = True
            seat.last = "Fold"
            self.note(f"{seat.name} folds")
            ev = {"kind": "action", "seat": i, "action": "fold"}
        elif action == "check_call":
            if to_call <= 0:
                seat.last = "Check"
                self.note(f"{seat.name} checks")
                ev = {"kind": "action", "seat": i, "action": "check"}
            else:
                paid = seat.put(to_call)
                seat.last = f"Call {paid:,}" + (" · all-in" if seat.all_in else "")
                self.note(f"{seat.name} calls {paid:,}" + (" (all-in)" if seat.all_in else ""))
                ev = {"kind": "action", "seat": i, "action": "allin" if seat.all_in else "call", "amount": paid}
        elif action == "allin":
            target = seat.bet + seat.stack
            if target > self.current_bet:
                ev = self._raise(seat, target)
            else:
                paid = seat.put(seat.stack)
                seat.last = f"Call {paid:,} · all-in"
                self.note(f"{seat.name} calls {paid:,} (all-in)")
                ev = {"kind": "action", "seat": i, "action": "allin", "amount": paid}
        elif action == "raise":
            ev = self._raise(seat, raise_to)
        else:
            raise Rejected("Unknown action.")
        seat.acted = True
        return ev

    def _raise(self, seat: Seat, target: int) -> dict:
        max_to = seat.bet + seat.stack
        min_to = self.current_bet + self.min_raise
        if target <= self.current_bet:
            raise Rejected(f"You must raise to more than {self.current_bet:,}.")
        if target > max_to:
            raise Rejected(f"You only have {max_to:,} available.")
        if target < min_to and target != max_to:
            raise Rejected(f"The minimum raise is to {min_to:,}.")
        word = "Bet" if self.current_bet == 0 else "Raise to"
        before = seat.bet
        seat.put(target - seat.bet)
        if target - self.current_bet >= self.min_raise:
            self.min_raise = target - self.current_bet
        self.current_bet = target
        for s in self.seats:
            if s is not seat and self.can_act(s):
                s.acted = False
        seat.acted = True
        seat.last = f"{word} {target:,}" + (" · all-in" if seat.all_in else "")
        self.note(f"{seat.name} {word.lower()} {target:,}" + (" (all-in)" if seat.all_in else ""))
        return {"kind": "action", "seat": self.index_of(seat), "action": "allin" if seat.all_in else "raise",
                "amount": target - before, "to": target}

    async def advance(self) -> None:
        """Decides what happens after an action: next player, next street, or end of hand."""
        live = self.live()
        if len(live) == 1:
            await self.win_uncontested(live[0])
            return
        actors = [s for s in live if not s.all_in]
        pending = [s for s in actors if not s.acted or s.bet < self.current_bet]
        if len(actors) == 1 and actors[0].bet >= self.current_bet:
            pending = []
        if not pending:
            await self.next_street()
            return
        self.turn = self.next_from(self.turn, lambda s: s in pending)
        await self.start_turn()

    async def next_street(self) -> None:
        # the bets of this round slide into the pot
        had_bets = any(s.bet for s in self.seats)
        for s in self.seats:
            s.bet = 0
            s.acted = False
            if s.in_hand and not s.folded and not s.all_in:
                s.last = ""
        self.current_bet = 0
        self.min_raise = self.bb
        self.turn = -1
        if self.street == "river":
            if had_bets:
                await self.push({"kind": "collect"})
                await asyncio.sleep(0.7)
            await self.showdown()
            return
        self.deck.pop()  # burn
        if self.street == "preflop":
            new = [self.deck.pop() for _ in range(3)]
            self.street = "flop"
        elif self.street == "flop":
            new = [self.deck.pop()]
            self.street = "turn"
        else:
            new = [self.deck.pop()]
            self.street = "river"
        self.board += new
        self.note(f"{STREET_NAMES[self.street].title()}: " + " ".join(str(c) for c in new))
        await self.push({"kind": "street", "street": self.street, "collect": had_bets,
                         "new": [card(c) for c in new], "from": len(self.board) - len(new)})
        actors = [s for s in self.live() if not s.all_in]
        if len(actors) <= 1:
            # Everyone (or all but one) is all-in: run out the board.
            self.turn_token += 1
            await asyncio.sleep(RUNOUT_PAUSE + (0.6 if had_bets else 0))
            await self.next_street()
            return
        await asyncio.sleep(STREET_PAUSE + (0.6 if had_bets else 0))
        self.turn = self.dealer
        await self.advance()

    def build_pots(self) -> list[list]:
        contributors = [s for s in self.seats if s.committed > 0]
        levels = sorted({s.committed for s in contributors})
        pots: list[list] = []
        prev = 0
        for level in levels:
            amount = sum(min(s.committed, level) - min(s.committed, prev) for s in contributors)
            eligible = [s for s in contributors if not s.folded and s.committed >= level]
            if amount:
                if pots and eligible and {id(s) for s in pots[-1][1]} == {id(s) for s in eligible}:
                    pots[-1][0] += amount
                elif eligible:
                    pots.append([amount, eligible])
                elif pots:
                    pots[-1][0] += amount
                else:
                    pots.append([amount, self.live()])
            prev = level
        return pots

    def order_from_dealer(self, seats: list[Seat]) -> list[Seat]:
        n = len(self.seats)
        index = {id(s): i for i, s in enumerate(self.seats)}
        return sorted(seats, key=lambda s: (index[id(s)] - self.dealer - 1) % n)

    async def showdown(self) -> None:
        live = self.live()
        scores = {}
        for s in live:
            score, five = best_hand(s.hole + self.board)
            scores[id(s)] = score
            self.reveal[s.user_id] = {"hand": hand_name(score), "best": [card(c) for c in five]}
        pots = self.build_pots()
        results = []
        self.won = {}
        payouts = []
        for i, (amount, eligible) in enumerate(pots):
            rake = mul_floor(amount, config.POKER_RAKE) if len(eligible) > 1 else 0
            amount -= rake
            best = max(scores[id(s)] for s in eligible)
            winners = self.order_from_dealer([s for s in eligible if scores[id(s)] == best])
            share, rem = divmod(amount, len(winners))
            for w in winners:
                got = share + (rem if w is winners[0] else 0)
                w.stack += got
                self.won[w.user_id] = self.won.get(w.user_id, 0) + got
                payouts.append({"seat": self.index_of(w), "amount": got, "pot": i, "split": len(winners) > 1,
                                "uncalled": len(eligible) == 1, "hand": spoken_hand(best)})
            pot_name = "pot" if len(pots) == 1 else ("main pot" if i == 0 else f"side pot {i}")
            if len(eligible) == 1:
                results.append(f"{winners[0].name} gets back {amount:,} (uncalled)")
            else:
                names = ", ".join(w.name for w in winners)
                verb = "split" if len(winners) > 1 else "wins"
                results.append(f"{names} {verb} the {pot_name} of {amount:,} with {hand_name(best)}")
        for line in results:
            self.note(line)
        self.result = " · ".join(results)
        # first the cards are turned over, then the pot goes to the winners
        self.phase = "between"
        self.turn = -1
        self.turn_token += 1
        await self.push({"kind": "showdown"})
        await asyncio.sleep(2.2)
        await self.push({"kind": "win", "payouts": payouts})
        await self.end_hand()

    async def win_uncontested(self, winner: Seat) -> None:
        amount = self.pot
        winner.stack += amount
        self.won = {winner.user_id: amount}
        self.result = f"{winner.name} wins {amount:,}, everyone else folded"
        self.note(self.result)
        self.phase = "between"
        self.turn = -1
        self.turn_token += 1
        await self.push({"kind": "win", "payouts": [{"seat": self.index_of(winner), "amount": amount, "pot": 0}],
                         "uncontested": True})
        await self.end_hand()

    async def end_hand(self) -> None:
        self.phase = "between"
        self.turn = -1
        for s in self.seats:
            if not s.in_hand:      # sat this hand out
                continue
            net = self.won.get(s.user_id, 0) - s.committed
            if net > 0:
                s.streak += 1
                await self.db.record_streak(self.guild_id, s.user_id, GAME, s.streak)
            elif net < 0:
                s.streak = 0
        for s in self.seats:
            await self.db.update_escrow(s.escrow_id, s.stack)
        self.next_task = asyncio.create_task(self._next_hand_later())

    async def _next_hand_later(self) -> None:
        await asyncio.sleep(NEXT_HAND_DELAY)
        async with self.lock:
            if self.phase == "between":
                try:
                    await self.start_hand()
                except Exception:
                    log.exception("Poker (Activity): failed to start a hand at table %s", self.id)

    async def close(self) -> None:
        self.phase = "closed"
        self.turn_token += 1
        for task in (self.timer, self.next_task):
            if task and task is not asyncio.current_task():
                task.cancel()
        for s in list(self.seats):
            await self.cash_out(s)
        self.room.tables.pop(self.id, None)
        for v in self.room.viewers.values():
            if v.watching == self.id:
                v.watching = None
        await self.room.push_room()
        for uid in list(self.room.viewers):
            await self.room.push_view(uid)

    # ---------------------------------------------------------------- turns
    async def start_turn(self) -> None:
        self.turn_token += 1
        token = self.turn_token
        self.deadline = time.time() + TURN_TIME
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = asyncio.create_task(self._turn_timeout(token))
        await self.push({"kind": "turn", "seat": self.turn})

    async def _turn_timeout(self, token: int) -> None:
        await asyncio.sleep(TURN_TIME)
        async with self.lock:
            if token != self.turn_token or self.phase != "playing" or self.turn < 0:
                return
            seat = self.seats[self.turn]
            seat.timeouts += 1
            ev = self.apply(seat, "check_call" if seat.bet >= self.current_bet else "fold")
            self.note(f"{seat.name} timed out")
            if seat.timeouts >= MAX_TIMEOUTS:
                seat.leaving = True
                seat.folded = True
                self.note(f"{seat.name} was removed for inactivity")
            try:
                await self.push(ev)
                await self.advance()
            except Exception:
                log.exception("Poker (Activity): advance after a timeout failed")

    async def act(self, user_id: int, action: str, raise_to: int) -> None:
        seat = self.seat_of(user_id)
        if self.phase != "playing" or self.turn < 0 or seat is None or self.seats[self.turn] is not seat:
            raise Rejected("It's not your turn.")
        ev = self.apply(seat, action, raise_to)
        seat.timeouts = 0
        self.turn_token += 1   # the turn timer of this turn no longer applies
        await self.push(ev)
        await self.advance()


@dataclass
class Viewer:
    user_id: int
    name: str
    avatar: bool
    sockets: set = field(default_factory=set)
    watching: int | None = None


class Room:
    """The poker room of one server: its tables and everyone who has the poker page open."""

    def __init__(self, hub: "PokerHub", guild_id: int):
        self.hub = hub
        self.guild_id = guild_id
        self.tables: dict[int, Table] = {}
        self.viewers: dict[int, Viewer] = {}
        self.next_id = 1

    def table_of(self, user_id: int) -> Table | None:
        return next((t for t in self.tables.values() if t.seat_of(user_id)), None)

    async def send(self, ws, data: dict) -> None:
        if ws.closed:
            return
        data["now"] = now_ms()
        try:
            await ws.send_str(bignum.compact_dumps(data))   # big credits as strings
        except (ConnectionResetError, RuntimeError, aiohttp.ClientError):
            pass

    async def to_user(self, user_id: int, data: dict) -> None:
        v = self.viewers.get(user_id)
        if v:
            await asyncio.gather(*(self.send(ws, dict(data)) for ws in list(v.sockets)))

    def room_state(self) -> dict:
        players = []
        for v in self.viewers.values():
            seated = self.table_of(v.user_id)
            players.append({"id": str(v.user_id), "name": v.name, "avatar": v.avatar,
                            "table": seated.id if seated else None, "seated": bool(seated)})
        return {"type": "room", "tables": [t.summary() for t in self.tables.values()], "players": players}

    async def push_room(self) -> None:
        state = self.room_state()
        await asyncio.gather(*(self.to_user(uid, state) for uid in list(self.viewers)))

    async def push_table(self, table: Table) -> None:
        watchers = [uid for uid, v in self.viewers.items() if v.watching == table.id]
        await asyncio.gather(*(self.to_user(uid, table.view(uid)) for uid in watchers))

    async def push_view(self, user_id: int) -> None:
        """The table this user is watching, or that they're watching nothing."""
        v = self.viewers.get(user_id)
        if v is None:
            return
        t = self.tables.get(v.watching) if v.watching is not None else None
        await self.to_user(user_id, t.view(user_id) if t else {"type": "table", "id": None})

    async def push_me(self, user_id: int) -> None:
        if user_id in self.viewers:
            await self.to_user(user_id, {"type": "me", "balance": await self.hub.server.db.get_balance(
                self.guild_id, user_id)})

    async def handle(self, v: Viewer, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "create":
            buy_in, max_players = msg.get("buy_in"), msg.get("max_players", 6)
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in (buy_in, max_players)):
                raise Rejected("Invalid table settings.")
            s = self.hub.server.settings
            if buy_in < max(20, s.min_bet):
                raise Rejected(f"The buy-in must be at least {max(20, s.min_bet):,}.")
            if buy_in not in buy_ins(s):
                raise Rejected("Pick one of the buy-ins on the list.")
            if not 2 <= max_players <= 8:
                raise Rejected("A table has 2 to 8 seats.")
            if self.table_of(v.user_id):
                raise Rejected("You're already sitting at a table. Leave it first.")
            if len(self.tables) >= MAX_TABLES:
                raise Rejected("This server has the most poker tables it can have open. Join one of them.")
            t = Table(self, self.next_id, v.user_id, v.name, buy_in, max_players)
            self.next_id += 1
            async with t.lock:
                await t.add_player(v.user_id, v.name, v.avatar)
                self.tables[t.id] = t
                v.watching = t.id
                await t.push()
            log.info("Poker (Activity): user %s opened table %s (buy-in %s, %s seats) in guild %s", v.user_id,
                     t.id, f"{buy_in:,}", max_players, self.guild_id)
        elif kind == "watch":
            tid = msg.get("table")
            seated = self.table_of(v.user_id)
            if seated and tid != seated.id:
                raise Rejected("You're sitting at a table. Leave it before looking at another one.")
            if tid is not None and tid not in self.tables:
                raise Rejected("That table is gone.")
            v.watching = tid
            await self.push_view(v.user_id)
            await self.push_room()
        elif kind in ("sit", "leave", "start", "act"):
            t = self.tables.get(v.watching) if v.watching is not None else None
            if t is None:
                raise Rejected("Open a table first.")
            async with t.lock:
                if kind == "sit":
                    await t.add_player(v.user_id, v.name, v.avatar)
                    if t.phase == "waiting" and t.hand_no > 0 and len(t.seats) >= 2:
                        await t.start_hand()
                    else:
                        await t.push()
                elif kind == "leave":
                    seat = t.seat_of(v.user_id)
                    if seat is None:
                        raise Rejected("You're not sitting at this table.")
                    msg_text = await t.request_leave(seat)
                    await self.to_user(v.user_id, {"type": "notice", "message": msg_text})
                elif kind == "start":
                    if v.user_id != t.host_id:
                        raise Rejected("Only the table's host can start the game.")
                    if t.phase != "waiting" or len(t.seats) < 2:
                        raise Rejected("You need at least 2 players to start.")
                    await t.start_hand()
                else:
                    action = msg.get("action")
                    raise_to = msg.get("to", 0)
                    if action not in ("fold", "check_call", "raise", "allin") or not isinstance(raise_to, int):
                        raise Rejected("Invalid action.")
                    await t.act(v.user_id, action, raise_to)


class PokerHub:
    def __init__(self, server):
        self.server = server          # activity.server.ActivityServer
        self.rooms: dict[int, Room] = {}

    async def stop(self) -> None:
        """Bot shutdown: stops the tables. Seated players' buy-ins stay in escrow and are refunded (their stack
        as it was at the start of the hand) when the bot starts again, like the chat poker tables."""
        for room in self.rooms.values():
            for t in room.tables.values():
                for task in (t.timer, t.next_task):
                    if task:
                        task.cancel()

    def welcome(self, user_id: int) -> dict:
        s = self.server.settings
        self.server.voice.wake()   # the announcer's voice loads while they pick a table
        return {"type": "welcome", "you": str(user_id), "min_buy_in": max(20, s.min_bet), "buy_ins": buy_ins(s),
                "turn_time": TURN_TIME,
                "max_tables": MAX_TABLES, "rake": config.POKER_RAKE, "server_voice": self.server.voice.available}

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await ws.prepare(request)
        room: Room | None = None
        viewer: Viewer | None = None
        try:
            first = await ws.receive(timeout=20)
            if first.type != aiohttp.WSMsgType.TEXT:
                return ws
            hello = json.loads(first.data)
            session, guild_id, member, problem = await self.server.admit(hello, ws)
            if problem:
                await ws.send_str(fatal_text(problem))
                return ws
            room = self.rooms.get(guild_id)
            if room is None:
                room = self.rooms[guild_id] = Room(self, guild_id)
            viewer = room.viewers.get(session.user_id)
            if viewer is None:
                viewer = room.viewers[session.user_id] = Viewer(session.user_id, member["name"],
                                                                bool(member.get("avatar_url")))
            if member.get("avatar_url"):
                self.server.avatars[session.user_id] = member["avatar_url"]
            viewer.sockets.add(ws)
            seated = room.table_of(session.user_id)
            if seated:
                viewer.watching = seated.id      # back at your table after closing the Activity
            await room.send(ws, self.welcome(session.user_id))
            await room.push_me(session.user_id)
            await room.push_room()
            await room.push_view(session.user_id)
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
                        await room.send(ws, {"type": "pong"})
                        continue
                    await room.handle(viewer, data)
                except Rejected as e:
                    await room.send(ws, {"type": "error", "message": str(e)})
                except (ValueError, TypeError):
                    continue
                except Exception:
                    log.exception("Poker (Activity): error handling %r from %s", msg.data[:200], viewer.user_id)
                    await room.send(ws, {"type": "error", "message": "Something went wrong with that."})
        except (asyncio.TimeoutError, ValueError, TypeError):
            pass
        finally:
            if viewer is not None and room is not None:
                viewer.sockets.discard(ws)
                if not viewer.sockets:
                    room.viewers.pop(viewer.user_id, None)   # a seated player keeps their seat
                await room.push_room()
                for t in list(room.tables.values()):
                    if t.seat_of(viewer.user_id):
                        await room.push_table(t)
        return ws
