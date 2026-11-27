"""/stats: a player's balance over time as a graph, with how much it changed, the peak and low, where the
change came from and profit per game. Buttons switch between 24 hours, 7 days, 30 days and all time."""
import asyncio
import datetime as dt
import io
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from cogs.serverstats import game_name
from utils.balance_image import BalanceInfo, render_balance, signed
from utils.economy import fmt
from utils.stats_image import short
from utils.views import OwnedView
from utils.bignum import credits_text

log = logging.getLogger("jaxcasino.playerstats")

# key: (button label, header label, seconds back or 0 for all time)
PERIODS = {
    "day": ("24 hours", "LAST 24 HOURS", 86400),
    "week": ("7 days", "LAST 7 DAYS", 7 * 86400),
    "month": ("30 days", "LAST 30 DAYS", 30 * 86400),
    "all": ("All time", "ALL TIME", 0),
}
# A bet that is paid back within this many seconds is drawn as one step (before -> after), not as a dip
# down by the stake and back up again.
DIP_WINDOW = 15 * 60
MAX_GAME_ROWS = 7
MONEY_LABELS = [("claim", "Daily claims"), ("restart", "Restarts"), ("admin", "Admin changes"),
                ("shop", "Shop"), ("received", "Payments in"), ("pay", "Payments out")]


def when(ts: float) -> str:
    return f"{dt.datetime.fromtimestamp(ts):%d %b %H:%M}"


def build_series(before: int | None, rows, since: float, now: float, current: int) -> list[tuple[float, int]]:
    rows = list(rows)
    pts: list[tuple[float, int]] = []
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if (r["delta"] < 0 and nxt is not None and nxt["delta"] > 0 and nxt["ts"] - r["ts"] < DIP_WINDOW
                and r["source"] is None):
            continue
        pts.append((r["ts"], r["balance"]))
    if before is not None and since:
        pts.insert(0, (since, before))
    if not pts:
        pts = [(since or now - 3600, current)]
    pts.append((now, current))
    return pts


def max_drawdown(points: list[tuple[float, int]]) -> tuple[int, int]:
    """Largest fall in credits from a high to a later low: (high, low). (0, 0) if it never fell."""
    best = (0, 0)
    peak = points[0][1]
    for _, b in points:
        peak = max(peak, b)
        if peak - b > best[0] - best[1]:
            best = (peak, b)
    return best


def pct_text(change: int, start: int) -> str:
    if start <= 0:
        return "—"
    p = change / start * 100
    return f"{p:+,.0f}%" if abs(p) >= 1000 else f"{p:+.1f}%"


def build_page(name: str, period: str, points, st: dict, current: int, rebuilt_until: float | None):
    """Returns (BalanceInfo for the image, highlight lines for the embed)."""
    start = points[0][1]
    change = current - start
    pct = pct_text(change, start)
    peak = max(points, key=lambda p: p[1])
    low = min(points, key=lambda p: p[1])
    dd_hi, dd_lo = max_drawdown(points)
    dd = dd_hi - dd_lo
    games, wins = st["games"], st["wins"]
    winrate = f"{wins / games * 100:.0f}% won" if games else "no games"
    kpis = [
        ("Balance", short(current), f"{credits_text(current)} credits"),
        ("Change", signed(change), f"{pct} from {short(start)}"),
        ("Peak", short(peak[1]), when(peak[0])),
        ("Lowest", short(low[1]), f"biggest drop −{short(dd)}" if dd else when(low[0])),
        ("Games", f"{credits_text(games)}", f"{winrate} · {short(st['wagered'])} bet"),
    ]
    sources = [("Games", st["paid"] - st["wagered"])]
    money = st["money"]
    sources += [(label, money[k]["amount"]) for k, label in MONEY_LABELS if k in money]
    other = change - sum(v for _, v in sources)
    if other:
        sources.append(("Other", other))
    # The games that moved the balance most (either way), best to worst; the small ones fold into "Other".
    played = sorted((g for g in st["per_game"] if g["plays"]), key=lambda g: -abs(g["net"]))
    rows = sorted(played[:MAX_GAME_ROWS - 1] if len(played) > MAX_GAME_ROWS else played, key=lambda g: -g["net"])
    if len(played) > MAX_GAME_ROWS:
        rest = played[MAX_GAME_ROWS - 1:]
        rows.append({"game": "Other", "net": sum(g["net"] for g in rest), "plays": sum(g["plays"] for g in rest)})
    games_rows = [(game_name(g["game"]) if g["game"] != "Other" else "Other", g["net"], g["plays"]) for g in rows]
    footer = "Every balance change is recorded"
    if rebuilt_until and rebuilt_until >= points[0][0]:
        footer = (f"Before {dt.datetime.fromtimestamp(rebuilt_until):%d %b %H:%M} the graph is rebuilt from game "
                  f"records; payments received from others aren't included there")
    seconds = PERIODS[period][2]
    info = BalanceInfo(name=name, period=PERIODS[period][1], points=points, kpis=kpis, sources=sources,
                       axis_start=points[-1][0] - seconds if seconds else None,
                       games=games_rows, footer=footer)

    lines = []
    if b := st["best"]:
        mult = f" ({b['payout'] / b['wagered']:,.1f}x)" if b["wagered"] else ""
        lines.append(f"🏆 **Biggest win:** {fmt(b['profit'])} on {game_name(b['game'])}{mult}")
    if w := st["worst"]:
        lines.append(f"💸 **Biggest loss:** {fmt(w['loss'])} on {game_name(w['game'])}")
    if dd:
        lines.append(f"📉 **Biggest drop:** {fmt(dd_hi)} → {fmt(dd_lo)} (−{dd / dd_hi * 100:.0f}%)")
    if rows:
        best_game = max(rows, key=lambda g: g["net"])
        if best_game["net"] > 0 and best_game["game"] != "Other":
            lines.append(f"🎯 **Best game:** {game_name(best_game['game'])} ({'+' if best_game['net'] > 0 else ''}"
                         f"{credits_text(best_game['net'])})")
    return info, lines


class PlayerStatsView(OwnedView):
    def __init__(self, cog: "PlayerStats", owner_id: int, member: discord.abc.User, period: str):
        super().__init__(owner_id, timeout=300)
        self.cog = cog
        self.member = member
        self.period = period
        for key, (label, *_rest) in PERIODS.items():
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            button.callback = self.make_callback(key)
            button.period = key
            self.add_item(button)
        self.refresh()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Run `/stats` yourself to pick a different period.",
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
            embed, file = await self.cog.page(interaction.guild, self.member, period)
            await interaction.edit_original_response(embed=embed, attachments=[file] if file else [], view=self)
        return callback

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class PlayerStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def page(self, guild: discord.Guild, member: discord.abc.User,
                   period: str) -> tuple[discord.Embed, discord.File | None]:
        db = self.bot.db
        seconds = PERIODS[period][2]
        now = time.time()
        since = now - seconds if seconds else 0
        user = await db.get_user(guild.id, member.id)
        before, rows = await db.balance_history(guild.id, member.id, since)
        points = build_series(before, rows, since, now, user["balance"])
        st = await db.player_stats(guild.id, member.id, since)
        cur = await db.conn.execute(
            "SELECT MAX(ts) AS t FROM balance_log WHERE guild_id=? AND user_id=? AND source='rebuilt'",
            (guild.id, member.id))
        rebuilt_until = (await cur.fetchone())["t"]
        info, lines = build_page(member.display_name, period, points, st, user["balance"], rebuilt_until)

        played = user["games_played"]
        winrate = f"{user['wins'] / played * 100:.1f}%" if played else "—"
        net = user["total_won"] - user["total_wagered"]
        lines.append(f"🎮 **All-time:** {credits_text(played)} games · {winrate} won · {fmt(user['total_wagered'])} wagered · "
                     f"net {'+' if net >= 0 else '-'}{fmt(abs(net))} · best win {fmt(user['biggest_win'])}")
        streaks = await db.best_streaks(guild.id, member.id)
        if streaks:
            g, n = max(streaks.items(), key=lambda kv: kv[1])
            others = " · ".join(f"{game_name(k)} {v}" for k, v in sorted(streaks.items()) if k != g)
            lines.append(f"🔥 **Best winstreak:** {n} in a row at {game_name(g)}" + (f" ({others})" if others else ""))
        label = PERIODS[period][0]
        embed = discord.Embed(title=f"📈 {member.display_name} — {label.lower() if period != 'all' else 'all time'}",
                              description="\n".join(lines), color=discord.Color.blurple())
        filename = f"stats_{period}.png"  # a new name per period, so Discord never shows a cached image
        embed.set_image(url=f"attachment://{filename}")
        try:
            png = await asyncio.to_thread(render_balance, info)
        except Exception:
            log.exception("Failed to draw the player stats image")
            embed.set_image(url=None)
            return embed, None
        return embed, discord.File(io.BytesIO(png), filename=filename)

    @app_commands.command(name="stats", description="Your (or someone's) balance over time, with stats and a graph.")
    @app_commands.describe(user="Whose stats to show (default: you)", period="Which time period (default: 24 hours)")
    @app_commands.choices(period=[app_commands.Choice(name=v[0], value=k) for k, v in PERIODS.items()])
    async def stats(self, interaction: discord.Interaction, user: discord.Member | None = None,
                    period: app_commands.Choice[str] | None = None):
        member = user or interaction.user
        key = period.value if period else "day"
        await interaction.response.defer()
        embed, file = await self.page(interaction.guild, member, key)
        view = PlayerStatsView(self, interaction.user.id, member, key)
        extra = {"file": file} if file else {}
        view.message = await interaction.followup.send(embed=embed, view=view, wait=True, **extra)


async def setup(bot):
    await bot.add_cog(PlayerStats(bot))
