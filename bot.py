"""JaxCasino entry point. Run with:  python bot.py"""
import logging
import logging.handlers
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

import config
from database import Database
from utils.checks import NotAdmin, WrongChannel
from utils.economy import refund_unposted, send_error

log = logging.getLogger("jaxcasino")

COGS = [
    "cogs.economy",
    "cogs.admin",
    "cogs.instant",
    "cogs.slots",
    "cogs.bangerbash",
    "cogs.blackjack",
    "cogs.higherlower",
    "cogs.mines",
    "cogs.crash",
    "cogs.tower",
    "cogs.videopoker",
    "cogs.duel",
    "cogs.horserace",
    "cogs.lottery",
    "cogs.poker",
    "cogs.serverstats",
    "cogs.playerstats",
    "cogs.cockfight",
    "cogs.shop",
    "cogs.spinfrenzy",
]


def setup_logging() -> None:
    """Console at LOG_LEVEL, plus a rotating log file that always has full detail."""
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        sys.stdout.reconfigure(errors="backslashreplace")  # don't choke on emoji in names
    except (AttributeError, ValueError):
        pass
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    console.setFormatter(formatter)
    handlers = [console]
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        file = logging.handlers.RotatingFileHandler(
            os.path.join(config.LOG_DIR, "jaxcasino.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        file.setLevel(logging.DEBUG)
        file.setFormatter(formatter)
        handlers.append(file)
    except OSError as e:
        print(f"Could not open the log file in {config.LOG_DIR!r}: {e}")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in handlers:
        root.addHandler(h)
    # discord.py's own debug output is a flood of gateway events; keep it at INFO.
    logging.getLogger("discord").setLevel(logging.INFO)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.INFO)


def _options(options: list | None) -> str:
    parts = []
    for o in options or []:
        if "options" in o:  # subcommand or group
            parts.append(o["name"])
            parts.append(_options(o["options"]))
        else:
            parts.append(f"{o['name']}={o.get('value')}")
    return " ".join(p for p in parts if p)


def _button_label(interaction: discord.Interaction) -> str:
    custom_id = (interaction.data or {}).get("custom_id")
    if interaction.message is not None:
        for row in interaction.message.components:
            for c in getattr(row, "children", []):
                if getattr(c, "custom_id", None) == custom_id:
                    return c.label or (str(c.emoji) if c.emoji else custom_id)
    return str(custom_id)


class GambleTree(app_commands.CommandTree):
    async def sync(self, *, guild=None):
        """Global sync that keeps the Activity's "Launch" command. Discord creates that Entry Point command when
        Activities are turned on, discord.py doesn't know about it, and a bulk overwrite without it fails."""
        if guild is not None or self.client.application_id is None:
            return await super().sync(guild=guild)
        app_id = self.client.application_id
        existing = await self._http.get_global_commands(app_id)
        entry_points = [c for c in existing if c.get("type") == 4]
        if not entry_points:
            return await super().sync()
        payload = [c.to_dict(self) for c in self._get_all_commands()]
        keep = ("id", "name", "type", "description", "handler", "integration_types", "contexts",
                "name_localizations", "description_localizations", "nsfw")
        payload += [{k: c[k] for k in keep if k in c} for c in entry_points]
        data = await self._http.bulk_upsert_global_commands(app_id, payload=payload)
        return [app_commands.AppCommand(data=d, state=self._state) for d in data if d.get("type") != 4]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("This bot only works inside a server.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await refund_unposted(interaction)
        if isinstance(error, NotAdmin):
            msg = "You don't have permission to use this command."
        elif isinstance(error, WrongChannel):
            if error.channels:
                msg = "Games can only be played in " + ", ".join(f"<#{c}>" for c in sorted(error.channels)) + "."
            else:
                msg = "No gambling channel has been set up yet. An admin can add one with `/config channel-add`."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use this command here."
        else:
            log.exception("Error in command %s", interaction.command and interaction.command.qualified_name,
                          exc_info=error)
            msg = "Something went wrong while running that command."
        try:
            await send_error(interaction, msg)
        except discord.HTTPException:
            pass


class JaxCasino(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # needed for /admin give-all and remove-all
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, tree_cls=GambleTree)
        self.db = Database(config.DATABASE_PATH)

    async def setup_hook(self) -> None:
        await self.db.connect()
        refunded = await self.db.refund_all_escrow()
        if refunded:
            log.info("Refunded %d bet(s) from games interrupted by the last shutdown.", len(refunded))
        for cog in COGS:
            await self.load_extension(cog)
        if config.GUILD_ID:
            guild = discord.Object(config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to guild %s.", len(synced), config.GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands (may take up to an hour to appear).", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Logs every command and button press, so a game that gets stuck can be traced."""
        try:
            where = f"#{getattr(interaction.channel, 'name', interaction.channel_id)}"
            who = f"{interaction.user} ({interaction.user.id})"
            data = interaction.data or {}
            if interaction.type is discord.InteractionType.application_command:
                log.info("/%s %s by %s in %s", data.get("name"), _options(data.get("options")), who, where)
            elif interaction.type is discord.InteractionType.component:
                log.debug("Button %r on message %s pressed by %s in %s", _button_label(interaction),
                          interaction.message and interaction.message.id, who, where)
            elif interaction.type is discord.InteractionType.modal_submit:
                values = [c.get("value") for row in data.get("components", []) for c in row.get("components", [])]
                log.debug("Form %s submitted by %s in %s: %s", data.get("custom_id"), who, where, values)
        except Exception:
            log.exception("Failed to log an interaction")

    async def close(self) -> None:
        await super().close()
        await self.db.close()


def main() -> None:
    if not config.TOKEN:
        print("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in.")
        sys.exit(1)
    setup_logging()
    try:
        JaxCasino().run(config.TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord didn't accept DISCORD_TOKEN. Copy the bot token again from the Developer Portal "
                  "(Bot -> Reset Token) into .env.")
        sys.exit(1)


if __name__ == "__main__":
    main()
