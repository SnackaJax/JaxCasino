"""Crash: a multiplier climbs until it crashes at a random point. Cash out before it does."""
import asyncio
import logging
import math

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import gambling_only
from utils.economy import Wager, fmt, fmt_mult, result_color, result_line, take_bet, tag
from utils.rng import rng
from utils.views import OwnedView

log = logging.getLogger("gamblebot.crash")

GROWTH = 0.12      # multiplier = e^(GROWTH * seconds): 2x ≈ 5.8s, 10x ≈ 19s, 100x ≈ 38s
MAX_CRASH = 1000.0
TICK = 1.2         # seconds between message updates (Discord rate limits)


def crash_point() -> float:
    """P(crash >= x) = (1 - edge) / x."""
    u = rng.random()
    point = math.floor((1 - config.HOUSE_EDGE) / (1 - u) * 100) / 100
    return min(max(point, 1.0), MAX_CRASH)


class CrashView(OwnedView):
    def __init__(self, wager: Wager, user: discord.abc.User, auto: float | None):
        super().__init__(user.id, timeout=None)
        self.wager = wager
        self.user = user
        self.auto = auto
        self.crash_at = crash_point()
        self.start = asyncio.get_running_loop().time()
        self.finished = False
        self.cashed_at: float | None = None
        self.current = 1.0
        self.status = ""

    def now_mult(self) -> float:
        elapsed = asyncio.get_running_loop().time() - self.start
        return math.floor(math.exp(GROWTH * elapsed) * 100) / 100

    def embed(self) -> discord.Embed:
        if self.finished and self.cashed_at is None:
            title, color = f"💥 Crashed at {fmt_mult(self.crash_at)}", discord.Color.red()
        elif self.finished:
            title, color = f"💰 Cashed out at {fmt_mult(self.cashed_at)}", discord.Color.green()
        else:
            title, color = f"📈 {fmt_mult(self.current)}", discord.Color.blurple()
        embed = discord.Embed(title=title, color=color)
        if not self.finished:
            embed.description = f"## 🚀 {fmt_mult(self.current)}"
        elif self.cashed_at is None:
            embed.description = f"## 💥 {fmt_mult(self.crash_at)}"
        else:
            embed.description = f"## 💰 {fmt_mult(self.cashed_at)}"
        embed.add_field(name="Bet", value=fmt(self.wager.stake))
        if not self.finished:
            embed.add_field(name="Cash out now", value=fmt(math.floor(self.wager.stake * self.current)))
        if self.auto:
            embed.add_field(name="Auto cash out", value=fmt_mult(self.auto))
        if self.status:
            embed.add_field(name="Result", value=self.status, inline=False)
        if self.finished:
            embed.color = result_color(self.wager.stake, self.wager.payout)
            embed.set_footer(text=f"Crash point was {fmt_mult(self.crash_at)}")
        return embed

    async def finish(self, cashed_at: float | None) -> None:
        if self.finished:
            return
        self.finished = True
        self.cashed_at = cashed_at
        self.stop()
        payout = math.floor(self.wager.stake * cashed_at) if cashed_at else 0
        bal = await self.wager.settle(payout)
        self.status = result_line(self.wager.stake, payout, bal)
        self.disable_all()
        if self.message:
            try:
                await self.message.edit(embed=self.embed(), view=self)
            except discord.HTTPException:
                pass

    async def run(self) -> None:
        try:
            await self._run()
        except Exception:
            log.exception("Crash game loop failed for %s; refunding the bet", self.user)
            if not self.finished:
                self.finished = True
                self.stop()
                await self.wager.refund()
                self.status = "⚠️ Something went wrong, so your bet was refunded."
                self.disable_all()
                try:
                    await self.message.edit(embed=self.embed(), view=self)
                except discord.HTTPException:
                    pass

    async def _run(self) -> None:
        while not self.finished:
            await asyncio.sleep(TICK)
            if self.finished:
                return
            m = self.now_mult()
            if self.auto and self.auto <= self.crash_at and m >= self.auto:
                await self.finish(self.auto)
                return
            if m >= self.crash_at:
                await self.finish(None)
                return
            self.current = m
            self.cash_btn.label = f"Cash out {math.floor(self.wager.stake * m):,}"
            try:
                await self.message.edit(embed=self.embed(), view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Cash out", style=discord.ButtonStyle.success, emoji="💰")
    async def cash_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.finished:
            return
        m = self.now_mult()
        await self.finish(m if m < self.crash_at else None)


class Crash(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="crash", description="Cash out before the multiplier crashes!")
    @app_commands.describe(bet="Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all",
                           auto_cashout="Optional: automatically cash out at this multiplier (e.g. 2.5)")
    @gambling_only()
    async def crash(self, interaction: discord.Interaction, bet: str,
                    auto_cashout: app_commands.Range[float, 1.01, MAX_CRASH] | None = None):
        wager = await take_bet(interaction, bet, "crash")
        if not wager:
            return
        view = CrashView(wager, interaction.user, round(auto_cashout, 2) if auto_cashout else None)
        await interaction.response.send_message(**tag(interaction.user), embed=view.embed(), view=view)
        view.message = await interaction.original_response()
        view.task = asyncio.create_task(view.run())  # keep a reference so it isn't garbage collected


async def setup(bot):
    await bot.add_cog(Crash(bot))
