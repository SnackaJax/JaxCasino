"""Poker hand evaluation (5-card hands, and best 5 out of 7)."""
from collections import Counter
from itertools import combinations

from utils.cards import Card

HAND_NAMES = [
    "High Card",
    "One Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
]

Score = tuple[int, tuple[int, ...]]


def eval5(cards: list[Card]) -> Score:
    """Returns a comparable score: (category, tiebreakers). Higher is better."""
    ranks = sorted((c.rank for c in cards), reverse=True)
    counts = Counter(ranks)
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    flush = len({c.suit for c in cards}) == 1
    uniq = sorted(set(ranks), reverse=True)

    straight_high = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:  # wheel: A-2-3-4-5
            straight_high = 5

    if straight_high and flush:
        return 8, (straight_high,)
    if groups[0][1] == 4:
        return 7, (groups[0][0], groups[1][0])
    if groups[0][1] == 3 and groups[1][1] == 2:
        return 6, (groups[0][0], groups[1][0])
    if flush:
        return 5, tuple(ranks)
    if straight_high:
        return 4, (straight_high,)
    if groups[0][1] == 3:
        return 3, (groups[0][0],) + tuple(r for r in ranks if r != groups[0][0])
    if groups[0][1] == 2 and groups[1][1] == 2:
        hi, lo = groups[0][0], groups[1][0]
        kicker = next(r for r in ranks if r not in (hi, lo))
        return 2, (hi, lo, kicker)
    if groups[0][1] == 2:
        pair = groups[0][0]
        return 1, (pair,) + tuple(r for r in ranks if r != pair)
    return 0, tuple(ranks)


def best_hand(cards: list[Card]) -> tuple[Score, list[Card]]:
    """Best 5-card hand from 5-7 cards."""
    best = None
    best_cards: list[Card] = []
    for combo in combinations(cards, 5):
        score = eval5(list(combo))
        if best is None or score > best:
            best, best_cards = score, list(combo)
    return best, best_cards


def hand_name(score: Score) -> str:
    if score[0] == 8 and score[1][0] == 14:
        return "Royal Flush"
    return HAND_NAMES[score[0]]
