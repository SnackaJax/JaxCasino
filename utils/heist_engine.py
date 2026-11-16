"""Raccoon Heist: four masked raccoons raid a bank vault. Pure game logic, no Discord or web code, so the Activity
(activity/heist.py) and the simulation below play exactly the same game.

    python -m utils.heist_engine [rounds]       # measures every mode's payback (uses every core)

The vault
- The vault is an 8x8 grid. A paid spin starts with only the middle 6x6 open; the outer ring is brick wall. Its four
  sides (top, bottom, left, right) are knocked through one at a time by Drills. A corner opens once both of its
  sides are open. Open cells in a column always form one unbroken run, so gravity works column by column.
- Every open cell holds loot (Rubies, Amethysts, Emeralds, Sapphires: one colour per raccoon), trash, a Golden Mask
  (wild) or a tool. The four raccoons (Rocco = red, Violet = purple, Moss = green, Skye = blue) land on random cells.

A drop
- The raccoons go on raids in turn (red, purple, green, blue). A raccoon raids when at least MIN_RAID pieces of its
  own colour's loot (Golden Masks count) are linked to it up, down, left or right; tools along the way link too.
  It then grabs everything linked: its own loot and masks pay its colour's current level (LEVEL_PAYS, times the
  bet), tools do their thing, and the raccoon ends up where its last grab was.
- When all four have had their turn, raccoons standing next to each other may get into a Scuffle (2 raccoons:
  SCUFFLE_CHANCE, 3 or 4: always; at most SCUFFLE_MAX per drop): everything around them is knocked loose and
  collected (loot pays its own colour's level).
- Then everything falls down, new symbols drop in from the top, and the raccoons go again, until nobody can raid.
- Every piece of loot a raccoon grabs fills the Loot Meter. Each time it reaches METER_SIZE: Team Up, every colour
  goes up one level.

Tools
  stetho   Stethoscope         the raccoon's colour goes up a level (max 7)
  gstetho  Golden Stethoscope  every colour goes up a level
  drill    Drill               knocks one wall side of the vault through (the vault grows)
  paint    Paint Bomb          loot and trash in the 8 cells around it turn the raccoon's colour
  cash     Cash Bag            pays its value (CASH_VALUES, times the bet)
  hook     Grappling Hook      when the raid runs out, the raccoon swings to the nearest loot of its colour
                               anywhere in the vault and keeps going
  radio    Walkie-Talkie       calls the Boss: when nobody can raid any more, he crashes in and dashes BOSS_DASHES
                               times across a row or column, grabbing everything in it
  safe     Safe                after this drop: Vault Grab (20 lockers, the raccoons open them until the alarms
                               catch all four)
  bp       Blueprint           3 grabbed in one paid spin start the Night Shift: NIGHT_DROPS free drops where the
                               vault, the levels and the Loot Meter carry over; each Blueprint during it is +1 drop
Tools knocked loose by a Scuffle or the Boss still work, except Paint Bombs and Hooks, which need a raccoon.

Modes (what a round is bought as): "spin" (a paid spin at 1x the bet) and the bonus buys in BUYS.
All amounts are in "x": multiples of the bet, before the RTP scale. Every mode's pays are scaled so it returns the
bot's HEIST_RTP (see scale()).
"""
from __future__ import annotations

import math
import sys

VW = VH = 8                     # the whole vault
N = VW * VH
COLORS = ("red", "purple", "green", "blue")
LOOT = {"red": "ruby", "purple": "amethyst", "green": "emerald", "blue": "sapphire"}
LOOT_COLOR = {v: k for k, v in LOOT.items()}
RACCOON = {c: f"coon_{c}" for c in COLORS}
RACCOON_COLOR = {v: k for k, v in RACCOON.items()}
RACCOON_NAMES = {"red": "Rocco", "purple": "Violet", "green": "Moss", "blue": "Skye"}
WILD = "mask"
JUNK = "junk"
TOOLS = ("stetho", "gstetho", "drill", "paint", "cash", "hook", "radio", "safe", "bp")
SIDES = ("top", "bottom", "left", "right")

NAMES = {"ruby": "Ruby", "amethyst": "Amethyst", "emerald": "Emerald", "sapphire": "Sapphire", WILD: "Golden Mask",
         JUNK: "Trash", "stetho": "Stethoscope", "gstetho": "Golden Stethoscope", "drill": "Drill",
         "paint": "Paint Bomb", "cash": "Cash Bag", "hook": "Grappling Hook", "radio": "Walkie-Talkie",
         "safe": "Safe", "bp": "Blueprint"}

MIN_RAID = 3
LEVELS = 7
# what one piece of loot pays at each level (times the bet)
LEVEL_PAYS = {
    "red":    (0.10, 0.25, 0.50, 1.00, 2.50, 6.00, 20.0),
    "purple": (0.08, 0.20, 0.40, 0.80, 2.00, 5.00, 15.0),
    "green":  (0.05, 0.12, 0.25, 0.50, 1.25, 3.00, 10.0),
    "blue":   (0.04, 0.10, 0.20, 0.40, 1.00, 2.50, 7.50),
}

# symbol weights for new cells. "boost" is the Blueprint Boost buy (Blueprints 2.5x as likely)
WEIGHTS = {
    "ruby": 14, "amethyst": 14, "emerald": 14, "sapphire": 14, JUNK: 60, WILD: 0.9,
    "stetho": 0.8, "gstetho": 0.2, "drill": 0.5, "paint": 0.5, "cash": 0.6, "hook": 0.4,
    "radio": 0.2, "safe": 0.14, "bp": 2.6,
}
BONUS_WEIGHTS = dict(WEIGHTS, junk=22, stetho=3.0, gstetho=0.8, drill=0.9, cash=1.0, bp=0.6)
BOOST_BP = 2.5

# Cash Bag values (times the bet) and their weights
CASH_VALUES = {0.5: 30, 1: 26, 2: 18, 3: 10, 5: 8, 10: 4.5, 25: 2, 50: 0.8, 100: 0.3, 500: 0.05, 1000: 0.015}

SCUFFLE_CHANCE = 0.3
SCUFFLE_MAX = 2
METER_SIZE = 15
BOSS_DASHES = 4
HOOK_REACH = 99                 # any distance

# Vault Grab: lockers, how many hold an alarm, and what the others hold (times the bet)
LOCKERS = 20
ALARMS = 5
LOCKER_VALUES = {1: 30, 2: 26, 3: 16, 5: 12, 10: 8, 20: 4, 50: 1.6, 100: 0.6, 250: 0.15, 1000: 0.03}

NIGHT_DROPS = 7                 # free drops for 3 Blueprints (each one more is +1)
BIG_SCORE_LEVEL = 3             # Big Score (the super bonus): every colour starts here, the vault starts open
MAX_DROPS = 60                  # free drops in one bonus, at most

MAX_WIN = 10_000

# The bonus buys (their prices, in bets, are in PRICES below)
BUYS = {
    "boost": {"name": "Blueprint Boost", "text": "One spin with Blueprints 2.5x as likely"},
    "boss": {"name": "Call the Boss", "text": "One spin where the Boss is on his way from the start"},
    "grab": {"name": "Vault Grab", "text": "Straight into Vault Grab"},
    "night": {"name": "Night Shift", "text": f"Straight into the Night Shift: {NIGHT_DROPS} free drops"},
    "big": {"name": "Big Score", "text": f"{NIGHT_DROPS} free drops in a fully open vault, every colour starting "
                                         f"at level {BIG_SCORE_LEVEL}"},
}

# Raw averages (x per round, before scaling), measured with `python -m utils.heist_engine`. Re-measure after changing
# anything above: every pay is scaled by HEIST_RTP / MEASURED[mode] * price, so each mode returns HEIST_RTP.
# Each is every independent run pooled: spin = 16 million rounds counted with measured Night Shifts of 7-9 drops plus
# 16 million whole rounds (± 0.4% at 95%); boost = 24 million whole rounds only (± 0.4%; counting it the first way
# undervalues its many 6+ Blueprint Night Shifts by about 1.8%); boss 5.4 million rounds (± 0.5%); grab and big
# 800,000 (± 0.3%); night 2 million (± 0.25%). `python -m utils.heist_engine verify` checks them against fresh rounds.
MEASURED = {"spin": 0.9958, "boost": 3.6895, "boss": 8.910, "grab": 56.68, "night": 81.23, "big": 249.52}
# Prices of the bonus buys in bets: round numbers close to break-even at the paid spin's scale, so a buy's pays are
# within about 1% of a paid spin's (the per-mode scale makes every price return HEIST_RTP exactly)
PRICES = {"boost": 3.7, "boss": 9, "grab": 57, "night": 82, "big": 250}

MAX_STEPS = 500                 # safety net for loops (never reached in practice)

# ------------------------------------------------------------------ geometry
NEIGH4: list[tuple[int, ...]] = []
NEIGH8: list[tuple[int, ...]] = []
for _c in range(VW):
    for _r in range(VH):
        NEIGH4.append(tuple(c * VH + r for c, r in ((_c - 1, _r), (_c + 1, _r), (_c, _r - 1), (_c, _r + 1))
                            if 0 <= c < VW and 0 <= r < VH))
        NEIGH8.append(tuple(c * VH + r for c in (_c - 1, _c, _c + 1) for r in (_r - 1, _r, _r + 1)
                            if (c, r) != (_c, _r) and 0 <= c < VW and 0 <= r < VH))


def cell(i: int) -> list[int]:
    return [i // VH, i % VH]


def open_mask(walls: dict) -> list[bool]:
    """Which cells are open, given which wall sides have been knocked through."""
    out = []
    for c in range(VW):
        col_ok = 1 <= c <= VW - 2 or (c == 0 and walls["left"]) or (c == VW - 1 and walls["right"])
        for r in range(VH):
            row_ok = 1 <= r <= VH - 2 or (r == 0 and walls["top"]) or (r == VH - 1 and walls["bottom"])
            out.append(col_ok and row_ok)
    return out


# ------------------------------------------------------------------ drawing symbols
def _picker(weights: dict):
    keys, cum, total = list(weights), [], 0.0
    for k in keys:
        total += weights[k]
        cum.append(total)
    return keys, cum, total


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


_PICKERS: dict = {}


def _symbol_picker(bonus: bool, boost: bool, drills: bool):
    k = (bonus, boost, drills)
    p = _PICKERS.get(k)
    if p is None:
        w = dict(BONUS_WEIGHTS if bonus else WEIGHTS)
        if boost:
            w["bp"] *= BOOST_BP
        if not drills:
            w["drill"] = 0.0
        p = _PICKERS[k] = _picker(w)
    return p


_CASH = _picker(CASH_VALUES)
_LOCKER = _picker(LOCKER_VALUES)


def is_cash(s) -> bool:
    return isinstance(s, str) and s.startswith("cash:")


def cash_value(s: str) -> float:
    return float(s[5:])


# ------------------------------------------------------------------ one round
class Round:
    """One bought round (a paid spin or a bonus buy), played out completely. `steps` is what the page animates.

    Cells are [column, row] in the 8x8 vault, grids are grid[column][row] (None = closed or empty for a moment).
    Steps:
      {"t": "drop", "grid", "open", "levels", "meter", "free": None | {"left", "done"}}
            a new board drops in (raccoons included)
      {"t": "raid", "who", "from": cell, "grabs": [grab], "x", "meter", "levels"}
            a raccoon raids. grab = {"cell", "sym", "x"?, "hook": True?, "levels"?, "wall"?, "open"?,
            "paint": [cells]?, "boss"?, "bp"?, "safe"?}; it ends on the last grab's cell
      {"t": "scuffle", "who": [colors], "grabs": [grab], "x", "levels"}
      {"t": "teamup", "levels", "meter"}
      {"t": "fall", "moves", "grid"}          moves[c][r]: the row the symbol now at (c, r) came from (a row above
                                              the open part of the column = new, dropped in from above; None = closed)
      {"t": "boss_in", "dashes"} / {"t": "dash", "line": "row"|"col", "index", "land": cell, "grabs", "x"}
      / {"t": "boss_out"}
      {"t": "grab", "lockers": [x or "alarm"], "order": [{"who", "locker"}], "x"}   Vault Grab
      {"t": "bonus", "kind": "night"|"big", "drops"} / {"t": "extra", "left"} / {"t": "bonus_end", "x"}
    Every step that pays has "x" (raw, this step) and the round keeps the running total in raw_x.
    """

    def __init__(self, rng, mode: str = "spin", play_bonus: bool = True):
        self.rng = rng
        self.mode = mode
        self.steps: list[dict] = []
        self.raw_x = 0.0
        self.boost = mode == "boost"
        self.in_bonus = False
        self.bonus_kind: str | None = None
        self.bonus_drops = 0          # free drops played
        self.bonus_x = 0.0
        self.bp_paid = 0              # Blueprints grabbed in the paid drop (not the bonus)
        self.bosses = 0
        self.grabs = 0
        self.teamups = 0
        self.scuffles = 0
        self.walls_opened = 0
        self.free_left = 0
        self._reset_state()
        if mode == "grab":
            self._vault_grab()
        elif mode in ("night", "big"):
            self._bonus(mode, NIGHT_DROPS)
        elif mode.startswith("night"):          # the simulation only: a Night Shift with this many free drops
            self._bonus("night", int(mode[5:]))
        else:
            self._paid_drop(boss=mode == "boss", play_bonus=play_bonus)

    # ---- state
    def _reset_state(self, big: bool = False) -> None:
        self.levels = {c: (BIG_SCORE_LEVEL if big else 1) for c in COLORS}
        self.walls = {s: big for s in SIDES}
        self.open = open_mask(self.walls)
        self.meter = 0
        self.g: list = [None] * N
        self.boss_pending = 0
        self.safe = False

    @property
    def x(self) -> float:
        return min(self.raw_x, MAX_WIN)

    def _draw(self):
        sym = _pick(self.rng, _symbol_picker(self.in_bonus, self.boost, not all(self.walls.values())))
        if sym == "cash":
            v = _pick(self.rng, _CASH)
            return f"cash:{v:g}"
        return sym

    def _nested(self) -> list:
        g, o = self.g, self.open
        return [[g[c * VH + r] if o[c * VH + r] else None for r in range(VH)] for c in range(VW)]

    def _open_list(self) -> list:
        return [[bool(self.open[c * VH + r]) for r in range(VH)] for c in range(VW)]

    def _pay(self, x: float) -> None:
        self.raw_x += x
        if self.in_bonus:
            self.bonus_x += x

    # ---- a whole drop
    def _new_board(self) -> None:
        g = self.g = [None] * N
        cells = [i for i in range(N) if self.open[i]]
        for i in cells:
            g[i] = self._draw()
        for c, i in zip(COLORS, self.rng.sample(cells, len(COLORS))):
            g[i] = RACCOON[c]
        free = {"left": self.free_left, "done": self.bonus_drops} if self.in_bonus else None
        self.steps.append({"t": "drop", "grid": self._nested(), "open": self._open_list(),
                           "levels": dict(self.levels), "meter": self.meter, "free": free})

    def _paid_drop(self, boss: bool, play_bonus: bool) -> None:
        self._new_board()
        if boss:
            self.boss_pending = BOSS_DASHES
        bp = self._play_drop()
        self.bp_paid = bp
        if self.safe:
            self._vault_grab()
        if bp >= 3 and play_bonus:
            self._bonus("night", NIGHT_DROPS + (bp - 3))

    def _play_drop(self) -> int:
        """Raids, scuffles, the Boss and falls until nothing more happens. Returns the Blueprints grabbed."""
        self.bp = 0
        self.safe = False
        scuffles = 0
        for _ in range(MAX_STEPS):
            moved = False
            for c in COLORS:
                if self._raid(c):
                    moved = True
            if scuffles < SCUFFLE_MAX and self._scuffle():
                scuffles += 1
                moved = True
            if moved:
                self._check_meter()
                self._fall()
                continue
            if self.boss_pending:
                self._boss()
                continue
            break
        return self.bp

    # ---- raids
    def _pos(self, color: str) -> int:
        return self.g.index(RACCOON[color])

    def _linkable(self, s, loot: str) -> bool:
        return s == loot or s == WILD or (s is not None and (s in TOOLS or is_cash(s)))

    def _raid(self, color: str) -> bool:
        g = self.g
        loot = LOOT[color]
        start = self._pos(color)
        # enough loot linked to the raccoon?
        seen = {start}
        stack = [start]
        count = 0
        while stack and count < MIN_RAID:
            i = stack.pop()
            for k in NEIGH4[i]:
                if k not in seen and self._linkable(g[k], loot):
                    seen.add(k)
                    stack.append(k)
                    if g[k] == loot or g[k] == WILD:
                        count += 1
        if count < MIN_RAID:
            return False
        grabs: list[dict] = []
        step_x = 0.0
        hooks = 0
        visited = {start}
        stack = [k for k in reversed(NEIGH4[start]) if self._linkable(g[k], loot)]
        last = start
        for _ in range(N * 4):
            while stack:
                i = stack.pop()
                if i in visited or not self._linkable(g[i], loot):
                    continue
                visited.add(i)
                grab = self._take(i, color)
                step_x += grab.get("x", 0.0)
                if grab["sym"] == "hook":
                    hooks += 1
                grabs.append(grab)
                last = i
                for k in reversed(NEIGH4[i]):
                    if k not in visited and self._linkable(g[k], loot):
                        stack.append(k)
            if not hooks:
                break
            # swing to the nearest loot of this colour (or a mask) anywhere in the vault
            lc, lr = divmod(last, VH)
            targets = [i for i in range(N) if (g[i] == loot or g[i] == WILD) and i not in visited]
            if not targets:
                break
            hooks -= 1
            best = min(abs(i // VH - lc) + abs(i % VH - lr) for i in targets)
            i = self.rng.choice([i for i in targets if abs(i // VH - lc) + abs(i % VH - lr) == best])
            stack = [i]
            # the first grab after the swing is marked so the page can show the swing
            visited_before = len(grabs)
            while stack:
                j = stack.pop()
                if j in visited or not self._linkable(g[j], loot):
                    continue
                visited.add(j)
                grab = self._take(j, color)
                if len(grabs) == visited_before:
                    grab["hook"] = True
                step_x += grab.get("x", 0.0)
                if grab["sym"] == "hook":
                    hooks += 1
                grabs.append(grab)
                last = j
                for k in reversed(NEIGH4[j]):
                    if k not in visited and self._linkable(g[k], loot):
                        stack.append(k)
        # the raccoon moves to where its last grab was
        g[start] = None
        g[last] = RACCOON[color]
        self._pay(step_x)
        self.steps.append({"t": "raid", "who": color, "from": cell(start), "grabs": grabs, "x": step_x,
                           "meter": self.meter, "levels": dict(self.levels)})
        return True

    def _best_pay(self) -> float:
        return max(LEVEL_PAYS[c][self.levels[c] - 1] for c in COLORS)

    def _take(self, i: int, color: str | None, boss: bool = False) -> dict:
        """Collect the symbol at i: by a raccoon of `color`, knocked loose in a scuffle (color None) or grabbed by
        the Boss (boss=True: every piece of loot pays the best level of any colour)."""
        g = self.g
        s = g[i]
        g[i] = None
        grab: dict = {"cell": cell(i), "sym": s}
        if s in LOOT_COLOR:
            if boss:
                grab["x"] = self._best_pay()
            else:
                lc = color if color and s == LOOT[color] else LOOT_COLOR[s]
                grab["x"] = LEVEL_PAYS[lc][self.levels[lc] - 1]
            if color:
                self.meter += 1
        elif s == WILD:
            if color:
                grab["x"] = LEVEL_PAYS[color][self.levels[color] - 1]
                self.meter += 1
            else:
                grab["x"] = self._best_pay()
        elif is_cash(s):
            grab["x"] = cash_value(s)
        elif s == "stetho":
            up = color or self.rng.choice(COLORS)
            self.levels[up] = min(LEVELS, self.levels[up] + 1)
            grab["levels"] = dict(self.levels)
        elif s == "gstetho":
            for c in COLORS:
                self.levels[c] = min(LEVELS, self.levels[c] + 1)
            grab["levels"] = dict(self.levels)
        elif s == "drill":
            closed = [k for k in SIDES if not self.walls[k]]
            if closed:
                side = self.rng.choice(closed)
                self.walls[side] = True
                self.open = open_mask(self.walls)
                self.walls_opened += 1
                grab["wall"] = side
                grab["open"] = self._open_list()
        elif s == "paint" and color:
            painted = []
            for k in NEIGH8[i]:
                if self.open[k] and (g[k] in LOOT_COLOR or g[k] == JUNK) and g[k] != LOOT[color]:
                    g[k] = LOOT[color]
                    painted.append(cell(k))
            grab["paint"] = painted
        elif s == "radio":
            self.boss_pending += BOSS_DASHES
            grab["boss"] = True
        elif s == "safe":
            self.safe = True
            grab["safe"] = True
        elif s == "bp":
            self.bp += 1
            grab["bp"] = self.bp
            if self.in_bonus:
                self.free_left += 1
                grab["left"] = self.free_left
        return grab

    # ---- scuffles
    def _scuffle(self) -> bool:
        g = self.g
        pos = {c: self._pos(c) for c in COLORS}
        at = {i: c for c, i in pos.items()}
        groups, done = [], set()
        for c in COLORS:
            if c in done:
                continue
            group, stack = [c], [pos[c]]
            done.add(c)
            while stack:
                i = stack.pop()
                for k in NEIGH4[i]:
                    if k in at and at[k] not in done:
                        done.add(at[k])
                        group.append(at[k])
                        stack.append(k)
            if len(group) >= 2:
                groups.append(group)
        fought = [grp for grp in groups if len(grp) >= 3 or self.rng.random() < SCUFFLE_CHANCE]
        if not fought:
            return False
        for grp in fought:
            cells = sorted({k for c in grp for k in NEIGH8[pos[c]] if self.open[k] and g[k] is not None
                            and g[k] not in RACCOON_COLOR})
            grabs, step_x = [], 0.0
            for i in cells:
                grab = self._take(i, None)
                step_x += grab.get("x", 0.0)
                grabs.append(grab)
            self._pay(step_x)
            self.scuffles += 1
            self.steps.append({"t": "scuffle", "who": grp, "grabs": grabs, "x": step_x, "levels": dict(self.levels)})
        return True

    # ---- the Loot Meter
    def _check_meter(self) -> None:
        while self.meter >= METER_SIZE:
            self.meter -= METER_SIZE
            for c in COLORS:
                self.levels[c] = min(LEVELS, self.levels[c] + 1)
            self.teamups += 1
            self.steps.append({"t": "teamup", "levels": dict(self.levels), "meter": self.meter})

    # ---- gravity
    def _fall(self) -> None:
        g, o = self.g, self.open
        moves = []
        for c in range(VW):
            base = c * VH
            rows = [r for r in range(VH) if o[base + r]]
            col_moves: list = [None] * VH
            if rows:
                top, bottom = rows[0], rows[-1]
                survivors = [(r, g[base + r]) for r in range(top, bottom + 1) if g[base + r] is not None]
                new = (bottom - top + 1) - len(survivors)
                for k, r in enumerate(range(top, bottom + 1)):
                    if k < new:
                        g[base + r] = self._draw()
                        col_moves[r] = top + k - new       # starts above the open part of the column
                    else:
                        src, sym = survivors[k - new]
                        g[base + r] = sym
                        col_moves[r] = src
            moves.append(col_moves)
        self.steps.append({"t": "fall", "moves": moves, "grid": self._nested()})

    # ---- the Boss
    def _boss(self) -> None:
        dashes, self.boss_pending = self.boss_pending, 0
        self.bosses += 1
        self.steps.append({"t": "boss_in", "dashes": dashes})
        g = self.g
        for d in range(dashes):
            spots = [i for i in range(N) if self.open[i] and g[i] is not None and g[i] not in RACCOON_COLOR]
            if not spots:
                break
            land = self.rng.choice(spots)
            lc, lr = divmod(land, VH)
            line = "row" if d % 2 == 0 else "col"
            cells = [i for i in range(N) if self.open[i] and (i % VH == lr if line == "row" else i // VH == lc)
                     and g[i] is not None and g[i] not in RACCOON_COLOR]
            # he runs out from where he lands, so grabs go outwards
            cells.sort(key=lambda i: abs(i // VH - lc) + abs(i % VH - lr))
            grabs, step_x = [], 0.0
            for i in cells:
                grab = self._take(i, None, boss=True)
                step_x += grab.get("x", 0.0)
                grabs.append(grab)
            self._pay(step_x)
            g[land] = WILD          # he drops a Golden Mask where he landed
            self.steps.append({"t": "dash", "line": line, "index": lr if line == "row" else lc, "land": cell(land),
                               "grabs": grabs, "x": step_x, "levels": dict(self.levels)})
            self._fall()
        self.steps.append({"t": "boss_out"})

    # ---- Vault Grab
    def _vault_grab(self) -> None:
        rng = self.rng
        lockers: list = ["alarm"] * ALARMS + [_pick(rng, _LOCKER) for _ in range(LOCKERS - ALARMS)]
        rng.shuffle(lockers)
        order = list(range(LOCKERS))
        rng.shuffle(order)
        free = list(COLORS)
        turn = 0
        opened = []
        x = 0.0
        for i in order:
            if not free:
                break
            who = free[turn % len(free)]
            opened.append({"who": who, "locker": i})
            if lockers[i] == "alarm":
                free.remove(who)
                turn = turn % len(free) if free else 0
            else:
                x += lockers[i]
                turn += 1
        self._pay(x)
        self.grabs += 1
        self.steps.append({"t": "grab", "lockers": [v if v == "alarm" else float(v) for v in lockers],
                           "order": opened, "x": x})

    # ---- the Night Shift (and Big Score)
    def _bonus(self, kind: str, drops: int) -> None:
        self.in_bonus = True
        self.boost = False          # the Blueprint Boost is for the paid spin only, never its Night Shift
        self.bonus_kind = kind
        self._reset_state(big=kind == "big")
        self.free_left = drops
        self.steps.append({"t": "bonus", "kind": kind, "drops": drops})
        while self.free_left > 0 and self.bonus_drops < MAX_DROPS:
            self.free_left -= 1
            self.bonus_drops += 1
            self._new_board()
            self._play_drop()
            if self.safe:
                self._vault_grab()
        self.steps.append({"t": "bonus_end", "x": self.bonus_x, "drops": self.bonus_drops})
        self.in_bonus = False

    def to_dict(self) -> dict:
        return {"steps": self.steps, "x": self.x, "mode": self.mode, "bonus": self.bonus_kind,
                "bosses": self.bosses, "grabs": self.grabs}


# ------------------------------------------------------------------ credits
MODES = ("spin", *BUYS)


def price(mode: str) -> float:
    """What a round in this mode costs, in bets."""
    return 1 if mode == "spin" else PRICES[mode]


def stake_for(bet: int, mode: str) -> int:
    """What a round in this mode costs, in credits."""
    return max(1, round(bet * price(mode)))


def scale(mode: str, rtp: float, bet: int | None = None, stake: int | None = None) -> float:
    """Every raw pay in a round of this mode is multiplied by this, so the mode returns `rtp` of what it cost
    (the real stake, when given, so rounding the price to whole credits costs nobody anything)."""
    cost = stake / bet if bet and stake else price(mode)
    return rtp * cost / MEASURED[mode]


def credits(bet: int, x: float, sc: float) -> int:
    return math.floor(bet * x * sc + 1e-9)


def payout(bet: int, stake: int, rnd: Round, rtp: float) -> int:
    return min(credits(bet, rnd.raw_x, scale(rnd.mode, rtp, bet, stake)), bet * MAX_WIN)


# ------------------------------------------------------------------ simulation
STAT_KEYS = ("n", "total", "sq", "hits", "above", "bonus", "bp3", "bp4", "bp5", "boss", "grab", "teamup", "scuffle",
             "walls", "ge100", "ge1000", "capped", "free")


def _sim_chunk(args) -> dict:
    import random
    seed, n, mode = args
    rng = random.Random(seed)
    s = {k: 0.0 for k in STAT_KEYS}
    s["biggest"] = 0.0
    s["n"] = n
    play_bonus = mode not in ("spin", "boost")
    cost = price(mode) if mode in MODES else price("night")
    for _ in range(n):
        rnd = Round(rng, mode, play_bonus=play_bonus)
        x = rnd.raw_x
        s["total"] += x
        s["sq"] += x * x
        s["hits"] += x > 0
        s["above"] += x > cost
        s["ge100"] += x >= 100
        s["ge1000"] += x >= 1000
        s["capped"] += x >= MAX_WIN
        s["biggest"] = max(s["biggest"], x)
        s["boss"] += rnd.bosses > 0
        s["grab"] += rnd.grabs > 0
        s["teamup"] += rnd.teamups
        s["scuffle"] += rnd.scuffles
        s["walls"] += rnd.walls_opened
        s["free"] += rnd.bonus_drops
        if rnd.bp_paid >= 3:
            s["bonus"] += 1
            s[f"bp{min(rnd.bp_paid, 5)}"] += 1
    return s


def _run(mode: str, n: int, workers: int) -> dict:
    import random
    from multiprocessing import Pool
    chunks = 4 * workers
    per = max(1, n // chunks)
    with Pool(workers) as pool:
        parts = pool.map(_sim_chunk, [(random.getrandbits(64), per, mode) for _ in range(chunks)])
    tot = {k: sum(p[k] for p in parts) for k in STAT_KEYS}
    tot["biggest"] = max(p["biggest"] for p in parts)
    return tot


def _summary(mode: str, t: dict) -> tuple[float, float]:
    n = t["n"]
    mean = t["total"] / n
    sd = math.sqrt(max(t["sq"] / n - mean * mean, 0))
    return mean, 1.96 * sd / math.sqrt(n)


def _simulate(n: int, modes: list[str]) -> None:
    import os
    import time
    workers = os.cpu_count() or 4
    res = {}
    for mode in modes:
        t0 = time.time()
        rn = n if mode in ("spin", "boost") else max(20_000, n // (20 if mode.startswith("night") else 40))
        t = _run(mode, rn, workers)
        mean, ci = _summary(mode, t)
        res[mode] = (t, mean, ci)
        nn = t["n"]
        print(f"[{mode}] {int(nn):,} rounds in {time.time() - t0:.0f}s: raw mean {mean:.4f} ± {ci:.4f} (95%)"
              f"{' (bonus not included)' if mode in ('spin', 'boost') else ''}")
        print(f"  wins on {t['hits'] / nn:.1%}, more than the price on {t['above'] / nn:.1%}, boss {t['boss'] / nn:.2%}, "
              f"vault grab {t['grab'] / nn:.2%}, team ups {t['teamup'] / nn:.3f}, scuffles {t['scuffle'] / nn:.3f}, "
              f"walls {t['walls'] / nn:.3f}, free drops {t['free'] / nn:.2f}")
        print(f"  100x+ every {nn / max(t['ge100'], 1):,.0f}, 1000x+ every {nn / max(t['ge1000'], 1):,.0f}, "
              f"biggest {t['biggest']:.0f}x, capped {int(t['capped'])}")
        if mode in ("spin", "boost"):
            print(f"  bonus every {nn / max(t['bonus'], 1):,.0f} rounds (3 bp {t['bp3'] / max(t['bonus'], 1):.1%}, "
                  f"4 bp {t['bp4'] / max(t['bonus'], 1):.1%}, 5+ bp {t['bp5'] / max(t['bonus'], 1):.1%})")
    # Paid spins are simulated without their bonus (far less noise): a triggered Night Shift is worth the measured
    # average of a Night Shift with that many free drops (3 Blueprints: 7 drops, 4: 8, 5 or more: 9; 6+ is rare
    # enough that counting it as 5 makes no measurable difference)
    lengths = {3: "night", 4: f"night{NIGHT_DROPS + 1}", 5: f"night{NIGHT_DROPS + 2}"}
    if all(m in res for m in lengths.values()):
        for mode in ("spin", "boost"):
            if mode not in res:
                continue
            t, mean, ci = res[mode]
            nn = t["n"]
            bonus_part = sum(t[f"bp{k}"] / nn * res[m][1] for k, m in lengths.items())
            res[mode] = (t, mean + bonus_part, ci)
            print(f"[{mode}] bonuses: {int(t['bp3'])} with 3 Blueprints, {int(t['bp4'])} with 4, {int(t['bp5'])} with 5+ "
                  f"in {int(nn):,} rounds")
            print(f"[{mode}] with the bonus: {mean:.4f} + {bonus_part:.4f} = {mean + bonus_part:.4f}")
    shown = [m for m in res if not (m.startswith("night") and m != "night")]
    print("\nMEASURED = {" + ", ".join(f'"{m}": {res[m][1]:.4f}' for m in shown) + "}")
    if "spin" in res:
        base = res["spin"][1]
        print("break-even prices at the base game's scale (bets): "
              + ", ".join(f"{m} {res[m][1] / base:.2f}" for m in shown if m != "spin"))


def _verify_chunk(args) -> tuple[int, int, int]:
    import random
    seed, n, mode, rtp = args
    rng = random.Random(seed)
    bet = 1000
    staked = paid = 0
    stake = stake_for(bet, mode)
    for _ in range(n):
        rnd = Round(rng, mode)
        staked += stake
        paid += payout(bet, stake, rnd, rtp)
    return n, staked, paid


def _verify(n: int, modes: list[str], rtp: float) -> None:
    """Plays whole rounds (bonuses included) through the bot's own payout code, with fresh random seeds, and prints
    what each mode really pays back."""
    import os
    import random
    import time
    from multiprocessing import Pool
    workers = os.cpu_count() or 4
    print(f"Checking the payback at HEIST_RTP {rtp:.2%} (bet 1,000 credits, bonuses included)")
    for mode in modes:
        t0 = time.time()
        rn = n if mode in ("spin", "boost") else max(20_000, n // 40)
        chunks = 4 * workers
        with Pool(workers) as pool:
            parts = pool.map(_verify_chunk, [(random.getrandbits(64), max(1, rn // chunks), mode, rtp)
                                             for _ in range(chunks)])
        rounds = sum(p[0] for p in parts)
        staked = sum(p[1] for p in parts)
        paid = sum(p[2] for p in parts)
        print(f"  {mode:6} {rounds:>11,} rounds in {time.time() - t0:4.0f}s: pays back {paid / staked:.2%}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "verify":
        _verify(int(args[1]) if len(args) > 1 else 4_000_000,
                args[2].split(",") if len(args) > 2 else list(MODES), float(args[3]) if len(args) > 3 else 0.99)
        sys.exit()
    count = int(args[0]) if args else 400_000
    which = args[1].split(",") if len(args) > 1 else ["spin", "boost", "boss", "grab", "night", "big"]
    if ("spin" in which or "boost" in which):
        which += [m for m in ("night", f"night{NIGHT_DROPS + 1}", f"night{NIGHT_DROPS + 2}") if m not in which]
    _simulate(count, which)
