"""Banger Bash: a 5-reel, 3-row barbecue slot with 20 fixed paylines, random base-game features and the
Grill Bonus. Pure game logic, no Discord or web code, so the chat command (cogs/bangerbash.py), the Activity
(activity/banger.py) and the simulation below all play exactly the same game.

    python -m utils.banger_engine            # measures the payback and prints the stats (takes a minute)
    python -m utils.banger_engine features   # measures each bonus feature's average value

How a spin works
- Every cell is drawn independently from its reel's symbol weights. Wilds never land on reel 1.
- Grills (the bonus symbol) are not drawn per cell: each spin first picks how many land (GRILL_ODDS), then puts
  them on that many different reels.
- Line wins pay left to right from reel 1 for 3 to 5 matching symbols. Pays are in line bets; the total bet
  covers all 20 lines, so a line bet is the total bet / 20.
- About one spin in FEATURE_CHANCE gets a random feature (Flame Wilds, Mega Banger, Hot Reels, Mystery Buns,
  Extra Charcoal, Sure Sizzle). A losing spin without a feature sometimes gets a Second Helping: one reel spins
  again.
- 3 or more grills start the Grill Bonus: the Grill Wheel awards one of five features (more grills, better
  features). Before playing it the player may "turn up the heat" up to MAX_GAMBLES times: win and the feature
  moves one rung up, lose and it drops one rung (from the bottom rung, the bonus is lost). The odds of every
  gamble are set from the features' average values (FEATURE_EV) so gambling neither gains nor loses on average.
- A paid spin, with everything it triggers, never wins more than MAX_WIN times the bet.

All amounts below are in "x": multiples of the total bet, before the RTP scale. The bot scales every pay by
BANGER_RTP / MEASURED_RTP so the game returns BANGER_RTP (set in .env).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field

REELS = 5
ROWS = 3

WILD = "wild"
SCATTER = "grill"
MYSTERY = "bun"        # only ever shown before Mystery Buns reveal what they are

# key: (name, emoji for text, pays for 3/4/5 in a row, in line bets)
SYMBOLS: dict[str, tuple[str, str, tuple[int, int, int]]] = {
    "ketchup": ("Ketchup", "🍅", (5, 16, 55)),
    "mustard": ("Mustard", "🟡", (5, 16, 55)),
    "pickle": ("Pickle", "🥒", (8, 22, 85)),
    "onion": ("Onion Rings", "🧅", (8, 22, 85)),
    "chorizo": ("Chorizo", "🌶️", (14, 42, 140)),
    "brat": ("Bratwurst", "🥖", (20, 62, 220)),
    "hotdog": ("Hot Dog", "🌭", (28, 110, 420)),
    "king": ("King Banger", "👑", (55, 280, 1400)),
    WILD: ("Flame Wild", "🔥", (0, 0, 0)),
    SCATTER: ("Grill", "♨️", (0, 0, 0)),
}
PAYING = [k for k in SYMBOLS if k not in (WILD, SCATTER)]

_SYMBOL_WEIGHTS = {"ketchup": 22, "mustard": 22, "pickle": 18, "onion": 18, "chorizo": 13, "brat": 10,
                   "hotdog": 7, "king": 4}
BASE_WILD = 3.5
FREE_WILD = {"grillspins": 11.0, "twinlinks": 9.5}   # wild weight during each free spin feature

GRILL_ODDS = {1: 0.22, 2: 0.045, 3: 1 / 118, 4: 1 / 1500, 5: 1 / 30_000}
FREE_GRILL_ODDS = {1: 0.25, 2: 0.07, 3: 1 / 45, 4: 1 / 400, 5: 1 / 6000}
CHARCOAL_GRILL_ODDS = {2: 0.6, 3: 0.32, 4: 0.07, 5: 0.01}   # Extra Charcoal: at least 2 grills

# Random base-game features
FEATURE_CHANCE = 0.09
FEATURES = {"flame": 30, "mystery": 20, "mega": 14, "hot": 14, "charcoal": 11, "sure": 11}
FEATURE_NAMES = {"flame": "Flame Wilds", "mystery": "Mystery Buns", "mega": "Mega Banger", "hot": "Hot Reels",
                 "charcoal": "Extra Charcoal", "sure": "Sure Sizzle", "respin": "Second Helping"}
FEATURE_TEXT = {
    "flame": "2 to 5 cells catch fire and turn wild",
    "mystery": "mystery buns land and all turn into the same symbol",
    "mega": "reels 2 to 4 show one giant 3×3 symbol",
    "hot": "one or two whole reels turn wild",
    "charcoal": "at least 2 grills are guaranteed, so the bonus is close",
    "sure": "the spin is guaranteed to win",
    "respin": "a losing spin sometimes gets one reel spun again",
}
RESPIN_CHANCE = 0.10

# The Grill Bonus: five features, rung 1 (lowest) to rung 5.
TIERS = ["potato", "picnic", "grillspins", "twinlinks", "golden"]
TIER_NAMES = {"potato": "Hot Potato", "picnic": "Picnic Pick", "grillspins": "Grill Spins",
              "twinlinks": "Twin Links", "golden": "Golden Grill"}
TIER_TEXT = {
    "potato": "Pick a sausage each round · dodge the burnt one",
    "picnic": "Open hampers until the ants show up",
    "grillspins": "11 free spins · multiplier +1 after every win",
    "twinlinks": "8 free spins · two linked 2x wilds every spin",
    "golden": "A multiplier climbs until the grill cools · up to 500x",
}
# Grill Wheel: chance of each rung, by the number of grills that started the bonus
AWARD_WEIGHTS = {3: (34, 28, 20, 12, 6), 4: (0, 40, 30, 20, 10), 5: (0, 0, 45, 35, 20)}
MAX_GAMBLES = 5

# Average value of each feature in x (before the RTP scale), measured with `python -m utils.banger_engine
# features`. The gamble odds come from these, so re-measure them after changing a feature.
FEATURE_EV = {"potato": 10.7, "picnic": 20.2, "grillspins": 32.6, "twinlinks": 45.4, "golden": 72.5}

MAX_WIN = 5000          # times the total bet
# Payback of everything above (taking every feature without gambling, random picks): 0.9917 over 6 million
# rounds, which is within the simulation's noise (about ±0.4%) of 0.99. It is set to 0.99 so that at the default
# BANGER_RTP of 99% every pay is exactly what the tables say. The bot scales every pay by BANGER_RTP / MEASURED_RTP.
MEASURED_RTP = 0.99

# 20 paylines: the row (0 = top) used on each reel.
PAYLINES: list[tuple[int, ...]] = [
    (1, 1, 1, 1, 1), (0, 0, 0, 0, 0), (2, 2, 2, 2, 2), (0, 1, 2, 1, 0), (2, 1, 0, 1, 2),
    (0, 0, 1, 2, 2), (2, 2, 1, 0, 0), (1, 0, 0, 0, 1), (1, 2, 2, 2, 1), (0, 1, 1, 1, 0),
    (2, 1, 1, 1, 2), (1, 0, 1, 2, 1), (1, 2, 1, 0, 1), (0, 1, 0, 1, 0), (2, 1, 2, 1, 2),
    (1, 1, 0, 1, 1), (1, 1, 2, 1, 1), (0, 2, 0, 2, 0), (2, 0, 2, 0, 2), (0, 2, 2, 2, 0),
]
LINES = len(PAYLINES)

# Hot Potato: up to 5 rounds of 3 sausages; from round 2 one of the three is burnt and ends the feature.
POTATO_ROUNDS = 5
POTATO_VALUES = {2: 30, 3: 30, 4: 20, 6: 12, 10: 6, 25: 2}
# Picnic Pick: 15 hampers: 12 prizes, 1 doubler, 2 ant nests. The first ant nest ends it.
PICNIC_VALUES = {1: 25, 2: 30, 3: 20, 5: 15, 8: 7, 20: 3}
PICNIC_CELLS, PICNIC_ANTS, PICNIC_DOUBLES = 15, 2, 1
# Free spin features
FREE_SPINS = {"grillspins": 11, "twinlinks": 8}
RETRIGGER_SPINS = 4
MAX_FREE_SPINS = 30
LINK_MULT = 2
# Golden Grill: starts at GOLDEN_START, each step adds a value until the grill cools (GOLDEN_COOL per step).
GOLDEN_START = 20
GOLDEN_STEPS = {3: 30, 5: 25, 10: 20, 20: 12, 40: 9, 75: 4}
GOLDEN_COOL = 0.2
GOLDEN_MAX = 500

Grid = list[list[str]]   # grid[reel][row]
Cell = tuple[int, int]   # (reel, row)


# ------------------------------------------------------------------ drawing symbols
def _reel_weights(wild: float) -> list[dict[str, float]]:
    return [{**_SYMBOL_WEIGHTS, WILD: 0 if r == 0 else wild} for r in range(REELS)]


def _picker(weights: dict):
    keys = list(weights)
    cum, total = [], 0.0
    for k in keys:
        total += weights[k]
        cum.append(total)
    return keys, cum, total


_PICKERS = {kind: [_picker(w) for w in _reel_weights(wild)]
            for kind, wild in {None: BASE_WILD, **FREE_WILD}.items()}
_PAYING_PICKER = _picker(_SYMBOL_WEIGHTS)


def _pick(rng, picker):
    keys, cum, total = picker
    x = rng.random() * total
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return keys[lo]


def weighted(rng, weights: dict):
    return _pick(rng, _picker(weights))


def _draw(rng, reel: int, free: str | None) -> str:
    """A symbol for `reel`; `free` is the free spin feature being played (None in the base game)."""
    return _pick(rng, _PICKERS[free][reel])


def _count(rng, odds: dict[int, float]) -> int:
    x = rng.random()
    acc = 0.0
    for k, p in odds.items():
        acc += p
        if x < acc:
            return k
    return 0


def _place_grills(rng, grid: Grid, n: int, avoid: set[Cell] = frozenset()) -> None:
    """Puts n grills on n different reels (random rows), never on a cell in `avoid`."""
    reels = [r for r in range(REELS) if any((r, row) not in avoid for row in range(ROWS))]
    for r in rng.sample(reels, min(n, len(reels))):
        rows = [row for row in range(ROWS) if (r, row) not in avoid]
        grid[r][rng.choice(rows)] = SCATTER


def random_grid(rng, free: str | None = None, grill_odds: dict | None = None,
                fixed: dict[Cell, str] | None = None) -> Grid:
    fixed = fixed or {}
    grid = [[fixed.get((r, row)) or _draw(rng, r, free) for row in range(ROWS)] for r in range(REELS)]
    n = _count(rng, grill_odds or (FREE_GRILL_ODDS if free else GRILL_ODDS))
    if n:
        _place_grills(rng, grid, n, set(fixed))
    return grid


def copy_grid(grid: Grid) -> Grid:
    return [col[:] for col in grid]


def grills_in(grid: Grid) -> list[Cell]:
    return [(r, row) for r in range(REELS) for row in range(ROWS) if grid[r][row] == SCATTER]


# ------------------------------------------------------------------ line wins
@dataclass
class LineWin:
    line: int          # 0-based payline index
    symbol: str
    count: int
    pay: int           # in line bets, before multipliers
    mult: int = 1      # 2 when the line runs through a Twin Links wild

    @property
    def cells(self) -> list[Cell]:
        return [(r, PAYLINES[self.line][r]) for r in range(self.count)]

    @property
    def x(self) -> float:
        return self.pay * self.mult / LINES

    def to_dict(self) -> dict:
        return {"line": self.line, "symbol": self.symbol, "count": self.count, "pay": self.pay,
                "mult": self.mult, "x": self.x}


def evaluate(grid: Grid, links: set[Cell] = frozenset()) -> list[LineWin]:
    wins = []
    for i, line in enumerate(PAYLINES):
        first = grid[0][line[0]]
        if first in (WILD, SCATTER):   # reel 1 never has wilds; a grill there starts no line
            continue
        count = 1
        for r in range(1, REELS):
            sym = grid[r][line[r]]
            if sym == first or sym == WILD:
                count += 1
            else:
                break
        if count >= 3:
            w = LineWin(i, first, count, SYMBOLS[first][2][count - 3])
            if links and any(c in links for c in w.cells):
                w.mult = LINK_MULT
            wins.append(w)
    return wins


# ------------------------------------------------------------------ one set of reels
@dataclass
class Spin:
    landed: Grid                    # what the reels stop on
    grid: Grid                      # after the feature changed it (the grid that pays)
    wins: list[LineWin]
    feature: str | None = None      # flame, mystery, mega, hot, charcoal, sure, respin
    detail: dict = field(default_factory=dict)
    links: list[Cell] = field(default_factory=list)   # Twin Links wilds
    mult: int = 1                   # Grill Spins multiplier for this spin

    @property
    def grills(self) -> list[Cell]:
        return grills_in(self.grid)

    @property
    def x(self) -> float:
        return sum(w.x for w in self.wins) * self.mult

    def to_dict(self) -> dict:
        return {"landed": self.landed, "grid": self.grid, "wins": [w.to_dict() for w in self.wins],
                "feature": self.feature, "detail": self.detail, "links": [list(c) for c in self.links],
                "mult": self.mult, "grills": [list(c) for c in self.grills], "x": self.x}


def _feature_spin(rng, feature: str) -> Spin:
    if feature == "charcoal":
        grid = random_grid(rng, grill_odds=CHARCOAL_GRILL_ODDS)
        return Spin(grid, grid, evaluate(grid), feature)
    if feature == "sure":
        for _ in range(200):
            grid = random_grid(rng)
            wins = evaluate(grid)
            if wins:
                return Spin(grid, grid, wins, feature)
        grid = random_grid(rng)   # practically never: force a 3-of-a-kind on line 1
        for r in range(3):
            grid[r][1] = "ketchup"
        return Spin(grid, grid, evaluate(grid), feature)
    if feature == "mega":
        sym = _pick(rng, _PAYING_PICKER)
        fixed = {(r, row): sym for r in (1, 2, 3) for row in range(ROWS)}
        grid = random_grid(rng, fixed=fixed)
        return Spin(grid, grid, evaluate(grid), feature, {"symbol": sym, "reels": [1, 2, 3]})
    landed = random_grid(rng)
    grid = copy_grid(landed)
    if feature == "flame":
        cells = [(r, row) for r in range(1, REELS) for row in range(ROWS) if grid[r][row] != SCATTER]
        lit = rng.sample(cells, rng.choice((2, 2, 3, 3, 4, 5)))
        for r, row in lit:
            grid[r][row] = WILD
        detail = {"cells": [list(c) for c in sorted(lit)]}
    elif feature == "hot":
        reels = sorted(rng.sample(range(1, REELS), 2 if rng.random() < 0.25 else 1))
        for r in reels:
            grid[r] = [WILD] * ROWS
        detail = {"reels": reels}
    elif feature == "mystery":
        cells = [(r, row) for r in range(REELS) for row in range(ROWS) if grid[r][row] != SCATTER]
        buns = sorted(rng.sample(cells, rng.randint(4, 8)))
        sym = _pick(rng, _PAYING_PICKER)
        for r, row in buns:
            landed[r][row] = MYSTERY
            grid[r][row] = sym
        detail = {"cells": [list(c) for c in buns], "symbol": sym}
    else:
        raise ValueError(feature)
    return Spin(landed, grid, evaluate(grid), feature, detail)


def base_spin(rng) -> Spin:
    if rng.random() < FEATURE_CHANCE:
        return _feature_spin(rng, weighted(rng, FEATURES))
    grid = random_grid(rng)
    wins = evaluate(grid)
    if not wins and len(grills_in(grid)) < 3 and rng.random() < RESPIN_CHANCE:
        # Second Helping: one reel spins again (any grill on it stays put)
        r = rng.randrange(REELS)
        new = copy_grid(grid)
        new[r] = [SCATTER if grid[r][row] == SCATTER else _draw(rng, r, None) for row in range(ROWS)]
        return Spin(grid, new, evaluate(new), "respin", {"reel": r})
    return Spin(grid, grid, wins)


def free_spin(rng, kind: str) -> Spin:
    if kind != "twinlinks":
        grid = random_grid(rng, free=kind)
        return Spin(grid, grid, evaluate(grid))
    r = rng.randint(1, REELS - 2)
    row = rng.randrange(ROWS)
    pair = [(r, row), (r + 1, row)]
    grid = random_grid(rng, free=kind, fixed={c: WILD for c in pair})
    return Spin(grid, grid, evaluate(grid, set(pair)), links=pair)


# ------------------------------------------------------------------ bonus features
class HotPotato:
    """Up to POTATO_ROUNDS rounds; each shows 3 sausages. Round 1 is safe; after that one sausage per round is
    burnt and ends the feature (you keep what you collected). Values are dealt up front."""
    kind = "potato"

    def __init__(self, rng):
        self.rounds: list[list[int | None]] = []
        for i in range(POTATO_ROUNDS):
            vals: list[int | None] = [weighted(rng, POTATO_VALUES) for _ in range(3)]
            if i > 0:
                vals[rng.randrange(3)] = None
            self.rounds.append(vals)
        self.picks: list[int] = []
        self.total = 0.0
        self.burnt = False

    @property
    def round(self) -> int:
        return len(self.picks)

    @property
    def done(self) -> bool:
        return self.burnt or self.round >= POTATO_ROUNDS

    def pick(self, i: int) -> int | None:
        if self.done or not 0 <= i < 3:
            raise ValueError("bad pick")
        value = self.rounds[self.round][i]
        self.picks.append(i)
        if value is None:
            self.burnt = True
        else:
            self.total += value
        return value

    def auto(self, rng) -> int | None:
        return self.pick(rng.randrange(3))

    def to_dict(self) -> dict:
        # only rounds already played are revealed
        return {"kind": self.kind, "round": self.round, "rounds": POTATO_ROUNDS, "picks": self.picks,
                "revealed": self.rounds[:self.round], "total": self.total, "burnt": self.burnt, "done": self.done}


class PicnicPick:
    """PICNIC_CELLS hampers: prizes, a doubler and ant nests. Opening an ant nest ends it (you keep your prizes);
    the doubler doubles the final total."""
    kind = "picnic"

    def __init__(self, rng):
        cells: list = [weighted(rng, PICNIC_VALUES) for _ in range(PICNIC_CELLS - PICNIC_ANTS - PICNIC_DOUBLES)]
        cells += ["ants"] * PICNIC_ANTS + ["double"] * PICNIC_DOUBLES
        rng.shuffle(cells)
        self.cells = cells
        self.opened: list[int] = []
        self.sum = 0.0
        self.doubled = False
        self.done = False

    @property
    def total(self) -> float:
        return self.sum * (2 if self.doubled else 1)

    def pick(self, i: int):
        if self.done or not 0 <= i < PICNIC_CELLS or i in self.opened:
            raise ValueError("bad pick")
        self.opened.append(i)
        v = self.cells[i]
        if v == "ants":
            self.done = True
        elif v == "double":
            self.doubled = True
        else:
            self.sum += v
        if not self.done and len(self.opened) >= PICNIC_CELLS - PICNIC_ANTS:
            self.done = True
        return v

    def auto(self, rng):
        return self.pick(rng.choice([i for i in range(PICNIC_CELLS) if i not in self.opened]))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "cells": PICNIC_CELLS, "opened": {str(i): self.cells[i] for i in self.opened},
                "all": self.cells if self.done else None, "total": self.total, "doubled": self.doubled,
                "done": self.done}


class FreeSpins:
    """Grill Spins (multiplier +1 after every winning spin) or Twin Links (two linked 2x wilds every spin).
    Played out up front; there is nothing to choose."""

    def __init__(self, rng, kind: str):
        self.kind = kind
        self.spins: list[Spin] = []
        self.awarded = FREE_SPINS[kind]
        self.retriggers = 0
        left, mult = self.awarded, 1
        while left > 0 and len(self.spins) < MAX_FREE_SPINS:
            left -= 1
            s = free_spin(rng, kind)
            if kind == "grillspins":
                s.mult = mult
                if s.wins:
                    mult += 1
            self.spins.append(s)
            if len(s.grills) >= 3 and len(self.spins) + left < MAX_FREE_SPINS:
                add = min(RETRIGGER_SPINS, MAX_FREE_SPINS - len(self.spins) - left)
                left += add
                self.awarded += add
                self.retriggers += 1
        self.total = sum(s.x for s in self.spins)
        self.done = True

    def to_dict(self) -> dict:
        return {"kind": self.kind, "spins": [s.to_dict() for s in self.spins], "awarded": self.awarded,
                "total": self.total, "done": True}


class GoldenGrill:
    """A multiplier that starts at GOLDEN_START and climbs by a random step until the grill cools."""
    kind = "golden"

    def __init__(self, rng):
        value = GOLDEN_START
        self.steps: list[int] = []
        while rng.random() >= GOLDEN_COOL and value < GOLDEN_MAX:
            step = weighted(rng, GOLDEN_STEPS)
            value = min(GOLDEN_MAX, value + step)
            self.steps.append(step)
        self.total = float(value)
        self.done = True

    def to_dict(self) -> dict:
        return {"kind": self.kind, "start": GOLDEN_START, "steps": self.steps, "total": self.total,
                "max": GOLDEN_MAX, "done": True}


def make_feature(rng, tier: int):
    kind = TIERS[tier]
    if kind == "potato":
        return HotPotato(rng)
    if kind == "picnic":
        return PicnicPick(rng)
    if kind == "golden":
        return GoldenGrill(rng)
    return FreeSpins(rng, kind)


def gamble_chance(tier: int) -> float:
    """Chance that turning up the heat on `tier` (0-based) moves one rung up. Winning gives the next feature,
    losing gives the one below (or nothing from the bottom rung); the chance makes both sides worth the same."""
    ev = [FEATURE_EV[t] for t in TIERS]
    below = ev[tier - 1] if tier > 0 else 0.0
    return (ev[tier] - below) / (ev[tier + 1] - below)


class Bonus:
    """The Grill Bonus: the awarded rung, the gambles, then the feature itself."""

    def __init__(self, rng, grills: int):
        self.grills = min(grills, 5)
        self.awarded = TIERS.index(weighted(rng, dict(zip(TIERS, AWARD_WEIGHTS[self.grills]))))
        self.tier = self.awarded
        self.gambles: list[dict] = []    # {"from", "to", "won", "chance"}
        self.lost = False
        self.feature = None

    @property
    def can_gamble(self) -> bool:
        return (self.feature is None and not self.lost and self.tier < len(TIERS) - 1
                and len(self.gambles) < MAX_GAMBLES)

    def gamble(self, rng) -> bool:
        if not self.can_gamble:
            raise ValueError("can't gamble now")
        chance = gamble_chance(self.tier)
        won = rng.random() < chance
        before = self.tier
        if won:
            self.tier += 1
        elif self.tier == 0:
            self.lost = True
        else:
            self.tier -= 1
        self.gambles.append({"from": before, "to": self.tier, "won": won, "chance": chance, "lost": self.lost})
        return won

    def take(self, rng) -> None:
        if self.feature is None and not self.lost:
            self.feature = make_feature(rng, self.tier)

    @property
    def done(self) -> bool:
        return self.lost or (self.feature is not None and self.feature.done)

    @property
    def total(self) -> float:
        return 0.0 if self.lost or self.feature is None else self.feature.total

    def to_dict(self) -> dict:
        return {"grills": self.grills, "awarded": self.awarded, "tier": self.tier, "gambles": self.gambles,
                "lost": self.lost, "can_gamble": self.can_gamble,
                "chance": gamble_chance(self.tier) if self.can_gamble else None,
                "feature": self.feature.to_dict() if self.feature else None, "done": self.done}


class Round:
    """One paid spin and, if it lands 3+ grills, its Grill Bonus."""

    def __init__(self, rng):
        self.spin = base_spin(rng)
        grills = len(self.spin.grills)
        self.bonus = Bonus(rng, grills) if grills >= 3 else None

    @property
    def done(self) -> bool:
        return self.bonus is None or self.bonus.done

    @property
    def x(self) -> float:
        """Everything won so far, in x, capped at MAX_WIN."""
        return min(self.spin.x + (self.bonus.total if self.bonus else 0.0), MAX_WIN)

    def finish(self, rng) -> None:
        """Plays out whatever is left with default choices: take the feature, random picks."""
        if self.bonus and not self.bonus.done:
            self.bonus.take(rng)
            f = self.bonus.feature
            while f is not None and not f.done:
                f.auto(rng)


# ------------------------------------------------------------------ credits
def scale(rtp: float) -> float:
    return rtp / MEASURED_RTP


def credits(stake: int, x: float, rtp: float) -> int:
    return math.floor(stake * x * scale(rtp) + 1e-9)


def payout(stake: int, rnd: Round, rtp: float) -> int:
    return min(credits(stake, rnd.x, rtp), stake * MAX_WIN)


# ------------------------------------------------------------------ simulation
def _simulate(n: int) -> None:
    import random
    import time
    rng = random.Random()
    t0 = time.time()
    total = hits = above = bonuses = feats = respins = capped = 0
    base_x = bonus_x = 0.0
    by_feature: dict[str, list[float]] = {}
    tiers = [0] * len(TIERS)
    biggest = 0.0
    for _ in range(n):
        rnd = Round(rng)
        rnd.finish(rng)
        x = rnd.x
        total += x
        base_x += rnd.spin.x
        if rnd.spin.feature:
            if rnd.spin.feature == "respin":
                respins += 1
            else:
                feats += 1
            by_feature.setdefault(rnd.spin.feature, []).append(rnd.spin.x)
        if rnd.bonus:
            bonuses += 1
            tiers[rnd.bonus.tier] += 1
            bonus_x += rnd.bonus.total
        hits += x > 0
        above += x > 1
        capped += x >= MAX_WIN
        biggest = max(biggest, x)
    print(f"{n:,} rounds in {time.time() - t0:.0f}s")
    print(f"raw payback {total / n:.4f} (lines {base_x / n:.4f}, bonus {bonus_x / n:.4f})")
    print(f"wins on {hits / n:.1%} of spins, more than the bet on {above / n:.1%}")
    print(f"random feature every {n / max(feats, 1):.1f} spins, second helping every {n / max(respins, 1):.1f}")
    print(f"bonus every {n / max(bonuses, 1):.0f} spins, rungs {[round(t / max(bonuses, 1), 3) for t in tiers]}")
    for k, v in sorted(by_feature.items()):
        print(f"  {FEATURE_NAMES[k]:15} avg {sum(v) / len(v):.2f}x over {len(v):,}")
    print(f"biggest {biggest:.0f}x, capped {capped}")


def _features(n: int) -> None:
    import random
    rng = random.Random()
    for tier, kind in enumerate(TIERS):
        tot = 0.0
        best = 0.0
        for _ in range(n):
            f = make_feature(rng, tier)
            while not f.done:
                f.auto(rng)
            tot += f.total
            best = max(best, f.total)
        print(f'"{kind}": {tot / n:.1f},   # best {best:.0f}x')


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "features":
        _features(int(sys.argv[2]) if len(sys.argv) > 2 else 200_000)
    else:
        _simulate(int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000)
