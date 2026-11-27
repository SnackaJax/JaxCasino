"""Cockfight: bet on your own chicken (bought with /buy chicken). Every win makes it stronger; one loss
and it's dead, so you need a new one."""
import asyncio
import logging
import math

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import gambling_only
from utils.economy import fmt, result_color, result_line, send_error, take_bet, tag
from utils.rng import rng
from utils.shop import get_prices, price_text
from utils.bignum import credits_text, mul_floor

log = logging.getLogger("jaxcasino.cockfight")

BET_DESC = "Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all"

OPPONENTS = ["Big Red", "Feathered Fury", "Sir Clucks-a-Lot", "The Colonel", "Beaky Blinders", "Drumstick Dan",
             "Nugget", "El Pollo Loco", "Cluck Norris", "Hen Solo", "Eggzecutioner", "Wingston Churchill"]
ACTION = [
    "Feathers fly everywhere! 🪶",
    "Your chicken lands a vicious peck! 💥",
    "{opp} goes for the eyes! 👀",
    "A flurry of wings! 🌪️",
    "Your chicken dodges and counters! ⚡",
    "{opp} kicks up a cloud of dust! 💨",
    "The crowd goes wild! 📣",
]


def win_chance(wins: int) -> float:
    return min(config.COCKFIGHT_MAX_CHANCE, config.COCKFIGHT_BASE_CHANCE + wins * config.COCKFIGHT_CHANCE_STEP)


def payout_mult() -> float:
    return round(2 * (1 - config.HOUSE_EDGE), 2)


def pct(chance: float) -> str:
    return f"{chance * 100:.0f}%"


class Cockfight(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.fighting: set[tuple[int, int]] = set()

    async def _no_chicken(self, interaction: discord.Interaction, whose: str = "You don't") -> None:
        price = (await get_prices(self.bot.db, interaction.guild_id))["chicken"]
        if price > 0:
            await send_error(interaction, f"{whose} have a chicken. Buy one with `/buy chicken` "
                                          f"for {price_text(price)}.")
        else:
            await send_error(interaction, f"{whose} have a chicken, and chickens aren't for sale on this server.")

    @app_commands.command(name="cockfight", description="Bet on your chicken in a cockfight. Buy one with /buy chicken.")
    @app_commands.describe(bet=BET_DESC)
    @gambling_only()
    async def cockfight(self, interaction: discord.Interaction, bet: str):
        db, gid, uid = self.bot.db, interaction.guild_id, interaction.user.id
        key = (gid, uid)
        if key in self.fighting:
            await send_error(interaction, "Your chicken is already in a fight!")
            return
        chicken = await db.get_chicken(gid, uid)
        if chicken is None:
            await self._no_chicken(interaction)
            return
        self.fighting.add(key)
        try:
            await self._fight(interaction, bet, chicken["wins"])
        finally:
            self.fighting.discard(key)

    async def _fight(self, interaction: discord.Interaction, bet: str, wins: int) -> None:
        db, gid, uid = self.bot.db, interaction.guild_id, interaction.user.id
        wager = await take_bet(interaction, bet, "cockfight")
        if not wager:
            return
        chance = win_chance(wins)
        mult = payout_mult()
        opp = rng.choice(OPPONENTS)
        header = (f"🐓 **Your chicken** ({credits_text(wins)} win{'s' if wins != 1 else ''}) vs 🐔 **{opp}**\n"
                  f"Win chance: **{pct(chance)}** · pays {mult}x")
        embed = discord.Embed(title="🐓 Cockfight", color=discord.Color.orange(),
                              description=f"{header}\n\nThe fighters enter the ring...")
        embed.set_footer(text=f"Bet {credits_text(wager.stake)}")
        await interaction.response.send_message(**tag(interaction.user), embed=embed)

        for line in rng.sample(ACTION, 2):
            await asyncio.sleep(1.2)
            embed.description = f"{header}\n\n{line.format(opp=opp)}"
            await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(1.2)

        won = rng.random() < chance
        if won:
            new_wins = await db.chicken_won(gid, uid)
            payout = mul_floor(wager.stake, mult)
            outcome = (f"🏆 **Your chicken won!** {opp} runs off in shame.\n"
                       f"It now has **{credits_text(new_wins)}** win{'s' if new_wins != 1 else ''}, and its win chance in the "
                       f"next fight is **{pct(win_chance(new_wins))}**.")
        else:
            await db.kill_chicken(gid, uid)
            payout = 0
            outcome = (f"💀 **Your chicken died** at the claws of {opp}"
                       + (f" after {credits_text(wins)} win{'s' if wins != 1 else ''}." if wins else ".")
                       + "\nBuy a new one with `/buy chicken`.")
        bal = await wager.settle(payout)
        log.info("cockfight: user %s's chicken (%d wins, %.0f%%) %s", uid, wins, chance * 100,
                 "won" if won else "died")
        embed.description = f"{header}\n\n{outcome}\n\n{result_line(wager.stake, payout, bal)}"
        embed.color = result_color(wager.stake, payout)
        await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="chicken", description="Check on your (or someone else's) fighting chicken.")
    async def chicken(self, interaction: discord.Interaction, user: discord.Member | None = None):
        user = user or interaction.user
        row = await self.bot.db.get_chicken(interaction.guild_id, user.id)
        if row is None:
            mine = user.id == interaction.user.id
            await self._no_chicken(interaction, "You don't" if mine else f"{user.mention} doesn't")
            return
        wins = row["wins"]
        embed = discord.Embed(title=f"🐓 {user.display_name}'s chicken", color=discord.Color.orange())
        embed.add_field(name="Wins", value=f"{credits_text(wins)}")
        embed.add_field(name="Win chance", value=pct(win_chance(wins)))
        embed.add_field(name="Hatched", value=f"<t:{int(row['born'])}:R>")
        if win_chance(wins) >= config.COCKFIGHT_MAX_CHANCE:
            embed.set_footer(text="This chicken is at full strength.")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Cockfight(bot))
