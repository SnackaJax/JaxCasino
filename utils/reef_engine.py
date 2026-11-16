"""Reef Rush: a 7x7 cluster-pays cascade slot under the sea, with the Tide Meter, Surges, the Kraken, Pearl
Wilds and Bubble Bursts. Pure game logic, no Discord or web code, so the Activity (activity/reef.py) and the
simulation below play exactly the same game.

    python -m utils.reef_engine [spins]        # measures the payback and prints the stats (uses every core)

How a spin works
- 49 symbols drop onto a 7x7 grid. Every cell is drawn independently from WEIGHTS (a few are wilds).
- A cluster is 5 or more matching symbols touching up, down, left or right. Wilds join any cluster. Every
  cluster pays (PAYS, times the total bet, by its size), then its symbols are removed, the symbols above fall
  down and new ones drop in from the top. This cascade repeats until no cluster is left.
- A cluster of PEARL_SIZE or more leaves a Pearl Wild (an ordinary wild) on one of its cells.
- Every symbol removed by a win charges the Tide Meter (it starts empty on every paid spin). When the cascade
  stops and the meter holds SURGE_AT charges, a random Surge hits the grid:
      Whirlpool    3 to 6 random symbols turn wild
      Riptide      a wild lands and washes away every other symbol in its row and column
      Undertow     every low symbol (shell, pearl, urchin, starfish) is washed away
      Coral Shift  every symbol of one kind turns into another kind already on the grid
  and the cascade carries on. If it stops again with KRAKEN_AT charges, the Kraken lands: a 3x3 wild on the
  bottom rows. After it takes part in a win it shrinks to 2x2, and after another win it splits into 4 wilds.
- When the first drop of a spin wins nothing, a Bubble Burst sometimes (BUBBLE_CHANCE) pops every symbol of
  one low kind; the gaps refill and the cascade may start.
- A paid spin never wins more than MAX_WIN times the bet.

All amounts are in "x": multiples of the total bet, before the RTP scale. The bot scales every pay by
REEF_RTP / MEASURED_RTP so the game returns REEF_RTP (set in .env).
"""
from __future__ import annotations

import math
import sys

COLS = ROWS = 7
N = COLS * ROWS
MIN_CLUSTER = 5

LOW = ["shell", "pearl", "urchin", "star"]
HIGH = ["clown", "puffer", "jelly", "turtle"]
PAYING = LOW + HIGH
WILD = "wild"
KRAKEN = "kraken"        # a cell covered by the Kraken (a giant wild)
WILDS = (WILD, KRAKEN)

NAMES = {"shell": "Scallop", "pearl": "Pearl", "urchin": "Urchin", "star": "Starfish", "clown": "Clownfish",
         "puffer": "Pufferfish", "jelly": "Jellyfish", "turtle": "Sea Turtle", WILD: "Wild",
         KRAKEN: "The Kraken"}

# Pays by cluster size: 5, 6, 7, ... 14, 15+ (times the total bet)
PAYS: dict[str, tuple[float, ...]] = {
    "shell":  (0.8, 1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 12),
    "pearl":  (0.8, 1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 12),
    "urchin": (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 10, 20),
    "star":   (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 10, 20),
    "clown":  (1.5, 2, 2.5, 3, 4, 5, 6, 10, 12, 20, 40),
    "puffer": (2, 2.5, 3, 4, 5, 6, 10, 12, 20, 30, 60),
    "jelly":  (2.5, 3, 4, 5, 6, 10, 12, 20, 30, 50, 120),
    "turtle": (3, 5, 6, 8, 10, 12, 20, 30, 50, 100, 300),
}
PAY_SIZES = len(PAYS["shell"])     # 5 .. 15+

WEIGHTS = {"shell": 16, "pearl": 16, "urchin": 15, "star": 15, "clown": 10, "puffer": 9, "jelly": 8,
           "turtle": 7, WILD: 0.8}

PEARL_SIZE = 9
SURGE_AT = 10
KRAKEN_AT = 32
SURGES = {"whirlpool": 25, "riptide": 25, "undertow": 25, "shift": 25}
SURGE_NAMES = {"whirlpool": "Whirlpool", "riptide": "Riptide", "undertow": "Undertow", "shift": "Coral Shift"}
SURGE_TEXT = {
    "whirlpool": "3 to 6 random symbols turn wild",
    "riptide": "a wild lands and washes away every other symbol in its row and column",
    "undertow": "every low symbol is washed away",
    "shift": "every symbol of one kind turns into another kind",
}
BUBBLE_CHANCE = 0.12

MAX_WIN = 5000
# Payback of everything above (raw, before scaling), measured with `python -m utils.reef_engine 24000000`:
# 0.9785 ± 0.0031 over 24 million spins. The bot scales every pay by REEF_RTP / MEASURED_RTP, so at the default
# REEF_RTP of 99% every pay is 1.2% more than the table above. Re-measure after changing anything above.
MEASURED_RTP = 0.9785

MAX_STEPS = 400           # safety net for the cascade loop (never reached in practice)

# neighbours of every cell; a cell is index c * ROWS + r (column c from the left, row r from the top)
NEIGH: list[tuple[int, ...]] = []
for _c in range(COLS):
    for _r in range(ROWS):
        NEIGH.append(tuple(c * ROWS + r for c, r in ((_c - 1, _r), (_c + 1, _r), (_c, _r - 1), (_c, _r + 1))
                           if 0 <= c < COLS and 0 <= r < ROWS))

Grid = list   # flat list of N symbols (None = empty for a moment, while symbols fall)


def cell(i: int) -> list[int]:
    return [i // ROWS, i % ROWS]


def nested(g: Grid) -> list[list[str]]:
    """grid[column][row], for the page."""
    return [g[c * ROWS:(c + 1) * ROWS] for c in range(COLS)]


def pay(sym: str, size: int) -> float:
    return PAYS[sym][min(size, MIN_CLUSTER + PAY_SIZES - 1) - MIN_CLUSTER]


# ------------------------------------------------------------------ drawing symbols
def _picker(weights: dict):
    keys, cum, total = list(weights), [], 0.0
    for k in keys:
        total += weights[k]
        cum.append(total)
    return keys, cum, total


_SYMBOL_PICKER = _picker(WEIGHTS)


def _pick(rng, picker) -> str:
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


def weighted(rng, weights: dict) -> str:
    return _pick(rng, _picker(weights))


def draw(rng) -> str:
    return _pick(rng, _SYMBOL_PICKER)


# ------------------------------------------------------------------ clusters
def clusters(g: Grid) -> list[tuple[str, list[int]]]:
    """Every winning cluster: (symbol, sorted cell indexes, wilds included)."""
    wild_count = sum(1 for s in g if s in WILDS)
    counts: dict[str, int] = {}
    for s in g:
        counts[s] = counts.get(s, 0) + 1
    found = []
    for sym in PAYING:
        if counts.get(sym, 0) + wild_count < MIN_CLUSTER or not counts.get(sym):
            continue
        seen: set[int] = set()
        for i in range(N):
            if g[i] != sym or i in seen:
                continue
            group = {i}
            stack = [i]
            while stack:
                j = stack.pop()
                for k in NEIGH[j]:
                    if k not in group and (g[k] == sym or g[k] in WILDS):
                        group.add(k)
                        stack.append(k)
            seen.update(k for k in group if g[k] == sym)
            if len(group) >= MIN_CLUSTER:
                found.append((sym, sorted(group)))
    return found


# ------------------------------------------------------------------ one paid spin
class Round:
    """One paid spin, played out completely: `steps` is what the page animates, in order.

    Steps (cells are [column, row], grids are grid[column][row]):
      {"t": "drop", "grid"}                                      the first symbols land
      {"t": "win", "clusters": [{"sym", "cells", "size", "x"}], "x", "meter", "pearls": [cell], "kraken"}
                                                                 clusters pay and are removed; the Kraken (if
                                                                 it took part) shrinks or splits; "kraken" is
                                                                 where it is afterwards (or None)
      {"t": "fall", "moves", "grid"}                             symbols fall and new ones drop in. moves[c][r]
                                                                 is the row the symbol now at (c, r) came from;
                                                                 negative for new ones (they start above the grid)
      {"t": "surge", "kind", ...}                                a Surge (see Round._surge)
      {"t": "kraken", "kraken": {"c", "size"}}                   the Kraken lands
      {"t": "bubble", "sym", "cells"}                            a Bubble Burst pops these cells
    """

    def __init__(self, rng):
        self.steps: list[dict] = []
        self.raw_x = 0.0
        self.meter = 0
        self.surge: str | None = None
        self.kraken_landed = False
        self.bubble = False
        self.cascades = 0
        self.kraken: dict | None = None      # {"c": left column, "size": 3 or 2}; it always sits on the bottom rows
        g: Grid = [draw(rng) for _ in range(N)]
        self.steps.append({"t": "drop", "grid": nested(g)})
        for _ in range(MAX_STEPS):
            found = clusters(g)
            if found:
                self._win(rng, g, found)
                continue
            if self.meter >= SURGE_AT and self.surge is None:
                self._surge(rng, g)
                continue
            if self.meter >= KRAKEN_AT and not self.kraken_landed:
                self._land_kraken(rng, g)
                continue
            if self.cascades == 0 and not self.bubble and rng.random() < BUBBLE_CHANCE:
                self._bubble(rng, g)
                continue
            break
        self.grid = g

    @property
    def x(self) -> float:
        return min(self.raw_x, MAX_WIN)

    # ---- pieces of a spin
    def _kraken_cells(self) -> list[int]:
        k = self.kraken
        if not k:
            return []
        size = k["size"]
        return [c * ROWS + r for c in range(k["c"], k["c"] + size) for r in range(ROWS - size, ROWS)]

    def _win(self, rng, g: Grid, found) -> None:
        self.cascades += 1
        step_x = 0.0
        out = []
        removed: set[int] = set()
        kraken_cells = set(self._kraken_cells())
        pearls: set[int] = set()
        for sym, cells in found:
            x = pay(sym, len(cells))
            step_x += x
            out.append({"sym": sym, "cells": [cell(i) for i in cells], "size": len(cells), "x": x})
            removed.update(cells)
            if len(cells) >= PEARL_SIZE:
                spots = [i for i in cells if i not in kraken_cells and i not in pearls]
                if spots:
                    pearls.add(rng.choice(spots))
        self.raw_x += step_x
        self.meter += len(removed - kraken_cells)
        for i in removed:
            if i not in kraken_cells and i not in pearls:
                g[i] = None
        for i in pearls:
            g[i] = WILD
        if kraken_cells & removed:
            k = self.kraken
            if k["size"] == 3:
                # shrinks to 2x2 on the bottom rows, under the left or right two columns of where it was
                new_c = k["c"] + rng.randrange(2)
                self.kraken = {"c": new_c, "size": 2}
                keep = set(self._kraken_cells())
                for i in kraken_cells - keep:
                    g[i] = None
            else:
                # splits into 4 ordinary wilds
                for i in kraken_cells:
                    g[i] = WILD
                self.kraken = None
        self.steps.append({"t": "win", "clusters": out, "x": step_x, "meter": self.meter,
                           "pearls": [cell(i) for i in sorted(pearls)],
                           "kraken": dict(self.kraken) if self.kraken else None})
        self._fall(rng, g)

    def _fall(self, rng, g: Grid) -> None:
        """Symbols fall into the gaps; new ones drop in from the top. The Kraken never moves (it sits on the
        bottom rows, so every gap is above it)."""
        fixed = set(self._kraken_cells())
        moves = []
        for c in range(COLS):
            base = c * ROWS
            top_fixed = ROWS
            while top_fixed > 0 and base + top_fixed - 1 in fixed:
                top_fixed -= 1
            survivors = [(r, g[base + r]) for r in range(top_fixed) if g[base + r] is not None]
            new = top_fixed - len(survivors)
            col_moves = []
            for r in range(top_fixed):
                if r < new:
                    g[base + r] = draw(rng)
                    col_moves.append(r - new)
                else:
                    src, sym = survivors[r - new]
                    g[base + r] = sym
                    col_moves.append(src)
            col_moves += list(range(top_fixed, ROWS))
            moves.append(col_moves)
        self.steps.append({"t": "fall", "moves": moves, "grid": nested(g)})

    def _surge(self, rng, g: Grid) -> None:
        kind = weighted(rng, SURGES)
        self.surge = kind
        fixed = set(self._kraken_cells())
        free = [i for i in range(N) if i not in fixed]
        step: dict = {"t": "surge", "kind": kind}
        if kind == "whirlpool":
            spots = [i for i in free if g[i] != WILD]
            turned = sorted(rng.sample(spots, min(len(spots), rng.randint(3, 6))))
            for i in turned:
                g[i] = WILD
            step.update(cells=[cell(i) for i in turned], grid=nested(g))
            self.steps.append(step)
        elif kind == "riptide":
            centre = rng.choice(free)
            c0, r0 = divmod(centre, ROWS)
            washed = sorted(i for i in free if i != centre and (i // ROWS == c0 or i % ROWS == r0))
            g[centre] = WILD
            for i in washed:
                g[i] = None
            step.update(centre=[c0, r0], cells=[cell(i) for i in washed])
            self.steps.append(step)
            self._fall(rng, g)
        elif kind == "undertow":
            washed = sorted(i for i in free if g[i] in LOW)
            for i in washed:
                g[i] = None
            step.update(cells=[cell(i) for i in washed])
            self.steps.append(step)
            if washed:
                self._fall(rng, g)
        else:   # shift: one kind on the grid turns into another kind on the grid
            present = sorted({g[i] for i in free if g[i] in PAYING}, key=PAYING.index)
            if len(present) >= 2:
                src = rng.choice(present)
                dst = rng.choice([s for s in present if s != src])
            else:
                src = present[0] if present else PAYING[0]
                dst = rng.choice([s for s in PAYING if s != src])
            turned = [i for i in free if g[i] == src]
            for i in turned:
                g[i] = dst
            step.update(sym=src, to=dst, cells=[cell(i) for i in turned], grid=nested(g))
            self.steps.append(step)

    def _land_kraken(self, rng, g: Grid) -> None:
        self.kraken_landed = True
        self.kraken = {"c": rng.randrange(COLS - 2), "size": 3}
        for i in self._kraken_cells():
            g[i] = KRAKEN
        self.steps.append({"t": "kraken", "kraken": dict(self.kraken), "grid": nested(g)})

    def _bubble(self, rng, g: Grid) -> None:
        self.bubble = True
        present = sorted({s for s in g if s in LOW}, key=PAYING.index)
        if not present:
            return
        sym = rng.choice(present)
        popped = [i for i in range(N) if g[i] == sym]
        for i in popped:
            g[i] = None
        self.steps.append({"t": "bubble", "sym": sym, "cells": [cell(i) for i in popped]})
        self._fall(rng, g)

    def to_dict(self) -> dict:
        return {"steps": self.steps, "x": self.x, "surge": self.surge, "kraken": self.kraken_landed,
                "bubble": self.bubble, "cascades": self.cascades}


# ------------------------------------------------------------------ credits
def scale(rtp: float) -> float:
    return rtp / MEASURED_RTP


def credits(stake: int, x: float, rtp: float) -> int:
    return math.floor(stake * x * scale(rtp) + 1e-9)


def payout(stake: int, rnd: Round, rtp: float) -> int:
    return min(credits(stake, rnd.x, rtp), stake * MAX_WIN)


# ------------------------------------------------------------------ simulation
def _sim_chunk(args) -> dict:
    import random
    seed, n = args
    rng = random.Random(seed)
    s = {"n": n, "total": 0.0, "sq": 0.0, "hits": 0, "above": 0, "surge": 0, "kraken": 0, "bubble": 0,
         "capped": 0, "biggest": 0.0, "cascades": 0, "surge_x": 0.0, "kraken_x": 0.0, "plain_x": 0.0,
         "by_surge": {k: 0 for k in SURGES}, "ge100": 0, "ge1000": 0}
    for _ in range(n):
        rnd = Round(rng)
        x = rnd.x
        s["total"] += x
        s["sq"] += x * x
        s["hits"] += x > 0
        s["above"] += x > 1
        s["cascades"] += rnd.cascades
        s["capped"] += rnd.raw_x >= MAX_WIN
        s["ge100"] += x >= 100
        s["ge1000"] += x >= 1000
        s["biggest"] = max(s["biggest"], x)
        s["bubble"] += rnd.bubble
        if rnd.kraken_landed:
            s["kraken"] += 1
            s["kraken_x"] += x
        elif rnd.surge:
            s["surge_x"] += x
        else:
            s["plain_x"] += x
        if rnd.surge:
            s["surge"] += 1
            s["by_surge"][rnd.surge] += 1
    return s


def _simulate(n: int) -> None:
    import os
    import random
    import time
    from multiprocessing import Pool
    t0 = time.time()
    workers = os.cpu_count() or 4
    chunks = 4 * workers
    per = max(1, n // chunks)
    seeds = [(random.getrandbits(64), per) for _ in range(chunks)]
    with Pool(workers) as pool:
        parts = pool.map(_sim_chunk, seeds)
    tot = {k: 0 for k in parts[0] if k not in ("by_surge", "biggest")}
    by_surge = {k: 0 for k in SURGES}
    biggest = 0.0
    for p in parts:
        for k in tot:
            tot[k] += p[k]
        for k in SURGES:
            by_surge[k] += p["by_surge"][k]
        biggest = max(biggest, p["biggest"])
    n = tot["n"]
    mean = tot["total"] / n
    sd = math.sqrt(max(tot["sq"] / n - mean * mean, 0))
    print(f"{n:,} spins in {time.time() - t0:.0f}s on {workers} cores")
    print(f"raw payback {mean:.4f} ± {1.96 * sd / math.sqrt(n):.4f} (95%), sd per spin {sd:.2f}")
    print(f"  from plain spins {tot['plain_x'] / n:.4f}, surge spins {tot['surge_x'] / n:.4f}, "
          f"kraken spins {tot['kraken_x'] / n:.4f}")
    print(f"wins on {tot['hits'] / n:.1%} of spins, more than the bet on {tot['above'] / n:.1%}, "
          f"{tot['cascades'] / n:.2f} cascades per spin")
    print(f"surge every {n / max(tot['surge'], 1):.1f} spins "
          f"({', '.join(f'{k} {v / max(tot['surge'], 1):.0%}' for k, v in by_surge.items())}), "
          f"kraken every {n / max(tot['kraken'], 1):.0f}, bubble burst every {n / max(tot['bubble'], 1):.1f}")
    print(f"100x+ every {n / max(tot['ge100'], 1):,.0f} spins, 1000x+ every {n / max(tot['ge1000'], 1):,.0f}, "
          f"biggest {biggest:.0f}x, capped {tot['capped']}")


if __name__ == "__main__":
    _simulate(int(sys.argv[1]) if len(sys.argv) > 1 else 2_000_000)
