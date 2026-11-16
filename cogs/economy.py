"""Player economy: balance, daily reward, paying others, leaderboard, stats and help."""
import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.economy import BetError, fmt, parse_amount, send_error
from utils.restart import send_panel

HELP_TEXT = {
    "💰 Economy": (
        "`/balance` · `/claim` · `/pay` · `/leaderboard` · `/stats` · `/serverstats`\n"
        "`/shop` · `/buy` — spend credits on a chicken, voice disconnects, mutes, deafens, timeouts, nicknames "
        "or a secret random kick\n"
        f"Broke? Press the restart button for {config.RESTART_AMOUNT:,} credits (or use `/restart`)."
    ),
    "⚡ Instant games": (
        "`/coinflip` — heads or tails\n"
        "`/dice` — roll over/under a target\n"
        "`/roulette` — European roulette\n"
        "`/wheel` — wheel of fortune\n"
        "`/cockfight` — bet on your chicken (`/buy chicken`); it gets stronger with every win"
    ),
    "🎮 Button games": (
        "`/blackjack` — hit, stand, double, split\n"
        "`/higherlower` — guess the next card, cash out any time\n"
        "`/mines` — avoid the mines, cash out any time\n"
        "`/crash` — cash out before it crashes\n"
        "`/tower` — climb floors, avoid the traps\n"
        "`/videopoker` — Jacks or Better\n"
        "`/slots` — Cosmic Lab: 6×4 reels, 20 lines, free spins with sticky wilds (`/paytable`)"
    ),
    "👥 Multiplayer": (
        "`/duel` — challenge someone (coinflip, dice, RPS)\n"
        "`/horserace` — open a race everyone can bet on\n"
        "`/lottery buy` · `/lottery info` — shared jackpot\n"
        "`/poker create` — Texas Hold'em table in this channel\n"
        "`/casino` — the casino inside Discord: Spin Frenzy (live wheel show), Banger Bash (barbecue slot, "
        "`/bangerbash-paytable`) and poker tables. Pick a game to jump straight in"
    ),
}


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance", description="Check your (or someone else's) balance.")
    async def balance(self, interaction: discord.Interaction, user: discord.Member | None = None):
        user = user or interaction.user
        bal = await self.bot.db.get_balance(interaction.guild_id, user.id)
        embed = discord.Embed(description=f"{user.mention} has {fmt(bal)}", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="claim", description=f"Claim your {config.DAILY_AMOUNT:,} credits (once every 24 hours).")
    async def claim(self, interaction: discord.Interaction):
        ok, next_at, bal = await self.bot.db.claim_daily(interaction.guild_id, interaction.user.id)
        if not ok:
            await send_error(interaction, f"You already claimed today. Come back <t:{int(next_at)}:R>.")
            return
        embed = discord.Embed(
            title="🎁 Daily reward",
            description=f"You received {fmt(config.DAILY_AMOUNT)}!\nBalance: {fmt(bal)}",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="restart",
                          description=f"Broke? Press a button to get {config.RESTART_AMOUNT:,} credits.")
    async def restart(self, interaction: discord.Interaction):
        await send_panel(interaction)

    @app_commands.command(name="pay", description="Send credits to another player.")
    @app_commands.describe(user="Who to pay", amount="Any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all")
    async def pay(self, interaction: discord.Interaction, user: discord.Member, amount: str):
        if user.bot or user.id == interaction.user.id:
            await send_error(interaction, "You can't pay yourself or a bot.")
            return
        db = self.bot.db
        bal = await db.get_balance(interaction.guild_id, interaction.user.id)
        try:
            value = parse_amount(amount, bal)
        except BetError as e:
            await send_error(interaction, str(e))
            return
        if value <= 0 or not await db.transfer(interaction.guild_id, interaction.user.id, user.id, value):
            await send_error(interaction, f"You can't afford that. Your balance is {fmt(bal)}.")
            return
        embed = discord.Embed(
            description=f"💸 {interaction.user.mention} paid {fmt(value)} to {user.mention}.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="The richest players on this server.")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await self.bot.db.leaderboard(interaction.guild_id, 10)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'`#{i + 1}`'} <@{r['user_id']}> — {fmt(r['balance'])}"
            for i, r in enumerate(rows)
        ]
        embed = discord.Embed(
            title="🏆 Leaderboard",
            description="\n".join(lines) or "Nobody has played yet.",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    # /stats lives in cogs/playerstats.py (balance graph and stats per period).

    @app_commands.command(name="help", description="List all games and commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎰 JaxCasino",
            description=(
                f"New players start with {fmt(config.STARTING_BALANCE)}. "
                f"Minimum bet is {fmt(config.MIN_BET)}. Bet any whole amount (like `1234`), "
                f"or use a shortcut: `2.5k`, `1m`, `half`, `all`."
            ),
            color=discord.Color.purple(),
        )
        for name, value in HELP_TEXT.items():
            embed.add_field(name=name, value=value, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Economy(bot))
