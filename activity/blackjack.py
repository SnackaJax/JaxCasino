"""Multiplayer blackjack inside the Discord Activity: several players at one table, each against the dealer.

The rules are the same as /blackjack in chat (cogs/blackjack.py): a 6-deck shoe, the dealer stands on all 17s,
blackjack pays 3:2, double on any two cards, split pairs up to 4 hands (split aces get one card each), and the
dealer's face-up card comes from the same shoe rule (cogs.blackjack.draw_upcard). The chat game stays as it is;
this is a second way to play.

Two side bets, each at most the player's main bet, settled right after the deal:
  Perfect Pairs (the player's first two cards): perfect pair 25:1, coloured pair 12:1, mixed pair 6:1
  21+3 (the player's first two cards and the dealer's face-up card as a poker hand): suited trips 100:1,
       straight flush 40:1, three of a kind 30:1, straight 10:1, flush 5:1
They return about 94% and 95.5% (6 decks, with the dealer face-up card rule above).

Every server has one blackjack room; anyone in it (from any voice call or text channel) sees its tables, can watch
one, open a new one (minimum bet and 1-7 seats) or sit down. There is no buy-in: every round each seated player
bets straight from their balance, held in escrow (game "blackjack") until the round is paid, so a bot restart
mid-round refunds it.

A round: betting (the countdown starts with the first bet, and the cards come out early once everyone seated has
pressed Deal) -> "no more bets" -> the deal -> side bets paid -> each player's turn, from the dealer's left -> the dealer turns over the hole card and
draws to 17 -> payouts -> the cards are cleared and betting opens again.

Websocket /ws/blackjack. The page sends:
  {"type": "join", "session", "instance_id", "guild_id"}     first message
  {"type": "create", "min_bet": int, "max_players": int}    open a table and sit at it
  {"type": "watch", "table": id | null}                     look at a table (or back to the room list)
  {"type": "sit", "seat": int | null} / {"type": "leave", "seat"?}   at the table you're watching
  {"type": "bet", "amount": int, "spot": "main"|"pp"|"t3", "seat"?} / "clear" / "rebet" / "double_bet" / "ready"
A player can sit at any number of the free seats of a table, each with its own bets and hands. "seat" picks which of
their seats a message is for; without it, "bet" goes to their first seat and the others are for all their seats.
  {"type": "act", "action": "hit"|"stand"|"double"|"split"}                         on your turn
  {"type": "chat", "id": int}                               a quick chat (an emote or a phrase from QUICK_CHATS)
The server sends "welcome", "room" (the list of tables), "table" (the table you're watching, with an "event"
describing what just happened, for the page's animations), "chat" (someone at your table said something),
"me" (balance), "notice", "error" and "fatal".
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

from cogs.blackjack import MAX_HANDS, card_value, draw_upcard, hand_value, is_blackjack
from utils.cards import Card, new_deck
from utils.economy import Wager

log = logging.getLogger("gamblebot.activity.blackjack")

GAME = "blackjack"            # same game name as the chat version, so /stats and /serverstats add them up
DECKS = 6
RESHUFFLE_AT = 0.25           # a fresh shoe once fewer than this share of its cards are left
BET_TIME = 15                 # seconds of betting after the first bet of a round
TURN_TIME = 20                # seconds a player has to act on a hand
MAX_TIMEOUTS = 2              # turns timed out in a row before a player is taken off the table
SIT_OUT_LIMIT = 5             # rounds in a row without a bet before a player is taken off the table
OFFLINE_GRACE = 60            # seconds a player who closed the Activity keeps an empty seat (no bet) between rounds
MAX_TABLES = 8                # per server
MAX_SEATS = 7
MAX_SEATS_EACH = MAX_SEATS    # seats one player can take at the same table (no limit: every free one)
MAX_BET_MULT = 100            # a table's maximum bet is this many times its minimum
MAX_MESSAGES_PER_SECOND = 12
# pauses, so the page can animate each step
DEAL_EACH = 0.28              # per card dealt
DEAL_END = 0.8
SHUFFLE_PAUSE = 2.2
REVEAL_PAUSE = 1.1
DEALER_CARD_PAUSE = 0.95
SETTLE_PAUSE = 4.2            # the payouts are shown before the cards are cleared
CLOSE_PAUSE = 1.4             # "no more bets" before the cards come out
SIDE_PAUSE = 2.6              # the side bets are paid before the first turn
SIDE_BETS = ("pp", "t3")
PP_PAYS = {"perfect": 25, "colored": 12, "mixed": 6}
T3_PAYS = {"suited_trips": 100, "straight_flush": 40, "trips": 30, "straight": 10, "flush": 5}
SIDE_NAMES = {"perfect": "Perfect pair", "colored": "Coloured pair", "mixed": "Mixed pair",
              "suited_trips": "Suited trips", "straight_flush": "Straight flush", "trips": "Three of a kind",
              "straight": "Straight", "flush": "Flush"}
BET_NAMES = {"main": "main bet", "pp": "Perfect Pairs", "t3": "21+3"}
# Quick chats: only these can be said at a table (no free text, so nothing to moderate). A chat is {"id": n}, an
# index into EMOTES + PHRASES; the pages get both lists in the welcome.
EMOTES = ("👍", "😂", "😮", "😭", "🔥", "💰", "🍀", "🙏", "😎", "🤡")
PHRASES = ("Kom maar hier met die poen! 💰", "Kassa! 💸", "Doe mij nog maar een kaartje 🃏",
           "Ik voel 'm, deze is raak! 🔥", "Kom op dealer, over die 21 heen! 🙏", "Wie niet waagt, die niet wint! 🎲",
           "Eitje! 🥚", "Hoppa! 🙌", "Lekker bezig! 👏", "Wat een mazzelaar! 🍀", "Gefeliciteerd! 🎉",
           "Pfoe, dat was spannend 😅", "Ik had moeten stoppen… 🤦", "Daar gaat m'n huur… 😭",
           "Dit is gewoon diefstal 😤", "Dealer, doe eens lief 🥺", "Volgende ronde beter 🙃",
           "Niet zeuren, gewoon spelen 😎", "Gezellig hier! 🍻")
QUICK_CHATS = EMOTES + PHRASES
CHAT_BURST, CHAT_WINDOW, CHAT_GAP = 4, 8.0, 0.8   # at most 4 chats per 8 seconds, 0.8 s apart
RED = set("♥♦")


def now_ms() -> int:
    return int(time.time() * 1000)


def card(c: Card) -> dict:
    return {"r": c.rank_name, "s": c.suit}


class Rejected(Exception):
    pass


def perfect_pairs(a: Card, b: Card) -> str | None:
    if a.rank != b.rank:
        return None
    if a.suit == b.suit:
        return "perfect"
    return "colored" if (a.suit in RED) == (b.suit in RED) else "mixed"


def twenty_one_plus_three(cards: list[Card]) -> str | None:
    ranks = sorted(c.rank for c in cards)
    flush = len({c.suit for c in cards}) == 1
    trips = ranks[0] == ranks[2]
    straight = (len(set(ranks)) == 3 and ranks[2] - ranks[0] == 2) or ranks == [2, 3, 14]
    if trips:
        return "suited_trips" if flush else "trips"
    if straight:
        return "straight_flush" if flush else "straight"
    return "flush" if flush else None


@dataclass
class Hand:
    cards: list
    stake: int
    doubled: bool = False
    from_split: bool = False
    done: bool = False
    result: str = ""          # "" while open, then blackjack | win | push | lose | bust
    payout: int = 0

    @property
    def value(self) -> int:
        return hand_value(self.cards)[0]

    def view(self) -> dict:
        total, soft = hand_value(self.cards)
        return {"cards": [card(c) for c in self.cards], "stake": self.stake, "doubled": self.doubled,
                "total": total, "soft": soft and total < 21, "done": self.done, "result": self.result,
                "payout": self.payout}


@dataclass(eq=False)          # seats are told apart by identity: one player's seats can look the same
class Seat:
    user_id: int
    name: str
    avatar: bool
    bet: int = 0                     # the bet for the coming round, while betting
    wager: Wager | None = None
    ready: bool = False
    hands: list = field(default_factory=list)   # this round's hands (empty when sitting the round out)
    last_bet: int = 0
    leaving: bool = False
    timeouts: int = 0
    sat_out: int = 0
    won: int = 0                     # paid back last round (stake included, side bets too)
    side: dict = field(default_factory=lambda: {"pp": 0, "t3": 0})   # side bets this round
    last_side: dict = field(default_factory=lambda: {"pp": 0, "t3": 0})
    side_results: list = field(default_factory=list)
    side_won: int = 0
    side_done: bool = False          # this round's side bets have been paid (or taken)

    @property
    def side_total(self) -> int:
        return sum(self.side.values())
    offline_since: float | None = None


class Table:
    def __init__(self, room: "Room", table_id: int, host_id: int, host_name: str, min_bet: int, max_players: int):
        self.room = room
        self.id = table_id
        self.host_id = host_id
        self.name = f"{host_name}'s table"
        self.min_bet = min_bet
        cap = room.hub.server.settings.max_bet
        self.max_bet = max(min_bet, min(min_bet * MAX_BET_MULT, cap) if cap else min_bet * MAX_BET_MULT)
        self.max_players = max_players
        self.seats: list[Seat | None] = [None] * max_players
        self.phase = "betting"       # betting | dealing | playing | dealer | settle | closed
        self.shoe: list[Card] = new_deck(DECKS)
        self.cut = int(len(self.shoe) * RESHUFFLE_AT)
        self.dealer: list[Card] = []
        self.hole_hidden = True
        self.turn: tuple[int, int] | None = None     # (seat, hand)
        self.deadline = 0.0
        self.round_no = 0
        self.log: list[str] = []
        self.result = ""
        self.event: dict = {}
        self.seq = 0
        self.lock = asyncio.Lock()
        self.token = 0                # bumped to cancel the running betting / turn timer
        self.timer: asyncio.Task | None = None
        self.flow: asyncio.Task | None = None

    @property
    def db(self):
        return self.room.hub.server.db

    @property
    def guild_id(self) -> int:
        return self.room.guild_id

    # ---------------------------------------------------------------- helpers
    def seated(self) -> list[tuple[int, Seat]]:
        return [(i, s) for i, s in enumerate(self.seats) if s is not None]

    def playing(self) -> list[tuple[int, Seat]]:
        return [(i, s) for i, s in self.seated() if s.hands]

    def seat_of(self, user_id: int) -> Seat | None:
        """The player's first seat at this table (from the dealer's left)."""
        return next((s for _, s in self.seated() if s.user_id == user_id), None)

    def seats_of(self, user_id: int) -> list[Seat]:
        return [s for _, s in self.seated() if s.user_id == user_id]

    def index_of(self, seat: Seat) -> int:
        return self.seats.index(seat)

    def note(self, text: str) -> None:
        self.log.append(text)
        self.log = self.log[-30:]

    def draw(self) -> Card:
        if not self.shoe:
            self.shoe = new_deck(DECKS)
        return self.shoe.pop()

    def spawn(self, coro) -> None:
        """Runs the next steps of the round (the deal, the dealer's turn) without holding the table up."""
        self.flow = asyncio.create_task(self._guarded(coro))

    async def _guarded(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Blackjack (Activity): round %s at table %s failed; refunding its bets", self.round_no,
                          self.id)
            async with self.lock:
                await self.recover()

    async def recover(self) -> None:
        """Something went wrong mid-round: every unpaid bet goes back and betting opens again."""
        for _, s in self.seated():
            if s.wager and not s.wager.settled:
                await s.wager.refund()
            s.wager = None
            s.bet, s.ready, s.hands, s.won = 0, False, [], 0
            self.reset_side(s)
            await self.room.push_me(s.user_id)
        self.dealer, self.turn, self.phase, self.deadline = [], None, "betting", 0
        self.note("Something went wrong, so this round's bets were refunded")
        await self.push({"kind": "clear"})

    @staticmethod
    def reset_side(s: Seat) -> None:
        s.side = {"pp": 0, "t3": 0}
        s.side_results, s.side_won, s.side_done = [], 0, False

    # ---------------------------------------------------------------- what the page sees
    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "min_bet": self.min_bet, "max_bet": self.max_bet,
                "seated": len(self.seated()), "max": self.max_players, "phase": self.phase, "round": self.round_no,
                "players": [s.name for _, s in self.seated()]}

    def view(self, user_id: int) -> dict:
        seats = []
        for s in self.seats:
            if s is None:
                seats.append(None)
                continue
            live = [h for h in s.hands if h.result not in ("bust", "lose")] if self.phase != "settle" else s.hands
            seats.append({"id": str(s.user_id), "name": s.name, "avatar": s.avatar,
                          "online": s.user_id in self.room.viewers, "ready": s.ready, "leaving": s.leaving,
                          "bet": s.bet if self.phase == "betting" else sum(h.stake for h in live),
                          "hands": [h.view() for h in s.hands], "last_bet": s.last_bet,
                          "won": s.won if self.phase == "settle" else 0,
                          "staked": sum(h.stake for h in s.hands) + (s.side_total if s.hands else 0),
                          "side": {k: 0 for k in SIDE_BETS} if s.side_done else dict(s.side),
                          "last_side": dict(s.last_side), "side_results": s.side_results})
        if self.dealer:
            shown = self.dealer[:1] if self.hole_hidden else self.dealer
            total, soft = hand_value(shown)
            dealer = {"cards": [card(c) for c in self.dealer[:1]] + ([None] if self.hole_hidden else
                                                                        [card(c) for c in self.dealer[1:]]),
                      "total": total, "soft": soft and total < 21,
                      "blackjack": not self.hole_hidden and is_blackjack(self.dealer)}
        else:
            dealer = {"cards": [], "total": 0, "soft": False, "blackjack": False}
        mine = [i for i, s in self.seated() if s.user_id == user_id]
        out = {"type": "table", "id": self.id, "name": self.name, "host": str(self.host_id),
               "min_bet": self.min_bet, "max_bet": self.max_bet, "max": self.max_players, "phase": self.phase,
               "round": self.round_no, "seats": seats, "dealer": dealer,
               "turn": {"seat": self.turn[0], "hand": self.turn[1]} if self.turn and self.phase == "playing" else None,
               "deadline": int(self.deadline * 1000), "you": mine[0] if mine else -1, "mine": mine, "log": self.log[-14:], "result": self.result,
               "event": self.event, "seq": self.seq, "bet_time": BET_TIME, "turn_time": TURN_TIME,
               "shoe": len(self.shoe), "shoe_size": DECKS * 52}
        me = self.seats[self.turn[0]] if self.turn and self.phase == "playing" else None
        if me and me.user_id == user_id:
            h = me.hands[self.turn[1]]
            two = len(h.cards) == 2 and not h.done
            out["can_double"] = two
            out["can_split"] = two and len(me.hands) < MAX_HANDS and card_value(h.cards[0]) == card_value(h.cards[1])
            out["extra"] = h.stake
        return out

    async def push(self, event: dict | None = None) -> None:
        """Sends everyone watching this table its new state, and the room list."""
        self.seq += 1
        self.event = event or {}
        await self.room.push_table(self)
        await self.room.push_room()

    # ---------------------------------------------------------------- seating
    async def add_player(self, user_id: int, name: str, avatar: bool, spot) -> None:
        if self.phase == "closed":
            raise Rejected("This table is closed.")
        mine = len(self.seats_of(user_id))
        if mine >= MAX_SEATS_EACH:
            raise Rejected(f"You can sit at up to {MAX_SEATS_EACH} seats of a table.")
        other = self.room.table_of(user_id)
        if other and other is not self:
            raise Rejected("You're already sitting at another table. Leave it first.")
        free = [i for i, s in enumerate(self.seats) if s is None]
        if not free:
            raise Rejected("This table is full.")
        if spot is None:
            spot = free[0]
        if not isinstance(spot, int) or isinstance(spot, bool) or spot not in free:
            raise Rejected("That seat is taken.")
        self.seats[spot] = Seat(user_id, name, avatar)
        self.note(f"{name} takes another seat" if mine else f"{name} sat down")
        await self.room.push_me(user_id)

    async def remove(self, seat: Seat, reason: str = "") -> None:
        """Takes a player off the table (any bet that isn't in play yet goes back to them)."""
        if seat.wager and not seat.wager.settled:
            await seat.wager.refund()
        seat.wager = None
        i = self.index_of(seat)
        self.seats[i] = None
        self.note(reason or (f"{seat.name} left a seat" if self.seat_of(seat.user_id) else f"{seat.name} left"))
        if seat.user_id == self.host_id and self.seated() and not self.seat_of(seat.user_id):
            self.host_id = self.seated()[0][1].user_id
        await self.room.push_me(seat.user_id)
        log.info("Blackjack (Activity): user %s left table %s", seat.user_id, self.id)

    async def leave_seats(self, seats: list[Seat]) -> str:
        """Leaves some of one player's seats: at once when nothing is in play there, else after the round."""
        texts = [await self.request_leave(s) for s in seats if s in self.seats and self.phase != "closed"]
        if len(texts) == 1:
            return texts[0]
        if any("once this round is paid" in t for t in texts):
            return "Your hands stand, and you'll leave the table once this round is paid."
        return "You left the table."

    async def request_leave(self, seat: Seat) -> str:
        if seat.hands and self.phase != "betting":
            seat.leaving = True
            if self.turn and self.seats[self.turn[0]] is seat:
                for h in seat.hands:
                    h.done = True
                self.note(f"{seat.name} stands and leaves")
                await self.push({"kind": "stand", "seat": self.turn[0], "hand": self.turn[1]})
                await self.advance()
            else:
                await self.push()
            return "Your hands stand, and you'll leave the table once this round is paid."
        await self.remove(seat)
        if not self.seated():
            await self.close()
        else:
            if self.phase == "betting":
                self.check_bets_left()
            await self.push()
        return "You left the table."

    # ---------------------------------------------------------------- betting
    def check_bets_left(self) -> None:
        """With no bets left on the table, the betting countdown stops until someone bets again."""
        if self.phase == "betting" and not any(s.bet for _, s in self.seated()):
            self.token += 1
            self.deadline = 0

    async def place_bets(self, seat: Seat, adds: dict[str, int]) -> None:
        """Puts chips on the main bet and/or the side bets ({"main": 100, "pp": 50}), all at once."""
        if self.phase != "betting":
            raise Rejected("Bets are closed. Wait for the next round.")
        if seat.leaving:
            raise Rejected("You're leaving this table.")
        if any(k not in ("main", *SIDE_BETS) or not isinstance(v, int) or v < 0 for k, v in adds.items()):
            raise Rejected("Invalid bet.")
        main = adds.get("main", 0)
        if main:
            if seat.bet + main < self.min_bet:
                raise Rejected(f"The minimum bet at this table is {self.min_bet:,}.")
            main = min(main, self.max_bet - seat.bet)
            if main <= 0:
                raise Rejected(f"The maximum bet at this table is {self.max_bet:,}.")
        new_main = seat.bet + main
        sides = {}
        for k in SIDE_BETS:
            if not adds.get(k):
                continue
            if new_main < self.min_bet:
                raise Rejected("Put your main bet down first: a side bet goes with it.")
            sides[k] = max(0, min(adds[k], new_main - seat.side[k]))   # a side bet is at most the main bet
            if not sides[k] and len(adds) == 1:
                raise Rejected(f"A side bet can't be more than your main bet ({new_main:,}).")
        amount = main + sum(sides.values())
        if amount <= 0:
            return
        if seat.wager is None:
            wager = await Wager.create(self.db, self.guild_id, seat.user_id, amount, GAME)
            if wager is None:
                raise Rejected("You don't have enough credits for that.")
            seat.wager = wager
        elif not await seat.wager.add_stake(amount):
            raise Rejected("You don't have enough credits for that.")
        seat.bet = new_main
        for k, v in sides.items():
            seat.side[k] += v
        seat.sat_out = 0
        if not self.deadline:
            self.start_bet_timer()
        await self.push({"kind": "bet", "seat": self.index_of(seat), "amounts": {"main": main, **sides}})
        await self.room.push_me(seat.user_id)

    async def clear_bet(self, seat: Seat) -> None:
        if self.phase != "betting":
            raise Rejected("Bets are closed. Wait for the next round.")
        if not seat.bet and not seat.side_total:
            return
        if seat.wager:
            await seat.wager.refund()
        seat.wager = None
        amounts = {"main": seat.bet, **seat.side}
        seat.bet, seat.ready = 0, False
        self.reset_side(seat)
        self.check_bets_left()
        await self.push({"kind": "unbet", "seat": self.index_of(seat), "amounts": amounts})
        await self.room.push_me(seat.user_id)

    async def set_ready(self, seats: list[Seat]) -> None:
        """Deal: the player is done betting, on all their seats (one without a bet sits this round out)."""
        if self.phase != "betting":
            raise Rejected("The cards are already out.")
        if not any(s.bet >= self.min_bet for s in seats):
            raise Rejected("Place a bet first.")
        for s in seats:
            s.ready = True
        if not await self.maybe_start():
            await self.push()

    async def maybe_start(self) -> bool:
        """Deals straight away once everyone seated has bet and pressed Deal."""
        active = [s for _, s in self.seated() if not s.leaving]
        if self.phase == "betting" and any(s.bet for s in active) and all(s.ready for s in active):
            self.begin_round()
            return True
        return False

    def start_bet_timer(self) -> None:
        self.token += 1
        token = self.token
        self.deadline = time.time() + BET_TIME
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = asyncio.create_task(self._bet_timeout(token))

    async def _bet_timeout(self, token: int) -> None:
        await asyncio.sleep(BET_TIME)
        async with self.lock:
            if token != self.token or self.phase != "betting":
                return
            if any(s.bet for _, s in self.seated()):
                self.begin_round()
            else:
                self.deadline = 0
                await self.push()

    def begin_round(self) -> None:
        self.token += 1
        self.phase = "dealing"
        self.deadline = 0
        self.spawn(self.round_flow())

    # ---------------------------------------------------------------- the round
    async def round_flow(self) -> None:
        async with self.lock:
            for i, s in self.seated():
                if s.bet:
                    s.hands = [Hand([], s.bet)]
                    s.last_bet, s.ready, s.won, s.sat_out = s.bet, False, 0, 0
                    s.last_side = dict(s.side)
                    s.side_results, s.side_won, s.side_done = [], 0, False
                    continue
                s.sat_out += 1
                offline = s.offline_since is not None and time.time() - s.offline_since > OFFLINE_GRACE
                if s.leaving or s.sat_out >= SIT_OUT_LIMIT or offline:
                    await self.remove(s, f"{s.name} was taken off the table (no bets)")
            if not self.playing():
                self.phase = "betting"
                if not self.seated():
                    await self.close()
                else:
                    await self.push()
                return
            self.round_no += 1
            self.result = ""
            await self.push({"kind": "closed"})
        await asyncio.sleep(CLOSE_PAUSE)
        async with self.lock:
            shuffle = len(self.shoe) < self.cut
            if shuffle:
                self.shoe = new_deck(DECKS)
                self.note("The dealer shuffles a fresh shoe")
                await self.push({"kind": "shuffle"})
        if shuffle:
            await asyncio.sleep(SHUFFLE_PAUSE)
        async with self.lock:
            players = self.playing()
            for _, s in players:
                s.hands[0].cards.append(self.draw())
            up = draw_upcard(self.shoe) if self.shoe else self.draw()
            for _, s in players:
                s.hands[0].cards.append(self.draw())
            self.dealer = [up, self.draw()]
            self.hole_hidden = True
            for _, s in players:
                s.bet = 0
            self.note(f"— Round #{self.round_no} — dealer shows {up}")
            self.phase = "playing"
            self.turn = None
            await self.push({"kind": "deal", "round": self.round_no})
            cards = 2 * len(players) + 2
        await asyncio.sleep(DEAL_EACH * cards + DEAL_END)
        async with self.lock:
            paid = await self.settle_side_bets()
        if paid:
            await asyncio.sleep(SIDE_PAUSE)
        async with self.lock:
            if is_blackjack(self.dealer):
                self.turn = None
                self.phase = "dealer"
                self.spawn(self.dealer_flow())
                return
            naturals = []
            for i, s in self.playing():
                h = s.hands[0]
                if is_blackjack(h.cards):
                    h.done, h.result = True, "blackjack"
                    naturals.append(i)
                    self.note(f"{s.name} has blackjack!")
            if naturals:
                await self.push({"kind": "blackjack", "seats": naturals})
            await self.advance()

    async def settle_side_bets(self) -> bool:
        """Perfect Pairs and 21+3 are decided by the first two cards and the dealer's face-up card. Wins are paid
        with the round (one escrow per player); the page shows them now. Returns whether anyone had a side bet."""
        results = []
        for i, s in self.playing():
            cards = s.hands[0].cards
            for k in SIDE_BETS:
                stake = s.side.get(k, 0)
                if not stake:
                    continue
                if k == "pp":
                    hit, pays = perfect_pairs(cards[0], cards[1]), PP_PAYS
                else:
                    hit, pays = twenty_one_plus_three(cards[:2] + self.dealer[:1]), T3_PAYS
                mult = pays[hit] if hit else 0
                payout = stake * (mult + 1) if hit else 0
                r = {"seat": i, "bet": k, "stake": stake, "win": hit, "name": SIDE_NAMES.get(hit, ""), "mult": mult,
                     "payout": payout}
                s.side_results.append(r)
                s.side_won += payout
                results.append(r)
                if hit:
                    self.note(f"{s.name}: {SIDE_NAMES[hit]} on {BET_NAMES[k]}, {mult}:1 (+{payout - stake:,})")
            s.side_done = True
        if results:
            await self.push({"kind": "side", "results": results})
        return bool(results)

    async def advance(self) -> None:
        """The next hand to play (from the dealer's left), or the dealer's turn once every hand is done."""
        for i, s in self.playing():
            for j, h in enumerate(s.hands):
                if h.done:
                    continue
                if s.leaving:
                    h.done = True
                    continue
                self.turn = (i, j)
                self.restart_turn_timer()
                await self.push({"kind": "turn", "seat": i, "hand": j})
                return
        self.turn = None
        self.token += 1
        self.phase = "dealer"
        self.spawn(self.dealer_flow())

    def restart_turn_timer(self) -> None:
        self.token += 1
        token = self.token
        self.deadline = time.time() + TURN_TIME
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = asyncio.create_task(self._turn_timeout(token))

    async def _turn_timeout(self, token: int) -> None:
        await asyncio.sleep(TURN_TIME)
        async with self.lock:
            if token != self.token or self.phase != "playing" or not self.turn:
                return
            i, j = self.turn
            s = self.seats[i]
            if s is None:
                return
            s.timeouts += 1
            s.hands[j].done = True
            self.note(f"{s.name} ran out of time and stands")
            if s.timeouts >= MAX_TIMEOUTS:
                s.leaving = True
                for h in s.hands:
                    h.done = True
                self.note(f"{s.name} will be taken off the table (inactive)")
            try:
                await self.push({"kind": "stand", "seat": i, "hand": j, "timeout": True})
                await self.advance()
            except Exception:
                log.exception("Blackjack (Activity): advance after a timeout failed")

    async def act(self, user_id: int, action: str) -> None:
        if self.phase != "playing" or not self.turn:
            raise Rejected("It's not your turn.")
        i, j = self.turn
        s = self.seats[i]
        if s is None or s.user_id != user_id:
            raise Rejected("It's not your turn.")
        h = s.hands[j]
        label = f"{s.name}" + (f" (hand {j + 1})" if len(s.hands) > 1 else "")
        if action == "hit":
            h.cards.append(self.draw())
            ev = {"kind": "card", "seat": i, "hand": j}
            self.note(f"{label} hits: {h.cards[-1]} ({h.value})")
        elif action == "stand":
            h.done = True
            ev = {"kind": "stand", "seat": i, "hand": j}
            self.note(f"{label} stands on {h.value}")
        elif action == "double":
            if len(h.cards) != 2 or h.done:
                raise Rejected("You can only double on your first two cards.")
            if not await s.wager.add_stake(h.stake):
                raise Rejected(f"You need another {h.stake:,} to double.")
            ev = {"kind": "double", "seat": i, "hand": j, "amount": h.stake}
            h.stake *= 2
            h.doubled = True
            h.cards.append(self.draw())
            h.done = True
            self.note(f"{label} doubles: {h.cards[-1]} ({h.value})")
            await self.room.push_me(user_id)
        elif action == "split":
            if (len(h.cards) != 2 or h.done or len(s.hands) >= MAX_HANDS
                    or card_value(h.cards[0]) != card_value(h.cards[1])):
                raise Rejected("You can only split two cards of the same value.")
            if not await s.wager.add_stake(h.stake):
                raise Rejected(f"You need another {h.stake:,} to split.")
            second = h.cards.pop()
            new = Hand([second, self.draw()], h.stake, from_split=True)
            h.cards.append(self.draw())
            h.from_split = True
            s.hands.insert(j + 1, new)
            if second.rank == 14:   # split aces get one card each
                h.done = new.done = True
            for hand in (h, new):
                if hand.value == 21:
                    hand.done = True
            ev = {"kind": "split", "seat": i, "hand": j, "amount": h.stake}
            self.note(f"{s.name} splits {second.rank_name}s")
            await self.room.push_me(user_id)
        else:
            raise Rejected("Unknown action.")
        s.timeouts = 0
        if h.value > 21:
            h.done, h.result = True, "bust"
            ev["bust"] = True
            self.note(f"{label} busts")
        elif h.value == 21:
            h.done = True
        if h.done:
            self.token += 1
            await self.push(ev)
            await self.advance()
        else:
            self.restart_turn_timer()
            await self.push(ev)

    async def dealer_flow(self) -> None:
        async with self.lock:
            self.phase = "dealer"
            self.turn = None
            self.hole_hidden = False
            dealer_bj = is_blackjack(self.dealer)
            self.note(f"Dealer turns over {self.dealer[1]}: " + ("blackjack!" if dealer_bj else
                                                                 str(hand_value(self.dealer)[0])))
            await self.push({"kind": "reveal", "blackjack": dealer_bj})
            hands = [h for _, s in self.playing() for h in s.hands]
            draws = not dealer_bj and any(not h.result for h in hands)
        await asyncio.sleep(REVEAL_PAUSE + (0.8 if dealer_bj else 0))
        while draws:
            async with self.lock:
                if hand_value(self.dealer)[0] >= 17:
                    break
                self.dealer.append(self.draw())
                total = hand_value(self.dealer)[0]
                self.note(f"Dealer draws {self.dealer[-1]} ({total})" + (", busts!" if total > 21 else ""))
                await self.push({"kind": "dealer_card", "bust": total > 21})
            await asyncio.sleep(DEALER_CARD_PAUSE)
        async with self.lock:
            await self.settle()
        await asyncio.sleep(SETTLE_PAUSE)
        async with self.lock:
            await self.clear_round()

    async def settle(self) -> None:
        dealer_total = hand_value(self.dealer)[0]
        dealer_bj = is_blackjack(self.dealer)
        payouts = []
        lines = []
        for i, s in self.playing():
            total = 0
            for j, h in enumerate(s.hands):
                if dealer_bj:
                    if is_blackjack(h.cards) and not h.from_split:
                        h.result, h.payout = "push", h.stake
                    else:
                        h.result, h.payout = "lose", 0
                elif h.result == "blackjack":
                    h.payout = math.floor(h.stake * 2.5)
                elif h.value > 21:
                    h.result, h.payout = "bust", 0
                elif dealer_total > 21 or h.value > dealer_total:
                    h.result, h.payout = "win", h.stake * 2
                elif h.value == dealer_total:
                    h.result, h.payout = "push", h.stake
                else:
                    h.result, h.payout = "lose", 0
                h.done = True
                total += h.payout
                payouts.append({"seat": i, "hand": j, "result": h.result, "payout": h.payout, "stake": h.stake})
            staked = sum(h.stake for h in s.hands) + s.side_total
            total += s.side_won
            if s.wager and not s.wager.settled:
                await s.wager.settle(total)
            s.wager = None
            s.won = total
            net = total - staked
            lines.append(f"{s.name} {'+' if net > 0 else ''}{net:,}" if net else f"{s.name} gets their bet back")
            await self.room.push_me(s.user_id)
        self.result = "Dealer has blackjack" if dealer_bj else (
            "Dealer busts" if dealer_total > 21 else f"Dealer has {dealer_total}")
        self.note(f"{self.result} · " + " · ".join(lines))
        self.phase = "settle"
        self.turn = None
        await self.push({"kind": "settle", "payouts": payouts})

    async def clear_round(self) -> None:
        for _, s in self.seated():
            s.hands, s.won, s.bet, s.ready = [], 0, 0, False
            self.reset_side(s)
            if s.leaving:
                await self.remove(s)
        self.dealer = []
        self.hole_hidden = True
        self.turn = None
        self.result = ""
        self.phase = "betting"
        self.deadline = 0
        if not self.seated():
            await self.close()
            return
        await self.push({"kind": "clear"})

    async def drop_if_away(self, user_id: int) -> None:
        """A player closed the Activity while holding a seat with nothing on it: free the seat after a while."""
        gone = [s for s in self.seats_of(user_id) if not s.hands and not s.bet and s.offline_since is not None
                and time.time() - s.offline_since >= OFFLINE_GRACE]
        if not gone:
            return
        for s in gone:
            await self.remove(s, f"{s.name} left (away)")
        if not self.seated():
            await self.close()
        else:
            self.check_bets_left()
            if not await self.maybe_start():
                await self.push()

    async def close(self) -> None:
        self.phase = "closed"
        self.token += 1
        for task in (self.timer, self.flow):
            if task and task is not asyncio.current_task():
                task.cancel()
        for _, s in self.seated():
            await self.remove(s)
        self.room.tables.pop(self.id, None)
        for v in self.room.viewers.values():
            if v.watching == self.id:
                v.watching = None
        await self.room.push_room()
        for uid in list(self.room.viewers):
            await self.room.push_view(uid)


@dataclass
class Viewer:
    user_id: int
    name: str
    avatar: bool
    sockets: set = field(default_factory=set)
    watching: int | None = None
    chats: list = field(default_factory=list)   # when this player last used quick chat


class Room:
    """The blackjack room of one server: its tables and everyone who has the blackjack page open."""

    def __init__(self, hub: "BlackjackHub", guild_id: int):
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
            await ws.send_str(json.dumps(data, separators=(",", ":")))
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
            min_bet, max_players = msg.get("min_bet"), msg.get("max_players", 5)
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in (min_bet, max_players)):
                raise Rejected("Invalid table settings.")
            s = self.hub.server.settings
            if min_bet < s.min_bet:
                raise Rejected(f"The minimum bet must be at least {s.min_bet:,}.")
            if s.max_bet and min_bet > s.max_bet:
                raise Rejected(f"The minimum bet can't be more than the bot's maximum bet ({s.max_bet:,}).")
            if not 1 <= max_players <= MAX_SEATS:
                raise Rejected(f"A table has 1 to {MAX_SEATS} seats.")
            if self.table_of(v.user_id):
                raise Rejected("You're already sitting at a table. Leave it first.")
            if len(self.tables) >= MAX_TABLES:
                raise Rejected("This server has the most blackjack tables it can have open. Join one of them.")
            t = Table(self, self.next_id, v.user_id, v.name, min_bet, max_players)
            self.next_id += 1
            async with t.lock:
                await t.add_player(v.user_id, v.name, v.avatar, max_players // 2)
                self.tables[t.id] = t
                v.watching = t.id
                await t.push()
            log.info("Blackjack (Activity): user %s opened table %s (bets %s-%s, %s seats) in guild %s", v.user_id,
                     t.id, f"{t.min_bet:,}", f"{t.max_bet:,}", max_players, self.guild_id)
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
        elif kind == "chat":
            await self.chat(v, msg.get("id"))
        elif kind in ("sit", "leave", "bet", "clear", "rebet", "double_bet", "ready", "act"):
            t = self.tables.get(v.watching) if v.watching is not None else None
            if t is None:
                raise Rejected("Open a table first.")
            async with t.lock:
                if kind == "sit":
                    await t.add_player(v.user_id, v.name, v.avatar, msg.get("seat"))
                    await t.push({"kind": "sit"})
                    return
                seats = t.seats_of(v.user_id)
                if not seats:
                    raise Rejected("You're not sitting at this table.")
                pick = msg.get("seat")
                if pick is not None:
                    if (not isinstance(pick, int) or isinstance(pick, bool) or not 0 <= pick < len(t.seats)
                            or t.seats[pick] not in seats):
                        raise Rejected("That's not your seat.")
                    seats = [t.seats[pick]]
                seat = seats[0]
                if kind == "leave":
                    text = await t.leave_seats(seats)
                    await self.to_user(v.user_id, {"type": "notice", "message": text})
                elif kind == "bet":
                    amount, spot = msg.get("amount"), msg.get("spot", "main")
                    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                        raise Rejected("Invalid amount.")
                    if spot not in ("main", *SIDE_BETS):
                        raise Rejected("Invalid bet.")
                    await t.place_bets(seat, {spot: amount})
                elif kind == "clear":
                    for s in seats:
                        await t.clear_bet(s)
                elif kind == "rebet":
                    todo = [s for s in seats if s.last_bet and not s.bet and not s.side_total]
                    if not todo:
                        raise Rejected("You already have a bet out. Clear it first, or use ×2." if any(
                            s.bet or s.side_total for s in seats) else "There's no bet to repeat yet.")
                    await self.each(t, todo, lambda s: t.place_bets(s, {
                        "main": min(s.last_bet, t.max_bet),
                        **{k: min(x, s.last_bet, t.max_bet) for k, x in s.last_side.items()}}))
                elif kind == "double_bet":
                    todo = [s for s in seats if s.bet]
                    if not todo:
                        raise Rejected("Place a bet first.")
                    await self.each(t, todo, lambda s: t.place_bets(s, {"main": s.bet, **s.side}))
                elif kind == "ready":
                    await t.set_ready(seats)
                else:
                    action = msg.get("action")
                    if action not in ("hit", "stand", "double", "split"):
                        raise Rejected("Invalid action.")
                    await t.act(v.user_id, action)

    @staticmethod
    async def each(t: Table, seats: list[Seat], do) -> None:
        """Does the same on several of a player's seats; an error only when it worked on none of them."""
        problem, done = None, False
        for s in seats:
            if s not in t.seats:
                continue
            try:
                await do(s)
                done = True
            except Rejected as e:
                problem = problem or e
        if problem and not done:
            raise problem

    async def chat(self, v: Viewer, chat_id) -> None:
        """A quick chat to everyone looking at the table: over the player's seat, or (for someone watching) under
        the dealer."""
        t = self.tables.get(v.watching) if v.watching is not None else None
        if t is None:
            raise Rejected("Open a table first.")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool) or not 0 <= chat_id < len(QUICK_CHATS):
            raise Rejected("Invalid chat.")
        now = time.monotonic()
        v.chats = [x for x in v.chats if now - x < CHAT_WINDOW]
        if len(v.chats) >= CHAT_BURST or (v.chats and now - v.chats[-1] < CHAT_GAP):
            raise Rejected("Easy there! One at a time.")
        v.chats.append(now)
        seat = t.seat_of(v.user_id)
        data = {"type": "chat", "table": t.id, "from": str(v.user_id), "name": v.name,
                "seat": t.index_of(seat) if seat else -1, "id": chat_id}
        watchers = [uid for uid, w in self.viewers.items() if w.watching == t.id]
        await asyncio.gather(*(self.to_user(uid, data) for uid in watchers))

    def set_online(self, user_id: int, online: bool) -> None:
        t = self.table_of(user_id)
        seats = t.seats_of(user_id) if t else []
        if not seats:
            return
        for s in seats:
            s.offline_since = None if online else time.time()
        if not online:
            asyncio.create_task(self._drop_later(t, user_id))

    async def _drop_later(self, t: Table, user_id: int) -> None:
        await asyncio.sleep(OFFLINE_GRACE + 1)
        if t.id not in self.tables or user_id in self.viewers:
            return
        async with t.lock:
            if t.phase != "closed":
                await t.drop_if_away(user_id)


class BlackjackHub:
    def __init__(self, server):
        self.server = server          # activity.server.ActivityServer
        self.rooms: dict[int, Room] = {}

    async def stop(self) -> None:
        """Bot shutdown: stops the tables. Bets that are out stay in escrow and are refunded when the bot starts
        again, like every other game."""
        for room in self.rooms.values():
            for t in room.tables.values():
                for task in (t.timer, t.flow):
                    if task:
                        task.cancel()

    def welcome(self, user_id: int) -> dict:
        s = self.server.settings
        return {"type": "welcome", "you": str(user_id), "min_bet": s.min_bet, "max_bet": s.max_bet,
                "bet_time": BET_TIME, "turn_time": TURN_TIME, "max_tables": MAX_TABLES, "max_seats": MAX_SEATS,
                "max_each": MAX_SEATS_EACH,
                "max_mult": MAX_BET_MULT, "decks": DECKS, "max_hands": MAX_HANDS,
                "side_pays": {"pp": PP_PAYS, "t3": T3_PAYS}, "side_names": SIDE_NAMES,
                "server_voice": self.server.voice.available, "emotes": EMOTES, "phrases": PHRASES}

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
            session, guild_id, member, problem = await self.server.admit(hello)
            if problem:
                await ws.send_str(json.dumps({"type": "fatal", "message": problem,
                                              "code": "session" if "session" in problem.lower() else ""}))
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
            room.set_online(session.user_id, True)
            seated = room.table_of(session.user_id)
            if seated:
                viewer.watching = seated.id      # back at your table after closing the Activity
            await room.send(ws, self.welcome(session.user_id))
            await room.push_me(session.user_id)
            await room.push_room()
            await room.push_view(session.user_id)
            if seated:
                await room.push_table(seated)    # the others see you're back
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
                    log.exception("Blackjack (Activity): error handling %r from %s", msg.data[:200],
                                  viewer.user_id)
                    await room.send(ws, {"type": "error", "message": "Something went wrong with that."})
        except (asyncio.TimeoutError, ValueError, TypeError):
            pass
        finally:
            if viewer is not None and room is not None:
                viewer.sockets.discard(ws)
                if not viewer.sockets:
                    room.viewers.pop(viewer.user_id, None)   # a seated player keeps their seat for now
                    room.set_online(viewer.user_id, False)
                await room.push_room()
                for t in list(room.tables.values()):
                    if t.seat_of(viewer.user_id):
                        await room.push_table(t)
        return ws
