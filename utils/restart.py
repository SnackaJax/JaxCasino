"""Restart: a player whose balance drops below the minimum bet presses a button (once by default) to get
RESTART_AMOUNT credits and keep playing.

The panel shows up on its own when a game leaves a player broke (a public message tagging them), and
privately when a broke player tries to place a bet or uses /restart.
"""
import asyncio
import logging

import discord

import config
from utils.economy import fmt
from utils.views import OwnedView

log = logging.getLogger("jaxcasino.restart")

PANEL_TIMEOUT = 15 * 60
OFFER_DELAY = 1.5   # let the game's own result message show first
_open_panels: dict[tuple[int, int], "RestartView"] = {}
_pending: set[tuple[int, int]] = set()


def progress(done: int) -> str:
    total = config.RESTART_PRESSES
    return f"{'🟩' * done}{'⬛' * (total - done)}  **{done}/{total}**"


def press_text(left: int) -> str:
    if config.RESTART_PRESSES == 1:
        return "Press the button"
    return f"Press the button **{left}** more time{'s' if left != 1 else ''}"


def broke_embed(user_id: int, balance: int, done: int) -> discord.Embed:
    left = config.RESTART_PRESSES - done
    embed = discord.Embed(title="💀 You're broke!", color=discord.Color.dark_red())
    embed.description = (
        f"<@{user_id}>, your balance is {fmt(balance)}, below the minimum bet of {fmt(config.MIN_BET)}.\n\n"
        f"{press_text(left)} to get {fmt(config.RESTART_AMOUNT)} and get back in the game."
    )
    if config.RESTART_PRESSES > 1:
        embed.description += f"\n\n{progress(done)}"
    return embed


def granted_embed(user_id: int, balance: int) -> discord.Embed:
    embed = discord.Embed(title="🔄 Back in the game!", color=discord.Color.green())
    embed.description = (f"✅ {fmt(config.RESTART_AMOUNT)} has been added to <@{user_id}>'s balance.\n"
                         f"New balance: {fmt(balance)}\n\nGood luck! 🍀")
    return embed


def blocked_embed(status: str, balance: int, until: float) -> discord.Embed:
    if status == "not_broke":
        text = (f"You have {fmt(balance)}, so you don't need a restart. Restarts are for players below the "
                f"minimum bet of {fmt(config.MIN_BET)}.")
    elif status == "in_game":
        text = ("You still have credits in a game that hasn't finished. Finish it first; if you end up broke, "
                "you'll get a restart button.")
    else:  # paid
        text = (f"You sent credits to someone with `/pay` recently, so restarts are locked until "
                f"<t:{int(until)}:R>. You can still use `/claim` for your daily credits.")
    return discord.Embed(title="🔄 Restart unavailable", description=text, color=discord.Color.light_grey())


class RestartView(OwnedView):
    def __init__(self, db, guild_id: int, user_id: int, balance: int):
        super().__init__(user_id, timeout=PANEL_TIMEOUT)
        self.db = db
        self.guild_id = guild_id
        self.balance = balance
        self.done = 0
        self.finished = False
        self.lock = asyncio.Lock()
        self.press_btn = discord.ui.Button(style=discord.ButtonStyle.danger, emoji="🔄")
        self.press_btn.callback = self.press
        self.add_item(self.press_btn)
        self.refresh()

    @property
    def key(self) -> tuple[int, int]:
        return self.guild_id, self.owner_id

    def refresh(self) -> None:
        left = config.RESTART_PRESSES - self.done
        if config.RESTART_PRESSES == 1:
            self.press_btn.label = f"Restart with {config.RESTART_AMOUNT:,} credits"
        else:
            self.press_btn.label = f"Press to restart ({left} left)"
        self.press_btn.style = discord.ButtonStyle.success if left == 1 else discord.ButtonStyle.danger

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This restart button isn't yours.", ephemeral=True)
            return False
        return True

    def close(self) -> None:
        self.finished = True
        self.stop()
        if _open_panels.get(self.key) is self:
            _open_panels.pop(self.key, None)

    async def press(self, interaction: discord.Interaction) -> None:
        async with self.lock:
            if self.finished:
                return await interaction.response.defer()
            self.done += 1
            if self.done < config.RESTART_PRESSES:
                self.refresh()
                return await interaction.response.edit_message(
                    embed=broke_embed(self.owner_id, self.balance, self.done), view=self)
            self.close()
            status, balance, until = await self.db.restart(self.guild_id, self.owner_id, grant=True)
            if status == "ok":
                log.info("User %s restarted in guild %s, balance now %s", self.owner_id, self.guild_id, balance)
                embed = granted_embed(self.owner_id, balance)
            else:
                embed = blocked_embed(status, balance, until)
            await interaction.response.edit_message(embed=embed, view=None)
            if status == "ok":
                # Restarted with a private button: the public panel in the channel is no longer needed.
                await retire(self.key, "already restarted with another button")

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.close()
        if self.message:
            embed = broke_embed(self.owner_id, self.balance, self.done)
            embed.description += "\n\n⏰ This button expired. Use `/restart` to get a new one."
            try:
                await self.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass


async def send_panel(interaction: discord.Interaction) -> None:
    """Answers an interaction with a private restart panel (or the reason a restart isn't possible)."""
    db = interaction.client.db
    status, balance, until = await db.restart(interaction.guild_id, interaction.user.id, grant=False)
    if status != "ok":
        embed = blocked_embed(status, balance, until)
        if status == "in_game" or status == "paid":
            embed.title = f"💀 Not enough credits ({balance:,})"
        await _respond(interaction, embed=embed)
        return
    view = RestartView(db, interaction.guild_id, interaction.user.id, balance)
    view.message = await _respond(interaction, embed=broke_embed(interaction.user.id, balance, 0), view=view)


async def _respond(interaction: discord.Interaction, **kwargs):
    """Sends a private reply and returns the message (None if it can't be fetched)."""
    if interaction.response.is_done():
        return await interaction.followup.send(ephemeral=True, wait=True, **kwargs)
    await interaction.response.send_message(ephemeral=True, **kwargs)
    try:
        return await interaction.original_response()
    except discord.HTTPException:
        return None


async def retire(key: tuple[int, int], note: str) -> None:
    """Takes the button off a player's open public restart panel (if any) and notes why."""
    old = _open_panels.pop(key, None)
    if old is None or old.finished:
        return
    old.close()
    if old.message:
        embed = discord.Embed(title="🔄 Restart button closed", color=discord.Color.light_grey(),
                              description=f"<@{old.owner_id}>, this button is closed: {note}.")
        try:
            await old.message.edit(content=None, embed=embed, view=None)
        except discord.HTTPException:
            pass


def offer_later(channel, db, guild_id: int, user_id: int) -> None:
    """Called when a game leaves a player below the minimum bet: posts a restart panel in the channel
    (tagging the player) after a short delay. An older panel of theirs that is still open is closed, so
    the newest one is always the one at the bottom of the chat."""
    key = (guild_id, user_id)
    if channel is None or key in _pending:
        return
    _pending.add(key)  # several bets settling at once (e.g. a horse race) must only post one panel
    asyncio.create_task(_offer(channel, db, guild_id, user_id), name=f"restart-offer-{user_id}")


async def _offer(channel, db, guild_id: int, user_id: int) -> None:
    key = (guild_id, user_id)
    try:
        await asyncio.sleep(OFFER_DELAY)
        status, balance, _ = await db.restart(guild_id, user_id, grant=False)
        if status != "ok":  # still in a game or locked after /pay
            return
        await retire(key, "a newer one was posted below")
        view = RestartView(db, guild_id, user_id, balance)
        _open_panels[view.key] = view
        view.message = await channel.send(
            content=f"<@{user_id}>", embed=broke_embed(user_id, balance, 0), view=view,
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(user_id)], roles=False, everyone=False))
        log.info("Offered a restart to user %s in guild %s (balance %s)", user_id, guild_id, balance)
    except Exception:
        _open_panels.pop(key, None)
        log.exception("Couldn't post a restart panel for user %s", user_id)
    finally:
        _pending.discard(key)
