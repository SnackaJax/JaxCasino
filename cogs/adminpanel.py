"""Starts the admin panel (admin_panel/server.py) with the bot when ADMIN_PANEL_ENABLED=1."""
import logging
from pathlib import Path

from discord.ext import commands

import config
from admin_panel.server import AdminPanel

log = logging.getLogger("jaxcasino.adminpanel")


class BotDirectory:
    """Server and player names for the panel, from the bot's member cache (no Discord requests)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._owners: set[int] | None = None

    def client_id(self) -> int | None:
        return self.bot.application_id

    async def owner_ids(self) -> set[int]:
        """The bot's owner in the Developer Portal, or every member of its team (asked once, then remembered)."""
        if self._owners is None:
            info = await self.bot.application_info()
            owners = {info.owner.id} if info.owner else set()
            if info.team:
                owners |= {m.id for m in info.team.members}
            self._owners = owners
        return self._owners

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

    async def setting_changed(self, guild_id: int, key: str, value) -> None:
        """A game switch changed in the panel (it's already saved): the running games pick it up now."""
        if key == "bj_double_side":
            server = getattr(self.bot.get_cog("SpinFrenzy"), "server", None)
            if server is not None:
                await server.blackjack.set_double_side(guild_id, bool(value))

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
        self.panel = AdminPanel(self.bot.db, config.ADMIN_PANEL_PASSWORD, BotDirectory(self.bot),
                                discord_secret=config.DISCORD_CLIENT_SECRET,
                                discord_users=frozenset(config.ADMIN_PANEL_USERS),
                                password_login=config.ADMIN_PANEL_PASSWORD_LOGIN,
                                state_path=Path(config.ADMIN_PANEL_STATE))
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
