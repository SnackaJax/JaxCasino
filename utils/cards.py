"""Playing cards shared by blackjack, higher-or-lower, video poker and poker."""
from dataclasses import dataclass

from utils.rng import rng

SUITS = "♠♥♦♣"
RANK_NAMES = {11: "J", 12: "Q", 13: "K", 14: "A"}
HIDDEN = "`??`"


@dataclass(frozen=True)
class Card:
    rank: int  # 2..14 (14 = Ace)
    suit: str

    @property
    def rank_name(self) -> str:
        return RANK_NAMES.get(self.rank, str(self.rank))

    def __str__(self) -> str:
        return f"{self.rank_name}{self.suit}"


def new_deck(decks: int = 1) -> list[Card]:
    cards = [Card(r, s) for _ in range(decks) for s in SUITS for r in range(2, 15)]
    rng.shuffle(cards)
    return cards


def random_card() -> Card:
    return Card(rng.randint(2, 14), rng.choice(SUITS))


def show(cards) -> str:
    return " ".join(f"`{c}`" for c in cards) if cards else "—"
