"""Bet parsing, formatting and the Wager helper every game uses to take and pay out bets."""
import logging
import math

import discord

import config

log = logging.getLogger("jaxcasino.bets")


def fmt(amount: int) -> str:
    return f"**{amount:,}** {config.COIN}"


def fmt_mult(mult: float) -> str:
    return f"{mult:,.2f}x"


class BetError(Exception):
    pass


def parse_amount(raw: str, balance: int) -> int:
    """Parses '500', '1,000', '2.5k', '1m', 'half' or 'all'."""
    s = str(raw).strip().lower().replace(",", "").replace("_", "").replace(" ", "")
    if s in ("all", "max", "allin", "all-in"):
        return balance
    if s == "half":
        return balance // 2
    mult = 1
    for suffix, m in (("k", 1_000), ("m", 1_000_000), ("b", 1_000_000_000)):
        if s.endswith(suffix):
            mult, s = m, s[: -len(suffix)]
            break
    try:
        value = float(s)
    except ValueError:
        raise BetError(f"`{raw}` is not a valid amount. Enter any whole number (like `1234`), "
                       f"or a shortcut: `2.5k`, `1m`, `half`, `all`.")
    if not math.isfinite(value) or value <= 0:
        raise BetError("The amount must be a positive number.")
    amount = value * mult
    if amount != int(amount):
        raise BetError(f"`{raw}` isn't a whole number of credits.")
    return int(amount)


def tag(user: discord.abc.User) -> dict:
    """Message kwargs that mention the player who started a game, so their game message is highlighted for them."""
    return {"content": user.mention, "allowed_mentions": discord.AllowedMentions(users=[user], roles=False, everyone=False)}


async def send_error(interaction: discord.Interaction, message: str) -> None:
    embed = discord.Embed(description=f"❌ {message}", color=discord.Color.red())
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Wager:
    """Credits taken from a player for a game. The stake is escrowed until `settle` is called,
    so a bot restart mid-game refunds it instead of losing it."""

    def __init__(self, db, guild_id: int, user_id: int, stake: int, game: str, escrow_id: int):
        self.db = db
        self.guild_id = guild_id
        self.user_id = user_id
        self.stake = stake
        self.game = game
        self.escrow_id = escrow_id
        self.settled = False
        self.payout = 0
        self.balance_after: int | None = None
        self.channel = None  # where to offer a restart if this game leaves the player broke

    @classmethod
    async def create(cls, db, guild_id: int, user_id: int, amount: int, game: str,
                     channel=None) -> "Wager | None":
        if not await db.try_debit(guild_id, user_id, amount):
            return None
        escrow_id = await db.open_escrow(guild_id, user_id, amount, game)
        log.debug("%s: user %s bet %s (escrow #%s)", game, user_id, f"{amount:,}", escrow_id)
        wager = cls(db, guild_id, user_id, amount, game, escrow_id)
        wager.channel = channel
        return wager

    async def add_stake(self, extra: int) -> bool:
        """Takes extra credits (blackjack double/split). Returns False if the player can't afford it."""
        if self.settled or not await self.db.try_debit(self.guild_id, self.user_id, extra):
            return False
        self.stake += extra
        await self.db.update_escrow(self.escrow_id, self.stake)
        log.debug("%s: user %s added %s, stake now %s", self.game, self.user_id, f"{extra:,}", f"{self.stake:,}")
        return True

    async def settle(self, payout: int, record: bool = True) -> int | None:
        """Pays out `payout` (total returned, including the stake if won). Safe to call twice."""
        if self.settled:
            return None
        self.settled = True
        self.payout = max(0, int(payout))
        self.balance_after = await self.db.settle_escrow(
            self.escrow_id, self.guild_id, self.user_id, self.payout
        )
        if record:
            await self.db.record_game(self.guild_id, self.user_id, self.stake, self.payout, self.game)
        log.info("%s: user %s %s stake %s, paid %s, balance %s", self.game, self.user_id,
                 "settled" if record else "refunded", f"{self.stake:,}", f"{self.payout:,}",
                 f"{self.balance_after:,}")
        if record and self.balance_after < config.MIN_BET:
            from utils.restart import offer_later
            offer_later(self.channel, self.db, self.guild_id, self.user_id)
        return self.balance_after

    async def refund(self) -> int | None:
        return await self.settle(self.stake, record=False)


async def take_bet(interaction: discord.Interaction, raw: str, game: str) -> Wager | None:
    """Validates and takes a bet from the user. Sends an error and returns None if it fails."""
    db = interaction.client.db
    gid, uid = interaction.guild_id, interaction.user.id
    balance = await db.get_balance(gid, uid)
    if balance < config.MIN_BET:
        from utils.restart import send_panel
        await send_panel(interaction)
        return None
    try:
        amount = parse_amount(raw, balance)
    except BetError as e:
        await send_error(interaction, str(e))
        return None
    if amount < config.MIN_BET:
        await send_error(interaction, f"The minimum bet is {fmt(config.MIN_BET)}.")
        return None
    if config.MAX_BET and amount > config.MAX_BET:
        await send_error(interaction, f"The maximum bet is {fmt(config.MAX_BET)}.")
        return None
    wager = await Wager.create(db, gid, uid, amount, game, channel=interaction.channel)
    if wager is None:
        await send_error(interaction, f"You can't afford that. Your balance is {fmt(balance)}.")
    else:
        interaction.extras.setdefault(UNPOSTED, []).append(wager)
    return wager


# Bets taken during an interaction, kept on interaction.extras so the error handlers can refund them
# if the game never got posted (e.g. Discord's 3-second reply window ran out: "Unknown interaction").
UNPOSTED = "unposted_wagers"


def keep_bet(interaction: discord.Interaction, wager: Wager) -> None:
    """Marks a bet as placed for good, even if replying to the interaction fails later (e.g. a horse race
    bet: the race settles it)."""
    bets = interaction.extras.get(UNPOSTED, [])
    if wager in bets:
        bets.remove(wager)


async def refund_unposted(interaction: discord.Interaction) -> None:
    """Called by the error handlers. If the interaction was never answered, the player never saw the game,
    so nothing will ever settle its bet: refund it now instead of leaving it stuck until the next restart."""
    bets = interaction.extras.pop(UNPOSTED, [])
    if interaction.response.is_done():
        return
    for wager in bets:
        if wager.settled:
            continue
        try:
            bal = await wager.refund()
        except Exception:
            log.exception("%s: couldn't refund bet #%s of user %s", wager.game, wager.escrow_id, wager.user_id)
            continue
        log.warning("%s: game for user %s was never shown, refunded %s (escrow #%s)",
                    wager.game, wager.user_id, f"{wager.stake:,}", wager.escrow_id)
        if wager.channel is not None:
            try:
                await wager.channel.send(
                    f"<@{wager.user_id}>, your {wager.game} game couldn't start, so your bet of "
                    f"{fmt(wager.stake)} was refunded. Balance: {fmt(bal)}",
                    allowed_mentions=discord.AllowedMentions(users=[discord.Object(wager.user_id)],
                                                             roles=False, everyone=False))
            except discord.HTTPException:
                pass


def result_color(stake: int, payout: int) -> discord.Color:
    if payout > stake:
        return discord.Color.green()
    if payout == stake:
        return discord.Color.light_grey()
    return discord.Color.red()


def result_line(stake: int, payout: int, balance: int | None) -> str:
    diff = payout - stake
    if diff > 0:
        text = f"✅ You won {fmt(diff)}!"
    elif diff == 0:
        text = "➖ Push — your bet was returned."
    else:
        text = f"❌ You lost {fmt(-diff)}."
    if balance is not None:
        text += f"\nBalance: {fmt(balance)}"
    return text
