"""Admin credit management and bot configuration."""
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.serverstats import MIN_PLAYS_FOR_ODDS, ODDS_GAMES, game_name
from utils.checks import admin_only, gambling_channels
from utils.economy import fmt, send_error
from utils.shop import ITEMS, PRICE_LABELS, get_prices, item_price_text, price_text
from utils.views import ConfirmView

MAX_AMOUNT = 10**15


def admin_embed(text: str, color=discord.Color.blurple()) -> discord.Embed:
    return discord.Embed(description=text, color=color)


class Admin(commands.Cog):
    admin = app_commands.Group(name="admin", description="Manage player credits (admins only).")
    cfg = app_commands.Group(name="config", description="Configure the bot (admins only).")

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------ credits
    @staticmethod
    async def refuse_bot(interaction: discord.Interaction, user: discord.Member) -> bool:
        """Bots (this one included) aren't players: credits given to one would just be gone from the game."""
        if user.bot:
            await send_error(interaction, "Bots can't have credits. Pick a player.")
            return True
        return False

    @admin.command(name="give", description="Give credits to a player.")
    @admin_only()
    async def give(self, interaction: discord.Interaction, user: discord.Member,
                   amount: app_commands.Range[int, 1, MAX_AMOUNT]):
        if await self.refuse_bot(interaction, user):
            return
        bal = await self.bot.db.add_balance(interaction.guild_id, user.id, amount)
        await interaction.response.send_message(embed=admin_embed(
            f"➕ Gave {fmt(amount)} to {user.mention}. New balance: {fmt(bal)}", discord.Color.green()))

    @admin.command(name="remove", description="Remove credits from a player (balance won't go below 0).")
    @admin_only()
    async def remove(self, interaction: discord.Interaction, user: discord.Member,
                     amount: app_commands.Range[int, 1, MAX_AMOUNT]):
        if await self.refuse_bot(interaction, user):
            return
        bal = await self.bot.db.add_balance(interaction.guild_id, user.id, -amount)
        await interaction.response.send_message(embed=admin_embed(
            f"➖ Removed {fmt(amount)} from {user.mention}. New balance: {fmt(bal)}", discord.Color.orange()))

    @admin.command(name="set", description="Set a player's balance to an exact amount.")
    @admin_only()
    async def set_(self, interaction: discord.Interaction, user: discord.Member,
                   amount: app_commands.Range[int, 0, MAX_AMOUNT]):
        if await self.refuse_bot(interaction, user):
            return
        await self.bot.db.set_balance(interaction.guild_id, user.id, amount)
        await interaction.response.send_message(embed=admin_embed(
            f"✏️ Set {user.mention}'s balance to {fmt(amount)}."))

    async def _everyone(self, guild: discord.Guild) -> list[int]:
        if not guild.chunked:
            await guild.chunk()
        return [m.id for m in guild.members if not m.bot]

    async def _bulk(self, interaction: discord.Interaction, amount: int, give: bool):
        members = await self._everyone(interaction.guild)
        verb = "give" if give else "remove"
        prep = "to" if give else "from"
        view = ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            embed=admin_embed(
                f"⚠️ Are you sure you want to **{verb} {fmt(amount)} {prep} all {len(members):,} members**?"
                + ("" if give else "\nBalances will not go below 0."),
                discord.Color.orange(),
            ),
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.value:
            msg = "Cancelled." if view.value is False else "Timed out — nothing was changed."
            await interaction.edit_original_response(embed=admin_embed(f"✖️ {msg}"), view=None)
            return
        await self.bot.db.bulk_add(interaction.guild_id, members, amount if give else -amount)
        await interaction.edit_original_response(embed=admin_embed("✅ Done."), view=None)
        text = (f"📢 {interaction.user.mention} gave {fmt(amount)} to **everyone**!" if give
                else f"📢 {interaction.user.mention} removed {fmt(amount)} from **everyone**.")
        await interaction.followup.send(embed=admin_embed(
            text, discord.Color.green() if give else discord.Color.red()))

    @admin.command(name="give-all", description="Give credits to every member (asks for confirmation).")
    @admin_only()
    async def give_all(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, MAX_AMOUNT]):
        await self._bulk(interaction, amount, give=True)

    @admin.command(name="remove-all", description="Remove credits from every member (asks for confirmation).")
    @admin_only()
    async def remove_all(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, MAX_AMOUNT]):
        await self._bulk(interaction, amount, give=False)

    # ------------------------------------------------------------- config
    async def _toggle(self, interaction: discord.Interaction, key: str, value: int, add: bool,
                      label: str) -> None:
        current = set(await self.bot.db.get_setting(interaction.guild_id, key, []))
        if add:
            current.add(value)
        else:
            current.discard(value)
        await self.bot.db.set_setting(interaction.guild_id, key, sorted(current))
        await interaction.response.send_message(
            embed=admin_embed(f"✅ {label} {'added' if add else 'removed'}."), ephemeral=True)

    @cfg.command(name="channel-add", description="Allow games in a channel.")
    @admin_only()
    async def channel_add(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self._toggle(interaction, "gambling_channels", channel.id, True, f"Gambling channel {channel.mention}")

    @cfg.command(name="channel-remove", description="Stop allowing games in a channel.")
    @admin_only()
    async def channel_remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self._toggle(interaction, "gambling_channels", channel.id, False, f"Gambling channel {channel.mention}")

    @cfg.command(name="adminrole-add", description="Give a role access to admin commands.")
    @admin_only()
    async def role_add(self, interaction: discord.Interaction, role: discord.Role):
        await self._toggle(interaction, "admin_roles", role.id, True, f"Admin role {role.mention}")

    @cfg.command(name="adminrole-remove", description="Remove a role's access to admin commands.")
    @admin_only()
    async def role_remove(self, interaction: discord.Interaction, role: discord.Role):
        await self._toggle(interaction, "admin_roles", role.id, False, f"Admin role {role.mention}")

    @cfg.command(name="adminuser-add", description="Give a user access to admin commands.")
    @admin_only()
    async def user_add(self, interaction: discord.Interaction, user: discord.Member):
        await self._toggle(interaction, "admin_users", user.id, True, f"Admin user {user.mention}")

    @cfg.command(name="adminuser-remove", description="Remove a user's access to admin commands.")
    @admin_only()
    async def user_remove(self, interaction: discord.Interaction, user: discord.Member):
        await self._toggle(interaction, "admin_users", user.id, False, f"Admin user {user.mention}")

    @cfg.command(name="shop-price", description="Set the price of a shop item (0 takes it off sale).")
    @app_commands.describe(item="The shop item (mute and deafen share one price per duration)",
                           price="New price in credits; 0 = not for sale")
    @app_commands.choices(item=[app_commands.Choice(name=label, value=key) for key, label in PRICE_LABELS.items()])
    @admin_only()
    async def shop_price(self, interaction: discord.Interaction, item: app_commands.Choice[str],
                         price: app_commands.Range[int, 0, MAX_AMOUNT]):
        prices = await self.bot.db.get_setting(interaction.guild_id, "shop_prices", {})
        prices[item.value] = price
        await self.bot.db.set_setting(interaction.guild_id, "shop_prices", prices)
        await interaction.response.send_message(embed=admin_embed(
            f"✅ **{item.name}**: {price_text(price)}"), ephemeral=True)

    @cfg.command(name="odds-reset", description="A game's odds changed: stats highlights count only its plays from now on.")
    @app_commands.describe(game="The game whose odds or drop rates changed")
    @app_commands.choices(game=[app_commands.Choice(name=game_name(g), value=g) for g in ODDS_GAMES])
    @admin_only()
    async def odds_reset(self, interaction: discord.Interaction, game: app_commands.Choice[str]):
        resets = await self.bot.db.get_setting(interaction.guild_id, "odds_reset", {})
        now = time.time()
        resets[game.value] = now
        await self.bot.db.set_setting(interaction.guild_id, "odds_reset", resets)
        await interaction.response.send_message(embed=admin_embed(
            f"✅ **{game.name}**: odds marked as changed <t:{int(now)}:R>. In `/serverstats`, \"Best game for the "
            f"house/players\" now counts only its plays from here on (it needs {MIN_PLAYS_FOR_ODDS} plays to be "
            f"named again). Totals and charts keep the full history."), ephemeral=True)

    @cfg.command(name="show", description="Show the current bot configuration.")
    @admin_only()
    async def show(self, interaction: discord.Interaction):
        db, gid = self.bot.db, interaction.guild_id
        channels = await gambling_channels(self.bot, gid)
        roles = config.ADMIN_ROLE_IDS | set(await db.get_setting(gid, "admin_roles", []))
        users = config.ADMIN_USER_IDS | set(await db.get_setting(gid, "admin_users", []))
        embed = discord.Embed(title="⚙️ Configuration", color=discord.Color.blurple())
        embed.add_field(name="Gambling channels",
                        value=", ".join(f"<#{c}>" for c in channels) or "None — games are disabled", inline=False)
        embed.add_field(name="Admin roles", value=", ".join(f"<@&{r}>" for r in roles) or "None", inline=False)
        embed.add_field(name="Admin users",
                        value=", ".join(f"<@{u}>" for u in users) or "None (server owner is always admin)",
                        inline=False)
        embed.add_field(name="Economy", value=(
            f"Starting balance: {fmt(config.STARTING_BALANCE)}\n"
            f"Daily: {fmt(config.DAILY_AMOUNT)}\n"
            f"Min bet: {fmt(config.MIN_BET)}\n"
            f"Max bet: {fmt(config.MAX_BET) if config.MAX_BET else 'unlimited'}\n"
            f"House edge: {config.HOUSE_EDGE * 100:g}% · Poker rake: {config.POKER_RAKE * 100:g}%"
        ), inline=False)
        prices = await get_prices(db, gid)
        embed.add_field(name="Shop prices", value="\n".join(
            f"{i.emoji} {i.name}: {item_price_text(i, prices)}" for i in ITEMS.values()), inline=False)
        resets = await db.get_setting(gid, "odds_reset", {})
        if resets:
            embed.add_field(name="Odds reset (stats highlights count plays since)", value="\n".join(
                f"{game_name(g)}: <t:{int(ts)}:f>" for g, ts in sorted(resets.items(), key=lambda kv: kv[1])),
                inline=False)
        embed.set_footer(text="Values from .env can only be changed in the .env file.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
