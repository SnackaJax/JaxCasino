"""/slots: Cosmic Lab, a 6x4 video slot with 20 paylines, wilds and free spins with sticky wilds.

The game logic lives in utils/slot_engine.py and the picture in utils/slot_image.py. A whole round (the paid
spin plus any free spins) is decided up front, then animated, then paid out in one go.

The bonus buy (/slots buy:Free Spins, SLOTS_BUYS in .env) costs E.BUY_PRICE times the bet for a spin that is sure to
start free spins. It is played and paid exactly like a paid spin: every pay is a multiple of the bet, not the price.
"""
import asyncio
import io
import logging
import math
from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import slot_engine as E
from utils.checks import gambling_only
from utils.economy import buy_limit_note, fmt, result_color, result_line, send_error, take_bet, tag
from utils.rng import rng
from utils.slot_image import SlotFrame, render, render_paylines
from utils.views import BaseView
from utils.bignum import mul_floor

log = logging.getLogger("jaxcasino.slots")

BET_DESC = "Total bet for all 20 lines: any amount (e.g. 1234) or a shortcut: 2.5k, 1m, half, all"
BUY_DESC = (f"Bonus buy: pay {E.BUY_PRICE:g}x your bet for a spin that is sure to land {E.BUY_COMETS} comets "
            f"({E.FREE_SPINS[E.BUY_COMETS]} free spins)")
BUYS_OFF = "Bonus buys are switched off on this server."
REEL_STEP = 0.7       # seconds between reel stops
FREE_SPIN_PAUSE = 1.0
VIEW_TIMEOUT = 180
FOOTER = "20 lines · wilds on reels 2–6 · 3+ comets = free spins with sticky wilds"

Edit = Callable[[discord.Embed, bytes, "SlotView | None"], Awaitable[None]]


def pay_scale() -> float:
    return config.SLOTS_RTP / E.MEASURED_RTP


def credits(stake: int, line_bets: int) -> int:
    return mul_floor(stake, line_bets, pay_scale(), div=E.LINES)


def mult_text(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") + "x"


def sym_name(key: str) -> str:
    name, emoji, _ = E.SYMBOLS[key]
    return f"{emoji} {name}"


def win_lines_text(stake: int, result: E.SpinResult, limit: int = 6) -> list[str]:
    wins = sorted(result.wins, key=lambda w: -w.pay)
    out = [f"Line {w.line + 1}: {w.count}× {sym_name(w.symbol)} — {fmt(credits(stake, w.pay))}" for w in wins[:limit]]
    if len(wins) > limit:
        out.append(f"…and {len(wins) - limit} more line{'s' if len(wins) - limit != 1 else ''}")
    return out


def frame_for(result: E.SpinResult, stake: int, won_so_far: int, **kw) -> SlotFrame:
    cells = {c for w in result.wins for c in w.cells}
    return SlotFrame(result.grid, lines=[w.line for w in result.wins], win_cells=cells, sticky=result.sticky,
                     bet=stake, win=won_so_far, spin_seed=rng.randrange(1 << 16), **kw)


def spin_message(stake: int, result: E.SpinResult) -> str:
    if not result.wins:
        return "No win"
    total = sum(credits(stake, w.pay) for w in result.wins)
    if len(result.wins) == 1:
        w = result.wins[0]
        return f"Line {w.line + 1} · {w.count}× {E.SYMBOLS[w.symbol][0]} · {total:,}"
    return f"{len(result.wins)} winning lines · {total:,}"


def embed(user: discord.abc.User, description: str, color=discord.Color.purple()) -> discord.Embed:
    e = discord.Embed(title="🎰 Cosmic Lab", description=description, color=color)
    e.set_image(url="attachment://slots.png")
    e.set_footer(text=FOOTER)
    return e


def paytable_embed() -> discord.Embed:
    scale = pay_scale()
    lines = []
    for key in reversed(E.PAYING):
        pays = " · ".join(f"{n}: **{mult_text(p * scale / E.LINES)}**" for n, p in zip((3, 4, 5, 6), E.SYMBOLS[key][2]))
        lines.append(f"{sym_name(key)} — {pays}")
    spins = " · ".join(f"{k}{'+' if k == 6 else ''}: {v}" for k, v in E.FREE_SPINS.items())
    e = discord.Embed(title="🎰 Cosmic Lab — paytable", color=discord.Color.purple(), description=(
        "Pays are times your **total bet**, for 3–6 matching symbols in a row on a payline, starting from the "
        "leftmost reel. Wins on different lines add up.\n\n" + "\n".join(lines) + "\n\n"
        f"{sym_name(E.WILD)} — lands on reels 2–6 and stands in for every symbol except the comet.\n"
        f"{sym_name(E.SCATTER)} — 3 or more anywhere start free spins ({spins} spins). During free spins every "
        f"wild that lands **sticks** until they end, and 3 more comets add {E.RETRIGGER_SPINS} spins.\n\n"
        f"Maximum win: {E.MAX_WIN:,}x your bet per spin. Returns {config.SLOTS_RTP * 100:g}% of bets "
        f"on average."
        + (f"\n\n**Bonus buy:** `/slots buy:{E.BUY_NAME}` costs **{E.BUY_PRICE:g}x** your bet for a spin that is "
           f"sure to land {E.BUY_COMETS} comets ({E.FREE_SPINS[E.BUY_COMETS]} free spins). It pays back "
           f"{E.buy_rtp(config.SLOTS_RTP) * 100:.1f}% on average.{buy_limit_note()}" if config.SLOTS_BUYS else "")))
    e.set_image(url="attachment://paylines.png")
    return e


class SlotView(BaseView):
    def __init__(self, cog: "Slots", owner: discord.abc.User, bet: int, mode: str = "spin"):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner = owner
        self.bet = bet
        self.mode = mode           # "spin", or "free" for the bonus buy (the button buys it again)
        self.busy = False
        self.message: discord.Message | discord.InteractionMessage | None = None
        if mode != "spin":
            self.spin_again.label = f"Buy again ({E.BUY_PRICE:g}x)"

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.spin_again.disabled = busy
        # No timeout while the reels turn (free spins can take a while); the countdown restarts after.
        self.timeout = None if busy else VIEW_TIMEOUT

    @discord.ui.button(label="Spin again", style=discord.ButtonStyle.success, emoji="🔁")
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("This isn't your slot machine! Start your own with `/slots`.",
                                                    ephemeral=True)
            return
        if self.busy:
            await interaction.response.defer()
            return
        if self.mode != "spin" and not config.SLOTS_BUYS:
            await send_error(interaction, BUYS_OFF)
            return
        if not await self.cog.claim(interaction):
            return
        try:
            wager = await take_bet(interaction, str(self.bet), "slots", *buy_price(self.mode))
            if not wager:
                return
            self.message = interaction.message
            self.set_busy(True)

            async def first(e, png, view):
                await interaction.response.edit_message(embed=e, attachments=[slot_file(png)], view=view)

            async def edit(e, png, view):
                await interaction.edit_original_response(embed=e, attachments=[slot_file(png)], view=view)

            await self.cog.play(self.owner, wager, self, first, edit, self.mode)
        finally:
            self.cog.release(interaction)

    @discord.ui.button(label="Paytable & lines", style=discord.ButtonStyle.secondary, emoji="📜")
    async def paylines(self, interaction: discord.Interaction, button: discord.ui.Button):
        png = await asyncio.to_thread(render_paylines)
        await interaction.response.send_message(embed=paytable_embed(),
                                                file=discord.File(io.BytesIO(png), filename="paylines.png"),
                                                ephemeral=True)

    async def on_timeout(self) -> None:
        if self.message and not self.busy:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


def slot_file(png: bytes) -> discord.File:
    return discord.File(io.BytesIO(png), filename="slots.png")


def buy_price(mode: str) -> tuple[float, str]:
    """take_bet's price and name for a round in this mode."""
    return (1, "") if mode == "spin" else (E.BUY_PRICE, E.BUY_NAME)


class Slots(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Players with a spin in progress (free spins included): one slot machine at a time per player.
        self.spinning: set[tuple[int, int]] = set()

    async def claim(self, interaction: discord.Interaction) -> bool:
        """Marks the player as spinning. If they already are, tells them so and returns False."""
        key = (interaction.guild_id, interaction.user.id)
        if key in self.spinning:
            await send_error(interaction, "You already have a slot machine spinning. Wait until it has finished "
                                          "(free spins included) before you start another spin.")
            return False
        self.spinning.add(key)
        return True

    def release(self, interaction: discord.Interaction) -> None:
        self.spinning.discard((interaction.guild_id, interaction.user.id))

    @app_commands.command(name="slots", description="Cosmic Lab: a 6x4 slot with 20 paylines and free spins.")
    @app_commands.describe(bet=BET_DESC, buy=BUY_DESC)
    @app_commands.choices(buy=[app_commands.Choice(name=f"{E.BUY_NAME} ({E.BUY_PRICE:g}x your bet)", value="free")])
    @gambling_only()
    async def slots(self, interaction: discord.Interaction, bet: str, buy: app_commands.Choice[str] | None = None):
        mode = buy.value if buy else "spin"
        if mode != "spin" and not config.SLOTS_BUYS:
            await send_error(interaction, BUYS_OFF)
            return
        if not await self.claim(interaction):
            return
        try:
            wager = await take_bet(interaction, bet, "slots", *buy_price(mode))
            if not wager:
                return
            view = SlotView(self, interaction.user, wager.bet, mode)
            view.set_busy(True)

            async def first(e, png, v):
                await interaction.response.send_message(**tag(interaction.user), embed=e, file=slot_file(png),
                                                        view=v)
                try:
                    view.message = await interaction.original_response()
                except discord.HTTPException:
                    pass  # the spin is on screen; only the timeout cleanup needs this

            async def edit(e, png, v):
                await interaction.edit_original_response(embed=e, attachments=[slot_file(png)], view=v)

            await self.play(interaction.user, wager, view, first, edit, mode)
        finally:
            self.release(interaction)

    async def play(self, user: discord.abc.User, wager, view: SlotView, first: Edit, edit: Edit,
                   mode: str = "spin") -> None:
        # every pay is a multiple of the bet; for the bonus buy the stake (what was paid) is its price x the bet
        paid = wager.stake
        stake = wager.bet
        rnd = E.play_round(rng, mode)
        base = rnd.base
        cap = stake * E.MAX_WIN
        base_won = min(sum(credits(stake, w.pay) for w in base.wins), cap)
        # The whole round is decided now; if showing it fails part-way, this is what gets paid.
        round_won = min(base_won + sum(credits(stake, w.pay) for s in rnd.free for w in s.wins), cap)

        shown = False

        async def show(frame: SlotFrame, text: str, send=edit, color=discord.Color.purple()):
            nonlocal shown
            png = await asyncio.to_thread(render, frame)
            await send(embed(user, text, color), png, view)
            shown = True

        try:
            # Paid spin: reels stop left to right.
            spinning = (f"Bet {fmt(stake)} on 20 lines — spinning..." if mode == "spin" else
                        f"☄️ Bought **{E.BUY_NAME}** for {fmt(paid)} (bet {fmt(stake)}) — spinning...")
            await show(SlotFrame(base.grid, stopped=0, bet=stake, message="Good luck!",
                                 spin_seed=rng.randrange(1 << 16)), spinning, send=first)
            for stopped in (2, 4):
                await asyncio.sleep(REEL_STEP)
                await show(SlotFrame(base.grid, stopped=stopped, bet=stake, message="Good luck!",
                                     spin_seed=rng.randrange(1 << 16)), spinning)
            await asyncio.sleep(REEL_STEP)
            payout, played = base_won, 0
            spins = E.free_spins_for(len(base.scatters))
            if spins:
                msg = f"{len(base.scatters)} COMETS · {spins} FREE SPINS!"
                text = "\n".join([f"☄️ **{len(base.scatters)} comets! You won {spins} free spins!**",
                                  *win_lines_text(stake, base)])
                await show(frame_for(base, stake, base_won, scatter_cells=set(base.scatters), message=msg), text)
                await asyncio.sleep(2.0)
                payout, played = await self._free_spins(rnd, stake, base_won, show)
            # Final result
            bal = await wager.settle(payout)
            lines = []
            if played:
                if base_won:
                    lines += win_lines_text(stake, base)
                lines.append(f"☄️ {played} free spins won {fmt(payout - base_won)}")
            else:
                lines += win_lines_text(stake, base)
            if payout >= cap:
                lines.append(f"🏆 **Maximum win reached: {E.MAX_WIN:,}x!**")
            lines.append("")
            if mode != "spin":
                lines.append(f"Bonus buy: paid {fmt(paid)} ({E.BUY_PRICE:g}x your bet of {fmt(stake)}).")
            lines.append(result_line(paid, payout, bal))
            if played:
                msg = f"FREE SPINS WON {payout - base_won:,}" if payout > base_won else "No free spin wins"
                frame = frame_for(rnd.free[played - 1], stake, payout, message=msg, free=(played, played))
            else:
                frame = frame_for(base, stake, payout, message=spin_message(stake, base))
            view.set_busy(False)
            await show(frame, "\n".join(lines), color=result_color(paid, payout))
            log.info("slots: user %s %s bet %s (paid %s), %d line wins, %d free spins, won %s", user.id, mode,
                     f"{stake:,}", f"{paid:,}", len(base.wins), played, f"{payout:,}")
        except Exception:
            # Never leave the bet in limbo. If the spin never appeared, the error handler refunds it
            # (refund_unposted); once it was on screen, the round was decided up front, so pay its full result.
            if shown and not wager.settled:
                await wager.settle(round_won)
            view.set_busy(False)
            raise

    async def _free_spins(self, rnd: E.Round, stake: int, won: int, show) -> tuple[int, int]:
        """Shows the free spins one by one. Returns (total won including the paid spin, spins played)."""
        total = E.free_spins_for(len(rnd.base.scatters))
        i = 0
        for i, result in enumerate(rnd.free, start=1):
            await show(SlotFrame(result.grid, stopped=0, sticky=result.sticky, bet=stake, win=won,
                                 free=(i, total), message="Free spin...", spin_seed=rng.randrange(1 << 16)),
                       f"☄️ Free spin **{i}** of **{total}** · won so far {fmt(won)}")
            await asyncio.sleep(REEL_STEP)
            new_wilds = E.wilds_in(result.grid) - result.sticky
            wild_line = (f"🌀 **{len(new_wilds)} new sticky wild{'s' if len(new_wilds) != 1 else ''}!** "
                         f"They stay for the rest of the free spins." if new_wilds else None)
            if new_wilds:
                # Show the new wilds on their own first, so they stand out before any win lines are drawn.
                plural = "S" if len(new_wilds) != 1 else ""
                await show(SlotFrame(result.grid, sticky=result.sticky, new_wilds=new_wilds, bet=stake, win=won,
                                     free=(i, total), message=f"+{len(new_wilds)} STICKY WILD{plural}!"),
                           "\n".join([f"☄️ Free spin **{i}** of **{total}** · won so far {fmt(won)}", wild_line]))
                await asyncio.sleep(1.2)
            spin_won = sum(credits(stake, w.pay) for w in result.wins)
            won = min(won + spin_won, stake * E.MAX_WIN)
            retrigger = len(result.scatters) >= 3 and i < E.MAX_FREE_SPINS
            if retrigger:
                total = min(total + E.RETRIGGER_SPINS, E.MAX_FREE_SPINS)
            msg = (f"+{E.RETRIGGER_SPINS} FREE SPINS!" if retrigger
                   else f"Free spin win {spin_won:,}" if spin_won
                   else f"+{len(new_wilds)} STICKY WILD{'S' if len(new_wilds) != 1 else ''}!" if new_wilds
                   else "No win")
            text = "\n".join([f"☄️ Free spin **{i}** of **{total}** · won so far {fmt(won)}",
                              *(["**☄️ 3 more comets: +5 free spins!**"] if retrigger else []),
                              *([wild_line] if wild_line else []),
                              *win_lines_text(stake, result, limit=4)])
            await show(frame_for(result, stake, won, free=(i, total), message=msg, new_wilds=new_wilds,
                                 scatter_cells=set(result.scatters) if retrigger else set()), text)
            await asyncio.sleep(FREE_SPIN_PAUSE + (1.0 if result.wins or retrigger else 0))
            if won >= stake * E.MAX_WIN:
                break
        return won, i

    @app_commands.command(name="paytable", description="Cosmic Lab slot: paytable, paylines and features.")
    async def paytable(self, interaction: discord.Interaction):
        png = await asyncio.to_thread(render_paylines)
        await interaction.response.send_message(embed=paytable_embed(),
                                                file=discord.File(io.BytesIO(png), filename="paylines.png"),
                                                ephemeral=True)


async def setup(bot):
    await bot.add_cog(Slots(bot))
