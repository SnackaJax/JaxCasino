"""Starts the admin panel (admin_panel/server.py) with the bot when ADMIN_PANEL_ENABLED=1."""
import logging

from discord.ext import commands

import config
from admin_panel.server import AdminPanel

log = logging.getLogger("jaxcasino.adminpanel")


class BotDirectory:
    """Server and player names for the panel, from the bot's member cache (no Discord requests)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def guilds(self) -> dict[int, dict]:
        return {g.id: {"name": g.name, "icon": g.icon.with_size(64).url if g.icon else None}
                for g in self.bot.guilds}

    def member(self, guild_id: int, user_id: int) -> dict | None:
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if member is not None:
            return {"name": member.display_name, "username": member.name,
                    "avatar": member.display_avatar.with_size(64).with_static_format("png").url, "in_server": True}
        user = self.bot.get_user(user_id)
        if user is not None:
            return {"name": user.display_name, "username": user.name,
                    "avatar": user.display_avatar.with_size(64).with_static_format("png").url, "in_server": False}
        return None

    async def crux_started(self, guild_id: int, user_id: int, until: float) -> None:
        """A crux given in the panel: their open casino games close and show the crux screen."""
        from cogs.crux import kick_from_activity
        await kick_from_activity(self.bot, guild_id, user_id, until)


class AdminPanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel: AdminPanel | None = None

    async def cog_load(self) -> None:
        if not config.ADMIN_PANEL_ENABLED:
            return
        if not config.ADMIN_PANEL_PASSWORD:
            log.warning("The admin panel is on (ADMIN_PANEL_ENABLED=1) but ADMIN_PANEL_PASSWORD is empty, so it "
                        "wasn't started. Set a password in .env.")
            return
        self.panel = AdminPanel(self.bot.db, config.ADMIN_PANEL_PASSWORD, BotDirectory(self.bot))
        try:
            await self.panel.start(config.ADMIN_PANEL_HOST, config.ADMIN_PANEL_PORT)
        except OSError as e:
            log.error("Couldn't start the admin panel on %s:%s: %s", config.ADMIN_PANEL_HOST,
                      config.ADMIN_PANEL_PORT, e)
            await self.panel.stop()
            self.panel = None

    async def cog_unload(self) -> None:
        if self.panel:
            await self.panel.stop()


async def setup(bot):
    await bot.add_cog(AdminPanelCog(bot))
