"""/crux: a player's own break from the bot and the casino Activity. The pieces several places share: reading a
duration, and the message someone with a crux gets."""
import re

import discord

MIN_SECONDS = 10 * 60            # a crux lasts at least 10 minutes...
MAX_SECONDS = 365 * 24 * 3600    # ...and at most a year
UNITS = {"m": 60, "min": 60, "h": 3600, "u": 3600, "d": 86400, "w": 7 * 86400}
PART = re.compile(r"(\d+(?:[.,]\d+)?)\s*(min|m|h|u|d|w)", re.IGNORECASE)


def parse_duration(text: str) -> int | None:
    """'2h', '3d', '1w', '1d12h', '90m' (also with spaces, 'u' for uur) -> seconds, or None if it can't be read."""
    s = (text or "").strip().lower().replace(" ", "")
    if not s:
        return None
    total, pos = 0.0, 0
    for m in PART.finditer(s):
        if m.start() != pos:
            return None
        total += float(m.group(1).replace(",", ".")) * UNITS[m.group(2)]
        pos = m.end()
    if pos != len(s) or total <= 0:
        return None
    return int(total)


def describe(seconds: float) -> str:
    """3 days 4 hours, 2 hours 5 minutes, 12 minutes."""
    s = int(max(0, seconds))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    parts = [(d, "day"), (h, "hour"), (m, "minute")]
    shown = [f"{n} {w}{'s' if n != 1 else ''}" for n, w in parts if n][:2]
    return " ".join(shown) if shown else "less than a minute"


def crux_embed(until: float) -> discord.Embed:
    """What someone with a crux sees when they try to play."""
    # Discord shows <t:...:R> as "in 9 minutes" and keeps it up to date, so no time left of our own next to it
    return discord.Embed(
        title="🧊 You have a crux",
        description=(f"You're taking a break from the casino. It ends <t:{int(until)}:F> "
                     f"(**<t:{int(until)}:R>**).\n\n"
                     "Until then the games, the Activity and the other commands are off for you. "
                     "Only a server admin can end it early."),
        color=discord.Color.blue(),
    )
