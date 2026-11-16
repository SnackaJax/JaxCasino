"""Permission and channel checks for slash commands."""
import discord
from discord import app_commands

import config


class NotAdmin(app_commands.CheckFailure):
    pass


class WrongChannel(app_commands.CheckFailure):
    def __init__(self, channels: set[int]):
        super().__init__()
        self.channels = channels


async def is_admin(bot, user: discord.abc.User, guild: discord.Guild | None) -> bool:
    if guild is None:
        return False
    if user.id == guild.owner_id or user.id in config.ADMIN_USER_IDS:
        return True
    if user.id in await bot.db.get_setting(guild.id, "admin_users", []):
        return True
    roles = config.ADMIN_ROLE_IDS | set(await bot.db.get_setting(guild.id, "admin_roles", []))
    member = user if isinstance(user, discord.Member) else guild.get_member(user.id)
    return bool(member and any(r.id in roles for r in member.roles))


async def gambling_channels(bot, guild_id: int) -> set[int]:
    return config.GAMBLING_CHANNEL_IDS | set(await bot.db.get_setting(guild_id, "gambling_channels", []))


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await is_admin(interaction.client, interaction.user, interaction.guild):
            return True
        raise NotAdmin()

    return app_commands.check(predicate)


def gambling_only():
    """Only allows the command in a configured gambling channel (or a thread inside one)."""

    async def predicate(interaction: discord.Interaction) -> bool:
        channels = await gambling_channels(interaction.client, interaction.guild_id)
        ch = interaction.channel
        if {ch.id, getattr(ch, "parent_id", None)} & channels:
            return True
        raise WrongChannel(channels)

    return app_commands.check(predicate)
