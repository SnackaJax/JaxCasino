"""Cosmic Lab: a 6-reel, 4-row video slot with 20 fixed paylines, wilds, scatters and free spins with
sticky wilds. Pure game logic, no Discord code, so it can be simulated (see tools/simulate_slots.py).

- Every cell is drawn independently from its reel's symbol weights.
- Line wins pay left to right from reel 1, for 3 to 6 matching symbols in a row. Pays are in line bets;
  the total bet covers all 20 lines, so one line bet is the total bet / 20.
- Wilds land on reels 2-6 only and stand in for every symbol except the comet.
- 3 or more comets anywhere start free spins. During free spins every wild that lands stays put until the
  free spins end, and 3 or more comets add more spins.
- A paid spin, together with any free spins it triggers, never wins more than MAX_WIN times the bet.
"""
from dataclasses import dataclass, field

REELS = 6
ROWS = 4

WILD = "wild"
SCATTER = "comet"

# key: (name, emoji for text, pays for 3/4/5/6 in a row, in line bets)
SYMBOLS: dict[str, tuple[str, str, tuple[int, int, int, int]]] = {
    "battery": ("Battery", "🔋", (10, 20, 40, 75)),
    "tube": ("Test tube", "🧪", (10, 20, 40, 75)),
    "magnet": ("Magnet", "🧲", (15, 25, 50, 100)),
    "bolt": ("Bolt", "⚡", (15, 25, 50, 100)),
    "planet": ("Planet", "🪐", (25, 50, 125, 250)),
    "robot": ("Robot", "🤖", (25, 75, 200, 400)),
    "ufo": ("UFO", "🛸", (50, 125, 375, 750)),
    "alien": ("Alien", "👽", (75, 250, 750, 2500)),
    WILD: ("Wild", "🌀", (0, 0, 0, 0)),
    SCATTER: ("Comet", "☄️", (0, 0, 0, 0)),
}
PAYING = [k for k in SYMBOLS if k not in (WILD, SCATTER)]

# Symbol weights per reel, for the base game and for free spins. Reel 1 never has wilds.
_SYMBOL_WEIGHTS = {"battery": 20, "tube": 20, "magnet": 17, "bolt": 17, "planet": 11, "robot": 8, "ufo": 5,
                   "alien": 3}
BASE_WILD, FREE_WILD = 4, 6.25

# Comets are not drawn per cell. Each spin first picks how many land, with exactly these odds, then puts
# them on random cells (never on a sticky wild). 0 comets gets whatever chance is left over.
COMET_ODDS = {1: 0.28, 2: 0.057, 3: 1 / 149, 4: 1 / 900, 5: 1 / 16_000, 6: 1 / 240_000}


def _reel_weights(wild: float) -> list[dict[str, float]]:
    return [{**_SYMBOL_WEIGHTS, WILD: 0 if r == 0 else wild} for r in range(REELS)]


WEIGHTS = _reel_weights(BASE_WILD)
FREE_WEIGHTS = _reel_weights(FREE_WILD)

# 20 paylines: the row (0 = top) used on each reel.
PAYLINES: list[tuple[int, ...]] = [
    (0, 0, 0, 0, 0, 0), (1, 1, 1, 1, 1, 1), (2, 2, 2, 2, 2, 2), (3, 3, 3, 3, 3, 3),
    (0, 1, 2, 2, 1, 0), (3, 2, 1, 1, 2, 3), (1, 2, 3, 3, 2, 1), (2, 1, 0, 0, 1, 2),
    (0, 1, 0, 1, 0, 1), (1, 0, 1, 0, 1, 0), (2, 3, 2, 3, 2, 3), (3, 2, 3, 2, 3, 2),
    (1, 2, 1, 2, 1, 2), (2, 1, 2, 1, 2, 1), (0, 0, 1, 1, 2, 2), (3, 3, 2, 2, 1, 1),
    (1, 1, 0, 0, 1, 1), (2, 2, 3, 3, 2, 2), (0, 1, 1, 1, 1, 0), (3, 2, 2, 2, 2, 3),
]
LINES = len(PAYLINES)

FREE_SPINS = {3: 8, 4: 10, 5: 12, 6: 15}  # comets -> spins (6 or more gives 15)
RETRIGGER_SPINS = 5
MAX_FREE_SPINS = 50   # a single feature never runs longer than this
MAX_WIN = 2500        # times the total bet
# Payback of the paytable above, measured over 10 million full rounds (base spin + free spins + cap).
# The bot scales every pay by SLOTS_RTP / MEASURED_RTP, so the game returns SLOTS_RTP (set in .env).
MEASURED_RTP = 0.9877

Grid = list[list[str]]  # grid[reel][row]


@dataclass
class LineWin:
    line: int          # 0-based payline index
    symbol: str
    count: int
    pay: int           # in line bets

    @property
    def cells(self) -> list[tuple[int, int]]:
        return [(r, PAYLINES[self.line][r]) for r in range(self.count)]


@dataclass
class SpinResult:
    grid: Grid
    wins: list[LineWin]
    scatters: list[tuple[int, int]]
    sticky: set[tuple[int, int]] = field(default_factory=set)  # wilds held from earlier free spins

    @property
    def pay(self) -> int:
        """Line pays in line bets."""
        return sum(w.pay for w in self.wins)


def _picker(weights: dict[str, float]):
    keys = list(weights)
    cum, total = [], 0.0
    for k in keys:
        total += weights[k]
        cum.append(total)
    return keys, cum, total


_PICKERS = {False: [_picker(w) for w in WEIGHTS], True: [_picker(w) for w in FREE_WEIGHTS]}


def _draw(rng, reel: int, free: bool = False) -> str:
    keys, cum, total = _PICKERS[free][reel]
    x = rng.random() * total
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return keys[lo]


_COMET_COUNTS = list(COMET_ODDS)
_COMET_CUM = []
_acc = 0.0
for _k in _COMET_COUNTS:
    _acc += COMET_ODDS[_k]
    _COMET_CUM.append(_acc)


def comet_count(rng) -> int:
    x = rng.random()
    for k, c in zip(_COMET_COUNTS, _COMET_CUM):
        if x < c:
            return k
    return 0


def random_grid(rng, sticky: set[tuple[int, int]] = frozenset(), free: bool = False) -> Grid:
    grid = [[WILD if (r, row) in sticky else _draw(rng, r, free) for row in range(ROWS)] for r in range(REELS)]
    k = comet_count(rng)
    if k:
        open_cells = [(r, row) for r in range(REELS) for row in range(ROWS) if (r, row) not in sticky]
        for r, row in rng.sample(open_cells, min(k, len(open_cells))):
            grid[r][row] = SCATTER
    return grid


def evaluate(grid: Grid, sticky: set[tuple[int, int]] = frozenset()) -> SpinResult:
    wins = []
    for i, line in enumerate(PAYLINES):
        first = grid[0][line[0]]
        if first in (WILD, SCATTER):  # reel 1 never has wilds; a comet there starts no line
            continue
        count = 1
        for r in range(1, REELS):
            sym = grid[r][line[r]]
            if sym == first or sym == WILD:
                count += 1
            else:
                break
        if count >= 3:
            wins.append(LineWin(i, first, count, SYMBOLS[first][2][count - 3]))
    scatters = [(r, row) for r in range(REELS) for row in range(ROWS) if grid[r][row] == SCATTER]
    return SpinResult(grid, wins, scatters, set(sticky))


def spin(rng, sticky: set[tuple[int, int]] = frozenset(), free: bool = False) -> SpinResult:
    return evaluate(random_grid(rng, sticky, free), sticky)


def wilds_in(grid: Grid) -> set[tuple[int, int]]:
    return {(r, row) for r in range(REELS) for row in range(ROWS) if grid[r][row] == WILD}


def free_spins_for(scatters: int) -> int:
    return FREE_SPINS[min(scatters, 6)] if scatters >= 3 else 0


@dataclass
class Round:
    """One paid spin plus the free spins it triggered. Pays are in line bets, before the win cap."""
    base: SpinResult
    free: list[SpinResult] = field(default_factory=list)
    retriggers: int = 0

    @property
    def pay(self) -> int:
        return self.base.pay + sum(s.pay for s in self.free)


def play_round(rng) -> Round:
    base = spin(rng)
    rnd = Round(base)
    left = free_spins_for(len(base.scatters))
    played = 0
    sticky: set[tuple[int, int]] = set()
    while left > 0 and played < MAX_FREE_SPINS:
        left -= 1
        played += 1
        result = spin(rng, sticky, free=True)
        rnd.free.append(result)
        sticky = sticky | wilds_in(result.grid)
        if len(result.scatters) >= 3:
            left += RETRIGGER_SPINS
            rnd.retriggers += 1
    return rnd


def capped_multiplier(line_bets: float) -> float:
    """Converts a pay in line bets to a multiple of the total bet, applying the win cap."""
    return min(line_bets / LINES, MAX_WIN)
