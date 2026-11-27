"""Credit amounts of any size.

Python ints have no upper limit, but SQLite's INTEGER is a signed 64-bit number (about 9.22e18) and JavaScript numbers
are only exact up to 2^53 (about 9e15). Balances grew past both, so:

- In the database, a money column has no type (so SQLite never converts what's in it). An amount that fits in 64 bits
  is stored as an INTEGER, exactly as before; a bigger one is stored as TEXT holding its exact decimal digits. Python
  ints are turned into that form on the way in (`adapt`, registered for every connection) and back into ints on the
  way out (`money_row`, the row factory). Because the TEXT form is only used outside the 64-bit range and is always
  written the same way (no leading zeros), `=` and `!=` still work in SQL.
- SQL that adds, subtracts, compares, sorts or sums money uses the exact functions registered by `register`:
  big_add, big_sub, big_neg, big_abs, big_max, big_min, big_cmp and big_key (a sort key), and SUM itself is replaced
  by an exact version, so a sum never overflows ("integer overflow") or turns into an inexact REAL.
- JSON for the browser (the Activity and the admin panel) sends any whole number past 2^53 as a string of digits
  (`dumps`), so the pages can show it exactly.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from fractions import Fraction

INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1
JS_SAFE = 2 ** 53 - 1
_DIGITS = re.compile(r"-?\d+")

# Short forms, smallest first. k/M/B/T as before; then quadrillion (Qa), quintillion (Qi), sextillion (Sx),
# septillion (Sp), octillion (Oc), nonillion (No) and decillion (Dc). Beyond that, short() uses e-notation.
SUFFIXES = [("k", 3), ("M", 6), ("B", 9), ("T", 12), ("Qa", 15), ("Qi", 18), ("Sx", 21), ("Sp", 24),
            ("Oc", 27), ("No", 30), ("Dc", 33)]
_PARSE_SUFFIXES = {s.lower(): e for s, e in SUFFIXES}


# ------------------------------------------------------------------ conversions
def to_int(v) -> int | None:
    """A value read from SQLite (or JSON) as an exact int: ints as they are, digit strings parsed, a REAL rounded down
    to the whole number it holds. None stays None. Anything else raises ValueError."""
    if v is None or isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if _DIGITS.fullmatch(s):
            return int(s)
        return int(Decimal(s))
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"not a finite amount: {v!r}")
        return int(v)
    if isinstance(v, (bytes, bytearray)):
        return to_int(v.decode())
    raise ValueError(f"not an amount: {v!r}")


def to_db(n: int):
    """An int as SQLite stores it: an INTEGER when it fits in 64 bits, its exact digits (TEXT) when it doesn't."""
    return n if INT64_MIN <= n <= INT64_MAX else str(n)


def adapt(n: int):
    """sqlite3 adapter for int (so a huge int is stored as text instead of raising OverflowError)."""
    return n if INT64_MIN <= n <= INT64_MAX else str(n)


sqlite3.register_adapter(int, adapt)

# Python refuses to turn ints of more than 4,300 digits into text (or back) unless told otherwise. Amounts are
# written as text in the database and JSON, so lift that; typed-in amounts are capped at MAX_INPUT characters instead.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)
MAX_INPUT = 1000


# Column names (and the names given to sums of them) that hold credits. The row factory turns their text form back
# into ints; nothing else is touched (a TEXT column such as a setting's JSON is never converted).
MONEY_NAMES = frozenset("""
    balance amount wagered payout pot cost before after delta total_wagered total_won biggest_win
    net paid profit loss held circulating added removed credits sent received stake bet
    t a p w held_before held_after edits_net all_wagered all_paid
""".split())


class Row(tuple):
    """Like sqlite3.Row (index by position or by column name, keys(), dict(row)), with credits as exact ints.
    Each set of column names gets its own subclass (made once, see _row_class), which holds the names."""
    __slots__ = ()
    _names: tuple = ()
    _index: dict = {}

    def __getitem__(self, key):
        if key.__class__ is str:
            i = self._index.get(key)
            if i is None:
                i = self._index.get(key.lower())
                if i is None:
                    raise IndexError(f"No item with that key: {key!r}")
            return tuple.__getitem__(self, i)
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._names)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._names, self))!r})"


_classes: dict[tuple, tuple] = {}      # column names -> (Row subclass, indexes of money columns)
_cursors: dict[int, tuple] = {}        # id(cursor) -> (its description, Row subclass, money indexes)


def _row_class(names: tuple) -> tuple:
    found = _classes.get(names)
    if found is None:
        index = {}
        for i, n in enumerate(names):
            index.setdefault(n, i)
            index.setdefault(n.lower(), i)
        cls = type("Row", (Row,), {"__slots__": (), "_names": names, "_index": index})
        found = _classes[names] = (cls, tuple(i for i, n in enumerate(names) if n in MONEY_NAMES))
        if len(_classes) > 5000:
            _classes.clear()
    return found


def money_row(cursor, values) -> Row:
    """Row factory: credits stored as text (past 64 bits) come back as ints."""
    d = cursor.description
    c = _cursors.get(id(cursor))
    if c is None or c[0] is not d:
        cls, money = _row_class(tuple(x[0] for x in d))
        if len(_cursors) > 1000:
            _cursors.clear()
        c = _cursors[id(cursor)] = (d, cls, money)
    _, cls, money = c
    for i in money:
        v = values[i]
        if v.__class__ is str and _DIGITS.fullmatch(v):
            values = list(values)
            for j in money:
                w = values[j]
                if w.__class__ is str and _DIGITS.fullmatch(w):
                    values[j] = int(w)
            break
    return cls(values)


# ------------------------------------------------------------------ SQL functions
def _num(v):
    """A SQL value as a Python number for the exact functions (None stays None)."""
    if v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if _DIGITS.fullmatch(s):
            return int(s)
        try:
            return float(s)
        except ValueError:
            return 0
    return 0


def _out(v):
    """A Python number back to SQL: whole numbers outside 64 bits as text."""
    if v.__class__ is int and not INT64_MIN <= v <= INT64_MAX:
        return str(v)
    return v


def _exact(v) -> int | None:
    v = _num(v)
    if isinstance(v, float):
        return int(v) if math.isfinite(v) else 0
    return v


def big_add(a, b):
    if a.__class__ is int and b.__class__ is int:
        return _out(a + b)
    a, b = _exact(a), _exact(b)
    return None if a is None or b is None else _out(a + b)


def big_sub(a, b):
    if a.__class__ is int and b.__class__ is int:
        return _out(a - b)
    a, b = _exact(a), _exact(b)
    return None if a is None or b is None else _out(a - b)


def big_neg(a):
    a = _exact(a)
    return None if a is None else _out(-a)


def big_abs(a):
    a = _exact(a)
    return None if a is None else _out(abs(a))


def big_max(*args):
    vals = [_exact(v) for v in args if v is not None]
    return _out(max(vals)) if vals else None


def big_min(*args):
    vals = [_exact(v) for v in args if v is not None]
    return _out(min(vals)) if vals else None


def big_cmp(a, b):
    """-1, 0 or 1 (NULL if either is NULL), comparing exactly."""
    if a.__class__ is int and b.__class__ is int:   # the usual case, fast
        return (a > b) - (a < b)
    a, b = _num(a), _num(b)
    if a is None or b is None:
        return None
    return (a > b) - (a < b)


def big_key(a):
    """A text that sorts like the amount does (for ORDER BY): sign, then length, then digits."""
    a = _exact(a)
    if a is None:
        return None
    if a >= 0:
        s = str(a)
        return f"p{len(s):05d}{s}"
    s = str(-a)
    return f"n{99999 - len(s):05d}" + s.translate(str.maketrans("0123456789", "9876543210"))


class BigSum:
    """SUM() that never overflows: exact for whole numbers (text past 64 bits), a float if any input is one."""

    def __init__(self):
        self.total = 0
        self.real = False
        self.seen = False

    def step(self, v):
        if v.__class__ is int:   # the usual case, fast
            self.total += v
            self.seen = True
            return
        v = _num(v)
        if v is None:
            return
        self.seen = True
        if isinstance(v, float):
            self.real = True
        self.total += v

    def finalize(self):
        if not self.seen:
            return None
        if self.real:
            return float(self.total)
        return _out(self.total)


def register(conn: sqlite3.Connection) -> None:
    """Adds the exact money functions to a connection and replaces SUM with the exact one."""
    for name, fn, n in (("big_add", big_add, 2), ("big_sub", big_sub, 2), ("big_neg", big_neg, 1),
                        ("big_abs", big_abs, 1), ("big_cmp", big_cmp, 2), ("big_key", big_key, 1),
                        ("big_max", big_max, -1), ("big_min", big_min, -1)):
        conn.create_function(name, n, fn, deterministic=True)
    conn.create_aggregate("sum", 1, BigSum)
    conn.create_aggregate("big_sum", 1, BigSum)


class Connection(sqlite3.Connection):
    """sqlite3 connection with the money functions registered (pass as factory= to sqlite3/aiosqlite.connect)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        register(self)


# ------------------------------------------------------------------ JSON for the browser
def jsonable(obj, exact: frozenset | None = None, key=None):
    """A copy of obj where every whole number past 2^53 (JavaScript's exact limit) is a string of its digits. With
    `exact`, only the values under those keys are (the rest stay numbers: the page then gets them as the nearest
    JavaScript number, which it can add up and compare, where a string would be joined instead of added)."""
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return str(obj) if abs(obj) > JS_SAFE and (exact is None or key in exact) else obj
    if isinstance(obj, dict):
        return {k: jsonable(v, exact, k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v, exact, key) for v in obj]
    return obj


def dumps(obj, **kwargs) -> str:
    """json.dumps for the browser (big whole numbers as strings)."""
    return json.dumps(jsonable(obj), **kwargs)


def compact_dumps(obj, exact: frozenset | None = None) -> str:
    return json.dumps(jsonable(obj, exact), separators=(",", ":"))


def json_int(v) -> int | None:
    """A whole number of credits sent by a page: an int, a string of digits, or a JavaScript number past 2^53, which
    arrives as a float (JSON.stringify writes the shortest form, such as 2.5e+21, and that's read back exactly).
    None when it isn't a whole number."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v) if len(v) <= MAX_INPUT and _DIGITS.fullmatch(v.strip()) else None
    if isinstance(v, float) and math.isfinite(v):
        d = Decimal(repr(v))
        return int(d) if d == d.to_integral_value() else None
    return None


def tagged(obj):
    """Like jsonable, but a big whole number becomes its digits followed by "n" ("<digits>n", like a
    JavaScript BigInt literal), so the admin panel's page can tell credits from Discord ids (also sent as digits)."""
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return f"{obj}n" if abs(obj) > JS_SAFE else obj
    if isinstance(obj, dict):
        return {k: tagged(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [tagged(v) for v in obj]
    return obj


def tagged_dumps(obj, **kwargs) -> str:
    """json.dumps for the admin panel's page (big whole numbers tagged, see `tagged`)."""
    return json.dumps(tagged(obj), **kwargs)


# ------------------------------------------------------------------ text
def short(n: int, digits: int = 2) -> str:
    """2.5k, 9.08M, 1.2B, 3.4T, 5.6Qa, 2.1Qi ... up to Dc (1e33), then 1.23e36. Below 1,000 in full."""
    n = int(n)
    sign, a = ("-" if n < 0 else ""), abs(n)
    if a < 1000:
        return f"{n:,}"
    exp = len(str(a)) - 1
    if exp >= SUFFIXES[-1][1] + 3:   # 1e36 and up
        m = float(Fraction(a, 10 ** exp))
        return f"{sign}{f'{m:.{digits}f}'.rstrip('0').rstrip('.')}e{exp}"
    i = max(k for k, (_, e) in enumerate(SUFFIXES) if e <= exp)
    suffix, e = SUFFIXES[i]
    x = float(Fraction(a, 10 ** e))   # 1 <= x < 1000
    t = round(x, digits if x < 10 else 1 if x < 100 else 0)
    if t >= 1000 and i + 1 < len(SUFFIXES):   # 999.96B reads as 1T
        suffix, t = SUFFIXES[i + 1][0], round(x / 1000, digits)
    return f"{sign}{t:g}{suffix}"


AMOUNT_DIGITS = 30


def credits_text(n) -> str:
    """An amount for Discord: in full with commas up to AMOUNT_DIGITS digits (999 octillion), short beyond that
    (1.25No, 7.05e51). Discord caps a button label at 80 characters, an embed field at 1,024 and a message at 2,000,
    and balances have no upper limit, so the full digits can't always fit. Anything that isn't a whole number is
    written as f"{n:,}" would."""
    if isinstance(n, bool) or not isinstance(n, int):
        return f"{n:,}"
    if len(str(abs(n))) <= AMOUNT_DIGITS:
        return f"{n:,}"
    return short(n)


def parse_decimal(s: str) -> Decimal | None:
    """'2.5k', '1.2B', '3.4T', '5qa', '2.1Qi', '1e21' or plain digits (commas, spaces and _ allowed) as an exact
    Decimal, with no float in between; None if it isn't a number like that (negative numbers aren't)."""
    s = str(s).strip().lower().replace(",", "").replace("_", "").replace(" ", "")
    if len(s) > MAX_INPUT:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)(?:e(\d{1,4}))?([a-z]{0,2})", s)
    if not m or (m[3] and m[3] not in _PARSE_SUFFIXES):
        return None
    try:
        return Decimal(m[1]).scaleb(int(m[2] or 0) + _PARSE_SUFFIXES.get(m[3], 0))
    except InvalidOperation:
        return None


def parse_short(s: str) -> int | None:
    """parse_decimal, but only whole numbers: an int, or None."""
    value = parse_decimal(s)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)


# Payouts: a game multiplies a bet by a float (2.5, a crash multiplier, an RTP scale). A float only holds 15-16
# digits, so for a bet past 2^52 the result is off by up to thousands of credits. These do the same sums exactly for
# big amounts; below 2^52 they do the very float maths the games always did (left to right), so nothing changes there.
_SMALL = 2 ** 52


def _frac(x) -> Fraction:
    return Fraction(x) if isinstance(x, int) else Fraction(repr(float(x)))


def _product(amount: int, factors, div) -> Fraction:
    v = Fraction(amount)
    for f in factors:
        v *= _frac(f)
    return v / _frac(div)


def mul_floor(amount: int, *factors, eps: float = 0.0, div=1) -> int:
    """floor(amount * f1 * f2 ... / div + eps)."""
    if abs(amount) < _SMALL:
        v = amount
        for f in factors:
            v = v * f
        return math.floor((v / div if div != 1 else v) + eps)
    return math.floor(_product(amount, factors, div) + _frac(eps))


def mul_ceil(amount: int, *factors, eps: float = 0.0) -> int:
    """ceil(amount * f1 * f2 ... - eps)."""
    if abs(amount) < _SMALL:
        v = amount
        for f in factors:
            v = v * f
        return math.ceil(v - eps)
    return math.ceil(_product(amount, factors, 1) - _frac(eps))


def mul_round(amount: int, *factors) -> int:
    """round(amount * f1 * f2 ...)."""
    if abs(amount) < _SMALL:
        v = amount
        for f in factors:
            v = v * f
        return round(v)
    return round(_product(amount, factors, 1))


def div_floor(amount: int, by) -> int:
    """int(amount / by) for a positive amount: how many times `by` fits, exactly."""
    if abs(amount) < _SMALL:
        return int(amount / by)
    return math.floor(Fraction(amount) / _frac(by))
