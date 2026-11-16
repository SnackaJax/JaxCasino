"""Multiplayer Texas Hold'em (no-limit). Players buy in with credits, play hands, and cash out when they leave."""
import asyncio
import io
import logging
import math
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.cards import Card, new_deck, show
from utils.checks import gambling_only, is_admin
from utils.economy import BetError, fmt, parse_amount, send_error, tag
from utils.restart import offer_later
from utils.views import BaseModal, BaseView
from utils.hand_eval import best_hand, hand_name
from utils.rng import rng
from utils.table_image import SeatInfo, TableInfo, render_hand, render_table

log = logging.getLogger("gamblebot.poker")

STREET_NAMES = {"preflop": "PRE-FLOP", "flop": "FLOP", "turn": "TURN", "river": "RIVER"}

TURN_TIME = 45          # seconds a player has to act
NEXT_HAND_DELAY = 8     # seconds between hands
MAX_TIMEOUTS = 2        # consecutive timeouts before a player is removed


class Seat:
    def __init__(self, user: discord.Member, stack: int, escrow_id: int):
        self.user = user
        self.stack = stack
        self.escrow_id = escrow_id
        self.bought_in = stack
        self.hole: list[Card] = []
        self.bet = 0           # chips put in this betting round
        self.committed = 0     # chips put in this hand
        self.in_hand = False
        self.folded = False
        self.all_in = False
        self.acted = False
        self.leaving = False
        self.timeouts = 0

    @property
    def name(self) -> str:
        return self.user.display_name

    def reset_for_hand(self) -> None:
        self.hole = []
        self.bet = self.committed = 0
        self.in_hand = True
        self.folded = self.all_in = self.acted = False

    def put(self, amount: int) -> int:
        amount = min(amount, self.stack)
        self.stack -= amount
        self.bet += amount
        self.committed += amount
        if self.stack == 0:
            self.all_in = True
        return amount


class PokerTable:
    def __init__(self, cog: "Poker", channel, host: discord.Member, buy_in: int, sb: int, bb: int, max_players: int):
        self.cog = cog
        self.db = cog.bot.db
        self.channel = channel
        self.guild_id = channel.guild.id
        self.host_id = host.id
        self.buy_in = buy_in
        self.sb = sb
        self.bb = bb
        self.max_players = max_players

        self.seats: list[Seat] = []
        self.phase = "waiting"    # waiting | playing | between | closed
        self.street = ""
        self.deck: list[Card] = []
        self.board: list[Card] = []
        self.dealer = -1
        self.turn = -1
        self.current_bet = 0
        self.min_raise = bb
        self.hand_no = 0
        self.actions: list[str] = []
        self.last_result = ""
        self.result_short = ""            # one-line result shown on the table image
        self.reveal: dict[int, str] = {}  # user id -> hand name, for cards shown at showdown
        self.won: dict[int, int] = {}     # user id -> chips won last hand
        self.closing = False

        self.lock = asyncio.Lock()
        self.turn_token = 0
        self.deadline = 0.0
        self.timer: asyncio.Task | None = None
        self.next_task: asyncio.Task | None = None
        self.ping: discord.Message | None = None
        self.message: discord.Message | None = None
        self.view = PokerView(self)

    # ---------------------------------------------------------------- helpers
    def seat_of(self, user_id: int) -> Seat | None:
        return next((s for s in self.seats if s.user.id == user_id), None)

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
        self.actions.append(text)
        self.actions = self.actions[-6:]

    # ----------------------------------------------------------- seating
    async def add_player(self, member: discord.Member) -> str | None:
        if self.phase == "closed":
            return "This table is closed."
        if self.seat_of(member.id):
            return "You're already at this table."
        if len(self.seats) >= self.max_players:
            return "This table is full."
        if not await self.db.try_debit(self.guild_id, member.id, self.buy_in):
            bal = await self.db.get_balance(self.guild_id, member.id)
            return f"The buy-in is {fmt(self.buy_in)} but you only have {fmt(bal)}."
        escrow_id = await self.db.open_escrow(self.guild_id, member.id, self.buy_in, "poker")
        self.seats.append(Seat(member, self.buy_in, escrow_id))
        self.note(f"➕ {member.display_name} sat down")
        return None

    async def cash_out(self, seat: Seat) -> None:
        """Returns the seat's stack to their balance and removes them from the table."""
        balance = await self.db.settle_escrow(seat.escrow_id, self.guild_id, seat.user.id, seat.stack)
        if balance < config.MIN_BET:
            offer_later(self.channel, self.db, self.guild_id, seat.user.id)
        await self.db.record_game(self.guild_id, seat.user.id, seat.bought_in, seat.stack, "poker")
        self.seats.remove(seat)
        if seat.user.id == self.host_id and self.seats:
            self.host_id = self.seats[0].user.id

    async def request_leave(self, seat: Seat) -> str:
        if self.phase == "playing" and seat.in_hand:
            seat.leaving = True
            if not seat.folded:
                if self.seats[self.turn] is seat:
                    self.apply(seat, "fold")
                    await self.advance()
                else:
                    seat.folded = True
                    self.note(f"🚪 {seat.name} folds and leaves")
                    if len(self.live()) == 1:
                        await self.win_uncontested(self.live()[0])
                    else:
                        await self.render()
            return "You folded and will be cashed out when this hand ends."
        if self.phase == "playing":
            seat.leaving = True
            return "You'll be cashed out when this hand ends."
        stack = seat.stack
        await self.cash_out(seat)
        self.note(f"🚪 {seat.name} left with {stack:,}")
        if not self.seats:
            await self.close("Everyone left the table.")
        else:
            await self.render()
        return f"You left the table and got {fmt(stack)} back."

    # ----------------------------------------------------------- hand flow
    async def start_hand(self) -> None:
        self.next_task = None
        old_ids = [s.user.id for s in self.seats]
        old_dealer = old_ids[self.dealer] if 0 <= self.dealer < len(old_ids) else None

        for s in list(self.seats):
            if s.leaving or s.stack <= 0 or self.closing:
                if s.stack <= 0 and not s.leaving:
                    self.note(f"💸 {s.name} is out of chips")
                await self.cash_out(s)
        if self.closing or not self.seats:
            await self.close("The table was closed.")
            return
        if len(self.seats) < 2:
            self.phase = "waiting"
            self.board = []
            for s in self.seats:
                s.in_hand = False
                s.bet = s.committed = 0
            await self.render()
            return

        # Move the dealer button to the next player still seated.
        if old_dealer is None:
            self.dealer = rng.randrange(len(self.seats))
        else:
            start = old_ids.index(old_dealer)
            ids = [s.user.id for s in self.seats]
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
        self.last_result = ""
        self.result_short = ""
        self.reveal = {}
        self.won = {}
        self.actions = []
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
        self.note(f"{self.seats[sb_i].name} posts small blind {sb_paid:,}")
        self.note(f"{self.seats[bb_i].name} posts big blind {bb_paid:,}")
        self.current_bet = self.bb
        self.min_raise = self.bb
        self.turn = bb_i
        await self.advance()

    def apply(self, seat: Seat, action: str, raise_to: int = 0) -> str | None:
        """Applies an action for the seat whose turn it is. Returns an error message if it's not allowed."""
        to_call = self.current_bet - seat.bet
        if action == "fold":
            seat.folded = True
            self.note(f"❌ {seat.name} folds")
        elif action == "check_call":
            if to_call <= 0:
                self.note(f"✔️ {seat.name} checks")
            else:
                paid = seat.put(to_call)
                self.note(f"📞 {seat.name} calls {paid:,}" + (" (all-in)" if seat.all_in else ""))
        elif action == "allin":
            target = seat.bet + seat.stack
            if target > self.current_bet:
                return self._raise(seat, target)
            paid = seat.put(seat.stack)
            self.note(f"📞 {seat.name} calls {paid:,} (all-in)")
        elif action == "raise":
            return self._raise(seat, raise_to)
        seat.acted = True
        return None

    def _raise(self, seat: Seat, target: int) -> str | None:
        max_to = seat.bet + seat.stack
        min_to = self.current_bet + self.min_raise
        if target <= self.current_bet:
            return f"You must raise to more than {self.current_bet:,}."
        if target > max_to:
            return f"You only have {max_to:,} available."
        if target < min_to and target != max_to:
            return f"The minimum raise is to {min_to:,}."
        verb = "bets" if self.current_bet == 0 else "raises to"
        seat.put(target - seat.bet)
        if target - self.current_bet >= self.min_raise:
            self.min_raise = target - self.current_bet
        self.current_bet = target
        for s in self.seats:
            if s is not seat and self.can_act(s):
                s.acted = False
        seat.acted = True
        self.note(f"⬆️ {seat.name} {verb} {target:,}" + (" (all-in)" if seat.all_in else ""))
        return None

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
        for s in self.seats:
            s.bet = 0
            s.acted = False
        self.current_bet = 0
        self.min_raise = self.bb
        if self.street == "river":
            await self.showdown()
            return
        self.deck.pop()  # burn
        if self.street == "preflop":
            self.board += [self.deck.pop() for _ in range(3)]
            self.street = "flop"
        elif self.street == "flop":
            self.board.append(self.deck.pop())
            self.street = "turn"
        else:
            self.board.append(self.deck.pop())
            self.street = "river"

        actors = [s for s in self.live() if not s.all_in]
        if len(actors) <= 1:
            # Everyone (or all but one) is all-in: run out the board.
            self.turn = -1
            self.turn_token += 1
            await self.clear_ping()
            await self.render()
            await asyncio.sleep(2)
            await self.next_street()
            return
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
                    pots[-1][0] += amount  # same players as the previous pot (a folded player's level): merge
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
        scores = {id(s): best_hand(s.hole + self.board)[0] for s in live}
        lines = [f"{s.name}: {show(s.hole)} — {hand_name(scores[id(s)])}" for s in self.order_from_dealer(live)]
        pots = self.build_pots()
        results = []
        self.reveal = {s.user.id: hand_name(scores[id(s)]) for s in live}
        self.won = {}
        for i, (amount, eligible) in enumerate(pots):
            rake = math.floor(amount * config.POKER_RAKE) if len(eligible) > 1 else 0
            amount -= rake
            best = max(scores[id(s)] for s in eligible)
            winners = self.order_from_dealer([s for s in eligible if scores[id(s)] == best])
            share, rem = divmod(amount, len(winners))
            for w in winners:
                w.stack += share
            winners[0].stack += rem
            pot_name = "Pot" if len(pots) == 1 else ("Main pot" if i == 0 else f"Side pot {i}")
            if len(eligible) == 1:
                results.append(f"↩️ {winners[0].name} gets back {amount:,} (uncalled)")
            else:
                names = ", ".join(w.name for w in winners)
                verb = "split" if len(winners) > 1 else "wins"
                results.append(f"🏆 **{names}** {verb} {pot_name.lower()} of {amount:,} with **{hand_name(best)}**"
                               + (f" (rake {rake:,})" if rake else ""))
                for w in winners:
                    self.won[w.user.id] = self.won.get(w.user.id, 0) + share + (rem if w is winners[0] else 0)
        self.last_result = "\n".join(results) + "\n\n" + "\n".join(lines)
        names = {s.user.id: s.name for s in self.seats}
        if len(self.won) == 1:
            uid, amt = next(iter(self.won.items()))
            self.result_short = f"{names[uid]} wins {amt:,} with {self.reveal[uid]}"
        else:
            self.result_short = ", ".join(f"{names[uid]} wins {amt:,}" for uid, amt in self.won.items())
        await self.end_hand()

    async def win_uncontested(self, winner: Seat) -> None:
        amount = self.pot
        winner.stack += amount
        self.won = {winner.user.id: amount}
        self.result_short = f"{winner.name} wins {amount:,} — everyone else folded"
        self.last_result = f"🏆 **{winner.name}** wins {amount:,} — everyone else folded."
        await self.end_hand()

    async def end_hand(self) -> None:
        self.phase = "between"
        self.turn = -1
        self.turn_token += 1
        await self.clear_ping()
        for s in self.seats:
            await self.db.update_escrow(s.escrow_id, s.stack)
        await self.render()
        self.next_task = asyncio.create_task(self._next_hand_later())

    async def _next_hand_later(self) -> None:
        await asyncio.sleep(NEXT_HAND_DELAY)
        async with self.lock:
            if self.phase == "between":
                try:
                    await self.start_hand()
                except Exception:
                    log.exception("Failed to start poker hand")

    async def close(self, reason: str) -> None:
        self.phase = "closed"
        self.turn_token += 1
        await self.clear_ping()
        for s in list(self.seats):
            await self.cash_out(s)
        self.cog.tables.pop(self.channel.id, None)
        self.view.stop()
        embed = discord.Embed(title="♠️ Poker table closed", description=f"{reason}\nAll players were cashed out.",
                              color=discord.Color.light_grey())
        if self.last_result:
            embed.add_field(name="Last hand", value=self.last_result[:1024], inline=False)
        if self.message:
            try:
                await self.message.edit(embed=embed, attachments=[], view=None)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------- turns
    async def start_turn(self) -> None:
        self.turn_token += 1
        token = self.turn_token
        self.deadline = time.time() + TURN_TIME
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = asyncio.create_task(self._turn_timeout(token))
        await self.render()
        await self.clear_ping()
        seat = self.seats[self.turn]
        to_call = self.current_bet - seat.bet
        what = f"{min(to_call, seat.stack):,} to call" if to_call > 0 else "you can check"
        try:
            self.ping = await self.channel.send(
                f"🃏 {seat.user.mention}, it's your turn ({what}). <t:{int(self.deadline)}:R>",
                allowed_mentions=discord.AllowedMentions(users=[seat.user]))
        except discord.HTTPException:
            self.ping = None

    async def clear_ping(self) -> None:
        if self.ping:
            try:
                await self.ping.delete()
            except discord.HTTPException:
                pass
            self.ping = None

    async def _turn_timeout(self, token: int) -> None:
        await asyncio.sleep(TURN_TIME)
        async with self.lock:
            if token != self.turn_token or self.phase != "playing" or self.turn < 0:
                return
            seat = self.seats[self.turn]
            seat.timeouts += 1
            self.apply(seat, "check_call" if seat.bet >= self.current_bet else "fold")
            self.note(f"⏰ {seat.name} timed out")
            if seat.timeouts >= MAX_TIMEOUTS:
                seat.leaving = True
                if not seat.folded:
                    seat.folded = True
                self.note(f"🚪 {seat.name} was removed for inactivity")
            try:
                await self.advance()
            except Exception:
                log.exception("Poker advance after timeout failed")

    # ------------------------------------------------------------ display
    def embed(self) -> discord.Embed:
        """Short text under the table image: whose turn it is, recent actions, and the last result."""
        embed = discord.Embed(title="♠️ Texas Hold'em", color=discord.Color.dark_green())
        if self.phase == "playing" and self.turn >= 0:
            s = self.seats[self.turn]
            to_call = self.current_bet - s.bet
            todo = f"{min(to_call, s.stack):,} to call" if to_call > 0 else "can check or bet"
            embed.description = f"▶️ **{s.name}** to act — {todo} · <t:{int(self.deadline)}:R>"
        elif self.phase == "playing":
            embed.description = "Dealing the rest of the board…"
        elif self.phase == "waiting":
            if self.hand_no == 0:
                embed.description = (f"Waiting for players — <@{self.host_id}> presses **Start** "
                                     f"once 2+ players have joined.")
            else:
                embed.description = "Waiting for more players — the next hand starts when someone joins."
        elif self.phase == "between":
            embed.description = "Next hand starting soon…"
        if self.actions and self.phase == "playing":
            embed.add_field(name="Last actions", value="\n".join(self.actions[-4:]), inline=False)
        if self.last_result and self.phase in ("between", "waiting"):
            embed.add_field(name=f"Hand #{self.hand_no} result", value=self.last_result[:1024], inline=False)
        footer = (f"Buy-in {self.buy_in:,} · {len(self.seats)}/{self.max_players} seats · "
                  f"{TURN_TIME}s per turn · 🃏 My cards shows your hand")
        if config.POKER_RAKE:
            footer += f" · {config.POKER_RAKE * 100:g}% rake"
        embed.set_footer(text=footer)
        return embed

    def snapshot(self) -> TableInfo:
        """Plain-data copy of the table for the image renderer (which runs in a worker thread)."""
        playing = self.phase in ("playing", "between")
        seats = []
        for i, s in enumerate(self.seats):
            in_hand = playing and s.in_hand
            info = SeatInfo(
                name=s.name,
                stack=s.stack,
                bet=s.bet if self.phase == "playing" else 0,
                turn=self.phase == "playing" and i == self.turn,
                dealer=playing and i == self.dealer,
                folded=in_hand and s.folded,
                all_in=self.phase == "playing" and in_hand and s.all_in and not s.folded,
                won=self.won.get(s.user.id, 0) if self.phase == "between" else 0,
            )
            if in_hand and not s.folded and s.hole:
                if self.phase == "between" and s.user.id in self.reveal:
                    info.cards = [(c.rank_name, c.suit) for c in s.hole]
                    info.status = self.reveal[s.user.id]
                else:
                    info.hidden = len(s.hole)
            if self.phase == "playing" and not s.in_hand:
                info.status = "Next hand"
            if s.leaving:
                info.status = "Leaving"
            seats.append(info)

        if self.phase == "playing":
            street = STREET_NAMES.get(self.street, "")
        elif self.phase == "between":
            street = "SHOWDOWN" if self.reveal else "HAND OVER"
        else:
            street = ""
        if self.hand_no == 0:
            sub = f"{len(self.seats)}/{self.max_players} seated · the host presses Start"
        else:
            sub = "The next hand starts when another player joins"
        return TableInfo(
            seats=seats,
            board=[(c.rank_name, c.suit) for c in self.board] if playing else [],
            pot=self.pot if playing else 0,
            street=street,
            hand_no=self.hand_no,
            sb=self.sb,
            bb=self.bb,
            center_title="Waiting for players",
            center_sub=sub,
            result=self.result_short if self.phase == "between" else "",
        )

    async def payload(self) -> tuple[discord.Embed, discord.File | None]:
        """The table embed plus a freshly drawn table image (None if drawing fails)."""
        embed = self.embed()
        try:
            png = await asyncio.to_thread(render_table, self.snapshot())
        except Exception:
            log.exception("Failed to draw the poker table image")
            return embed, None
        embed.set_image(url="attachment://table.png")
        return embed, discord.File(io.BytesIO(png), filename="table.png")

    async def render(self) -> None:
        if not self.message or self.phase == "closed":
            return
        self.view.refresh()
        embed, file = await self.payload()
        try:
            await self.message.edit(embed=embed, attachments=[file] if file else [], view=self.view)
        except discord.HTTPException:
            log.warning("Failed to update poker table message", exc_info=True)


class RaiseModal(BaseModal):
    def __init__(self, table: PokerTable, seat: Seat):
        super().__init__(title="Raise" if table.current_bet else "Bet")
        self.table = table
        max_to = seat.bet + seat.stack
        min_to = min(table.current_bet + table.min_raise, max_to)
        self.amount = discord.ui.TextInput(
            label="Raise to (your total bet this round)",
            placeholder=f"Min {min_to:,} · Max {max_to:,} · or 'all'",
            max_length=20,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        t = self.table
        await interaction.response.defer()
        async with t.lock:
            seat = t.seat_of(interaction.user.id)
            if t.phase != "playing" or t.turn < 0 or t.seats[t.turn] is not seat:
                return await interaction.followup.send("It's no longer your turn.", ephemeral=True)
            try:
                target = parse_amount(self.amount.value, seat.bet + seat.stack)
            except BetError as e:
                return await interaction.followup.send(str(e), ephemeral=True)
            err = t.apply(seat, "raise", target)
            if err:
                return await interaction.followup.send(err, ephemeral=True)
            seat.timeouts = 0
            await t.advance()


class PokerView(BaseView):
    def __init__(self, table: PokerTable):
        super().__init__(timeout=None)
        self.table = table

    def refresh(self) -> None:
        t = self.table
        my_turn = t.phase == "playing" and t.turn >= 0
        for b in (self.fold_btn, self.call_btn, self.raise_btn, self.allin_btn):
            b.disabled = not my_turn
        if my_turn:
            s = t.seats[t.turn]
            to_call = t.current_bet - s.bet
            self.call_btn.label = "Check" if to_call <= 0 else f"Call {min(to_call, s.stack):,}"
            self.raise_btn.label = "Raise" if t.current_bet else "Bet"
            self.raise_btn.disabled = s.stack <= to_call
            self.allin_btn.label = f"All-in ({s.stack:,})"
        else:
            self.call_btn.label = "Check / Call"
            self.raise_btn.label = "Raise"
            self.allin_btn.label = "All-in"
        self.join_btn.label = f"Join ({t.buy_in:,})"
        self.join_btn.disabled = len(t.seats) >= t.max_players
        self.start_btn.disabled = t.phase != "waiting" or len(t.seats) < 2

    async def _action(self, interaction: discord.Interaction, action: str) -> None:
        t = self.table
        async with t.lock:
            seat = t.seat_of(interaction.user.id)
            if t.phase != "playing" or t.turn < 0 or seat is None or t.seats[t.turn] is not seat:
                return await interaction.response.send_message("It's not your turn.", ephemeral=True)
            if action == "raise":
                return await interaction.response.send_modal(RaiseModal(t, seat))
            await interaction.response.defer()
            err = t.apply(seat, action)
            if err:
                return await interaction.followup.send(err, ephemeral=True)
            seat.timeouts = 0
            await t.advance()

    @discord.ui.button(label="Fold", style=discord.ButtonStyle.danger, row=0)
    async def fold_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "fold")

    @discord.ui.button(label="Check / Call", style=discord.ButtonStyle.primary, row=0)
    async def call_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "check_call")

    @discord.ui.button(label="Raise", style=discord.ButtonStyle.success, row=0)
    async def raise_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "raise")

    @discord.ui.button(label="All-in", style=discord.ButtonStyle.danger, row=0)
    async def allin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "allin")

    @discord.ui.button(label="My cards", emoji="🃏", style=discord.ButtonStyle.secondary, row=1)
    async def cards_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.table
        seat = t.seat_of(interaction.user.id)
        if not seat or not seat.hole or not seat.in_hand:
            return await interaction.response.send_message("You don't have cards in this hand.", ephemeral=True)
        best = hand_name(best_hand(seat.hole + t.board)[0]) if len(t.board) >= 3 else ""
        text = f"Your cards: {show(seat.hole)}" + (f" · **{best}**" if best and not seat.folded else "")
        try:
            png = await asyncio.to_thread(
                render_hand, [(c.rank_name, c.suit) for c in seat.hole],
                [(c.rank_name, c.suit) for c in t.board], best, seat.folded)
            file = discord.File(io.BytesIO(png), filename="hand.png")
        except Exception:
            log.exception("Failed to draw a poker hand image")
            return await interaction.response.send_message(text, ephemeral=True)
        await interaction.response.send_message(text, file=file, ephemeral=True)

    @discord.ui.button(label="Join", emoji="🪑", style=discord.ButtonStyle.success, row=1)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.table
        async with t.lock:
            other = t.cog.table_of(interaction.user.id)
            if other and other is not t:
                return await interaction.response.send_message("You're already sitting at another table.",
                                                               ephemeral=True)
            err = await t.add_player(interaction.user)
            if err:
                return await interaction.response.send_message(err, ephemeral=True)
            msg = f"You joined with {fmt(t.buy_in)}."
            if t.phase == "playing":
                msg += " You'll be dealt in next hand."
            await interaction.response.send_message(msg, ephemeral=True)
            if t.phase == "waiting" and t.hand_no > 0 and len(t.seats) >= 2:
                await t.start_hand()
            else:
                await t.render()

    @discord.ui.button(label="Leave", emoji="🚪", style=discord.ButtonStyle.secondary, row=1)
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.table
        async with t.lock:
            seat = t.seat_of(interaction.user.id)
            if not seat:
                return await interaction.response.send_message("You're not at this table.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            msg = await t.request_leave(seat)
            await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.primary, row=1)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.table
        async with t.lock:
            if interaction.user.id != t.host_id:
                return await interaction.response.send_message("Only the table host can start the game.",
                                                                ephemeral=True)
            if t.phase != "waiting" or len(t.seats) < 2:
                return await interaction.response.send_message("You need at least 2 players to start.",
                                                               ephemeral=True)
            await interaction.response.defer()
            await t.start_hand()


class Poker(commands.Cog):
    poker = app_commands.Group(name="poker", description="Multiplayer Texas Hold'em.")

    def __init__(self, bot):
        self.bot = bot
        self.tables: dict[int, PokerTable] = {}

    def table_of(self, user_id: int) -> PokerTable | None:
        return next((t for t in self.tables.values() if t.seat_of(user_id)), None)

    @poker.command(name="create", description="Open a Texas Hold'em table in this channel.")
    @app_commands.describe(buy_in="Chips each player brings to the table",
                           big_blind="Big blind (default: buy-in / 50). Small blind is half.",
                           max_players="Seats at the table (2-8, default 8)")
    @gambling_only()
    async def create(self, interaction: discord.Interaction, buy_in: app_commands.Range[int, 20, 10**15],
                     big_blind: app_commands.Range[int, 2, 10**15] | None = None,
                     max_players: app_commands.Range[int, 2, 8] = 8):
        if interaction.channel_id in self.tables:
            return await send_error(interaction, "There's already a poker table in this channel. "
                                                 "Use a different channel or thread for another table.")
        if self.table_of(interaction.user.id):
            return await send_error(interaction, "You're already sitting at a poker table.")
        bb = big_blind or max(2, buy_in // 50)
        if bb > buy_in // 2:
            return await send_error(interaction, "The big blind must be at most half the buy-in.")
        table = PokerTable(self, interaction.channel, interaction.user, buy_in, max(1, bb // 2), bb, max_players)
        err = await table.add_player(interaction.user)
        if err:
            return await send_error(interaction, err)
        self.tables[interaction.channel_id] = table
        table.view.refresh()
        try:
            embed, file = await table.payload()
            if file:
                await interaction.response.send_message(**tag(interaction.user), embed=embed, file=file, view=table.view)
            else:
                await interaction.response.send_message(**tag(interaction.user), embed=embed, view=table.view)
            table.message = await interaction.original_response()
        except Exception:
            # The table never appeared: remove it and give the buy-in back instead of leaving a ghost table.
            self.tables.pop(interaction.channel_id, None)
            for seat in list(table.seats):
                await self.bot.db.settle_escrow(seat.escrow_id, table.guild_id, seat.user.id, seat.stack)
            table.view.stop()
            raise

    @poker.command(name="close", description="Close the poker table in this channel (host or admin).")
    async def close(self, interaction: discord.Interaction):
        table = self.tables.get(interaction.channel_id)
        if not table:
            return await send_error(interaction, "There's no poker table in this channel.")
        if interaction.user.id != table.host_id and not await is_admin(self.bot, interaction.user, interaction.guild):
            return await send_error(interaction, "Only the table host or an admin can close the table.")
        async with table.lock:
            if table.phase == "playing":
                table.closing = True
                await interaction.response.send_message("The table will close after the current hand.")
            else:
                if table.next_task:
                    table.next_task.cancel()
                await interaction.response.send_message("Closing the table…", ephemeral=True)
                await table.close(f"Closed by {interaction.user.mention}.")

    @poker.command(name="leave", description="Leave the poker table you're sitting at.")
    async def leave(self, interaction: discord.Interaction):
        table = self.table_of(interaction.user.id)
        if not table:
            return await send_error(interaction, "You're not sitting at a poker table.")
        async with table.lock:
            seat = table.seat_of(interaction.user.id)
            if not seat:
                return await send_error(interaction, "You're not sitting at a poker table.")
            await interaction.response.defer(ephemeral=True)
            msg = await table.request_leave(seat)
            await interaction.followup.send(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Poker(bot))
