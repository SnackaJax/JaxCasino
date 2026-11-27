"""Blackjack: 6-deck shoe, dealer stands on all 17s, blackjack pays 3:2, double and split (up to 4 hands).

Every player has their own shoe on each server, dealt down to a quarter before it's shuffled again, the same way a
table's shoe is dealt in the Activity (activity/blackjack.py). That keeps both ways of playing on the same odds: a
fresh shoe for every hand would leave the house a little more, because the dealer's face-up card rule (below) leaves
the 10-value cards it skips in the shoe for the cards after it."""
import asyncio
import math

import discord
from discord import app_commands
from discord.ext import commands

from utils.cards import HIDDEN, Card, new_deck, show
from utils.checks import gambling_only
from utils.economy import fmt, result_color, result_line, take_bet, Wager, tag
from utils.rng import rng
from utils.views import OwnedView
from utils.bignum import credits_text, mul_floor

MAX_HANDS = 4
DECKS = 6
RESHUFFLE_AT = 0.25   # a fresh shoe once fewer than this share of its cards are left (the Activity's tables too)
# Chance that the dealer's face-up card is a 10-value card (10, J, Q, K). A fair shoe gives 4/13 ≈ 30.8%;
# this is lowered on purpose so the dealer starts strong less often. The hole card and every later card
# are dealt normally from the shoe.
DEALER_TEN_CHANCE = 0.20


LOSE = "Lose ❌"
DEALER_BLACKJACK = "Dealer blackjack ❌"


def card_value(card: Card) -> int:
    if card.rank == 14:
        return 11
    return min(card.rank, 10)


def hand_value(cards: list[Card]) -> tuple[int, bool]:
    """Returns (total, is_soft)."""
    total = sum(card_value(c) for c in cards)
    aces = sum(1 for c in cards if c.rank == 14)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def draw_upcard(shoe: list[Card]) -> Card:
    """Takes the dealer's face-up card out of the shoe, a 10-value card only DEALER_TEN_CHANCE of the time."""
    tens = [i for i, c in enumerate(shoe) if card_value(c) == 10]
    others = [i for i, c in enumerate(shoe) if card_value(c) != 10]
    want_ten = rng.random() < DEALER_TEN_CHANCE
    pool = tens if (want_ten and tens) or not others else others
    return shoe.pop(rng.choice(pool))


# (server, player) -> that player's shoe. Kept in memory: after a restart everyone starts on a fresh shoe.
SHOES: dict[tuple[int, int], list[Card]] = {}


def player_shoe(guild_id: int, user_id: int) -> tuple[list[Card], bool]:
    """The player's shoe for their next hand, and whether it was just shuffled."""
    key = (guild_id or 0, user_id)
    shoe = SHOES.get(key)
    if shoe is None or len(shoe) < int(DECKS * 52 * RESHUFFLE_AT):
        SHOES[key] = shoe = new_deck(DECKS)
        return shoe, True
    return shoe, False


def is_blackjack(cards: list[Card]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


class Hand:
    def __init__(self, cards: list[Card], stake: int, from_split: bool = False):
        self.cards = cards
        self.stake = stake
        self.done = False
        self.doubled = False
        self.from_split = from_split
        self.result = ""
        self.payout = 0

    @property
    def value(self) -> int:
        return hand_value(self.cards)[0]

    def label(self) -> str:
        total, soft = hand_value(self.cards)
        return f"{'Soft ' if soft and total < 21 else ''}{total}"


class BlackjackView(OwnedView):
    def __init__(self, wager: Wager, user: discord.abc.User):
        super().__init__(user.id, timeout=180)
        self.wager = wager
        self.user = user
        self.bet = wager.stake  # the original bet, before any double or split; what "Deal again" repeats
        self.shoe, self.shuffled = player_shoe(wager.guild_id, user.id)
        # dealt in the Activity's order: the player's first card, the dealer's face-up card, the player's second
        # card, the dealer's hole card
        first = self.draw()
        up = draw_upcard(self.shoe) if self.shoe else self.draw()
        self.hands = [Hand([first, self.draw()], wager.stake)]
        self.dealer: list[Card] = [up, self.draw()]
        self.current = 0
        self.finished = False
        self.lock = asyncio.Lock()
        self.summary = ""

    def draw(self) -> Card:
        if not self.shoe:   # (only with several hands going at once on one shoe) the same list, refilled
            self.shoe.extend(new_deck(DECKS))
        return self.shoe.pop()

    # ------------------------------------------------------------- state
    @property
    def hand(self) -> Hand:
        return self.hands[self.current]

    def can_double(self) -> bool:
        return len(self.hand.cards) == 2 and not self.hand.done

    def can_split(self) -> bool:
        h = self.hand
        return (len(h.cards) == 2 and len(self.hands) < MAX_HANDS
                and card_value(h.cards[0]) == card_value(h.cards[1]))

    def refresh_buttons(self) -> None:
        if self.finished:
            self.disable_all()
            return
        self.double_btn.disabled = not self.can_double()
        self.split_btn.disabled = not self.can_split()

    def dealer_won_with_21(self) -> bool:
        """True when the dealer finished on 21 (blackjack or drawn) and beat at least one of the player's hands."""
        return (hand_value(self.dealer)[0] == 21
                and any(h.result in (LOSE, DEALER_BLACKJACK) for h in self.hands))

    def embed(self) -> discord.Embed:
        if self.finished:
            dealer = f"{show(self.dealer)} — **{hand_value(self.dealer)[0]}**"
        else:
            dealer = f"`{self.dealer[0]}` {HIDDEN} — **{card_value(self.dealer[0])}**"
        lines = []
        for i, h in enumerate(self.hands):
            marker = "▶️ " if (not self.finished and i == self.current and len(self.hands) > 1) else ""
            name = f"Hand {i + 1}" if len(self.hands) > 1 else "Your hand"
            extra = f" — {h.result}" if h.result else ""
            lines.append(f"{marker}**{name}** ({credits_text(h.stake)}{' doubled' if h.doubled else ''}): "
                         f"{show(h.cards)} — **{h.label()}**{extra}")
        embed = discord.Embed(title="🃏 Blackjack", color=discord.Color.dark_teal())
        # The player's profile picture as a small icon on the author line, so everyone can tell whose game
        # is whose at a glance. (A thumbnail would take width from the hand lines and wrap them on mobile.)
        embed.set_author(name=self.user.display_name, icon_url=self.user.display_avatar.url)
        embed.add_field(name="Dealer", value=dealer, inline=False)
        embed.add_field(name=self.user.display_name, value="\n".join(lines), inline=False)
        if self.finished:
            embed.color = result_color(self.wager.stake, self.wager.payout)
            summary = self.summary
            if self.dealer_won_with_21():
                summary = f"**That's 21 for the dealer, thanks for playing**\n{summary}"
            embed.add_field(name="Result", value=summary, inline=False)
        else:
            embed.set_footer(text=("The dealer shuffles a fresh shoe · " if self.shuffled else "")
                             + "Dealer stands on all 17s · Blackjack pays 3:2")
        return embed

    # ------------------------------------------------------------ flow
    async def advance(self) -> None:
        """Moves to the next unfinished hand, or finishes the game."""
        while self.current < len(self.hands) and self.hands[self.current].done:
            self.current += 1
        if self.current >= len(self.hands):
            self.current = len(self.hands) - 1
            await self.finish()

    async def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.stop()
        if any(h.value <= 21 for h in self.hands):
            while hand_value(self.dealer)[0] < 17:
                self.dealer.append(self.draw())
        dealer_total = hand_value(self.dealer)[0]
        for h in self.hands:
            if h.value > 21:
                h.result, h.payout = "Bust 💥", 0
            elif dealer_total > 21 or h.value > dealer_total:
                h.result, h.payout = "Win ✅", h.stake * 2
            elif h.value == dealer_total:
                h.result, h.payout = "Push ➖", h.stake
            else:
                h.result, h.payout = LOSE, 0
        payout = sum(h.payout for h in self.hands)
        bal = await self.wager.settle(payout)
        self.summary = result_line(self.wager.stake, payout, bal)

    def next_view(self) -> discord.ui.View:
        """The buttons to show: the game buttons while playing, a "Deal again" button once it's over."""
        if self.finished:
            return AgainView(self.user, self.bet)
        self.refresh_buttons()
        return self

    async def update(self, interaction: discord.Interaction) -> None:
        view = self.next_view()
        await interaction.response.edit_message(embed=self.embed(), view=view)
        view.message = interaction.message

    async def on_timeout(self) -> None:
        async with self.lock:
            if self.finished:
                return
            for h in self.hands:
                h.done = True
            await self.finish()
            if self.message:
                view = self.next_view()
                try:
                    await self.message.edit(embed=self.embed(), view=view)
                    view.message = self.message
                except discord.HTTPException:
                    pass

    # --------------------------------------------------------- buttons
    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="➕")
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.finished:
                return await interaction.response.defer()
            self.hand.cards.append(self.draw())
            if self.hand.value >= 21:
                self.hand.done = True
                await self.advance()
            await self.update(interaction)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.finished:
                return await interaction.response.defer()
            self.hand.done = True
            await self.advance()
            await self.update(interaction)

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success, emoji="💰")
    async def double_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.finished or not self.can_double():
                return await interaction.response.defer()
            if not await self.wager.add_stake(self.hand.stake):
                return await interaction.response.send_message(
                    f"You need another {fmt(self.hand.stake)} to double.", ephemeral=True)
            self.hand.stake *= 2
            self.hand.doubled = True
            self.hand.cards.append(self.draw())
            self.hand.done = True
            await self.advance()
            await self.update(interaction)

    @discord.ui.button(label="Split", style=discord.ButtonStyle.success, emoji="✂️")
    async def split_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.finished or not self.can_split():
                return await interaction.response.defer()
            if not await self.wager.add_stake(self.hand.stake):
                return await interaction.response.send_message(
                    f"You need another {fmt(self.hand.stake)} to split.", ephemeral=True)
            h = self.hand
            second = h.cards.pop()
            new = Hand([second, self.draw()], h.stake, from_split=True)
            h.cards.append(self.draw())
            h.from_split = True
            self.hands.insert(self.current + 1, new)
            if second.rank == 14:  # split aces get one card each
                h.done = new.done = True
            for hand in (h, new):
                if hand.value == 21:
                    hand.done = True
            await self.advance()
            await self.update(interaction)


class AgainView(OwnedView):
    """Shown under a finished hand: deals a new hand (as a new message) with the same bet."""

    def __init__(self, user: discord.abc.User, bet: int):
        super().__init__(user.id, timeout=180)
        self.bet = bet
        self.used = False
        self.lock = asyncio.Lock()
        self.again.label = f"Deal again ({credits_text(bet)})"

    @discord.ui.button(label="Deal again", style=discord.ButtonStyle.success, emoji="🔁")
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:  # a double click must not start two hands
            if self.used:
                return await interaction.response.defer()
            wager = await take_bet(interaction, str(self.bet), "blackjack")
            if not wager:  # too poor (restart panel / error shown privately); the button stays for later
                return
            self.used = True
            self.stop()
        await start_game(interaction, wager)
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        if self.message and not self.used:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


async def start_game(interaction: discord.Interaction, wager: Wager) -> None:
    """Deals a hand for a bet that has been taken and posts it as a reply to the interaction."""
    view = BlackjackView(wager, interaction.user)
    player, dealer = view.hands[0].cards, view.dealer
    if is_blackjack(player) or is_blackjack(dealer):
        view.finished = True
        view.stop()
        if is_blackjack(player) and is_blackjack(dealer):
            payout, view.hands[0].result = wager.stake, "Push ➖"
        elif is_blackjack(player):
            payout, view.hands[0].result = mul_floor(wager.stake, 2.5), "Blackjack! 🎉"
        else:
            payout, view.hands[0].result = 0, DEALER_BLACKJACK
        bal = await wager.settle(payout)
        view.summary = result_line(wager.stake, payout, bal)
    shown = view.next_view()
    await interaction.response.send_message(**tag(interaction.user), embed=view.embed(), view=shown)
    shown.message = await interaction.original_response()


class Blackjack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="blackjack", description="Play blackjack against the dealer.")
    @app_commands.describe(bet="Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all")
    @gambling_only()
    async def blackjack(self, interaction: discord.Interaction, bet: str):
        wager = await take_bet(interaction, bet, "blackjack")
        if not wager:
            return
        await start_game(interaction, wager)


async def setup(bot):
    await bot.add_cog(Blackjack(bot))
