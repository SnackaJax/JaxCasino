"""Draws the /serverstats page as a PNG: headline numbers, wagered per game, daily volume, activity by hour and
the credits in circulation over time.

Takes plain data (no Discord objects) so it can run in a worker thread. Drawn at 2x and downscaled.
Colours follow one rule: every chart is a single series, so every bar is the same blue, and all
numbers and labels use the text colours, never the bar colour.
"""
import io
import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from utils.table_image import K, box, fit, font, s, text, text_width

W, H = 1000, 1036
UPPER_BOTTOM = 674             # bottom of the per-game / per-day / per-hour panels
PAD = 24

SURFACE = (26, 26, 25)        # #1a1a19
PANEL = (35, 35, 33)
GRID = (56, 56, 53)           # #383835
INK = (240, 239, 236)         # #f0efec  primary text
INK_2 = (195, 194, 183)       # #c3c2b7  secondary text
INK_3 = (138, 137, 130)       # muted text
SERIES = (57, 135, 229)       # #3987e5  the one series colour
BRAND = (245, 196, 72)        # brand gold, only for the title mark


@dataclass
class StatsInfo:
    server: str
    period: str                                   # e.g. "LAST 7 DAYS"
    kpis: list[tuple[str, str, str]]              # (label, value, sub-line)
    per_game: list[tuple[str, int, int]]          # (game, wagered, plays), largest first
    daily: list[tuple[str, int]]                  # (day label, wagered), oldest first
    hours: list[int] = field(default_factory=lambda: [0] * 24)  # games started per hour of day
    footer: str = ""
    circulation: list[tuple[float, int]] = field(default_factory=list)  # (unix time, credits held), oldest first
    circulation_start: float | None = None        # left edge of the time axis (period start); None = first point


def short(n: float) -> str:
    n = int(n)
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if abs(n) >= div:
            v = n / div
            return (f"{v:.1f}".removesuffix(".0") if abs(v) < 100 else f"{v:.0f}") + suffix
    return f"{n:,}"


def nice_step(raw: float) -> float:
    """Rounds a gridline spacing up to 1, 2 or 5 × a power of ten."""
    if raw <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def bar_h(d: ImageDraw.ImageDraw, x, y, length, thick, fill) -> None:
    """Horizontal bar from a left baseline: square at the baseline, 4px rounded at the data end."""
    if length <= 0:
        return
    r = min(4, length / 2, thick / 2)
    d.rounded_rectangle(box(x, y, x + length, y + thick), radius=s(r), fill=fill)
    if length > r:
        d.rectangle(box(x, y, x + min(r, length), y + thick), fill=fill)


def bar_v(d: ImageDraw.ImageDraw, x, base, height, thick, fill) -> None:
    """Column growing up from `base`: square at the baseline, 4px rounded at the top."""
    if height <= 0:
        return
    r = min(4, height / 2, thick / 2)
    d.rounded_rectangle(box(x, base - height, x + thick, base), radius=s(r), fill=fill)
    if height > r:
        d.rectangle(box(x, base - min(r, height), x + thick, base), fill=fill)


def panel(d, x0, y0, x1, y1, title: str, subtitle: str = "") -> None:
    d.rounded_rectangle(box(x0, y0, x1, y1), radius=s(12), fill=PANEL)
    text(d, x0 + 18, y0 + 26, title, font("bold", 16), INK, anchor="lm")
    if subtitle:
        text(d, x1 - 18, y0 + 26, subtitle, font("regular", 13), INK_3, anchor="rm")


def empty(d, cx, cy, msg="No games in this period yet") -> None:
    text(d, cx, cy, msg, font("regular", 15), INK_3)


def draw_kpis(d, y, kpis) -> None:
    n = len(kpis)
    gap = 12
    w = (W - PAD * 2 - gap * (n - 1)) / n
    for i, (label, value, sub) in enumerate(kpis):
        x = PAD + i * (w + gap)
        d.rounded_rectangle(box(x, y, x + w, y + 104), radius=s(12), fill=PANEL)
        text(d, x + 16, y + 22, label.upper(), font("bold", 11), INK_3, anchor="lm")
        f = font("bold", 30)
        while text_width(d, value, f) > w - 28 and f.size > 16 * K:
            f = font("bold", f.size // K - 2)
        text(d, x + 16, y + 56, value, f, INK, anchor="lm")
        text(d, x + 16, y + 86, fit(d, sub, font("regular", 12), w - 28), font("regular", 12), INK_2, anchor="lm")


def draw_games(d, x0, y0, x1, y1, rows) -> None:
    panel(d, x0, y0, x1, y1, "Wagered by game", "plays")
    if not rows:
        return empty(d, (x0 + x1) / 2, (y0 + y1) / 2 + 14)
    top = y0 + 58
    avail = y1 - 16 - top
    band = min(34, avail / len(rows))
    thick = min(18, band - 8)
    label_w, plays_w, value_w = 118, 46, 60
    bx = x0 + 18 + label_w
    bmax = x1 - 18 - plays_w - value_w - bx
    biggest = max(r[1] for r in rows) or 1
    f_label, f_val = font("regular", 14), font("bold", 13)
    for i, (name, wagered, plays) in enumerate(rows):
        cy = top + band * i + band / 2
        text(d, x0 + 18, cy, fit(d, name, f_label, label_w - 8), f_label, INK_2, anchor="lm")
        length = max(2, bmax * wagered / biggest) if wagered else 0
        bar_h(d, bx, cy - thick / 2, length, thick, SERIES)
        text(d, bx + length + 8, cy, short(wagered), f_val, INK, anchor="lm")
        text(d, x1 - 18, cy, f"{plays:,}", font("regular", 13), INK_3, anchor="rm")


def draw_daily(d, x0, y0, x1, y1, days) -> None:
    total = sum(v for _, v in days)
    panel(d, x0, y0, x1, y1, f"Wagered per day", f"last {len(days)} days")
    if not total:
        return empty(d, (x0 + x1) / 2, (y0 + y1) / 2 + 14, f"No games in the last {len(days)} days")
    left, right = x0 + 52, x1 - 18
    top, base = y0 + 66, y1 - 34
    peak_v = max(v for _, v in days)
    step = nice_step(peak_v / 3)
    scale_max = max(step * math.ceil(peak_v / step), 1)
    # recessive grid on round numbers, labelled on the left
    tick = step
    while tick <= scale_max:
        gy = base - (base - top) * tick / scale_max
        d.line(box(left, gy, right, gy), fill=GRID, width=s(1))
        text(d, left - 8, gy, short(tick), font("regular", 11), INK_3, anchor="rm")
        tick += step
    d.line(box(left, base, right, base), fill=GRID, width=s(1))
    text(d, left - 8, base, "0", font("regular", 11), INK_3, anchor="rm")
    slot = (right - left) / len(days)
    thick = min(24, slot - 2)  # 2px surface gap when bars touch
    peak = max(range(len(days)), key=lambda i: days[i][1])
    every = max(1, math.ceil(len(days) / 7))
    for i, (label, v) in enumerate(days):
        x = left + slot * i + (slot - thick) / 2
        bar_v(d, x, base, (base - top) * v / scale_max if v else 0, thick, SERIES)
        if (len(days) - 1 - i) % every == 0:  # counted back from today, so today always has a label
            text(d, x + thick / 2, base + 16, label, font("regular", 11), INK_3)
    # direct label on the peak day only
    px = left + slot * peak + slot / 2
    py = base - (base - top) * days[peak][1] / scale_max
    f = font("bold", 12)
    px = min(max(px, left + text_width(d, short(peak_v), f) / 2), right - text_width(d, short(peak_v), f) / 2)
    text(d, px, py - 10, short(peak_v), f, INK)


def draw_hours(d, x0, y0, x1, y1, hours) -> None:
    total = sum(hours)
    busiest = max(range(24), key=lambda h: hours[h]) if total else None
    sub = f"busiest {busiest:02d}:00–{(busiest + 1) % 24:02d}:00" if busiest is not None else ""
    panel(d, x0, y0, x1, y1, "Games by hour of day", sub)
    if not total:
        return empty(d, (x0 + x1) / 2, (y0 + y1) / 2 + 14)
    left, right = x0 + 18, x1 - 18
    top, base = y0 + 56, y1 - 34
    d.line(box(left, base, right, base), fill=GRID, width=s(1))
    slot = (right - left) / 24
    thick = slot - 2
    biggest = max(hours)
    for h, n in enumerate(hours):
        x = left + slot * h + 1
        bar_v(d, x, base, (base - top) * n / biggest if n else 0, thick, SERIES)
        if h % 6 == 0:
            text(d, x + thick / 2, base + 16, f"{h:02d}:00", font("regular", 11), INK_3)


def draw_circulation(img, d, x0, y0, x1, y1, points, axis_start) -> None:
    from utils.balance_image import draw_chart   # imported here: balance_image builds on this module
    if not points:
        panel(d, x0, y0, x1, y1, "Credits in circulation")
        return empty(d, (x0 + x1) / 2, (y0 + y1) / 2 + 14, "No balance history yet")
    draw_chart(img, d, x0, y0, x1, y1, points, axis_start, title="Credits in circulation",
               steady="No change in this period · steady at {end:,} credits", max_pct=1000)


def render_stats(info: StatsInfo) -> bytes:
    img = Image.new("RGB", (W * K, H * K), SURFACE)
    d = ImageDraw.Draw(img)

    # header
    d.ellipse(box(PAD, 22, PAD + 14, 36), fill=BRAND)
    text(d, PAD + 24, 29, "JAX CASINO · SERVER STATS", font("bold", 12), INK_3, anchor="lm")
    text(d, PAD, 62, fit(d, info.server, font("bold", 28), W - 2 * PAD - 220), font("bold", 28), INK, anchor="lm")
    f = font("bold", 13)
    pw = text_width(d, info.period, f) + 28
    d.rounded_rectangle(box(W - PAD - pw, 46, W - PAD, 76), radius=s(15), fill=PANEL, outline=GRID, width=s(1))
    text(d, W - PAD - pw / 2, 61, info.period, f, INK)

    draw_kpis(d, 96, info.kpis)

    top = 216
    mid = PAD + 470
    draw_games(d, PAD, top, mid, UPPER_BOTTOM, info.per_game)
    draw_daily(d, mid + 16, top, W - PAD, top + 250, info.daily)
    draw_hours(d, mid + 16, top + 266, W - PAD, UPPER_BOTTOM, info.hours)
    draw_circulation(img, d, PAD, UPPER_BOTTOM + 16, W - PAD, H - 46, info.circulation, info.circulation_start)

    if info.footer:
        text(d, PAD, H - 22, info.footer, font("regular", 12), INK_3, anchor="lm")

    out = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
