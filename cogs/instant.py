"""Instant games: coinflip, dice, roulette and wheel of fortune. (Slots live in cogs/slots.py.)"""
import asyncio
import math

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import gambling_only
from utils.economy import fmt, fmt_mult, result_color, result_line, send_error, take_bet, tag
from utils.rng import rng
from utils.bignum import credits_text, mul_floor

BET_DESC = "Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all"

# --------------------------------------------------------------- roulette
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROULETTE_BETS = {
    "red": ("Red", 2, lambda n: n in RED_NUMBERS),
    "black": ("Black", 2, lambda n: n != 0 and n not in RED_NUMBERS),
    "odd": ("Odd", 2, lambda n: n != 0 and n % 2 == 1),
    "even": ("Even", 2, lambda n: n != 0 and n % 2 == 0),
    "low": ("Low (1-18)", 2, lambda n: 1 <= n <= 18),
    "high": ("High (19-36)", 2, lambda n: 19 <= n <= 36),
    "dozen1": ("1st dozen (1-12)", 3, lambda n: 1 <= n <= 12),
    "dozen2": ("2nd dozen (13-24)", 3, lambda n: 13 <= n <= 24),
    "dozen3": ("3rd dozen (25-36)", 3, lambda n: 25 <= n <= 36),
    "col1": ("1st column", 3, lambda n: n != 0 and n % 3 == 1),
    "col2": ("2nd column", 3, lambda n: n != 0 and n % 3 == 2),
    "col3": ("3rd column", 3, lambda n: n != 0 and n % 3 == 0),
    "number": ("Single number", 36, None),
}


def roulette_emoji(n: int) -> str:
    return "🟩" if n == 0 else ("🟥" if n in RED_NUMBERS else "⬛")


# ------------------------------------------------------------------ wheel
# 20 segments, average multiplier 0.985.
WHEEL = [0, 0.5, 1.2, 0, 2, 0, 0.5, 1.5, 0, 3, 0, 0.5, 1.5, 0, 2, 0, 0.5, 1.5, 0, 5]
WHEEL_EMOJI = {0: "⬛", 0.5: "🟥", 1.2: "🟧", 1.5: "🟨", 2: "🟩", 3: "🟦", 5: "🟪"}


def wheel_strip(pos: int) -> str:
    segs = [WHEEL[(pos + i) % len(WHEEL)] for i in range(-3, 4)]
    top = "▫️ ▫️ ▫️ 🔻 ▫️ ▫️ ▫️"
    mid = " ".join(WHEEL_EMOJI[s] for s in segs)
    return f"{top}\n{mid}"


WHEEL_LEGEND = "  ".join(f"{e} {m}x" for m, e in WHEEL_EMOJI.items())


class Instant(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------ coinflip
    @app_commands.command(name="coinflip", description="Flip a coin. Win almost double your bet.")
    @app_commands.describe(bet=BET_DESC, side="Heads or tails")
    @app_commands.choices(side=[app_commands.Choice(name="Heads", value="heads"),
                                app_commands.Choice(name="Tails", value="tails")])
    @gambling_only()
    async def coinflip(self, interaction: discord.Interaction, bet: str, side: app_commands.Choice[str]):
        wager = await take_bet(interaction, bet, "coinflip")
        if not wager:
            return
        mult = round(2 * (1 - config.HOUSE_EDGE), 2)
        await interaction.response.send_message(**tag(interaction.user), embed=discord.Embed(
            title="🪙 Coinflip", description=f"Flipping... you called **{side.name}**",
            color=discord.Color.gold()))
        await asyncio.sleep(1.5)
        result = rng.choice(["heads", "tails"])
        won = result == side.value
        payout = mul_floor(wager.stake, mult) if won else 0
        bal = await wager.settle(payout)
        embed = discord.Embed(
            title="🪙 Coinflip",
            description=f"The coin landed on **{result.title()}**!\n\n{result_line(wager.stake, payout, bal)}",
            color=result_color(wager.stake, payout),
        )
        embed.set_footer(text=f"Bet {credits_text(wager.stake)} on {side.name} · pays {mult}x")
        await interaction.edit_original_response(embed=embed)

    # ---------------------------------------------------------------- dice
    @app_commands.command(name="dice", description="Roll 1-100. Bet the roll lands over or under your target.")
    @app_commands.describe(bet=BET_DESC, direction="Win if the roll is over or under the target",
                           target="The target number (2-99)")
    @app_commands.choices(direction=[app_commands.Choice(name="Under", value="under"),
                                     app_commands.Choice(name="Over", value="over")])
    @gambling_only()
    async def dice(self, interaction: discord.Interaction, bet: str, direction: app_commands.Choice[str],
                   target: app_commands.Range[int, 2, 99]):
        if direction.value == "under":
            chance = (target - 1) / 100
        else:
            chance = (100 - target) / 100
        if not 0.01 <= chance <= 0.95:
            await send_error(interaction, "That target gives a win chance outside 1%–95%. Pick another target.")
            return
        mult = math.floor((1 - config.HOUSE_EDGE) / chance * 100) / 100
        wager = await take_bet(interaction, bet, "dice")
        if not wager:
            return
        roll = rng.randint(1, 100)
        won = roll < target if direction.value == "under" else roll > target
        payout = mul_floor(wager.stake, mult) if won else 0
        bal = await wager.settle(payout)
        embed = discord.Embed(
            title="🎲 Dice",
            description=(
                f"You needed **{direction.name.lower()} {target}** ({chance * 100:.0f}% chance, {fmt_mult(mult)})\n"
                f"🎲 Rolled: **{roll}**\n\n{result_line(wager.stake, payout, bal)}"
            ),
            color=result_color(wager.stake, payout),
        )
        await interaction.response.send_message(**tag(interaction.user), embed=embed)

    # ------------------------------------------------------------ roulette
    @app_commands.command(name="roulette", description="European roulette (single zero).")
    @app_commands.describe(bet=BET_DESC, bet_on="What to bet on", number="Required when betting on a single number")
    @app_commands.choices(bet_on=[app_commands.Choice(name=f"{v[0]} — pays {v[1]}x", value=k)
                                  for k, v in ROULETTE_BETS.items()])
    @gambling_only()
    async def roulette(self, interaction: discord.Interaction, bet: str, bet_on: app_commands.Choice[str],
                       number: app_commands.Range[int, 0, 36] | None = None):
        name, mult, check = ROULETTE_BETS[bet_on.value]
        if bet_on.value == "number":
            if number is None:
                await send_error(interaction, "Pick a `number` (0-36) when betting on a single number.")
                return
            name = f"Number {number}"
            target = number
            check = lambda n: n == target  # noqa: E731
        wager = await take_bet(interaction, bet, "roulette")
        if not wager:
            return
        await interaction.response.send_message(**tag(interaction.user), embed=discord.Embed(
            title="🎡 Roulette", description=f"The ball is spinning... you bet on **{name}**",
            color=discord.Color.dark_green()))
        await asyncio.sleep(2)
        result = rng.randint(0, 36)
        payout = wager.stake * mult if check(result) else 0
        bal = await wager.settle(payout)
        embed = discord.Embed(
            title="🎡 Roulette",
            description=(f"The ball landed on {roulette_emoji(result)} **{result}**\n"
                         f"You bet on **{name}** ({mult}x)\n\n{result_line(wager.stake, payout, bal)}"),
            color=result_color(wager.stake, payout),
        )
        await interaction.edit_original_response(embed=embed)

    # --------------------------------------------------------------- wheel
    @app_commands.command(name="wheel", description="Spin the wheel of fortune for a multiplier.")
    @app_commands.describe(bet=BET_DESC)
    @gambling_only()
    async def wheel(self, interaction: discord.Interaction, bet: str):
        wager = await take_bet(interaction, bet, "wheel")
        if not wager:
            return
        final = rng.randrange(len(WHEEL))
        pos = rng.randrange(len(WHEEL))
        embed = discord.Embed(title="🎡 Wheel of Fortune", color=discord.Color.blurple(),
                              description=f"{wheel_strip(pos)}\n\n{WHEEL_LEGEND}")
        await interaction.response.send_message(**tag(interaction.user), embed=embed)
        # Spin forward a few frames, slowing down, then land on `final`.
        total = len(WHEEL) * 2 + (final - pos) % len(WHEEL)
        steps = [total // 2, total // 4, total - total // 2 - total // 4]
        for step in steps:
            await asyncio.sleep(0.9)
            pos = (pos + step) % len(WHEEL)
            embed.description = f"{wheel_strip(pos)}\n\n{WHEEL_LEGEND}"
            await interaction.edit_original_response(embed=embed)
        mult = WHEEL[final]
        payout = mul_floor(wager.stake, mult)
        bal = await wager.settle(payout)
        embed.description = (f"{wheel_strip(final)}\n\nLanded on **{fmt_mult(mult)}**\n\n"
                             f"{result_line(wager.stake, payout, bal)}\n\n{WHEEL_LEGEND}")
        embed.color = result_color(wager.stake, payout)
        await interaction.edit_original_response(embed=embed)


async def setup(bot):
    await bot.add_cog(Instant(bot))
