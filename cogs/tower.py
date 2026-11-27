"""Tower: climb 8 floors by picking a safe door on each floor. Cash out any time."""
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
from utils.bignum import credits_text, mul_floor

FLOORS = 8
# difficulty: (doors per floor, traps per floor)
DIFFICULTIES = {
    "easy": (4, 1),
    "medium": (3, 1),
    "hard": (2, 1),
    "expert": (3, 2),
}


class Door(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(style=discord.ButtonStyle.primary, label=f"Door {index + 1}", emoji="🚪", row=0)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await self.view.pick(interaction, self.index)


class TowerView(OwnedView):
    def __init__(self, wager: Wager, user: discord.abc.User, difficulty: str):
        super().__init__(user.id, timeout=180)
        self.wager = wager
        self.user = user
        self.difficulty = difficulty
        self.doors, traps = DIFFICULTIES[difficulty]
        self.traps = [set(rng.sample(range(self.doors), traps)) for _ in range(FLOORS)]
        self.step = self.doors / (self.doors - traps)
        self.picks: list[int] = []   # chosen door per cleared floor
        self.failed_pick: int | None = None
        self.finished = False
        self.status = ""
        self.lock = asyncio.Lock()
        for i in range(self.doors):
            self.add_item(Door(i))
        self.cashout = discord.ui.Button(style=discord.ButtonStyle.success, label="Cash out", emoji="💰", row=1)
        self.cashout.callback = self.cash_out
        self.add_item(self.cashout)
        self.refresh()

    def mult_at(self, floors: int) -> float:
        return (1 - config.HOUSE_EDGE) * self.step ** floors if floors else 1.0

    @property
    def floor(self) -> int:
        return len(self.picks)

    def refresh(self) -> None:
        for item in self.children:
            if isinstance(item, Door):
                item.disabled = self.finished
        self.cashout.disabled = self.finished or self.floor == 0
        self.cashout.label = f"Cash out {credits_text(mul_floor(self.wager.stake, self.mult_at(self.floor)))}"

    def tower_text(self) -> str:
        lines = []
        for f in range(FLOORS - 1, -1, -1):
            cells = []
            for d in range(self.doors):
                if f < self.floor:
                    cells.append("✅" if self.picks[f] == d else ("💀" if self.finished and d in self.traps[f] else "⬛"))
                elif f == self.floor and self.failed_pick is not None:
                    cells.append("💥" if d == self.failed_pick else ("💀" if d in self.traps[f] else "⬛"))
                elif self.finished:
                    cells.append("💀" if d in self.traps[f] else "⬛")
                else:
                    cells.append("🟦" if f == self.floor else "⬜")
            marker = "▶️" if f == self.floor and not self.finished else "▫️"
            lines.append(f"{marker} {' '.join(cells)}  `{fmt_mult(self.mult_at(f + 1)):>9}`")
        return "\n".join(lines)

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"🗼 Tower — {self.difficulty.title()}", color=discord.Color.teal())
        embed.description = self.tower_text()
        embed.add_field(name="Bet", value=fmt(self.wager.stake))
        embed.add_field(name="Floor", value=f"{self.floor}/{FLOORS}")
        embed.add_field(name="Multiplier", value=fmt_mult(self.mult_at(self.floor)))
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

    async def pick(self, interaction: discord.Interaction, door: int) -> None:
        async with self.lock:
            if self.finished:
                return await interaction.response.defer()
            if door in self.traps[self.floor]:
                self.failed_pick = door
                await self.end(0, "💥 You picked a trap!")
            else:
                self.picks.append(door)
                if self.floor == FLOORS:
                    m = self.mult_at(self.floor)
                    await self.end(mul_floor(self.wager.stake, m), f"🏆 You reached the top at {fmt_mult(m)}!")
                else:
                    self.refresh()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def cash_out(self, interaction: discord.Interaction) -> None:
        async with self.lock:
            if self.finished or self.floor == 0:
                return await interaction.response.defer()
            m = self.mult_at(self.floor)
            await self.end(mul_floor(self.wager.stake, m), f"💰 Cashed out at {fmt_mult(m)}!")
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        async with self.lock:
            if self.finished:
                return
            if self.floor:
                m = self.mult_at(self.floor)
                await self.end(mul_floor(self.wager.stake, m), "⏰ Timed out — auto cashed out.")
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


class Tower(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="tower", description="Climb the tower by picking safe doors. Cash out any time.")
    @app_commands.describe(bet="Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all", difficulty="How many doors and traps per floor")
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="Easy — 4 doors, 1 trap", value="easy"),
        app_commands.Choice(name="Medium — 3 doors, 1 trap", value="medium"),
        app_commands.Choice(name="Hard — 2 doors, 1 trap", value="hard"),
        app_commands.Choice(name="Expert — 3 doors, 2 traps", value="expert"),
    ])
    @gambling_only()
    async def tower(self, interaction: discord.Interaction, bet: str,
                    difficulty: app_commands.Choice[str] | None = None):
        wager = await take_bet(interaction, bet, "tower")
        if not wager:
            return
        view = TowerView(wager, interaction.user, difficulty.value if difficulty else "medium")
        await interaction.response.send_message(**tag(interaction.user), embed=view.embed(), view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Tower(bot))
