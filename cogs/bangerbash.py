"""/bangerbash: Banger Bash, a 5x3 barbecue slot with 20 paylines, random features and the Grill Bonus.

The game logic lives in utils/banger_engine.py (shared with the Activity version in activity/banger.py) and the
pictures in utils/banger_image.py. Each paid spin is one Round: the reels are decided at once and animated;
if 3+ grills land, the player chooses whether to turn up the heat, then plays the feature (picks with buttons,
or free spins / Golden Grill shown automatically). The bet is settled when the round ends. A round left
waiting for a choice finishes itself with default choices after DECISION_TIMEOUT.
"""
from __future__ import annotations

import asyncio
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import banger_engine as E
from utils import banger_image as I
from utils.checks import gambling_only
from utils.economy import buy_limit_note, fmt, result_color, result_line, send_error, take_bet, tag
from utils.rng import rng
from utils.views import BaseView
from utils.bignum import credits_text

log = logging.getLogger("jaxcasino.bangerbash")

GAME = "bangerbash"
BET_DESC = "Total bet for all 20 lines: any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all"
REEL_STEP = 0.65
DECISION_TIMEOUT = 120
AFTER_TIMEOUT = 180
FOOTER = "20 lines · random features · 3+ grills = Grill Bonus · /bangerbash-paytable"
ORANGE = discord.Color.orange()

PRE_FEATURES = {"charcoal": "EXTRA CHARCOAL! At least 2 grills!", "sure": "SURE SIZZLE! This spin will win!",
                "mega": "MEGA BANGER incoming!"}


def rtp() -> float:
    return config.BANGER_RTP


def cr(stake: int, x: float) -> int:
    return E.credits(stake, x, rtp())


def sym(key: str) -> str:
    name, emoji, _ = E.SYMBOLS[key]
    return f"{emoji} {name}"


def png_file(png: bytes, name: str = "banger.png") -> discord.File:
    return discord.File(io.BytesIO(png), filename=name)


def make_embed(text: str, color: discord.Color = ORANGE, title: str = "🌭 Banger Bash") -> discord.Embed:
    e = discord.Embed(title=title, description=text, color=color)
    e.set_image(url="attachment://banger.png")
    e.set_footer(text=FOOTER)
    return e


def win_lines(stake: int, spin: E.Spin, limit: int = 6) -> list[str]:
    wins = sorted(spin.wins, key=lambda w: -w.x)
    out = []
    for w in wins[:limit]:
        extra = " (2x link)" if w.mult > 1 else ""
        out.append(f"Line {w.line + 1}: {w.count}× {sym(w.symbol)}{extra} — {fmt(cr(stake, w.x * spin.mult))}")
    if len(wins) > limit:
        out.append(f"…and {len(wins) - limit} more")
    return out


def spin_message(stake: int, spin: E.Spin) -> str:
    if not spin.wins:
        return "No win"
    total = cr(stake, spin.x)
    if len(spin.wins) == 1:
        w = spin.wins[0]
        return f"Line {w.line + 1} · {w.count}× {E.SYMBOLS[w.symbol][0]} · {credits_text(total)}"
    return f"{len(spin.wins)} winning lines · {credits_text(total)}"


def paytable_embed() -> discord.Embed:
    s = E.scale(rtp())
    feats = "\n".join(f"• **{E.FEATURE_NAMES[k]}** — {E.FEATURE_TEXT[k]}" for k in E.FEATURE_NAMES)
    tiers = "\n".join(f"{i + 1}. **{E.TIER_NAMES[k]}** — {E.TIER_TEXT[k]} (avg {E.FEATURE_EV[k] * s:.0f}x)"
                      for i, k in enumerate(E.TIERS))
    e = discord.Embed(title="🌭 Banger Bash — how it works", color=ORANGE, description=(
        "Pays are times your **total bet**, for 3–5 matching symbols in a row on any of the 20 lines, starting "
        "from the leftmost reel. 🔥 Flame Wilds (reels 2–5) stand in for everything but the grill.\n\n"
        f"**Random features** (about 1 spin in {1 / E.FEATURE_CHANCE:.0f})\n{feats}\n\n"
        "**Grill Bonus** — 3+ ♨️ grills anywhere. The Grill Wheel awards one of five features (4 or 5 grills "
        f"skip the lowest rungs):\n{tiers}\n\n"
        f"**Turn up the heat** — before playing, gamble up to {E.MAX_GAMBLES} times: win and the feature climbs a "
        "rung, lose and it drops one (from Hot Potato, the bonus burns). The odds are set so a gamble is "
        "worth exactly what you risk.\n\n"
        f"Maximum win {credits_text(E.MAX_WIN)}x per spin. Returns {rtp() * 100:g}% of bets on average."
        + ("\n\n**Bonus buys** — one spin that is sure to land the grills, straight into the Grill Bonus: "
           + "; ".join(f"**{b['name']}** ({b['grills']} grills) {E.PRICES[k]:g}x your bet, pays back "
                       f"{E.buy_rtp(k, rtp()) * 100:.1f}%" for k, b in E.BUYS.items())
           + "." + buy_limit_note() if config.BANGER_BUYS else "")))
    e.set_image(url="attachment://banger.png")
    return e


class Game:
    """One paid spin of one player, from the reels to the end of its Grill Bonus."""

    def __init__(self, cog: "BangerBash", user: discord.abc.User, wager, mode: str = "spin"):
        self.cog = cog
        self.user = user
        self.wager = wager
        self.mode = mode
        # every pay is a multiple of the bet; a bonus buy's stake (what was paid) is its price x the bet
        self.stake = wager.bet
        self.paid = wager.stake
        self.rnd = E.Round(rng, mode)
        self.message: discord.Message | discord.InteractionMessage | None = None
        self.send = None             # async (embed, png, view): updates the game message
        self.last_png: bytes | None = None
        self.view: "BaseView | None" = None
        self.lock = asyncio.Lock()
        self.finished = False
        self.shown = False
        self.bonus_text: list[str] = []

    # ------------------------------------------------------------------ message plumbing
    def bind(self, interaction: discord.Interaction, new: bool) -> None:
        """Routes the next updates through this interaction: the first one answers it, later ones edit."""
        state = {"first": True}

        async def send(e, png, view):
            f = png_file(png)
            if state["first"]:
                state["first"] = False
                if new:
                    # send_message rejects view=None (unlike the edit calls), so only pass a real view
                    extra = {"view": view} if view is not None else {}
                    await interaction.response.send_message(**tag(self.user), embed=e, file=f, **extra)
                    try:
                        self.message = await interaction.original_response()
                    except discord.HTTPException:
                        pass
                else:
                    await interaction.response.edit_message(embed=e, attachments=[f], view=view)
                    self.message = interaction.message
            else:
                await interaction.edit_original_response(embed=e, attachments=[f], view=view)
        self.send = send

    def bind_message(self) -> None:
        """Routes updates through the message itself (after a timeout, when there is no interaction)."""
        async def send(e, png, view):
            if self.message is not None:
                await self.message.edit(embed=e, attachments=[png_file(png)], view=view)
        self.send = send

    async def show(self, png: bytes, text: str, view=None, color=ORANGE) -> None:
        self.last_png = png
        await self.send(make_embed(text, color), png, view)
        self.shown = True

    async def frame(self, text: str, view=None, **kw) -> None:
        png = await asyncio.to_thread(I.render, I.Frame(bet=self.stake, spin_seed=rng.randrange(1 << 16), **kw))
        await self.show(png, text, view)

    # ------------------------------------------------------------------ the reels
    async def animate_spin(self, spin: E.Spin, header: str, won_before: int = 0, free: tuple | None = None,
                           free_name: str = "") -> int:
        """Spins, stops and shows one set of reels. Returns the credits won on it."""
        kw = dict(free=free, free_name=free_name, mult=spin.mult if free_name == "Grill Spins" else None)
        pre = PRE_FEATURES.get(spin.feature or "")
        msg = pre or ("Free spin..." if free else "Good luck!")
        # free spins get one spinning picture instead of three, so a long feature doesn't take minutes
        for stopped in ((0,) if free else (0, 2, 4)):
            await self.frame(header + (f"\n🔥 **{pre}**" if pre else ""), grid=spin.landed, stopped=stopped,
                             message=msg, win=won_before, **kw)
            await asyncio.sleep(REEL_STEP)
        f = spin.feature
        if f in ("flame", "hot", "mystery", "respin"):
            # the reels land first, then the feature changes them
            text = {"flame": "🔥 **FLAME WILDS!** Cells are catching fire...",
                    "hot": "🔥 **HOT REELS!** A whole reel is heating up...",
                    "mystery": "🍔 **MYSTERY BUNS!** What's inside?",
                    "respin": "🍽️ **SECOND HELPING!** One reel spins again..."}[f]
            respin = spin.detail.get("reel") if f == "respin" else None
            await self.frame(header + "\n" + text, grid=spin.landed, message=text.split("**")[1], respin=respin,
                             win=won_before, **kw)
            await asyncio.sleep(REEL_STEP * 1.6)
        lit: set = set()
        if f == "flame":
            lit = {tuple(c) for c in spin.detail["cells"]}
        elif f == "hot":
            lit = {(r, row) for r in spin.detail["reels"] for row in range(E.ROWS)}
        mega = (1, 3, spin.detail["symbol"]) if f == "mega" else None
        spin_won = cr(self.stake, spin.x)
        cells = {c for w in spin.wins for c in w.cells}
        grills = set(spin.grills) if len(spin.grills) >= 3 else set()
        lines = [header]
        if f and f not in PRE_FEATURES and f != "respin":
            lines.append(f"🔥 **{E.FEATURE_NAMES[f]}!**")
        if f == "mystery":
            lines.append(f"The buns were all {sym(spin.detail['symbol'])}!")
        lines += win_lines(self.stake, spin, limit=4 if free else 6)
        message = spin_message(self.stake, spin)
        if spin.mult > 1 and spin.wins:
            message = f"Win ×{spin.mult} · {credits_text(spin_won)}"
        await self.frame("\n".join(lines), grid=spin.grid, lines=[w.line for w in spin.wins], win_cells=cells,
                         lit=lit, mega=mega, links=spin.links, grill_cells=grills, win=won_before + spin_won,
                         message=message, **kw)
        await asyncio.sleep(REEL_STEP * (2 if spin.wins or lit else 1.2))
        return spin_won

    async def play(self) -> None:
        """The paid spin. Ends with the ladder (waiting for a choice) or with the result."""
        spin = self.rnd.spin
        await self.animate_spin(spin, f"Bet {fmt(self.stake)} on 20 lines" if self.mode == "spin" else
                                f"♨️ Bought the **{E.BUYS[self.mode]['name']}** for {fmt(self.paid)} "
                                f"(bet {fmt(self.stake)})")
        if self.rnd.bonus is None:
            await self.finish()
            return
        b = self.rnd.bonus
        won = cr(self.stake, spin.x)
        await self.frame(f"♨️ **{b.grills} GRILLS! The Grill Bonus is lit!**", grid=spin.grid,
                         grill_cells=set(spin.grills), banner=f"{b.grills} GRILLS · GRILL BONUS!", win=won,
                         message="Spinning the Grill Wheel...")
        await asyncio.sleep(2.0)
        await self.show_ladder()

    # ------------------------------------------------------------------ the Grill Bonus
    async def show_ladder(self, flash: str = "", note: str = "") -> None:
        b = self.rnd.bonus
        chance = E.gamble_chance(b.tier) if b.can_gamble else None
        png = await asyncio.to_thread(I.render_ladder, b.tier, chance=chance,
                                      gambles_left=E.MAX_GAMBLES - len(b.gambles), stake=self.stake, rtp=rtp(),
                                      flash=flash, note=note)
        kind = E.TIERS[b.tier]
        text = [f"🔥 **Grill Bonus** — your feature: **{E.TIER_NAMES[kind]}**", E.TIER_TEXT[kind]]
        if chance is not None:
            up = E.TIER_NAMES[E.TIERS[b.tier + 1]]
            down = E.TIER_NAMES[E.TIERS[b.tier - 1]] if b.tier else "nothing (the bonus burns)"
            text.append(f"\n**Turn up the heat?** {chance:.0%} to climb to **{up}**, otherwise you drop to "
                        f"**{down}**. Gambles left: {E.MAX_GAMBLES - len(b.gambles)}.")
        self.view = LadderView(self, chance)
        await self.show(png, "\n".join(text), self.view)

    async def gamble(self) -> None:
        b = self.rnd.bonus
        before = E.TIER_NAMES[E.TIERS[b.tier]]
        won = b.gamble(rng)
        if b.lost:
            self.bonus_text.append(f"🔥 Turned up the heat on {before}… and the grill burnt out!")
            png = await asyncio.to_thread(I.render_ladder, 0, chance=None, gambles_left=0, stake=self.stake,
                                          rtp=rtp(), lost=True, note="Better luck next time!")
            await self.show(png, "💨 **The grill burnt out!** The bonus is gone.", None, discord.Color.red())
            await asyncio.sleep(2.0)
            await self.finish()
            return
        now = E.TIER_NAMES[E.TIERS[b.tier]]
        self.bonus_text.append(f"🔥 Heat {'up' if won else 'down'}: {before} → {now}")
        await self.show_ladder(flash="up" if won else "down",
                               note=f"HEAT RISES! Now playing {now}" if won else f"Dropped to {now}"
                               if not b.can_gamble else "")

    async def take(self) -> None:
        b = self.rnd.bonus
        b.take(rng)
        f = b.feature
        self.bonus_text.append(f"🎁 Played **{E.TIER_NAMES[f.kind]}**")
        if f.kind == "potato":
            await self.show_potato()
        elif f.kind == "picnic":
            await self.show_picnic()
        elif f.kind == "golden":
            await self.play_golden()
        else:
            await self.play_free_spins()

    async def show_potato(self, reveal: list | None = None, pick: int | None = None) -> None:
        f = self.rnd.bonus.feature
        st = f.to_dict()
        if reveal is not None:
            png = await asyncio.to_thread(I.render_potato, st, self.stake, rtp(), reveal, pick)
            v = reveal[pick]
            text = (f"🔥 **BURNT!** Hot Potato ends with {fmt(cr(self.stake, f.total))}." if v is None
                    else f"🌭 **+{credits_text(cr(self.stake, v))}!** Total {fmt(cr(self.stake, f.total))}")
            await self.show(png, text, None)
            await asyncio.sleep(1.8)
            if f.done:
                await self.finish()
                return
        png = await asyncio.to_thread(I.render_potato, f.to_dict(), self.stake, rtp())
        text = (f"🥔 **Hot Potato** — round {f.round + 1} of {E.POTATO_ROUNDS}. "
                + ("Round 1 is safe: pick any sausage!" if f.round == 0 else "One of the three is burnt!")
                + f"\nTotal so far: {fmt(cr(self.stake, f.total))}")
        self.view = PotatoView(self)
        await self.show(png, text, self.view)

    async def potato_pick(self, i: int) -> None:
        f = self.rnd.bonus.feature
        rnd = f.round
        f.pick(i)
        await self.show_potato(reveal=f.rounds[rnd], pick=i)

    async def show_picnic(self, last: int | None = None) -> None:
        f = self.rnd.bonus.feature
        png = await asyncio.to_thread(I.render_picnic, f.to_dict(), self.stake, rtp(), last)
        if f.done:
            text = (f"🐜 **The ants found the picnic!** Picnic Pick won {fmt(cr(self.stake, f.total))}"
                    if "ants" in [f.cells[i] for i in f.opened] else
                    f"🧺 **Every hamper opened!** Picnic Pick won {fmt(cr(self.stake, f.total))}")
            await self.show(png, text, None)
            await asyncio.sleep(2.2)
            await self.finish()
            return
        v = f.cells[last] if last is not None else None
        head = ("🧺 **Picnic Pick** — open hampers until the ants show up!" if last is None
                else "✖️ **DOUBLER!** Your total will be doubled!" if v == "double"
                else f"🧺 **+{credits_text(cr(self.stake, v))}!**")
        self.view = PicnicView(self)
        await self.show(png, f"{head}\nTotal so far: {fmt(cr(self.stake, f.total))}", self.view)

    async def picnic_pick(self, i: int) -> None:
        self.rnd.bonus.feature.pick(i)
        await self.show_picnic(last=i)

    async def play_free_spins(self) -> None:
        f = self.rnd.bonus.feature
        name = E.TIER_NAMES[f.kind]
        base_won = cr(self.stake, self.rnd.spin.x)
        won, total = 0, E.FREE_SPINS[f.kind]
        for i, s in enumerate(f.spins, start=1):
            header = f"🔥 **{name}** — spin {i} of {total} · won so far {fmt(won)}"
            won += await self.animate_spin(s, header, won_before=base_won + won, free=(i, total), free_name=name)
            if len(s.grills) >= 3 and total < f.awarded:
                total = min(f.awarded, total + E.RETRIGGER_SPINS)
                await self.frame(f"♨️ **3 grills: +{E.RETRIGGER_SPINS} free spins!**", grid=s.grid,
                                 grill_cells=set(s.grills), banner=f"+{E.RETRIGGER_SPINS} SPINS!",
                                 free=(i, total), free_name=name, win=base_won + won)
                await asyncio.sleep(1.5)
        await asyncio.sleep(0.5)
        await self.finish()

    async def play_golden(self) -> None:
        f = self.rnd.bonus.feature
        value = E.GOLDEN_START
        steps = f.steps
        # at most 8 pictures: group the steps if there are many
        group = max(1, -(-len(steps) // 8))
        png = await asyncio.to_thread(I.render_golden, value, None, self.stake, rtp(), False)
        await self.show(png, f"👑 **Golden Grill** starts at **{value}x**!")
        await asyncio.sleep(1.4)
        for i in range(0, len(steps), group):
            add = sum(steps[i:i + group])
            value = min(E.GOLDEN_MAX, value + add)
            png = await asyncio.to_thread(I.render_golden, value, add, self.stake, rtp(), False)
            await self.show(png, f"👑 **Golden Grill** — **{value}x** and climbing...")
            await asyncio.sleep(1.1)
        png = await asyncio.to_thread(I.render_golden, int(f.total), None, self.stake, rtp(), True)
        await self.show(png, f"👑 **The grill cooled at {int(f.total)}x!** "
                             f"Golden Grill won {fmt(cr(self.stake, f.total))}")
        await asyncio.sleep(2.0)
        await self.finish()

    # ------------------------------------------------------------------ the end
    async def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        if self.view is not None:
            self.view.stop()
        rnd = self.rnd
        rnd.finish(rng)   # only does something after a timeout
        payout = E.payout(self.paid, rnd, rtp(), self.stake)
        bal = await self.wager.settle(payout)
        lines = []
        base = cr(self.stake, rnd.spin.x)
        if rnd.spin.wins:
            lines.append(f"🎰 Reels won {fmt(base)}")
        if rnd.bonus:
            lines += self.bonus_text
            if rnd.bonus.feature is not None:
                lines.append(f"♨️ Grill Bonus won {fmt(cr(self.stake, rnd.bonus.total))}")
        if payout >= self.stake * E.MAX_WIN:
            lines.append(f"🏆 **Maximum win reached: {credits_text(E.MAX_WIN)}x!**")
        mult = payout / self.stake if self.stake else 0
        if mult >= 50:
            lines.insert(0, "🎆 **EPIC WIN!**")
        elif mult >= 20:
            lines.insert(0, "🎉 **MEGA WIN!**")
        elif mult >= 10:
            lines.insert(0, "🎉 **BIG WIN!**")
        if self.mode != "spin":
            lines += ["", f"Bonus buy: paid {fmt(self.paid)} ({E.PRICES[self.mode]:g}x your bet of {fmt(self.stake)})."]
        lines += ["", result_line(self.paid, payout, bal)]
        view = AfterView(self.cog, self.user, self.stake, self.mode)
        view.message = self.message
        png = self.last_png
        if png is None or not rnd.bonus:
            spin = rnd.spin
            mega = (1, 3, spin.detail["symbol"]) if spin.feature == "mega" else None
            lit = ({tuple(c) for c in spin.detail["cells"]} if spin.feature == "flame" else
                   {(r, row) for r in spin.detail.get("reels", []) for row in range(E.ROWS)}
                   if spin.feature == "hot" else set())
            png = await asyncio.to_thread(I.render, I.Frame(
                spin.grid, lines=[w.line for w in spin.wins], win_cells={c for w in spin.wins for c in w.cells},
                lit=lit, mega=mega, bet=self.stake, win=payout, message=spin_message(self.stake, spin)))
        try:
            await self.show(png, "\n".join(lines), view, result_color(self.paid, payout))
        finally:
            self.cog.release(self)
        log.info("bangerbash: user %s %s bet %s (paid %s), feature %s, bonus %s, won %s", self.user.id, self.mode,
                 f"{credits_text(self.stake)}", f"{credits_text(self.paid)}", rnd.spin.feature or "-",
                 (E.TIERS[rnd.bonus.tier] + (" (burnt)" if rnd.bonus.lost else "")) if rnd.bonus else "-",
                 f"{credits_text(payout)}")

    async def timed_out(self) -> None:
        """Nobody chose in time: finish with default choices on the message itself."""
        async with self.lock:
            if self.finished:
                return
            self.bind_message()
            try:
                await self.finish()
            except discord.HTTPException:
                pass

    async def act(self, interaction: discord.Interaction, coro_factory) -> None:
        """Runs a player's choice: one at a time, only for the owner, routed through their interaction."""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This isn't your grill! Start your own with `/bangerbash`.",
                                                    ephemeral=True)
            return
        if self.lock.locked() or self.finished:
            await interaction.response.defer()
            return
        async with self.lock:
            if self.view is not None:
                self.view.stop()
            self.bind(interaction, new=False)
            try:
                await coro_factory()
            except Exception:
                # never leave the bet hanging: finish with defaults and pay what the round is worth
                if not self.finished:
                    self.bind_message()
                    try:
                        await self.finish()
                    except Exception:
                        log.exception("bangerbash: couldn't finish the round of user %s", self.user.id)
                raise


class DecisionView(BaseView):
    def __init__(self, game: Game):
        super().__init__(timeout=DECISION_TIMEOUT)
        self.game = game

    async def on_timeout(self) -> None:
        await self.game.timed_out()


class LadderView(DecisionView):
    def __init__(self, game: Game, chance: float | None):
        super().__init__(game)
        kind = E.TIERS[game.rnd.bonus.tier]
        take = discord.ui.Button(label=f"Take {E.TIER_NAMES[kind]}", style=discord.ButtonStyle.success, emoji="🍽️")
        take.callback = self.on_take
        self.add_item(take)
        if chance is not None:
            heat = discord.ui.Button(label=f"Turn up the heat ({chance:.0%})", style=discord.ButtonStyle.danger,
                                     emoji="🔥")
            heat.callback = self.on_heat
            self.add_item(heat)

    async def on_take(self, interaction: discord.Interaction):
        await self.game.act(interaction, self.game.take)

    async def on_heat(self, interaction: discord.Interaction):
        await self.game.act(interaction, self.game.gamble)


class PotatoView(DecisionView):
    def __init__(self, game: Game):
        super().__init__(game)
        for i, letter in enumerate("ABC"):
            b = discord.ui.Button(label=f"Sausage {letter}", style=discord.ButtonStyle.primary, emoji="🌭")
            b.callback = self._pick(i)
            self.add_item(b)

    def _pick(self, i: int):
        async def cb(interaction: discord.Interaction):
            await self.game.act(interaction, lambda: self.game.potato_pick(i))
        return cb


class PicnicView(DecisionView):
    def __init__(self, game: Game):
        super().__init__(game)
        f = game.rnd.bonus.feature
        for i in range(E.PICNIC_CELLS):
            opened = i in f.opened
            b = discord.ui.Button(label=str(i + 1), row=i // 5, disabled=opened,
                                  style=discord.ButtonStyle.secondary if opened else discord.ButtonStyle.primary)
            b.callback = self._pick(i)
            self.add_item(b)

    def _pick(self, i: int):
        async def cb(interaction: discord.Interaction):
            await self.game.act(interaction, lambda: self.game.picnic_pick(i))
        return cb


BUYS_OFF = "Bonus buys are switched off on this server."


def buy_price(mode: str) -> tuple[float, str]:
    """take_bet's price and name for a round in this mode."""
    return (1, "") if mode == "spin" else (E.PRICES[mode], E.BUYS[mode]["name"])


class AfterView(BaseView):
    def __init__(self, cog: "BangerBash", owner: discord.abc.User, stake: int, mode: str = "spin"):
        super().__init__(timeout=AFTER_TIMEOUT)
        self.cog = cog
        self.owner = owner
        self.stake = stake          # the bet
        self.mode = mode            # "spin", or the bonus buy the button buys again
        self.message = None
        if mode != "spin":
            self.spin_again.label = f"Buy again ({E.PRICES[mode]:g}x)"

    @discord.ui.button(label="Spin again", style=discord.ButtonStyle.success, emoji="🔁")
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("This isn't your grill! Start your own with `/bangerbash`.",
                                                    ephemeral=True)
            return
        if self.cog.busy(interaction):
            await send_error(interaction, "You already have a Banger Bash round going. Finish it first.")
            return
        if self.mode != "spin" and not config.BANGER_BUYS:
            await send_error(interaction, BUYS_OFF)
            return
        self.cog.hold(interaction)
        game = None
        try:
            wager = await take_bet(interaction, str(self.stake), GAME, *buy_price(self.mode))
            if not wager:
                return
            self.stop()
            game = Game(self.cog, self.owner, wager, self.mode)
            self.cog.games[(interaction.guild_id, interaction.user.id)] = game
            game.bind(interaction, new=False)
            await self.cog.run(game)
        finally:
            if game is None or game.finished:
                self.cog.release_key((interaction.guild_id, interaction.user.id), game)

    @discord.ui.button(label="Paytable", style=discord.ButtonStyle.secondary, emoji="📜")
    async def paytable(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_paytable(interaction)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class BangerBash(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # one round at a time per player: (guild, user) -> Game (None while the bet is being taken)
        self.games: dict[tuple[int, int], Game | None] = {}

    def busy(self, interaction: discord.Interaction) -> bool:
        return (interaction.guild_id, interaction.user.id) in self.games

    def hold(self, interaction: discord.Interaction) -> None:
        self.games[(interaction.guild_id, interaction.user.id)] = None

    def release(self, game: Game) -> None:
        self.release_key((game.wager.guild_id, game.user.id), game)

    def release_key(self, key, game: Game | None) -> None:
        if self.games.get(key) is game:
            self.games.pop(key, None)

    async def run(self, game: Game) -> None:
        """Plays the paid spin. If showing it fails part-way, the round is finished with defaults and paid."""
        try:
            async with game.lock:
                await game.play()
        except Exception:
            if game.shown and not game.finished:
                game.bind_message()
                try:
                    await game.finish()
                except Exception:
                    log.exception("bangerbash: couldn't finish the round of user %s", game.user.id)
            elif not game.finished:
                # Never shown, so nothing was played: refund now. (The error handler's refund_unposted skips
                # bets whose interaction was already answered, so it can't be relied on here.)
                self.release(game)
                if not game.wager.settled:
                    try:
                        await game.wager.refund()
                        log.warning("bangerbash: round of user %s failed before it was shown; refunded %s",
                                    game.user.id, f"{credits_text(game.paid)}")
                    except Exception:
                        log.exception("bangerbash: couldn't refund the bet of user %s", game.user.id)
            raise

    # Banger Bash is played in the Activity (/casino). The chat version below is switched off but kept: with
    # BANGERBASH_IN_CHAT=1 in .env, /bangerbash takes a bet and plays it in the text channel.
    if config.BANGERBASH_IN_CHAT:
        @app_commands.command(name="bangerbash", description="Banger Bash: a 5x3 barbecue slot with random "
                                                             "features and the Grill Bonus.")
        @app_commands.describe(bet=BET_DESC, buy="Bonus buy: a spin that is sure to land the grills (costs more "
                                                  "than the bet)")
        @app_commands.choices(buy=[app_commands.Choice(name=f"{b['name']}: {b['grills']} grills ({E.PRICES[k]:g}x "
                                                            f"your bet)", value=k) for k, b in E.BUYS.items()])
        @gambling_only()
        async def bangerbash(self, interaction: discord.Interaction, bet: str,
                             buy: app_commands.Choice[str] | None = None):
            await self.play_in_chat(interaction, bet, buy.value if buy else "spin")

    async def play_in_chat(self, interaction: discord.Interaction, bet: str, mode: str = "spin") -> None:
        """The chat version of Banger Bash (only used when BANGERBASH_IN_CHAT is on)."""
        if self.busy(interaction):
            await send_error(interaction, "You already have a Banger Bash round going. Finish it first.")
            return
        if mode != "spin" and not config.BANGER_BUYS:
            await send_error(interaction, BUYS_OFF)
            return
        self.hold(interaction)
        game = None
        try:
            wager = await take_bet(interaction, bet, GAME, *buy_price(mode))
            if not wager:
                return
            game = Game(self, interaction.user, wager, mode)
            self.games[(interaction.guild_id, interaction.user.id)] = game
            game.bind(interaction, new=True)
            await self.run(game)
        finally:
            if game is None or game.finished:
                self.release_key((interaction.guild_id, interaction.user.id), game)

    @app_commands.command(name="bangerbash-paytable", description="Banger Bash: paytable, features and the bonus.")
    async def paytable_cmd(self, interaction: discord.Interaction):
        await self.send_paytable(interaction)

    async def send_paytable(self, interaction: discord.Interaction) -> None:
        png = await asyncio.to_thread(I.render_symbols, rtp())
        lines = await asyncio.to_thread(I.render_paylines)
        await interaction.response.send_message(embeds=[paytable_embed(), discord.Embed(color=ORANGE).set_image(
            url="attachment://lines.png")], files=[png_file(png), png_file(lines, "lines.png")], ephemeral=True)

    async def cog_unload(self) -> None:
        # pay out every unfinished round with default choices, so nobody's bonus is lost
        for game in [g for g in self.games.values() if g is not None and not g.finished]:
            try:
                await game.timed_out()
            except Exception:
                log.exception("bangerbash: couldn't finish the round of user %s on unload", game.user.id)


async def setup(bot):
    await bot.add_cog(BangerBash(bot))
