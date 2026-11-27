"""The shop catalog and per-server prices.

Most items have one price. Items sold in fixed durations (mute/deafen, timeout) have a price per duration,
stored under keys like "voice1" or "timeout15". Mute and deafen share the "voice" prices, so they always
cost the same.
"""
from dataclasses import dataclass

import config
from utils.bignum import credits_text


@dataclass(frozen=True)
class Item:
    key: str
    how: str  # how to buy it, shown in /shop
    emoji: str
    name: str
    description: str
    durations: tuple[int, ...] = ()  # minutes; each one is priced separately

    def price_key(self, minutes: int | None = None) -> str:
        return self.key if minutes is None else f"{self.key}{minutes}"


def duration_label(minutes: int) -> str:
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _delay_text() -> str:
    m, s = divmod(config.RANDOM_KICK_MAX_DELAY, 60)
    return duration_label(m) if not s else f"{config.RANDOM_KICK_MAX_DELAY} seconds"


VOICE_DURATIONS = (1, 5)
TIMEOUT_DURATIONS = (5, 15, 30)

ITEMS: dict[str, Item] = {i.key: i for i in [
    Item("chicken", "/buy chicken", "🐓", "Fighting chicken",
         "Lets you play `/cockfight`. It gets stronger with every fight it wins, and dies when it loses."),
    Item("disconnect", "/buy disconnect", "🔌", "Voice disconnect",
         "Kick someone out of the voice channel they're in."),
    Item("voice", "/buy mute · /buy deafen", "🔇", "Voice mute or deafen",
         "Server-mute someone (they can't talk) or server-deafen them (they can't hear) in voice. "
         "Buying again adds time.", durations=VOICE_DURATIONS),
    Item("timeout", "/buy timeout", "⏳", "Timeout",
         "Time someone out: no chatting, reacting or joining voice. Buying again adds time.",
         durations=TIMEOUT_DURATIONS),
    Item("nickname", "/buy nickname", "🏷️", "Nickname change",
         "Change someone's nickname on this server."),
    Item("randomkick", "/buy random-kick", "🎲", "Random kick",
         f"Disconnects a random person in voice (anyone, even you) at a random moment within "
         f"{_delay_text()}. Completely secret: nobody else can see that you bought it."),
]}

# Every separately priced option: price key -> label.
PRICE_LABELS: dict[str, str] = {}
for _item in ITEMS.values():
    if _item.durations:
        for _m in _item.durations:
            PRICE_LABELS[_item.price_key(_m)] = f"{_item.name} · {duration_label(_m)}"
    else:
        PRICE_LABELS[_item.key] = _item.name


async def get_prices(db, guild_id: int) -> dict[str, int]:
    custom = await db.get_setting(guild_id, "shop_prices", {})
    return {key: int(custom.get(key, config.SHOP_PRICES[key])) for key in PRICE_LABELS}


def price_text(price: int) -> str:
    return "Not for sale" if price <= 0 else f"**{credits_text(price)}** {config.COIN}"


def item_price_text(item: Item, prices: dict[str, int]) -> str:
    if not item.durations:
        return price_text(prices[item.key])
    return " · ".join(f"{duration_label(m)}: {price_text(prices[item.price_key(m)])}" for m in item.durations)
