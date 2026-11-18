"""/serverstats: a stats page for the whole server — how much was wagered, paid out, won and lost."""
import asyncio
import datetime as dt
import io
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.economy import fmt
from utils.stats_image import StatsInfo, render_stats, short
from utils.views import OwnedView

log = logging.getLogger("jaxcasino.serverstats")

GAME_NAMES = {
    "coinflip": "Coinflip", "dice": "Dice", "slots": "Slots", "roulette": "Roulette", "wheel": "Wheel",
    "blackjack": "Blackjack", "higherlower": "Higher/Lower", "mines": "Mines", "crash": "Crash",
    "tower": "Tower", "videopoker": "Video poker", "duel": "Duels", "horserace": "Horse racing",
    "lottery": "Lottery", "poker": "Poker", "cockfight": "Cockfight", "bangerbash": "Banger Bash", "reefrush": "Reef Rush",
    "raccoonheist": "Raccoon Heist",
    "spinfrenzy": "Spin Frenzy",
}
# key: (button label, header label, seconds back or 0 for all time, days in the per-day chart)
PERIODS = {
    "day": ("24 hours", "LAST 24 HOURS", 86400, 14),
    "week": ("7 days", "LAST 7 DAYS", 7 * 86400, 14),
    "month": ("30 days", "LAST 30 DAYS", 30 * 86400, 30),
    "all": ("All time", "ALL TIME", 0, 30),
}
MAX_GAME_ROWS = 11
# A game needs this many plays (since its odds last changed) to be named best game for the house / players,
# so one lucky hit on a barely played game doesn't top the list.
MIN_PLAYS_FOR_ODDS = 20
# Games whose odds can be reset with /config odds-reset (poker and duels are player vs player).
ODDS_GAMES = [g for g in GAME_NAMES if g not in ("poker", "duel")]


def game_name(key: str) -> str:
    return GAME_NAMES.get(key, key.title())


def signed(n: int) -> str:
    return f"+{n:,}" if n > 0 else f"−{-n:,}" if n < 0 else "0"


def pct(part: float, whole: float) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


def build_info(server: str, period: str, st: dict, circulation: list, period_start: float) -> StatsInfo:
    _, header, _, days = PERIODS[period]
    profit = st["wagered"] - st["paid"]
    if st["wagered"]:
        edge = f"kept {pct(profit, st['wagered'])} of all bets" if profit >= 0 else "players are ahead!"
    else:
        edge = "no bets yet"
    kpis = [
        ("Wagered", short(st["wagered"]), f"{st['wagered']:,} credits"),
        ("Paid out", short(st["paid"]), f"{st['paid']:,} credits"),
        ("House profit", ("−" if profit < 0 else "") + short(abs(profit)), edge),
        ("Games played", f"{st['games']:,}", f"by {st['players']:,} player{'s' if st['players'] != 1 else ''}"),
        ("Player win rate", pct(st["wins"], st["games"]), f"{st['wins']:,} games won"),
    ]
    rows = [(game_name(g["game"]), g["wagered"], g["plays"]) for g in st["per_game"] if g["wagered"]]
    if len(rows) > MAX_GAME_ROWS:  # fold the tail into "Other" rather than shrinking every row
        rest = rows[MAX_GAME_ROWS - 1:]
        rows = rows[:MAX_GAME_ROWS - 1] + [("Other", sum(r[1] for r in rest), sum(r[2] for r in rest))]

    today = dt.date.today()
    daily = []
    for i in range(days - 1, -1, -1):
        day = today - dt.timedelta(days=i)
        daily.append((f"{day.day} {day:%b}", st["daily"].get(day.isoformat(), (0, 0))[0]))
    hours = [st["hours"].get(h, 0) for h in range(24)]
    since = st["tracking_since"]
    footer = (f"Stats tracked since {dt.datetime.fromtimestamp(since):%d %b %Y}" if since
              else "Stats are tracked from the first game played")
    if circulation:   # end the line at the live total
        circulation = circulation + [(time.time(), circulation[-1][1])]
    return StatsInfo(server=server, period=header, kpis=kpis, per_game=rows, daily=daily, hours=hours,
                     footer=footer, circulation=circulation, circulation_start=period_start or None)


def build_embed(period: str, st: dict) -> discord.Embed:
    label = PERIODS[period][0]
    embed = discord.Embed(title=f"📊 Server stats — {label.lower() if period != 'all' else 'all time'}",
                          color=discord.Color.gold())
    if not st["games"] and not st["paid"]:
        embed.description = "No games have been played in this period yet. Go make some history! 🎰"
    lines = []
    if w := st["biggest_win"]:
        mult = f" ({w['payout'] / w['wagered']:,.1f}x)" if w["wagered"] else ""
        lines.append(f"🏆 **Biggest win:** <@{w['user_id']}> won {fmt(w['profit'])} on {game_name(w['game'])}{mult}")
    if m := st["best_mult"]:
        if m["mult"] > 1:
            lines.append(f"🎯 **Best multiplier:** {m['mult']:,.2f}x on {game_name(m['game'])} by <@{m['user_id']}>")
    if lo := st["biggest_loss"]:
        lines.append(f"💸 **Biggest loss:** <@{lo['user_id']}> lost {fmt(lo['loss'])} on {game_name(lo['game'])}")
    if u := st["high_roller"]:
        if u["wagered"]:
            lines.append(f"🎲 **High roller:** <@{u['user_id']}> wagered {fmt(u['wagered'])}")
    if u := st["most_active"]:
        if u["plays"]:
            lines.append(f"🔥 **Most active:** <@{u['user_id']}> with {u['plays']:,} games")
    if u := st["luckiest"]:
        if u["net"] > 0:
            lines.append(f"🍀 **Luckiest:** <@{u['user_id']}> is up {fmt(u['net'])}")
    if u := st["unluckiest"]:
        if u["net"] < 0:
            lines.append(f"🥀 **Unluckiest:** <@{u['user_id']}> is down {fmt(-u['net'])}")
    # Judged on plays since each game's odds last changed, and only games with enough plays to mean something.
    played = [g for g in st["returns"] if g["wagered"] and g["plays"] >= MIN_PLAYS_FOR_ODDS
              and g["game"] not in ("poker", "duel")]
    if len(played) >= 2:
        best = max(played, key=lambda g: g["wagered"] - g["paid"])
        worst = min(played, key=lambda g: (g["wagered"] - g["paid"]) / g["wagered"])
        lines.append(f"🏠 **Best game for the house:** {game_name(best['game'])} "
                     f"({signed(best['wagered'] - best['paid'])} credits)")
        ret = worst["paid"] / worst["wagered"] * 100
        lines.append(f"🎁 **Best game for players:** {game_name(worst['game'])} (paid back {ret:.0f}% of bets)")
    if (k := st.get("top_streak")) and k["best"] >= 2:
        lines.append(f"🔥 **Longest winstreak:** <@{k['user_id']}> won {k['best']} in a row at {game_name(k['game'])}")
    if lines:
        embed.add_field(name="Highlights", value="\n".join(lines)[:1024], inline=False)

    money = st["money"]
    eco = [f"💰 **In circulation:** {fmt(st['circulating'])} across {st['accounts']:,} accounts"]
    if r := st["richest"]:
        eco.append(f"👑 **Richest:** <@{r['user_id']}> with {fmt(r['balance'])}")
    if c := money.get("claim"):
        eco.append(f"🎁 **Daily claims:** {fmt(c['amount'])} from {c['n']:,} claims")
    if r := money.get("restart"):
        eco.append(f"🔄 **Restarts:** {r['n']:,} broke player{'s' if r['n'] != 1 else ''} "
                   f"got back in with {fmt(r['amount'])}")
    if p := money.get("pay"):
        eco.append(f"🤝 **Player payments:** {fmt(p['amount'])} in {p['n']:,} payments")
    if a := money.get("admin"):
        parts = []
        if a["added"]:
            parts.append(f"{a['added']:,} given")
        if a["removed"]:
            parts.append(f"{a['removed']:,} removed")
        eco.append(f"🛠️ **Admin changes:** {' · '.join(parts)} ({a['n']:,} change{'s' if a['n'] != 1 else ''})")
    if s := money.get("shop"):
        if s["amount"] < 0:
            eco.append(f"🛒 **Shop:** {fmt(-s['amount'])} spent")
    if st["lottery_pot"]:
        eco.append(f"🎟️ **Lottery jackpot:** {fmt(st['lottery_pot'] + config.LOTTERY_BANK_BONUS)}")
    embed.add_field(name="Economy", value="\n".join(eco)[:1024], inline=False)
    embed.set_image(url="attachment://serverstats.png")
    embed.set_footer(text="In circulation, richest and jackpot are live totals; everything else covers the period.")
    return embed


class StatsView(OwnedView):
    def __init__(self, cog: "ServerStats", owner_id: int, period: str):
        super().__init__(owner_id, timeout=300)
        self.cog = cog
        self.period = period
        for key, (label, *_rest) in PERIODS.items():
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            button.callback = self.make_callback(key)
            button.period = key
            self.add_item(button)
        self.refresh()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Run `/serverstats` yourself to pick a different period.",
                                                    ephemeral=True)
            return False
        return True

    def refresh(self) -> None:
        for b in self.children:
            b.style = discord.ButtonStyle.primary if b.period == self.period else discord.ButtonStyle.secondary
            b.disabled = b.period == self.period

    def make_callback(self, period: str):
        async def callback(interaction: discord.Interaction):
            self.period = period
            self.refresh()
            await interaction.response.defer()
            embed, file = await self.cog.page(interaction.guild, period)
            await interaction.edit_original_response(embed=embed, attachments=[file] if file else [], view=self)
        return callback

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class ServerStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def page(self, guild: discord.Guild, period: str) -> tuple[discord.Embed, discord.File | None]:
        _, _, seconds, days = PERIODS[period]
        since = time.time() - seconds if seconds else 0
        st = await self.bot.db.server_stats(guild.id, since, days)
        st["top_streak"] = await self.bot.db.top_streak(guild.id, since)
        embed = build_embed(period, st)
        try:
            circulation = await self.bot.db.circulation_history(guild.id, since)
            png = await asyncio.to_thread(render_stats, build_info(guild.name, period, st, circulation, since))
        except Exception:
            log.exception("Failed to draw the server stats image")
            embed.set_image(url=None)
            return embed, None
        return embed, discord.File(io.BytesIO(png), filename="serverstats.png")

    @app_commands.command(name="serverstats", description="Server-wide gambling stats: wagered, paid out, big wins.")
    @app_commands.describe(period="Which time period to show (default: 24 hours)")
    @app_commands.choices(period=[app_commands.Choice(name=v[0], value=k) for k, v in PERIODS.items()])
    async def serverstats(self, interaction: discord.Interaction, period: app_commands.Choice[str] | None = None):
        key = period.value if period else "day"
        await interaction.response.defer()
        embed, file = await self.page(interaction.guild, key)
        view = StatsView(self, interaction.user.id, key)
        extra = {"file": file} if file else {}
        view.message = await interaction.followup.send(embed=embed, view=view, wait=True, **extra)


async def setup(bot):
    await bot.add_cog(ServerStats(bot))
