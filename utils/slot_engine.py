"""Cosmic Lab: a 6-reel, 4-row video slot with 20 fixed paylines, wilds, scatters and free spins with
sticky wilds. Pure game logic, no Discord code, so it can be simulated (`python -m utils.slot_engine`).

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

# The bonus buy: a spin that is sure to land BUY_COMETS comets (so FREE_SPINS[3] free spins). It pays at the paid
# spin's scale, like everything else, so its price sets its payback: SLOTS_RTP / MEASURED_RTP x raw value / price.
# Raw value (in total bets, retriggers and the win cap included), measured with `python -m utils.slot_engine buy`
# over 4 million rounds: 40.843 ± 0.054 (95%). At 41.5x the bet it pays back 98.6% (98.5-98.8%) at SLOTS_RTP 99%;
# a paid spin pays back 99%. Re-measure after changing the reels, the pays or the free spins.
BUY_COMETS = 3
BUY_PRICE = 41.5
BUY_VALUE = 40.843    # raw value of the buy, in total bets
BUY_NAME = "Free Spins"
BUY_TEXT = f"One spin that is sure to land {BUY_COMETS} comets: {FREE_SPINS[BUY_COMETS]} free spins with sticky wilds"
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


def random_grid(rng, sticky: set[tuple[int, int]] = frozenset(), free: bool = False,
                comets: int | None = None) -> Grid:
    """comets: exactly this many comets land (a bonus buy); None draws how many from COMET_ODDS."""
    grid = [[WILD if (r, row) in sticky else _draw(rng, r, free) for row in range(ROWS)] for r in range(REELS)]
    k = comet_count(rng) if comets is None else comets
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


def spin(rng, sticky: set[tuple[int, int]] = frozenset(), free: bool = False,
         comets: int | None = None) -> SpinResult:
    return evaluate(random_grid(rng, sticky, free, comets), sticky)


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


def play_round(rng, mode: str = "spin") -> Round:
    """A paid spin, or a bonus buy (mode "free": the spin is sure to land BUY_COMETS comets)."""
    base = spin(rng, comets=BUY_COMETS if mode == "free" else None)
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


def buy_rtp(rtp: float) -> float:
    """What the bonus buy pays back on average (0.99 = 99%) when paid spins are set to `rtp`."""
    return rtp / MEASURED_RTP * BUY_VALUE / BUY_PRICE


def capped_multiplier(line_bets: float) -> float:
    """Converts a pay in line bets to a multiple of the total bet, applying the win cap."""
    return min(line_bets / LINES, MAX_WIN)


# ------------------------------------------------------------------ simulation
def _sim_chunk(args) -> tuple[int, float, float, int]:
    import random
    seed, n, mode = args
    rng = random.Random(seed)
    total = sq = 0.0
    capped = 0
    for _ in range(n):
        x = capped_multiplier(play_round(rng, mode).pay)
        total += x
        sq += x * x
        capped += x >= MAX_WIN
    return n, total, sq, capped


def _simulate(n: int, mode: str) -> None:
    import math
    import os
    import random
    import time
    from multiprocessing import Pool
    t0 = time.time()
    workers = os.cpu_count() or 4
    chunks = 4 * workers
    with Pool(workers) as pool:
        parts = pool.map(_sim_chunk, [(random.getrandbits(64), max(1, n // chunks), mode) for _ in range(chunks)])
    n = sum(p[0] for p in parts)
    mean = sum(p[1] for p in parts) / n
    sd = math.sqrt(max(sum(p[2] for p in parts) / n - mean * mean, 0))
    ci = 1.96 * sd / math.sqrt(n)
    scale = 0.99 / MEASURED_RTP
    print(f"[{mode}] {n:,} rounds in {time.time() - t0:.0f}s: raw {mean:.4f} ± {ci:.4f} (95%), "
          f"capped {sum(p[3] for p in parts)}")
    if mode != "spin":
        print(f"  price {BUY_PRICE:g}x: pays back {mean * scale / BUY_PRICE:.2%} ({(mean - ci) * scale / BUY_PRICE:.2%}-"
              f"{(mean + ci) * scale / BUY_PRICE:.2%}) at SLOTS_RTP 99%; break-even (99%) price "
              f"{mean * scale / 0.99:.3f}x")


if __name__ == "__main__":
    # python -m utils.slot_engine [rounds]          paid spins (raw payback, for MEASURED_RTP)
    # python -m utils.slot_engine buy [rounds]      the bonus buy (its raw value and payback)
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "buy":
        _simulate(int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000, "free")
    else:
        _simulate(int(sys.argv[1]) if len(sys.argv) > 1 else 2_000_000, "spin")
