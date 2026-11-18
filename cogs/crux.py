"""/crux: take a break from the bot and the casino Activity for a while. Admins can end it early with
/admin uncrux or in the admin panel; the player can't."""
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.crux import MAX_SECONDS, MIN_SECONDS, crux_embed, describe, parse_duration
from utils.economy import send_error
from utils.views import ConfirmView

log = logging.getLogger("jaxcasino.crux")


async def kick_from_activity(bot, guild_id: int, user_id: int, until: float) -> None:
    """Closes the player's open casino games: they show the crux screen."""
    cog = bot.get_cog("SpinFrenzy")
    server = getattr(cog, "server", None)
    if server is not None:
        try:
            await server.kick_crux(guild_id, user_id, until)
        except Exception:
            log.exception("Couldn't close %s's casino games for their crux", user_id)


class Crux(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="crux", description="Take a break from the casino: no games or commands until it ends.")
    @app_commands.describe(duration="How long, for example 2h, 3d, 1w or 1d12h (10 minutes to a year)")
    async def crux(self, interaction: discord.Interaction, duration: str):
        gid, uid = interaction.guild_id, interaction.user.id
        seconds = parse_duration(duration)
        if seconds is None:
            await send_error(interaction, "I can't read that duration. Try something like `2h`, `3d`, `1w` or `1d12h`.")
            return
        if seconds < MIN_SECONDS or seconds > MAX_SECONDS:
            await send_error(interaction, "A crux lasts from 10 minutes up to a year.")
            return
        now = time.time()
        until = now + seconds
        current = await self.bot.db.crux_until(gid, uid)
        if current and until <= current:
            # already on a break: it can only get longer, never shorter
            embed = crux_embed(current)
            embed.set_footer(text="A running crux can be made longer, not shorter.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        what = "make your crux longer" if current else "start a crux"
        view = ConfirmView(uid, timeout=60)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🧊 Crux",
                description=(f"Are you sure you want to {what}? For **{describe(seconds)}**, until "
                             f"<t:{int(until)}:F>, you can't play any game here, open the casino Activity or use "
                             "the bot's other commands.\n\n**You can't undo this yourself**: only a server admin "
                             "can end it early."),
                color=discord.Color.orange()),
            view=view, ephemeral=True)
        await view.wait()
        if not view.value:
            msg = "Cancelled: no crux." if view.value is False else "Timed out: no crux."
            await interaction.edit_original_response(embed=discord.Embed(description=msg), view=None)
            return
        until = time.time() + seconds
        await self.bot.db.set_crux(gid, uid, until)
        log.info("Crux: user %s in guild %s until %s (%s)", uid, gid, time.strftime("%Y-%m-%d %H:%M",
                 time.localtime(until)), describe(seconds))
        await kick_from_activity(self.bot, gid, uid, until)
        embed = crux_embed(until)
        embed.title = "🧊 Crux started"
        await interaction.edit_original_response(embed=embed, view=None)


async def setup(bot):
    await bot.add_cog(Crux(bot))
