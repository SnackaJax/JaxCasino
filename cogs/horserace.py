"""Horse racing: anyone opens a race, everyone bets on horses with fixed odds, then the race plays out."""
import asyncio
import logging
import math
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import gambling_only
from utils.economy import Wager, fmt, fmt_mult, keep_bet, send_error, take_bet, tag
from utils.views import BaseModal, BaseView
from utils.rng import rng
from utils.bignum import mul_floor

log = logging.getLogger("jaxcasino.horserace")

HORSES = [
    ("🔴", "Crimson Comet"),
    ("🔵", "Blue Thunder"),
    ("🟢", "Lucky Clover"),
    ("🟡", "Golden Gallop"),
    ("🟣", "Purple Haze"),
]
TRACK = 16
FRAMES = 8
FRAME_DELAY = 1.5


class Race:
    def __init__(self, cog: "HorseRacing", channel: discord.abc.Messageable, host: discord.Member, seconds: int):
        self.cog = cog
        self.channel = channel
        self.host = host
        weights = [rng.uniform(1, 4) for _ in HORSES]
        total = sum(weights)
        self.probs = [w / total for w in weights]
        self.odds = [math.floor((1 - config.HOUSE_EDGE) / p * 100) / 100 for p in self.probs]
        self.bets: list[tuple[discord.abc.User, int, Wager]] = []
        self.closes_at = time.time() + seconds
        self.seconds = seconds
        self.open = True
        self.message: discord.InteractionMessage | None = None
        self.view = RaceView(self)
        self.lock = asyncio.Lock()

    def horse_label(self, i: int) -> str:
        emoji, name = HORSES[i]
        return f"{emoji} **{i + 1}. {name}**"

    def betting_embed(self) -> discord.Embed:
        embed = discord.Embed(title="🏇 Horse Race — place your bets!", color=discord.Color.dark_green())
        totals = [0] * len(HORSES)
        for _, h, w in self.bets:
            totals[h] += w.stake
        embed.description = "\n".join(
            f"{self.horse_label(i)} — pays {fmt_mult(self.odds[i])}"
            + (f" · {totals[i]:,} bet" if totals[i] else "")
            for i in range(len(HORSES))
        )
        embed.description += f"\n\nBetting closes <t:{int(self.closes_at)}:R>. Click a horse to bet!"
        if self.bets:
            lines = [f"{u.mention} — {w.stake:,} on {HORSES[h][0]}" for u, h, w in self.bets[-15:]]
            embed.add_field(name=f"Bets ({len(self.bets)})", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Started by {self.host.display_name}")
        return embed

    def track_embed(self, positions: list[int], title: str) -> discord.Embed:
        lines = []
        for i, pos in enumerate(positions):
            remaining = TRACK - pos
            lines.append(f"{HORSES[i][0]} 🏁{'·' * remaining}🏇{'·' * pos} {i + 1}")
        return discord.Embed(title=title, description="\n".join(lines), color=discord.Color.dark_green())

    async def run(self) -> None:
        try:
            await asyncio.sleep(self.seconds)
            async with self.lock:
                self.open = False
            self.view.stop()
            if not self.bets:
                embed = discord.Embed(title="🏇 Horse Race", color=discord.Color.light_grey(),
                                      description="Nobody placed a bet, so the race was cancelled.")
                await self.message.edit(embed=embed, view=None)
                return
            await self.race()
        except Exception:
            log.exception("Horse race in channel %s failed; unsettled bets will be refunded", self.channel.id)
        finally:
            self.cog.races.pop(self.channel.id, None)
            for _, _, wager in self.bets:  # refund anything left unsettled if the race errored
                if not wager.settled:
                    await wager.refund()
            log.info("Horse race in channel %s finished with %d bet(s)", self.channel.id, len(self.bets))

    async def race(self) -> None:
        # Finishing order: weighted draw without replacement.
        remaining = list(range(len(HORSES)))
        order = []
        while remaining:
            pick = rng.choices(remaining, weights=[self.probs[i] for i in remaining])[0]
            order.append(pick)
            remaining.remove(pick)
        winner = order[0]

        final = [0] * len(HORSES)
        pos = TRACK
        for place, horse in enumerate(order):
            final[horse] = pos
            pos = max(1, pos - rng.randint(1, 3))

        positions = [0] * len(HORSES)
        for f in range(1, FRAMES + 1):
            for h in range(len(HORSES)):
                if f == FRAMES:
                    positions[h] = final[h]
                    continue
                target = final[h] * f / FRAMES + rng.uniform(-2, 2)
                cap = final[h] - 1 if final[h] == TRACK else final[h]
                positions[h] = int(min(max(positions[h], round(target)), cap))
            await self.message.edit(embed=self.track_embed(positions, "🏇 And they're off!"), view=None)
            await asyncio.sleep(FRAME_DELAY)

        # Payouts
        results = []
        for user, horse, wager in self.bets:
            payout = mul_floor(wager.stake, self.odds[horse]) if horse == winner else 0
            await wager.settle(payout)
            if payout:
                results.append(f"✅ {user.mention} won {fmt(payout)}")
        embed = self.track_embed(final, f"🏆 {HORSES[winner][1]} wins!")
        embed.add_field(name="Finish", value="\n".join(
            f"{['🥇', '🥈', '🥉', '4.', '5.'][p]} {self.horse_label(h)}" for p, h in enumerate(order)), inline=False)
        embed.add_field(name="Winners", value="\n".join(results[:20]) or "Nobody backed the winner. 😢",
                        inline=False)
        await self.message.edit(embed=embed, view=None)


class BetModal(BaseModal):
    def __init__(self, race: Race, horse: int):
        super().__init__(title=f"Bet on {HORSES[horse][1]}"[:45])
        self.race = race
        self.horse = horse
        self.amount = discord.ui.TextInput(label="Bet amount", placeholder="Any amount, e.g. 1234 (or 2.5k, half, all)", max_length=20)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        race = self.race
        async with race.lock:
            if not race.open:
                return await send_error(interaction, "Betting for this race has closed.")
            wager = await take_bet(interaction, self.amount.value, "horserace")
            if not wager:
                return
            race.bets.append((interaction.user, self.horse, wager))
            keep_bet(interaction, wager)  # the race settles it, even if the confirmation below fails
        await interaction.response.send_message(
            f"You bet {fmt(wager.stake)} on {race.horse_label(self.horse)} "
            f"(pays {fmt_mult(race.odds[self.horse])}).", ephemeral=True)
        try:
            await race.message.edit(embed=race.betting_embed(), view=race.view)
        except discord.HTTPException:
            pass


class RaceView(BaseView):
    def __init__(self, race: Race):
        super().__init__(timeout=None)
        self.race = race
        for i, (emoji, name) in enumerate(HORSES):
            button = discord.ui.Button(label=str(i + 1), emoji=emoji, style=discord.ButtonStyle.secondary)
            button.callback = self.make_callback(i)
            self.add_item(button)

    def make_callback(self, horse: int):
        async def callback(interaction: discord.Interaction):
            if not self.race.open:
                return await send_error(interaction, "Betting for this race has closed.")
            await interaction.response.send_modal(BetModal(self.race, horse))
        return callback


class HorseRacing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.races: dict[int, Race] = {}

    @app_commands.command(name="horserace", description="Open a horse race that everyone can bet on.")
    @app_commands.describe(betting_time="Seconds before the race starts (20-180, default 60)")
    @gambling_only()
    async def horserace(self, interaction: discord.Interaction,
                        betting_time: app_commands.Range[int, 20, 180] = 60):
        if interaction.channel_id in self.races:
            await send_error(interaction, "A race is already running in this channel.")
            return
        race = Race(self, interaction.channel, interaction.user, betting_time)
        self.races[interaction.channel_id] = race
        await interaction.response.send_message(**tag(interaction.user), embed=race.betting_embed(), view=race.view)
        race.message = await interaction.original_response()
        race.task = asyncio.create_task(race.run())


async def setup(bot):
    await bot.add_cog(HorseRacing(bot))
