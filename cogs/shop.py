"""The shop: spend credits on a fighting chicken, or on mischief: disconnecting, muting, deafening, timing
out or renaming other members, or a secret random kick from voice.

Mutes, deafens and random kicks finish later, so they are stored in the shop_jobs table and picked up again
after a restart. A server mute or deafen stays on when someone leaves voice, and Discord only lets the bot
lift it while they're connected, so one that expires while its target is out of voice is lifted when they
come back.
"""
import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.economy import fmt, send_error
from utils.rng import rng
from utils.shop import (ITEMS, PRICE_LABELS, TIMEOUT_DURATIONS, VOICE_DURATIONS, duration_label, get_prices,
                        item_price_text)

log = logging.getLogger("gamblebot.shop")

MAX_TIMEOUT = dt.timedelta(days=28)  # Discord's limit
# A random kick that should have fired more than this long ago (the bot was offline) is refunded instead.
STALE_KICK = 5 * 60


@dataclass(frozen=True)
class VoiceEffect:
    name: str        # the /buy subcommand
    job: str         # shop_jobs kind that lifts it again
    flag: str        # VoiceState attribute that is True while it's on
    edit_kw: str     # Member.edit keyword that turns it on/off
    perm: str        # permission the bot needs in the voice channel
    perm_name: str
    emoji: str
    done: str
    ends: str


VOICE_EFFECTS = {e.name: e for e in [
    VoiceEffect("mute", "unmute", "mute", "mute", "mute_members", "Mute Members", "🔇", "muted",
                "They can talk again"),
    VoiceEffect("deafen", "undeafen", "deaf", "deafen", "deafen_members", "Deafen Members", "🙉", "deafened",
                "They can hear again"),
]}
EFFECT_BY_JOB = {e.job: e for e in VOICE_EFFECTS.values()}
VOICE_CHOICES = [app_commands.Choice(name=duration_label(m), value=m) for m in VOICE_DURATIONS]
TIMEOUT_CHOICES = [app_commands.Choice(name=duration_label(m), value=m) for m in TIMEOUT_DURATIONS]


def name_of(member: discord.Member) -> str:
    return discord.utils.escape_markdown(member.display_name)


class Shop(commands.Cog):
    buy = app_commands.Group(name="buy", description="Spend your credits in the shop.")

    def __init__(self, bot):
        self.bot = bot
        self.tasks: dict[int, asyncio.Task] = {}
        # (guild, member, "unmute"/"undeafen") -> the job that lifts that mute or deafen
        self.voice_jobs: dict[tuple[int, int, str], dict] = {}
        self.kick_buyers: dict[int, discord.Interaction] = {}  # random kick job -> purchase, to report back
        self._restore_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self._restore_task = asyncio.create_task(self._restore())

    async def cog_unload(self) -> None:
        if self._restore_task:
            self._restore_task.cancel()
        for task in self.tasks.values():
            task.cancel()

    # ------------------------------------------------------------- jobs
    async def _restore(self) -> None:
        await self.bot.wait_until_ready()
        db, now = self.bot.db, time.time()
        for row in await db.shop_jobs():
            job = dict(row)
            if job["kind"] == "randomkick" and job["due"] < now - STALE_KICK:
                if await db.delete_shop_job(job["id"]):
                    await db.add_balance(job["guild_id"], job["buyer_id"], job["cost"], kind="shop")
                    log.info("Refunded random kick #%s from user %s: it was due while the bot was offline",
                             job["id"], job["buyer_id"])
                continue
            self._schedule(job)
        if self.tasks:
            log.info("Restored %d pending shop effect(s).", len(self.tasks))

    def _schedule(self, job: dict) -> None:
        old = self.tasks.pop(job["id"], None)
        if old:
            old.cancel()
        if job["kind"] in EFFECT_BY_JOB:
            self.voice_jobs[(job["guild_id"], job["target_id"], job["kind"])] = job
        self.tasks[job["id"]] = asyncio.create_task(self._run(job))

    async def _run(self, job: dict) -> None:
        try:
            await asyncio.sleep(max(0.0, job["due"] - time.time()))
            if job["kind"] in EFFECT_BY_JOB:
                await self._voice_expired(job)
            elif job["kind"] == "randomkick":
                await self._random_kick(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Shop job #%s (%s) failed", job["id"], job["kind"])
        finally:
            if self.tasks.get(job["id"]) is asyncio.current_task():
                del self.tasks[job["id"]]

    async def _voice_expired(self, job: dict) -> None:
        guild = self.bot.get_guild(job["guild_id"])
        member = guild.get_member(job["target_id"]) if guild else None
        if member is None or member.voice is None:
            log.debug("Shop %s #%s expired while user %s is out of voice; lifting it when they rejoin",
                      EFFECT_BY_JOB[job["kind"]].name, job["id"], job["target_id"])
            return  # stays in self.voice_jobs; on_voice_state_update lifts it
        await self._lift(member, job)

    async def _lift(self, member: discord.Member, job: dict) -> None:
        effect = EFFECT_BY_JOB[job["kind"]]
        self.voice_jobs.pop((member.guild.id, member.id, job["kind"]), None)
        await self.bot.db.delete_shop_job(job["id"])
        if member.voice and getattr(member.voice, effect.flag):
            try:
                await member.edit(**{effect.edit_kw: False}, reason=f"Shop {effect.name} expired")
                log.info("Shop %s on %s (%s) expired", effect.name, member, member.id)
            except discord.HTTPException as e:
                log.warning("Could not lift the shop %s on %s (%s): %s", effect.name, member, member.id, e)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        if after.channel is None:
            return
        for kind in EFFECT_BY_JOB:
            job = self.voice_jobs.get((member.guild.id, member.id, kind))
            if job and job["due"] <= time.time():
                await self._lift(member, job)

    async def _random_kick(self, job: dict) -> None:
        db = self.bot.db
        if not await db.delete_shop_job(job["id"]):
            return
        purchase = self.kick_buyers.pop(job["id"], None)
        guild = self.bot.get_guild(job["guild_id"])
        candidates = []
        if guild:
            for channel in [*guild.voice_channels, *guild.stage_channels]:
                if channel.permissions_for(guild.me).move_members:
                    candidates += [m for m in channel.members if not m.bot]
        rng.shuffle(candidates)
        victim = None
        for member in candidates:
            try:
                # The audit-log reason deliberately doesn't name the buyer.
                await member.move_to(None, reason="Random kick")
                victim = member
                break
            except discord.HTTPException as e:
                log.warning("Random kick #%s could not disconnect %s: %s", job["id"], member, e)
        if victim is None:
            await db.add_balance(job["guild_id"], job["buyer_id"], job["cost"], kind="shop")
            log.info("Random kick #%s from user %s found nobody to disconnect; refunded", job["id"], job["buyer_id"])
            text = (f"🎲 Your random kick fizzled: there was nobody in voice I could disconnect. "
                    f"You got your {fmt(job['cost'])} back.")
        else:
            log.info("Random kick #%s from user %s disconnected %s (%s)", job["id"], job["buyer_id"],
                     victim, victim.id)
            text = f"🎲 Your random kick hit **{name_of(victim)}**!"
            if victim.id == job["buyer_id"]:
                text += " That's you. Ouch."
        if purchase:
            try:
                await purchase.followup.send(text, ephemeral=True)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------ helpers
    async def _charge(self, interaction: discord.Interaction, price_key: str) -> int | None:
        """Takes the price of a shop option. Sends an error and returns None if that fails."""
        cost = (await get_prices(self.bot.db, interaction.guild_id))[price_key]
        if cost <= 0:
            await send_error(interaction, f"**{PRICE_LABELS[price_key]}** isn't for sale on this server.")
            return None
        if not await self.bot.db.spend(interaction.guild_id, interaction.user.id, cost):
            bal = await self.bot.db.get_balance(interaction.guild_id, interaction.user.id)
            await send_error(interaction, f"That costs {fmt(cost)}, but your balance is {fmt(bal)}.")
            return None
        return cost

    async def _refund(self, interaction: discord.Interaction, cost: int, error: Exception, what: str) -> None:
        await self.bot.db.add_balance(interaction.guild_id, interaction.user.id, cost, kind="shop")
        log.warning("Shop %s by %s failed, refunded %s: %s", what, interaction.user, f"{cost:,}", error)
        why = ("I don't have permission to do that." if isinstance(error, discord.Forbidden)
               else "Discord wouldn't let me do that right now.")
        await send_error(interaction, f"{why} You got your {fmt(cost)} back.")

    async def _announce(self, interaction: discord.Interaction, text: str, cost: int) -> None:
        bal = await self.bot.db.get_balance(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(description=text, color=discord.Color.orange())
        embed.set_footer(text=f"🛒 Bought for {cost:,} credits · /shop")
        await interaction.response.send_message(embed=embed)
        log.info("Shop: %s spent %s (balance %s): %s", interaction.user, f"{cost:,}", f"{bal:,}", text)

    @staticmethod
    def _target_problem(guild: discord.Guild, target: discord.Member, hierarchy: bool) -> str | None:
        if target.bot:
            return "The shop doesn't work on bots."
        if hierarchy:
            if target.id == guild.owner_id:
                return "Nobody can do that to the server owner."
            if target.top_role >= guild.me.top_role:
                return f"I can't do that to {target.mention}: their highest role is above mine."
        return None

    @staticmethod
    def _voice_problem(guild: discord.Guild, target: discord.Member, perm: str, perm_name: str) -> str | None:
        if target.voice is None or target.voice.channel is None:
            return f"{target.mention} isn't in a voice channel."
        channel = target.voice.channel
        if not getattr(channel.permissions_for(guild.me), perm):
            return f"I need the **{perm_name}** permission in {channel.mention} to do that."
        return None

    # --------------------------------------------------------------- shop
    @app_commands.command(name="shop", description="See what you can buy with your credits.")
    async def shop(self, interaction: discord.Interaction):
        prices = await get_prices(self.bot.db, interaction.guild_id)
        bal = await self.bot.db.get_balance(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(title="🛒 Shop", color=discord.Color.gold(),
                              description="Buy anything here with `/buy <item>`.")
        for item in ITEMS.values():
            embed.add_field(name=f"{item.emoji} {item.name} · {item.how}",
                            value=f"{item_price_text(item, prices)}\n{item.description}", inline=False)
        embed.set_footer(text=f"Your balance: {bal:,} credits")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------------------------------------------------------- buy
    @buy.command(name="chicken", description="Buy a fighting chicken for /cockfight.")
    async def buy_chicken(self, interaction: discord.Interaction):
        db, gid, uid = self.bot.db, interaction.guild_id, interaction.user.id
        price = (await get_prices(db, gid))["chicken"]
        if price <= 0:
            await send_error(interaction, "Chickens aren't for sale on this server.")
            return
        status = await db.buy_chicken(gid, uid, price)
        if status == "owned":
            await send_error(interaction, "You already have a chicken. Take it to `/cockfight`!")
            return
        if status == "poor":
            bal = await db.get_balance(gid, uid)
            await send_error(interaction, f"A chicken costs {fmt(price)}, but your balance is {fmt(bal)}.")
            return
        from cogs.cockfight import pct, win_chance
        await self._announce(interaction, (
            f"🐣 {interaction.user.mention} bought a fighting chicken!\n"
            f"It starts with a **{pct(win_chance(0))}** win chance, which grows with every fight it wins. "
            f"Bet on it with `/cockfight`."), price)

    @buy.command(name="disconnect", description="Disconnect someone from voice.")
    @app_commands.describe(user="Who to disconnect")
    async def buy_disconnect(self, interaction: discord.Interaction, user: discord.Member):
        guild = interaction.guild
        problem = (self._target_problem(guild, user, hierarchy=False)
                   or self._voice_problem(guild, user, "move_members", "Move Members"))
        if problem:
            await send_error(interaction, problem)
            return
        channel = user.voice.channel
        cost = await self._charge(interaction, "disconnect")
        if cost is None:
            return
        try:
            await user.move_to(None, reason=f"Shop: disconnected by {interaction.user}")
        except discord.HTTPException as e:
            await self._refund(interaction, cost, e, "disconnect")
            return
        await self._announce(interaction, f"🔌 {interaction.user.mention} disconnected {user.mention} "
                                          f"from {channel.mention}!", cost)

    async def _buy_voice(self, interaction: discord.Interaction, user: discord.Member, minutes: int,
                         effect: VoiceEffect) -> None:
        """Server-mutes or server-deafens someone for `minutes`, or adds the time to one that's running."""
        guild = interaction.guild
        problem = (self._target_problem(guild, user, hierarchy=False)
                   or self._voice_problem(guild, user, effect.perm, effect.perm_name))
        if problem:
            await send_error(interaction, problem)
            return
        key = (guild.id, user.id, effect.job)
        # One that expired but was never lifted (its target was out of voice) is simply reused.
        existing = self.voice_jobs.get(key)
        active = getattr(user.voice, effect.flag)
        if active and existing is None:
            await send_error(interaction, f"{user.mention} is already server-{effect.done}.")
            return
        cost = await self._charge(interaction, ITEMS["voice"].price_key(minutes))
        if cost is None:
            return
        try:
            if not active:
                await user.edit(**{effect.edit_kw: True},
                                reason=f"Shop: {effect.done} by {interaction.user} for {duration_label(minutes)}")
        except discord.HTTPException as e:
            await self._refund(interaction, cost, e, effect.name)
            return
        db, now = self.bot.db, time.time()
        if existing:
            due = max(existing["due"], now) + minutes * 60
            await db.update_shop_job(existing["id"], due)
            job = {**existing, "due": due}
            verb = (f"added {duration_label(minutes)} to {user.mention}'s {effect.name}" if existing["due"] > now
                    else f"{effect.done} {user.mention} in voice")
        else:
            due = now + minutes * 60
            job_id = await db.add_shop_job(guild.id, effect.job, due, user.id, interaction.user.id, cost)
            job = {"id": job_id, "guild_id": guild.id, "kind": effect.job, "due": due, "target_id": user.id,
                   "buyer_id": interaction.user.id, "cost": cost}
            verb = f"{effect.done} {user.mention} in voice"
        self._schedule(job)
        await self._announce(interaction, f"{effect.emoji} {interaction.user.mention} {verb}! "
                                          f"{effect.ends} <t:{int(due)}:R>.", cost)

    @buy.command(name="mute", description="Server-mute someone in voice (they can't talk) for 1 or 5 minutes.")
    @app_commands.describe(user="Who to mute", duration="How long (adds to a mute they already have)")
    @app_commands.choices(duration=VOICE_CHOICES)
    async def buy_mute(self, interaction: discord.Interaction, user: discord.Member,
                       duration: app_commands.Choice[int]):
        await self._buy_voice(interaction, user, duration.value, VOICE_EFFECTS["mute"])

    @buy.command(name="deafen", description="Server-deafen someone in voice (they can't hear) for 1 or 5 minutes.")
    @app_commands.describe(user="Who to deafen", duration="How long (adds to a deafen they already have)")
    @app_commands.choices(duration=VOICE_CHOICES)
    async def buy_deafen(self, interaction: discord.Interaction, user: discord.Member,
                         duration: app_commands.Choice[int]):
        await self._buy_voice(interaction, user, duration.value, VOICE_EFFECTS["deafen"])

    @buy.command(name="timeout", description="Time someone out for 5, 15 or 30 minutes.")
    @app_commands.describe(user="Who to time out", duration="How long (adds to a timeout they already have)")
    @app_commands.choices(duration=TIMEOUT_CHOICES)
    async def buy_timeout(self, interaction: discord.Interaction, user: discord.Member,
                          duration: app_commands.Choice[int]):
        minutes = duration.value
        guild = interaction.guild
        problem = self._target_problem(guild, user, hierarchy=True)
        if not problem and user.guild_permissions.administrator:
            problem = f"Discord doesn't allow timing out {user.mention}: they're an administrator."
        if not problem and not guild.me.guild_permissions.moderate_members:
            problem = "I need the **Timeout Members** permission to do that."
        if problem:
            await send_error(interaction, problem)
            return
        now = discord.utils.utcnow()
        extending = user.is_timed_out()
        start = user.timed_out_until if extending else now
        until = start + dt.timedelta(minutes=minutes)
        if until - now > MAX_TIMEOUT:
            await send_error(interaction, "Discord timeouts can't last longer than 28 days in total.")
            return
        cost = await self._charge(interaction, ITEMS["timeout"].price_key(minutes))
        if cost is None:
            return
        try:
            await user.timeout(until, reason=f"Shop: timed out by {interaction.user} for {duration_label(minutes)}")
        except discord.HTTPException as e:
            await self._refund(interaction, cost, e, "timeout")
            return
        verb = (f"added {duration_label(minutes)} to {user.mention}'s timeout" if extending
                else f"timed out {user.mention}")
        ts = int(until.timestamp())
        await self._announce(interaction, f"⏳ {interaction.user.mention} {verb}! It ends <t:{ts}:R>.", cost)

    @buy.command(name="nickname", description="Change someone's nickname on this server.")
    @app_commands.describe(user="Whose nickname to change", name="Their new nickname (up to 32 characters)")
    async def buy_nickname(self, interaction: discord.Interaction, user: discord.Member,
                           name: app_commands.Range[str, 1, 32]):
        guild = interaction.guild
        name = name.strip()
        problem = self._target_problem(guild, user, hierarchy=True)
        if not problem and not guild.me.guild_permissions.manage_nicknames:
            problem = "I need the **Manage Nicknames** permission to do that."
        if not problem and not name:
            problem = "The new nickname can't be empty."
        if problem:
            await send_error(interaction, problem)
            return
        old = name_of(user)
        cost = await self._charge(interaction, "nickname")
        if cost is None:
            return
        try:
            await user.edit(nick=name, reason=f"Shop: renamed by {interaction.user}")
        except discord.HTTPException as e:
            await self._refund(interaction, cost, e, "nickname change")
            return
        await self._announce(interaction, f"🏷️ {interaction.user.mention} renamed **{old}** to "
                                          f"**{discord.utils.escape_markdown(name)}**!", cost)

    @buy.command(name="random-kick", description="Secretly disconnect a random person in voice, at a random moment.")
    async def buy_random_kick(self, interaction: discord.Interaction):
        cost = await self._charge(interaction, "randomkick")
        if cost is None:
            return
        db, gid, uid = self.bot.db, interaction.guild_id, interaction.user.id
        due = time.time() + rng.uniform(0, config.RANDOM_KICK_MAX_DELAY)
        job_id = await db.add_shop_job(gid, "randomkick", due, None, uid, cost)
        log.info("Random kick #%s bought by %s for %s, fires in %.0fs", job_id, interaction.user, f"{cost:,}",
                 due - time.time())
        bal = await db.get_balance(gid, uid)
        embed = discord.Embed(title="🎲 Random kick bought", color=discord.Color.dark_purple(), description=(
            "Somebody in voice will be disconnected at a random moment soon. It could be anyone, even you.\n"
            "Only you can see this message, and I'll tell you here who got hit.\n\n"
            f"Paid {fmt(cost)} · balance {fmt(bal)}"))
        # Reply before scheduling, so a near-zero delay can't try to report back before this message exists.
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.kick_buyers[job_id] = interaction
        self._schedule({"id": job_id, "guild_id": gid, "kind": "randomkick", "due": due, "target_id": None,
                        "buyer_id": uid, "cost": cost})


async def setup(bot):
    await bot.add_cog(Shop(bot))
