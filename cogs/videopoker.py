"""Video poker — Jacks or Better (9/6 paytable, ~99.5% return with perfect play)."""
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from utils.cards import Card, new_deck
from utils.checks import gambling_only
from utils.economy import Wager, fmt, result_color, result_line, take_bet, tag
from utils.hand_eval import eval5
from utils.views import OwnedView

PAYTABLE = [
    ("Royal Flush", 800),
    ("Straight Flush", 50),
    ("Four of a Kind", 25),
    ("Full House", 9),
    ("Flush", 6),
    ("Straight", 4),
    ("Three of a Kind", 3),
    ("Two Pair", 2),
    ("Jacks or Better", 1),
]
PAYOUTS = dict(PAYTABLE)


def classify(cards: list[Card]) -> str | None:
    cat, tiebreak = eval5(cards)
    if cat == 8:
        return "Royal Flush" if tiebreak[0] == 14 else "Straight Flush"
    names = {7: "Four of a Kind", 6: "Full House", 5: "Flush", 4: "Straight", 3: "Three of a Kind", 2: "Two Pair"}
    if cat in names:
        return names[cat]
    if cat == 1 and tiebreak[0] >= 11:
        return "Jacks or Better"
    return None


class HoldButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="?", row=0)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: VideoPokerView = self.view
        async with view.lock:
            if view.finished:
                return await interaction.response.defer()
            view.held[self.index] = not view.held[self.index]
            view.refresh()
            await interaction.response.edit_message(embed=view.embed(), view=view)


class VideoPokerView(OwnedView):
    def __init__(self, wager: Wager, user: discord.abc.User):
        super().__init__(user.id, timeout=120)
        self.wager = wager
        self.user = user
        self.deck = new_deck()
        self.hand = [self.deck.pop() for _ in range(5)]
        self.held = [False] * 5
        self.finished = False
        self.status = ""
        self.lock = asyncio.Lock()
        self.hold_buttons = [HoldButton(i) for i in range(5)]
        for b in self.hold_buttons:
            self.add_item(b)
        self.draw_btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Draw", emoji="🔄", row=1)
        self.draw_btn.callback = self.draw
        self.add_item(self.draw_btn)
        self.refresh()

    def refresh(self) -> None:
        for b in self.hold_buttons:
            card = self.hand[b.index]
            b.label = f"{card}{' HELD' if self.held[b.index] else ''}"
            b.style = discord.ButtonStyle.success if self.held[b.index] else discord.ButtonStyle.secondary
            b.disabled = self.finished
        self.draw_btn.disabled = self.finished

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="🃏 Video Poker — Jacks or Better", color=discord.Color.dark_blue())
        cards = "  ".join(f"`{c}`" + ("🔒" if self.held[i] and not self.finished else "")
                          for i, c in enumerate(self.hand))
        embed.description = f"## {cards}"
        current = classify(self.hand)
        embed.add_field(name="Bet", value=fmt(self.wager.stake))
        embed.add_field(name="Hand", value=current or "Nothing")
        if self.status:
            embed.add_field(name="Result", value=self.status, inline=False)
            embed.color = result_color(self.wager.stake, self.wager.payout)
        else:
            embed.add_field(name="How to play",
                            value="Click cards to **hold** them, then press **Draw** to replace the rest.",
                            inline=False)
        embed.set_footer(text=" · ".join(f"{n} {m}x" for n, m in PAYTABLE))
        return embed

    async def finish(self) -> None:
        self.finished = True
        self.stop()
        for i in range(5):
            if not self.held[i]:
                self.hand[i] = self.deck.pop()
        hand = classify(self.hand)
        payout = self.wager.stake * PAYOUTS[hand] if hand else 0
        bal = await self.wager.settle(payout)
        self.status = f"**{hand or 'No winning hand'}**\n{result_line(self.wager.stake, payout, bal)}"
        self.refresh()

    async def draw(self, interaction: discord.Interaction) -> None:
        async with self.lock:
            if self.finished:
                return await interaction.response.defer()
            await self.finish()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        async with self.lock:
            if self.finished:
                return
            await self.finish()
            self.status = "⏰ Timed out — drew automatically.\n" + self.status
            if self.message:
                try:
                    await self.message.edit(embed=self.embed(), view=self)
                except discord.HTTPException:
                    pass


class VideoPoker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="videopoker", description="Jacks or Better video poker.")
    @app_commands.describe(bet="Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all")
    @gambling_only()
    async def videopoker(self, interaction: discord.Interaction, bet: str):
        wager = await take_bet(interaction, bet, "videopoker")
        if not wager:
            return
        view = VideoPokerView(wager, interaction.user)
        await interaction.response.send_message(**tag(interaction.user), embed=view.embed(), view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(VideoPoker(bot))
