"""Mines: a 4x5 grid with hidden mines. Every safe tile raises the multiplier. Cash out before you hit one."""
import asyncio
import math

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import gambling_only
from utils.economy import Wager, fmt, fmt_mult, result_color, result_line, take_bet, tag
from utils.rng import rng
from utils.views import OwnedView

ROWS, COLS = 4, 5
TILES = ROWS * COLS


def mines_mult(mines: int, revealed: int) -> float:
    """Fair odds of surviving `revealed` picks, minus the house edge."""
    if revealed == 0:
        return 1.0
    m = 1 - config.HOUSE_EDGE
    for i in range(revealed):
        m *= (TILES - i) / (TILES - mines - i)
    return m


class Tile(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="​", row=index // COLS)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await self.view.reveal(interaction, self.index)


class MinesView(OwnedView):
    def __init__(self, wager: Wager, user: discord.abc.User, mines: int):
        super().__init__(user.id, timeout=180)
        self.wager = wager
        self.user = user
        self.mines = mines
        self.mine_set = set(rng.sample(range(TILES), mines))
        self.revealed: set[int] = set()
        self.finished = False
        self.hit: int | None = None
        self.status = ""
        self.lock = asyncio.Lock()
        self.tiles = [Tile(i) for i in range(TILES)]
        for t in self.tiles:
            self.add_item(t)
        self.cashout = discord.ui.Button(style=discord.ButtonStyle.success, label="Cash out", emoji="💰", row=4)
        self.cashout.callback = self.cash_out
        self.add_item(self.cashout)
        self.refresh()

    @property
    def mult(self) -> float:
        return mines_mult(self.mines, len(self.revealed))

    def refresh(self) -> None:
        for t in self.tiles:
            i = t.index
            if i in self.revealed:
                t.emoji, t.label, t.style, t.disabled = "💎", None, discord.ButtonStyle.success, True
            elif self.finished and i in self.mine_set:
                t.emoji, t.label = "💥" if i == self.hit else "💣", None
                t.style = discord.ButtonStyle.danger
                t.disabled = True
            else:
                t.disabled = self.finished
        self.cashout.disabled = self.finished or not self.revealed
        self.cashout.label = f"Cash out {math.floor(self.wager.stake * self.mult):,}"

    def embed(self) -> discord.Embed:
        safe_left = TILES - self.mines - len(self.revealed)
        nxt = mines_mult(self.mines, len(self.revealed) + 1) if safe_left else self.mult
        embed = discord.Embed(title="💣 Mines", color=discord.Color.dark_gold())
        embed.description = f"**{self.mines}** mines hidden in **{TILES}** tiles."
        embed.add_field(name="Bet", value=fmt(self.wager.stake))
        embed.add_field(name="Multiplier", value=fmt_mult(self.mult))
        embed.add_field(name="Next tile", value=fmt_mult(nxt))
        if self.status:
            embed.add_field(name="Result", value=self.status, inline=False)
        if self.finished:
            embed.color = result_color(self.wager.stake, self.wager.payout)
        return embed

    async def end(self, payout: int, reason: str) -> None:
        self.finished = True
        self.stop()
        bal = await self.wager.settle(payout)
        self.status = f"{reason}\n{result_line(self.wager.stake, payout, bal)}"
        self.refresh()

    async def reveal(self, interaction: discord.Interaction, index: int) -> None:
        async with self.lock:
            if self.finished or index in self.revealed:
                return await interaction.response.defer()
            if index in self.mine_set:
                self.hit = index
                await self.end(0, "💥 You hit a mine!")
            else:
                self.revealed.add(index)
                if len(self.revealed) == TILES - self.mines:
                    await self.end(math.floor(self.wager.stake * self.mult),
                                   f"🏆 You cleared the board at {fmt_mult(self.mult)}!")
                else:
                    self.refresh()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def cash_out(self, interaction: discord.Interaction) -> None:
        async with self.lock:
            if self.finished or not self.revealed:
                return await interaction.response.defer()
            await self.end(math.floor(self.wager.stake * self.mult), f"💰 Cashed out at {fmt_mult(self.mult)}!")
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        async with self.lock:
            if self.finished:
                return
            if self.revealed:
                await self.end(math.floor(self.wager.stake * self.mult), "⏰ Timed out — auto cashed out.")
            else:
                self.finished = True
                bal = await self.wager.refund()
                self.status = f"⏰ Timed out — your bet was refunded.\nBalance: {fmt(bal)}"
                self.refresh()
            if self.message:
                try:
                    await self.message.edit(embed=self.embed(), view=self)
                except discord.HTTPException:
                    pass


class Mines(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="mines", description="Reveal tiles without hitting a mine. Cash out any time.")
    @app_commands.describe(bet="Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all", mines="Number of mines (1-19, default 3)")
    @gambling_only()
    async def mines(self, interaction: discord.Interaction, bet: str,
                    mines: app_commands.Range[int, 1, TILES - 1] = 3):
        wager = await take_bet(interaction, bet, "mines")
        if not wager:
            return
        view = MinesView(wager, interaction.user, mines)
        await interaction.response.send_message(**tag(interaction.user), embed=view.embed(), view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Mines(bot))
