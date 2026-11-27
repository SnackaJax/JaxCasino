"""Duels: challenge another player to a coinflip, dice roll or rock-paper-scissors. Winner takes the pot."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import gambling_only
from utils.economy import Wager, fmt, send_error, take_bet
from utils.rng import rng
from utils.views import BaseView
from utils.bignum import credits_text

log = logging.getLogger("jaxcasino.duel")

GAMES = {"coinflip": "🪙 Coinflip", "dice": "🎲 Dice", "rps": "✊ Rock Paper Scissors"}
RPS = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
CHALLENGE_TIME = 60
PICK_TIME = 60


class Duel:
    def __init__(self, challenger: discord.Member, opponent: discord.Member, wager: Wager, game: str):
        self.challenger = challenger
        self.opponent = opponent
        self.stake = wager.stake
        self.game = game
        self.wagers: dict[int, Wager] = {challenger.id: wager}
        self.message: discord.Message | discord.InteractionMessage | None = None
        self.finished = False

    def __str__(self) -> str:
        return f"{self.game} duel {self.challenger} vs {self.opponent} for {credits_text(self.stake)}"

    @property
    def title(self) -> str:
        return f"⚔️ Duel — {GAMES[self.game]}"

    @property
    def content(self) -> str:
        """Both players are tagged so each can spot their duel in the channel."""
        return f"{self.challenger.mention} vs {self.opponent.mention}"

    def players_line(self) -> str:
        return f"{self.challenger.mention} vs {self.opponent.mention} for {fmt(self.stake)} each"

    async def resolve(self, winner: discord.Member, detail: str) -> discord.Embed:
        self.finished = True
        loser = self.opponent if winner.id == self.challenger.id else self.challenger
        win_bal = await self.wagers[winner.id].settle(self.stake * 2)
        await self.wagers[loser.id].settle(0)
        log.info("%s: %s won", self, winner)
        embed = discord.Embed(title=self.title, color=discord.Color.gold())
        embed.description = (f"{self.players_line()}\n\n{detail}\n\n"
                             f"🏆 {winner.mention} wins {fmt(self.stake * 2)}!\n"
                             f"Balance: {fmt(win_bal)}")
        return embed

    async def refund_all(self, reason: str) -> None:
        self.finished = True
        log.info("%s: refunded (%s)", self, reason)
        for w in self.wagers.values():
            await w.refund()

    async def edit(self, **kwargs) -> None:
        """Edits the duel message outside of a button press (timeouts)."""
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            log.warning("%s: couldn't edit the duel message", self, exc_info=True)


async def update(interaction: discord.Interaction, **kwargs) -> None:
    """Answers a button press by editing the duel message. If the press can't be answered any more
    (Discord gives 3 seconds), the message is edited directly so the duel never looks stuck."""
    try:
        await interaction.response.edit_message(**kwargs)
        return
    except (discord.NotFound, discord.InteractionResponded) as e:
        log.warning("Couldn't answer the button press (%s); editing the message directly", e)
    try:
        await interaction.message.edit(**kwargs)
    except discord.HTTPException:
        log.exception("Couldn't edit the duel message")


class ChallengeView(BaseView):
    def __init__(self, duel: Duel):
        super().__init__(timeout=CHALLENGE_TIME)
        self.duel = duel
        self.lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.duel.challenger.id, self.duel.opponent.id):
            await interaction.response.send_message("This duel isn't yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        async with self.lock:
            if d.finished or d.opponent.id in d.wagers:
                return await interaction.response.defer()
            if interaction.user.id != d.opponent.id:
                return await interaction.response.send_message("Only the challenged player can accept.", ephemeral=True)
            wager = await Wager.create(interaction.client.db, interaction.guild_id, d.opponent.id, d.stake, "duel",
                                       channel=interaction.channel)
            if wager is None:
                return await interaction.response.send_message(f"You can't afford {fmt(d.stake)}.", ephemeral=True)
            d.wagers[d.opponent.id] = wager
            self.stop()
            log.info("%s: accepted", d)

            if d.game == "rps":
                view = RPSView(d)
                await update(interaction, content=d.content, embed=view.embed(), view=view)
                return

            if d.game == "coinflip":
                winner = rng.choice([d.challenger, d.opponent])
                detail = f"🪙 The coin landed on **{winner.display_name}**'s side!"
            else:
                while True:
                    a, b = rng.randint(1, 100), rng.randint(1, 100)
                    if a != b:
                        break
                winner = d.challenger if a > b else d.opponent
                detail = (f"🎲 {d.challenger.display_name} rolled **{a}**\n"
                          f"🎲 {d.opponent.display_name} rolled **{b}**")
            embed = await d.resolve(winner, detail)
            await update(interaction, content=d.content, embed=embed, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✖️")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        async with self.lock:
            if d.finished or d.opponent.id in d.wagers:
                return await interaction.response.defer()
            self.stop()
            who = "was declined" if interaction.user.id == d.opponent.id else "was cancelled"
            await d.refund_all(who)
            embed = discord.Embed(title=d.title, color=discord.Color.light_grey(),
                                  description=f"{d.players_line()}\n\nThe duel {who}. Bets were refunded.")
            await update(interaction, content=d.content, embed=embed, view=None)

    async def on_timeout(self) -> None:
        d = self.duel
        async with self.lock:
            if d.finished or d.opponent.id in d.wagers:
                return
            await d.refund_all("challenge expired")
            embed = discord.Embed(title=d.title, color=discord.Color.light_grey(),
                                  description=f"{d.players_line()}\n\n⏰ The challenge expired. Bets were refunded.")
            await d.edit(content=d.content, embed=embed, view=None)


class RPSView(BaseView):
    def __init__(self, duel: Duel):
        super().__init__(timeout=PICK_TIME)
        self.duel = duel
        self.picks: dict[int, str] = {}
        self.note = ""
        self.lock = asyncio.Lock()
        for name, emoji in RPS.items():
            button = discord.ui.Button(label=name.title(), emoji=emoji, style=discord.ButtonStyle.primary)
            button.callback = self.make_callback(name)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.duel.challenger.id, self.duel.opponent.id):
            await interaction.response.send_message("This duel isn't yours.", ephemeral=True)
            return False
        return True

    def embed(self) -> discord.Embed:
        d = self.duel
        status = "\n".join(
            f"{'✅ picked' if p.id in self.picks else '⏳ choosing…'} — {p.display_name}"
            for p in (d.challenger, d.opponent))
        return discord.Embed(title=d.title, color=discord.Color.blurple(),
                             description=f"{d.players_line()}\n\n{self.note}"
                                         f"Pick your move! Moves stay hidden until both players have picked.\n\n"
                                         f"{status}")

    def outcome(self) -> tuple[discord.Member | None, str]:
        """Returns (winner, detail) once both have picked; winner is None on a tie."""
        d = self.duel
        a, b = self.picks[d.challenger.id], self.picks[d.opponent.id]
        detail = (f"{d.challenger.display_name}: {RPS[a]} **{a}**\n"
                  f"{d.opponent.display_name}: {RPS[b]} **{b}**")
        if a == b:
            return None, detail
        return (d.challenger if BEATS[a] == b else d.opponent), detail

    def make_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            d = self.duel
            async with self.lock:
                if d.finished:
                    return await interaction.response.defer()
                if interaction.user.id in self.picks:
                    return await interaction.response.send_message(
                        f"You already picked {RPS[self.picks[interaction.user.id]]}. Waiting for the other player.",
                        ephemeral=True)
                self.picks[interaction.user.id] = choice
                log.debug("%s: %s picked %s", d, interaction.user, choice)

                if len(self.picks) < 2:
                    await update(interaction, embed=self.embed(), view=self)
                    first_pick = True
                else:
                    first_pick = False
                    winner, detail = self.outcome()
                    if winner is None:
                        a = self.picks[d.challenger.id]
                        log.info("%s: tie on %s, picking again", d, a)
                        self.picks.clear()
                        self.note = f"Both picked {RPS[a]} **{a}**, it's a tie! Pick again.\n\n"
                        await update(interaction, embed=self.embed(), view=self)
                    else:
                        self.stop()
                        embed = await d.resolve(winner, detail)
                        await update(interaction, embed=embed, view=None)
            if first_pick:
                try:
                    await interaction.followup.send(f"You picked {RPS[choice]} **{choice}**.", ephemeral=True)
                except discord.HTTPException:
                    pass
        return callback

    async def on_timeout(self) -> None:
        d = self.duel
        async with self.lock:
            if d.finished:
                return
            if len(self.picks) == 1:
                picker_id = next(iter(self.picks))
                winner = d.challenger if picker_id == d.challenger.id else d.opponent
                embed = await d.resolve(winner, "⏰ The other player didn't pick in time and forfeits.")
            else:
                await d.refund_all("nobody picked")
                embed = discord.Embed(title=d.title, color=discord.Color.light_grey(),
                                      description=f"{d.players_line()}\n\n⏰ Nobody picked. Bets were refunded.")
            await d.edit(embed=embed, view=None)


class Duels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="duel", description="Challenge another player. Winner takes the pot.")
    @app_commands.describe(opponent="Who to challenge", bet="Amount each player puts in", game="What to play")
    @app_commands.choices(game=[app_commands.Choice(name=v, value=k) for k, v in GAMES.items()])
    @gambling_only()
    async def duel(self, interaction: discord.Interaction, opponent: discord.Member, bet: str,
                   game: app_commands.Choice[str] | None = None):
        if opponent.bot or opponent.id == interaction.user.id:
            await send_error(interaction, "You can't duel yourself or a bot.")
            return
        wager = await take_bet(interaction, bet, "duel")
        if not wager:
            return
        d = Duel(interaction.user, opponent, wager, game.value if game else "coinflip")
        log.info("%s: challenge sent", d)
        embed = discord.Embed(
            title=d.title, color=discord.Color.orange(),
            description=f"{d.players_line()}\n\n{opponent.mention}, do you accept? (expires in {CHALLENGE_TIME}s)")
        view = ChallengeView(d)
        await interaction.response.send_message(
            content=d.content, embed=embed, view=view,
            allowed_mentions=discord.AllowedMentions(users=[interaction.user, opponent], roles=False, everyone=False))
        d.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Duels(bot))
