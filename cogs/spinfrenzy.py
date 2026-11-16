"""The bot's Discord Activity (the casino): Spin Frenzy, Banger Bash, poker and blackjack, played inside Discord.

This cog starts the Activity's web server (activity/server.py) together with the bot, so the games use the
same database and credits, and adds /casino, which opens the Activity (on its game menu, or straight into one
game). Setup steps are in the README.
"""
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from activity.server import ActivityServer, Settings
from utils.economy import send_error

log = logging.getLogger("gamblebot.spinfrenzy")

# games /casino can open directly (activity.server.GAME_PAGES has their pages)
GAMES = {"spinfrenzy": "Spin Frenzy", "bangerbash": "Banger Bash", "poker": "Poker", "blackjack": "Blackjack",
         **({"reefrush": "Reef Rush"} if config.REEF_RUSH_ENABLED else {}),
         **({"raccoonheist": "Raccoon Heist"} if config.RACCOON_HEIST_ENABLED else {})}


class SpinFrenzy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.server: ActivityServer | None = None
        self._embedded: bool | None = None
        self._embedded_checked = 0.0

    async def cog_load(self) -> None:
        if not config.ACTIVITY_ENABLED:
            log.info("Spin Frenzy Activity is turned off (ACTIVITY_ENABLED=0).")
            return
        if not config.DISCORD_CLIENT_SECRET:
            log.warning("DISCORD_CLIENT_SECRET is not set: players won't be able to sign in to the Spin Frenzy "
                        "Activity until it is (see the README).")
        settings = Settings(rtp=config.SPIN_FRENZY_RTP, bonus_weight=config.SPIN_FRENZY_BONUS_WEIGHT,
                            bonus_rtp=config.SPIN_FRENZY_BONUS_PAYBACK,
                            bet_seconds=config.SPIN_FRENZY_BET_SECONDS,
                            max_win_mult=config.SPIN_FRENZY_MAX_WIN, min_bet=config.MIN_BET,
                            max_bet=config.MAX_BET)
        self.server = ActivityServer(self.bot.db, settings, self.resolve_member, config.DISCORD_CLIENT_SECRET,
                                     config.TOKEN, lambda: self.bot.application_id)
        try:
            await self.server.start(config.ACTIVITY_HOST, config.ACTIVITY_PORT)
        except OSError as e:
            log.error("Couldn't start the Spin Frenzy Activity server on %s:%s: %s", config.ACTIVITY_HOST,
                      config.ACTIVITY_PORT, e)
            await self.server.stop()
            self.server = None

    async def cog_unload(self) -> None:
        if self.server:
            await self.server.stop()

    async def resolve_member(self, guild_id: int, user_id: int) -> dict | None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        avatar = member.display_avatar.with_size(64).with_static_format("png")
        return {"name": member.display_name, "avatar_url": avatar.url}

    @app_commands.command(name="casino", description="Open the casino: Spin Frenzy, Banger Bash, poker, "
                                                     "blackjack and more, right here in Discord")
    @app_commands.describe(game="Go straight to one game (leave empty for the game menu)")
    @app_commands.choices(game=[app_commands.Choice(name=name, value=key) for key, name in GAMES.items()])
    async def casino(self, interaction: discord.Interaction, game: app_commands.Choice[str] | None = None):
        if game is None:
            await self.launch(interaction, None, "The casino")
        else:
            await self.launch(interaction, game.value, game.name)

    async def launch(self, interaction: discord.Interaction, game: str | None = None, name: str = "the games") -> None:
        """Opens the Activity for this player. With `game`, the menu opens that game straight away (the menu asks
        the web server which game the player launched; see ActivityServer.want_game)."""
        if self.server is None:
            await send_error(interaction, f"The Activity with {name} isn't running on this bot right now.")
            return
        if not await self.activities_enabled():
            await send_error(interaction, f"{name} isn't switched on for this bot yet. The bot owner needs to "
                                          "turn on **Enable Activities** in the Discord Developer Portal "
                                          "(Activities → Settings).")
            return
        if game:
            self.server.want_game(interaction.user.id, game)
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as e:
            log.warning("Couldn't launch the Activity (%s): %s", game or "menu", e)
            # A rejected launch usually uses up the interaction, so this reply may not get through.
            try:
                await send_error(interaction, "Couldn't open the Activity here. Join a voice channel and start "
                                              "it from the 🚀 Activities button in the call.")
            except discord.HTTPException:
                pass

    async def activities_enabled(self) -> bool:
        """Whether Activities are turned on for the app (the EMBEDDED flag). Rechecked at most once a minute,
        so turning it on in the Developer Portal works without restarting the bot."""
        now = time.monotonic()
        if self._embedded is None or now - self._embedded_checked > 60:
            try:
                info = await self.bot.application_info()
                self._embedded = info.flags.embedded
                self._embedded_checked = now
            except discord.HTTPException:
                return True  # can't tell; just try the launch
        return self._embedded


async def setup(bot):
    await bot.add_cog(SpinFrenzy(bot))
