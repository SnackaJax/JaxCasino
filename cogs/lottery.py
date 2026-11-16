"""Lottery: players buy tickets into a shared pot. One ticket is drawn on a timer and wins everything."""
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.checks import admin_only, gambling_only
from utils.economy import fmt, send_error, tag
from utils.restart import offer_later
from utils.rng import rng

import logging

log = logging.getLogger("jaxcasino.lottery")


def winner_tag(winner: int | None) -> dict:
    if winner is None:
        return {}
    return {"content": f"🎉 <@{winner}>",
            "allowed_mentions": discord.AllowedMentions(users=[discord.Object(winner)], roles=False, everyone=False)}


class Lottery(commands.Cog):
    lottery = app_commands.Group(name="lottery", description="The server lottery.")

    def __init__(self, bot):
        self.bot = bot
        self.draw_loop.start()

    def cog_unload(self):
        self.draw_loop.cancel()

    async def draw(self, guild_id: int) -> tuple[discord.Embed, int | None]:
        """Draws the lottery. Returns the announcement and the winner's user ID (None if nobody played)."""
        state = await self.bot.db.lottery_state(guild_id)
        tickets = await self.bot.db.lottery_tickets(guild_id)
        if not tickets:
            await self.bot.db.finish_lottery(guild_id, None, 0)
            log.info("Lottery in guild %s: no tickets sold", guild_id)
            return discord.Embed(title="🎟️ Lottery", color=discord.Color.light_grey(),
                                 description="No tickets were sold, so there was no draw. A new round has started."), None
        users = [r["user_id"] for r in tickets]
        weights = [r["tickets"] for r in tickets]
        winner = rng.choices(users, weights=weights)[0]
        pot = state["pot"]
        await self.bot.db.finish_lottery(guild_id, winner, pot)
        total = sum(weights)
        mine = weights[users.index(winner)]
        log.info("Lottery in guild %s: user %s won %s with %d of %d tickets", guild_id, winner, f"{pot:,}", mine, total)
        return discord.Embed(
            title="🎉 Lottery Draw!", color=discord.Color.gold(),
            description=(f"<@{winner}> won the jackpot of {fmt(pot)}!\n"
                         f"They held {mine:,} of {total:,} tickets ({mine / total * 100:.1f}% chance).\n\n"
                         f"A new round has started — `/lottery buy` to enter.")), winner

    @tasks.loop(minutes=1)
    async def draw_loop(self):
        for row in await self.bot.db.due_lotteries():
            try:
                embed, winner = await self.draw(row["guild_id"])
                channel = self.bot.get_channel(row["channel_id"]) if row["channel_id"] else None
                if channel and row["pot"] > 0:
                    await channel.send(**winner_tag(winner), embed=embed)
            except Exception:
                log.exception("Lottery draw failed for guild %s", row["guild_id"])

    @draw_loop.before_loop
    async def before_draw(self):
        await self.bot.wait_until_ready()

    @lottery.command(name="buy", description=f"Buy lottery tickets ({config.LOTTERY_TICKET_PRICE:,} credits each).")
    @app_commands.describe(tickets="How many tickets to buy")
    @gambling_only()
    async def buy(self, interaction: discord.Interaction, tickets: app_commands.Range[int, 1, 100000] = 1):
        price = config.LOTTERY_TICKET_PRICE
        ok = await self.bot.db.buy_tickets(interaction.guild_id, interaction.user.id, tickets, price,
                                           interaction.channel_id)
        if not ok:
            bal = await self.bot.db.get_balance(interaction.guild_id, interaction.user.id)
            await send_error(interaction, f"{tickets:,} tickets cost {fmt(tickets * price)} — you have {fmt(bal)}.")
            return
        state = await self.bot.db.lottery_state(interaction.guild_id)
        if await self.bot.db.get_balance(interaction.guild_id, interaction.user.id) < config.MIN_BET:
            offer_later(interaction.channel, self.bot.db, interaction.guild_id, interaction.user.id)
        embed = discord.Embed(
            title="🎟️ Tickets bought", color=discord.Color.green(),
            description=(f"{interaction.user.mention} bought **{tickets:,}** ticket(s) for {fmt(tickets * price)}.\n"
                         f"Jackpot is now {fmt(state['pot'])} · draw <t:{int(state['draw_at'])}:R>"))
        await interaction.response.send_message(**tag(interaction.user), embed=embed)

    @lottery.command(name="info", description="Show the current jackpot and your tickets.")
    async def info(self, interaction: discord.Interaction):
        state = await self.bot.db.lottery_state(interaction.guild_id)
        tickets = await self.bot.db.lottery_tickets(interaction.guild_id)
        total = sum(r["tickets"] for r in tickets)
        mine = next((r["tickets"] for r in tickets if r["user_id"] == interaction.user.id), 0)
        embed = discord.Embed(title="🎟️ Lottery", color=discord.Color.gold())
        embed.add_field(name="Jackpot", value=fmt(state["pot"]))
        embed.add_field(name="Next draw", value=f"<t:{int(state['draw_at'])}:R>")
        embed.add_field(name="Ticket price", value=fmt(config.LOTTERY_TICKET_PRICE))
        embed.add_field(name="Tickets sold", value=f"{total:,} ({len(tickets)} players)")
        chance = f" ({mine / total * 100:.1f}% chance)" if mine else ""
        embed.add_field(name="Your tickets", value=f"{mine:,}{chance}")
        await interaction.response.send_message(embed=embed)

    @lottery.command(name="draw", description="Draw the lottery right now (admins only).")
    @admin_only()
    async def force_draw(self, interaction: discord.Interaction):
        embed, winner = await self.draw(interaction.guild_id)
        await interaction.response.send_message(**winner_tag(winner), embed=embed)


async def setup(bot):
    await bot.add_cog(Lottery(bot))
