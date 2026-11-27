"""Higher or Lower: guess whether the next card is higher/lower (or the same). Cash out any time."""
import asyncio
import math

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.cards import Card, random_card
from utils.checks import gambling_only
from utils.economy import Wager, fmt, fmt_mult, result_color, result_line, take_bet, tag
from utils.views import OwnedView
from utils.bignum import credits_text, mul_floor


def chances(card: Card) -> tuple[float, float]:
    """(P(next >= card), P(next <= card)) with an infinite deck of 13 equally likely ranks."""
    return (15 - card.rank) / 13, (card.rank - 1) / 13


def step_mult(p: float) -> float:
    return (1 - config.HOUSE_EDGE) / p


class HigherLowerView(OwnedView):
    def __init__(self, wager: Wager, user: discord.abc.User):
        super().__init__(user.id, timeout=120)
        self.wager = wager
        self.user = user
        self.card = random_card()
        self.history: list[str] = []
        self.mult = 1.0
        self.correct = 0
        self.finished = False
        self.status = ""
        self.lock = asyncio.Lock()
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        if self.finished:
            self.disable_all()
            return
        p_hi, p_lo = chances(self.card)
        self.higher_btn.label = f"Higher or same ({fmt_mult(step_mult(p_hi))})"
        self.higher_btn.disabled = p_hi >= 1
        self.lower_btn.label = f"Lower or same ({fmt_mult(step_mult(p_lo))})"
        self.lower_btn.disabled = p_lo >= 1
        self.cashout_btn.label = f"Cash out {credits_text(mul_floor(self.wager.stake, self.mult))}"
        self.cashout_btn.disabled = self.correct == 0

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="🔼🔽 Higher or Lower", color=discord.Color.blue())
        embed.description = f"## Current card: `{self.card}`"
        if self.history:
            embed.description += f"\nPrevious: {' → '.join(self.history[-10:])}"
        embed.add_field(name="Bet", value=fmt(self.wager.stake))
        embed.add_field(name="Multiplier", value=fmt_mult(self.mult))
        embed.add_field(name="Cash out value", value=fmt(mul_floor(self.wager.stake, self.mult)))
        if self.status:
            embed.add_field(name="Result", value=self.status, inline=False)
        if self.finished:
            embed.color = result_color(self.wager.stake, self.wager.payout)
        else:
            embed.set_footer(text="Ace is high · Skip draws a new card for free")
        return embed

    async def end(self, payout: int, reason: str) -> None:
        self.finished = True
        self.stop()
        bal = await self.wager.settle(payout)
        self.status = f"{reason}\n{result_line(self.wager.stake, payout, bal)}"
        self.refresh_buttons()

    async def guess(self, interaction: discord.Interaction, higher: bool) -> None:
        async with self.lock:
            if self.finished:
                return await interaction.response.defer()
            p_hi, p_lo = chances(self.card)
            p = p_hi if higher else p_lo
            nxt = random_card()
            won = nxt.rank >= self.card.rank if higher else nxt.rank <= self.card.rank
            self.history.append(f"`{self.card}`")
            self.card = nxt
            if won:
                self.mult *= step_mult(p)
                self.correct += 1
                self.status = f"✅ Correct! The card was `{nxt}`."
                self.refresh_buttons()
            else:
                await self.end(0, f"❌ Wrong! The card was `{nxt}`.")
            await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Higher", style=discord.ButtonStyle.primary, emoji="⬆️", row=0)
    async def higher_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.guess(interaction, True)

    @discord.ui.button(label="Lower", style=discord.ButtonStyle.primary, emoji="⬇️", row=0)
    async def lower_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.guess(interaction, False)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️", row=1)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.finished:
                return await interaction.response.defer()
            self.history.append(f"~~`{self.card}`~~")
            self.card = random_card()
            self.status = "⏭️ Skipped."
            self.refresh_buttons()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Cash out", style=discord.ButtonStyle.success, emoji="💰", row=1)
    async def cashout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.lock:
            if self.finished or self.correct == 0:
                return await interaction.response.defer()
            await self.end(mul_floor(self.wager.stake, self.mult), f"💰 Cashed out at {fmt_mult(self.mult)}!")
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        async with self.lock:
            if self.finished:
                return
            if self.correct:
                await self.end(mul_floor(self.wager.stake, self.mult), "⏰ Timed out — auto cashed out.")
            else:
                self.finished = True
                bal = await self.wager.refund()
                self.status = f"⏰ Timed out — your bet was refunded.\nBalance: {fmt(bal)}"
                self.refresh_buttons()
            if self.message:
                try:
                    await self.message.edit(embed=self.embed(), view=self)
                except discord.HTTPException:
                    pass


class HigherLower(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="higherlower", description="Guess if the next card is higher or lower. Cash out any time.")
    @app_commands.describe(bet="Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all")
    @gambling_only()
    async def higherlower(self, interaction: discord.Interaction, bet: str):
        wager = await take_bet(interaction, bet, "higherlower")
        if not wager:
            return
        view = HigherLowerView(wager, interaction.user)
        await interaction.response.send_message(**tag(interaction.user), embed=view.embed(), view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(HigherLower(bot))
