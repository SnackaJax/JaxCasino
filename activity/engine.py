"""Spin Frenzy rules: the main wheel, the Boost Reel and the four bonus rounds.

Everything random is decided here with the bot's secure RNG. The server calls these functions at the moment
each result is needed (never earlier), so nothing about a result exists before betting on it has closed.

Payouts
-------
A winning number bet N pays N to 1: the stake comes back plus N x stake. A winning bonus bet pays the bonus
value V the same way: stake back plus V x stake. When the Boost Reel lands on the spot that wins, the winnings
(N or V) are multiplied by the reel's multiplier.

Return to player
----------------
Number spots: the wheel and the reel multipliers are fixed, and `boost_chances(rtp)` solves, per number, how
often the Boost Reel lands on it so that number returns `rtp` on average.

Bonus spots: they win more often than their share of the wheel (BONUS_WEIGHT) and the Boost Reel lands on them
at a fixed chance (BONUS_BOOST_CHANCE). What is tuned is the shape of each prize table: every table has a "main"
part and a "top" part. The top part holds the rare big prizes, and their chance never changes. configure() tilts
the main part toward its smaller or bigger values until the bonus pays back `bonus_rtp`.
Run `python -m activity.engine` to print the exact figures and check them with a simulation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from utils.rng import rng

# ------------------------------------------------------------------ the main wheel
NUMBERS = {"1": 1, "2": 2, "5": 5, "10": 10}
BONUSES = ["coin", "grid", "drop", "frenzy"]
SPOTS = list(NUMBERS) + BONUSES
SEGMENT_COUNTS = {"1": 20, "2": 12, "5": 7, "10": 4, "coin": 3, "grid": 2, "drop": 2, "frenzy": 1}
SEGMENTS = sum(SEGMENT_COUNTS.values())  # 51

SPOT_NAMES = {"1": "1", "2": "2", "5": "5", "10": "10", "coin": "Coin Toss", "grid": "Treasure Grid",
              "drop": "Peg Drop", "frenzy": "Frenzy Wheel"}


def _layout() -> list[str]:
    """Spreads each spot evenly around the wheel (fixed order, the same every round)."""
    placed = []
    for i, (spot, count) in enumerate(SEGMENT_COUNTS.items()):
        for k in range(count):
            # small per-spot offset so spots with the same count don't all start at 0
            placed.append(((k + 0.5) * SEGMENTS / count + i * 0.37, spot))
    placed.sort()
    order = [s for _, s in placed]
    # avoid two identical bonus segments side by side
    for _ in range(3):
        for i in range(SEGMENTS):
            a, b = order[i], order[(i + 1) % SEGMENTS]
            if a == b and a in BONUSES:
                for j in range(SEGMENTS):
                    c = order[j]
                    if c in NUMBERS and order[(j - 1) % SEGMENTS] != a and order[(j + 1) % SEGMENTS] != a:
                        order[(i + 1) % SEGMENTS], order[j] = order[j], order[(i + 1) % SEGMENTS]
                        break
    return order


WHEEL = _layout()

# ------------------------------------------------------------------ the Boost Reel
# Multiplier on the reel's second window: value -> weight.
BOOST_MULTS = {2: 30, 3: 20, 4: 15, 5: 12, 7: 8, 10: 7, 15: 4, 25: 2.5, 50: 1.5}
# Chance per round that the reel lands on each bonus (fixed; the bonus prize tables are tuned around it instead).
BONUS_BOOST_CHANCE = {"coin": 0.035, "grid": 0.025, "drop": 0.035, "frenzy": 0.015}

# ------------------------------------------------------------------ bonus prize tables (value -> weight in %)
# MAIN tables are reshaped by configure() to hit the payback target. TOP tables are the rare big prizes: their
# weight is the % of games (or cells, slots, segments) that get them, and it never changes.
COIN_LOW_MAIN = {2: 18, 3: 20, 4: 18, 5: 15, 7: 12, 10: 10, 15: 7}
COIN_HIGH_MAIN = {10: 14, 12: 15, 15: 16, 20: 15, 25: 12, 30: 10, 40: 8, 50: 6}
COIN_HIGH_TOP = {75: 1.0, 100: 0.6, 150: 0.3, 250: 0.15, 500: 0.06}

GRID_CELLS = 48          # 8 x 6
GRID_MAIN = {10: 20, 12: 16, 15: 14, 20: 12, 25: 9, 30: 7, 40: 6, 50: 5}
GRID_TOP: dict = {}      # the big prizes are the GRID_HIGH chests instead
# Every game has 1 or 2 big chests (75x and up), at random places among the others.
GRID_HIGH = {75: 45, 100: 30, 150: 17, 200: 8}
GRID_HIGH_COUNT = {1: 50, 2: 50}   # number of big chests -> weight

DROP_SLOTS = 20
DROP_ROWS = DROP_SLOTS - 1
DROP_DOUBLES = 3
DROP_MAX_DOUBLES = 4
DROP_MAIN = {3: 10, 5: 14, 7: 15, 10: 16, 15: 14, 20: 11, 25: 8, 30: 6, 40: 4}
DROP_TOP = {50: 1.2}
# Every board has 1 or 2 big slots (75x and up) on its outer edges, like a real plinko board: the ball reaches
# each edge slot only DROP_EDGE_CHANCE of the time; the 18 inner slots share the rest equally. An edge without a
# big prize holds an ordinary value.
DROP_HIGH = {75: 45, 100: 30, 150: 15, 250: 7, 500: 3}
DROP_HIGH_COUNT = {1: 50, 2: 50}   # number of big edge slots -> weight
DROP_EDGE_CHANCE = 0.015

FRENZY_SEGMENTS = 48
FRENZY_POINTERS = 3              # at 0, 1/3 and 2/3 of the way round
FRENZY_DOUBLES = 3
FRENZY_TRIPLES = 2
FRENZY_MAX_BOOSTS = 4            # after this many, a pointer ignores DOUBLE/TRIPLE and the wheel spins again
FRENZY_MAX_SPINS = 25            # hard stop; practically never reached
FRENZY_MAIN = {5: 10, 7: 12, 10: 16, 15: 15, 20: 14, 25: 12, 50: 10, 75: 6}
FRENZY_TOP = {100: 1.6, 150: 0.9, 200: 0.5, 300: 0.25, 500: 0.12, 1000: 0.04}
POINTER_NAMES = ["Ruby", "Jade", "Sapphire"]


def _mean(table: dict) -> float:
    total = sum(table.values())
    return sum(v * w for v, w in table.items()) / total


def _draw(table: dict) -> int:
    return rng.choices(list(table), weights=list(table.values()))[0]


# ------------------------------------------------------------------ exact expected bonus values
def coin_expected() -> float:
    return (_mean(COIN_LOW) + _mean(COIN_HIGH)) / 2


def grid_expected() -> float:
    big = _mean(GRID_HIGH_COUNT) / GRID_CELLS   # chance that a picked chest is a big one
    return (1 - big) * _mean(GRID_VALUES) + big * _mean(GRID_HIGH)


def drop_landing() -> list[float]:
    """Chance of the ball landing in each slot: the two edges are rare, the inner slots are equally likely."""
    inner = (1 - 2 * DROP_EDGE_CHANCE) / (DROP_SLOTS - 2)
    return [DROP_EDGE_CHANCE] + [inner] * (DROP_SLOTS - 2) + [DROP_EDGE_CHANCE]


def drop_expected() -> float:
    mu, high = _mean(DROP_VALUES), _mean(DROP_HIGH)
    n_high = _mean(DROP_HIGH_COUNT)
    q = DROP_EDGE_CHANCE
    inner = (1 - 2 * q) / (DROP_SLOTS - 2)
    p = DROP_DOUBLES * inner                      # doubles sit on inner slots
    # value won per landing, counting only landings on a value (the edges: big or ordinary; the inner values)
    a = q * n_high * high + q * (2 - n_high) * mu + (DROP_SLOTS - 2 - DROP_DOUBLES) * inner * mu
    e = a / (1 - p)  # after the last allowed double, the ball can only land on a value
    for _ in range(DROP_MAX_DOUBLES):
        e = a + p * 2 * e
    return e


def frenzy_expected() -> float:
    mu = _mean(FRENZY_VALUES)
    p2, p3 = FRENZY_DOUBLES / FRENZY_SEGMENTS, FRENZY_TRIPLES / FRENZY_SEGMENTS
    pv = 1 - p2 - p3
    e = mu
    for _ in range(FRENZY_MAX_BOOSTS):
        e = pv * mu + (2 * p2 + 3 * p3) * e
    return e


# ------------------------------------------------------------------ prize tables in play
def _shape(main: dict, top: dict, tilt: float) -> dict:
    """main tilted (tilt > 0 favors its small values, < 0 its big ones) and scaled so it plus top make 100%."""
    raw = {v: w * v ** -tilt for v, w in main.items()}
    k = (100 - sum(top.values())) / sum(raw.values())
    out = {v: w * k for v, w in raw.items()}
    for v, w in top.items():
        out[v] = out.get(v, 0) + w
    return out


# bonus -> [(table name, main part, top part)]
_PARTS = {
    "coin": [("COIN_LOW", COIN_LOW_MAIN, {}), ("COIN_HIGH", COIN_HIGH_MAIN, COIN_HIGH_TOP)],
    "grid": [("GRID_VALUES", GRID_MAIN, GRID_TOP)],
    "drop": [("DROP_VALUES", DROP_MAIN, DROP_TOP)],
    "frenzy": [("FRENZY_VALUES", FRENZY_MAIN, FRENZY_TOP)],
}
TILT = {b: 0.0 for b in _PARTS}


def _apply_tilt(bonus: str, tilt: float) -> None:
    TILT[bonus] = tilt
    for name, main, top in _PARTS[bonus]:
        globals()[name] = _shape(main, top, tilt)


for _b in _PARTS:
    _apply_tilt(_b, 0.0)


# ------------------------------------------------------------------ how often the wheel lands on each spot
# Bonus segments come up more often than their share of the wheel (BONUS_WEIGHT > 1) without changing the
# wheel's look.
BONUS_WEIGHT = 1.0


def _segment_weight(spot: str) -> float:
    return BONUS_WEIGHT if spot in BONUSES else 1.0


def land_chance(spot: str) -> float:
    total = sum(c * _segment_weight(s) for s, c in SEGMENT_COUNTS.items())
    return SEGMENT_COUNTS[spot] * _segment_weight(spot) / total


def configure(rtp: float, bonus_weight: float = 1.0, bonus_rtp: float = 1.0) -> dict[str, float]:
    """Sets how much more often bonus segments win (1 = their fair share of the wheel), reshapes each bonus prize
    table so the bonus pays back `bonus_rtp` (as close as its table allows), and returns the Boost Reel chances.
    The number spots always return `rtp`."""
    global BONUS_WEIGHT
    BONUS_WEIGHT = max(1.0, float(bonus_weight))
    chances = boost_chances(rtp)
    chances.update(BONUS_BOOST_CHANCE)
    if sum(chances.values()) > 1:
        raise ValueError("SPIN_FRENZY_BONUS_WEIGHT is too high for this wheel; lower it.")
    for b in BONUSES:
        lo, hi = -4.0, 12.0          # the average prize goes down as the tilt goes up
        for _ in range(60):
            mid = (lo + hi) / 2
            _apply_tilt(b, mid)
            if spot_rtp(b, chances) > bonus_rtp:
                lo = mid
            else:
                hi = mid
        _apply_tilt(b, (lo + hi) / 2)
    return chances


def spot_value(spot: str) -> float:
    """Average winnings multiple (N, or the average bonus value) of a spot, before the Boost Reel."""
    if spot in NUMBERS:
        return NUMBERS[spot]
    return {"coin": coin_expected, "grid": grid_expected, "drop": drop_expected, "frenzy": frenzy_expected}[spot]()


def boost_chances(rtp: float) -> dict[str, float]:
    """Chance per round that the Boost Reel lands on each spot, solved so each spot returns `rtp`.

    Return of a spot = P(wheel lands on it) * (1 + V * (1 + q * (E[mult] - 1))), solved for q.
    A spot whose return without any boost is already above `rtp` gets q = 0.
    """
    m1 = _mean(BOOST_MULTS) - 1
    out = {}
    for spot in SPOTS:
        p = land_chance(spot)
        v = spot_value(spot)
        q = ((rtp / p - 1) / v - 1) / m1
        out[spot] = min(max(q, 0.0), 1.0)
    total = sum(out.values())
    if total > 1:
        raise ValueError(f"SPIN_FRENZY_RTP={rtp} is too high for this wheel (the Boost Reel would need to hit "
                         f"{total:.0%} of the time). Lower it.")
    return out


def spot_rtp(spot: str, chances: dict[str, float]) -> float:
    p = land_chance(spot)
    return p * (1 + spot_value(spot) * (1 + chances[spot] * (_mean(BOOST_MULTS) - 1)))


# ------------------------------------------------------------------ round results
@dataclass
class Boost:
    spot: str | None      # None = the reel missed
    mult: int             # 1 when missed

    def to_dict(self) -> dict:
        return {"spot": self.spot, "mult": self.mult}


def roll_boost(chances: dict[str, float]) -> Boost:
    r = rng.random()
    for spot in SPOTS:
        if r < chances[spot]:
            return Boost(spot, _draw(BOOST_MULTS))
        r -= chances[spot]
    return Boost(None, 1)


def spin_wheel() -> int:
    """Index of the segment the main wheel stops on (bonus segments weighted by BONUS_WEIGHT)."""
    return rng.choices(range(SEGMENTS), weights=[_segment_weight(s) for s in WHEEL])[0]


# ------------------------------------------------------------------ bonus rounds
@dataclass
class CoinToss:
    sides: dict[str, int] = field(default_factory=dict)   # {"gold": x, "silver": y}
    winner: str = ""

    @classmethod
    def play(cls) -> "CoinToss":
        low, high = _draw(COIN_LOW), _draw(COIN_HIGH)
        if rng.random() < 0.5:
            sides = {"gold": high, "silver": low}
        else:
            sides = {"gold": low, "silver": high}
        return cls(sides, rng.choice(["gold", "silver"]))

    def value(self) -> int:
        return self.sides[self.winner]


@dataclass
class TreasureGrid:
    cells: list[int]   # hidden value per cell (only sent to clients at the reveal)

    @classmethod
    def deal(cls) -> "TreasureGrid":
        cells = [_draw(GRID_VALUES) for _ in range(GRID_CELLS)]
        for i in rng.sample(range(GRID_CELLS), _draw(GRID_HIGH_COUNT)):
            cells[i] = _draw(GRID_HIGH)
        return cls(cells)

    def shown_values(self) -> list[int]:
        """The values shown face-up before they are hidden and shuffled (in a different order)."""
        vals = list(self.cells)
        rng.shuffle(vals)
        return vals


@dataclass
class PegDrop:
    slots: list                                  # int value, or "double"
    drops: list[dict] = field(default_factory=list)  # [{"path": [0/1...], "slot": i, "factor": f}]
    factor: int = 1
    final: int = 0

    @classmethod
    def play(cls) -> "PegDrop":
        # inner slots: ordinary values and the doubles, shuffled
        inner: list = [_draw(DROP_VALUES) for _ in range(DROP_SLOTS - 2 - DROP_DOUBLES)] + ["double"] * DROP_DOUBLES
        rng.shuffle(inner)
        # edges: 1 or 2 big prizes; an edge without one holds an ordinary value
        edges = [_draw(DROP_HIGH) for _ in range(_draw(DROP_HIGH_COUNT))]
        edges += [_draw(DROP_VALUES) for _ in range(2 - len(edges))]
        rng.shuffle(edges)
        slots: list = [edges[0]] + inner + [edges[1]]
        game = cls(slots)
        landing = drop_landing()
        doubles = 0
        while True:
            if doubles < DROP_MAX_DOUBLES:
                slot = rng.choices(range(DROP_SLOTS), weights=landing)[0]
            else:  # double slots are closed now
                slot = rng.choices(range(DROP_SLOTS),
                                   weights=[0 if s == "double" else w for s, w in zip(slots, landing)])[0]
            path = [1] * slot + [0] * (DROP_ROWS - slot)   # number of right-bounces = slot index
            rng.shuffle(path)
            hit_double = slots[slot] == "double"
            game.drops.append({"path": path, "slot": slot, "factor_before": game.factor, "double": hit_double,
                               "closed": doubles >= DROP_MAX_DOUBLES})
            if hit_double:
                doubles += 1
                game.factor *= 2
                continue
            game.final = slots[slot] * game.factor
            return game


@dataclass
class FrenzyWheel:
    segments: list                               # int value, "double" or "triple"
    spins: list[dict] = field(default_factory=list)  # [{"stop": i, "landed": [seg per pointer or None]}]
    factors: list[int] = field(default_factory=lambda: [1] * FRENZY_POINTERS)
    results: list[int] = field(default_factory=lambda: [0] * FRENZY_POINTERS)

    @classmethod
    def deal(cls) -> "FrenzyWheel":
        n_values = FRENZY_SEGMENTS - FRENZY_DOUBLES - FRENZY_TRIPLES
        segs: list = [_draw(FRENZY_VALUES) for _ in range(n_values)]
        segs += ["double"] * FRENZY_DOUBLES + ["triple"] * FRENZY_TRIPLES
        rng.shuffle(segs)
        return cls(segs)

    def pointer_segment(self, stop: int, pointer: int) -> int:
        return (stop + pointer * FRENZY_SEGMENTS // FRENZY_POINTERS) % FRENZY_SEGMENTS

    def play(self) -> None:
        done = [False] * FRENZY_POINTERS
        boosts = [0] * FRENZY_POINTERS
        for n in range(FRENZY_MAX_SPINS):
            stop = rng.randrange(FRENZY_SEGMENTS)
            landed = []
            for p in range(FRENZY_POINTERS):
                if done[p]:
                    landed.append(None)
                    continue
                seg = self.pointer_segment(stop, p)
                s = self.segments[seg]
                if n == FRENZY_MAX_SPINS - 1 and not isinstance(s, int):
                    # last allowed spin: settle on the nearest value segment
                    seg = next((seg + k) % FRENZY_SEGMENTS for k in range(FRENZY_SEGMENTS)
                               if isinstance(self.segments[(seg + k) % FRENZY_SEGMENTS], int))
                    s = self.segments[seg]
                landed.append(seg)
                if isinstance(s, int):
                    done[p] = True
                    self.results[p] = s * self.factors[p]
                elif boosts[p] < FRENZY_MAX_BOOSTS:
                    boosts[p] += 1
                    self.factors[p] *= 2 if s == "double" else 3
                # else: maxed out on boosts, this landing is ignored and the wheel spins again
            self.spins.append({"stop": stop, "landed": landed, "factors": list(self.factors)})
            if all(done):
                return


def payout(stake: int, multiple: float, boost: int, max_win_mult: int) -> int:
    """Total returned for a winning bet: stake back plus stake x multiple x boost (winnings capped)."""
    winnings = stake * multiple * boost
    if max_win_mult:
        winnings = min(winnings, stake * max_win_mult)
    return stake + math.floor(winnings)


# ------------------------------------------------------------------ self-check
def _simulate(rounds: int, chances: dict[str, float]) -> dict[str, float]:
    """Average return per spot for a 1-credit bet on every spot each round."""
    returned = {s: 0.0 for s in SPOTS}
    for _ in range(rounds):
        boost = roll_boost(chances)
        spot = WHEEL[spin_wheel()]
        b = boost.mult if boost.spot == spot else 1
        if spot in NUMBERS:
            v = NUMBERS[spot]
        elif spot == "coin":
            v = CoinToss.play().value()
        elif spot == "grid":
            v = rng.choice(TreasureGrid.deal().cells)
        elif spot == "drop":
            v = PegDrop.play().final
        else:
            fw = FrenzyWheel.deal()
            fw.play()
            v = fw.results[rng.randrange(FRENZY_POINTERS)]
        returned[spot] += 1 + v * b
    return {s: returned[s] / rounds for s in SPOTS}


if __name__ == "__main__":
    import sys

    target = float(sys.argv[1]) if len(sys.argv) > 1 else 0.97
    weight = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 400_000
    bonus_target = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
    q = configure(target, weight, bonus_target)
    print(f"Wheel: {SEGMENTS} segments: " + ", ".join(f"{s}x{c}" for s, c in SEGMENT_COUNTS.items()))
    print(f"Bonus weight {BONUS_WEIGHT}: a bonus wins {sum(land_chance(b) for b in BONUSES):.1%} of spins "
          f"(fair share {sum(SEGMENT_COUNTS[b] for b in BONUSES) / SEGMENTS:.1%})")
    print(f"Boost Reel average multiplier {_mean(BOOST_MULTS):.3f}, hits {sum(q.values()):.1%} of rounds")
    print("Prize tables (value: % chance):")
    for name in ("COIN_LOW", "COIN_HIGH", "GRID_VALUES", "DROP_VALUES", "FRENZY_VALUES"):
        print(f"  {name:<14}" + "  ".join(f"{v}x {w:.2f}%" for v, w in sorted(globals()[name].items())))
    print(f"{'spot':<8}{'chance':>8}{'avg value':>10}{'boost %':>9}{'exact RTP':>11}{'simulated':>11}")
    sim = _simulate(n, q)
    for s in SPOTS:
        print(f"{s:<8}{land_chance(s):>8.2%}{spot_value(s):>10.2f}{q[s]:>9.2%}{spot_rtp(s, q):>11.2%}"
              f"{sim[s]:>11.2%}")
