"""Draws a player's /stats page as a PNG: headline numbers, a balance-over-time graph, where the change came
from, and profit per game. Same look as the /serverstats page.

Takes plain data (no Discord objects) so it can run in a worker thread. Drawn at 2x and downscaled.
"""
import datetime as dt
import io
import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from utils.stats_image import (BRAND, GRID, INK, INK_2, INK_3, PAD, PANEL, SERIES, SURFACE, W, draw_kpis, empty,
                               nice_step, panel, short)
from utils.table_image import K, box, fit, font, s, text, text_width

H = 800
UP = (88, 196, 122)
DOWN = (232, 96, 96)


@dataclass
class BalanceInfo:
    name: str
    period: str                                   # e.g. "LAST 7 DAYS"
    points: list[tuple[float, int]]               # (unix time, balance), oldest first
    kpis: list[tuple[str, str, str]]              # (label, value, sub-line)
    sources: list[tuple[str, int]] = field(default_factory=list)       # (label, signed credits)
    games: list[tuple[str, int, int]] = field(default_factory=list)    # (game, net, plays)
    footer: str = ""
    axis_start: float | None = None               # left edge of the time axis (period start); None = first point


def signed(n: int) -> str:
    return ("+" if n > 0 else "−" if n < 0 else "") + short(abs(n))


def _time_label(ts: float, span: float) -> str:
    t = dt.datetime.fromtimestamp(ts)
    if span <= 2 * 86400:
        return f"{t:%H:%M}"
    if span <= 10 * 86400:
        return f"{t:%a} {t.day}"
    return f"{t.day} {t:%b}"


def draw_chart(img: Image.Image, d: ImageDraw.ImageDraw, x0, y0, x1, y1, points,
               axis_start: float | None = None, title: str = "Balance over time",
               steady: str = "No balance changes in this period · steady at {end:,} credits",
               max_pct: float | None = None) -> None:
    """max_pct: leave the percentage out of the change label when it is this big or bigger."""
    start, end = points[0][1], points[-1][1]
    change = end - start
    pct = change / start * 100 if start > 0 else 0
    sub = (f"{signed(change)} ({pct:+,.0f}%)" if abs(pct) >= 1000 else f"{signed(change)} ({pct:+.1f}%)") if start > 0 else signed(change)
    if max_pct is not None and abs(pct) >= max_pct:
        sub = signed(change)
    panel(d, x0, y0, x1, y1, title, sub)
    lo = min(b for _, b in points)
    hi = max(b for _, b in points)
    if len(points) < 2 or lo == hi:
        return empty(d, (x0 + x1) / 2, (y0 + y1) / 2 + 14, steady.format(end=end))
    left, right = x0 + 64, x1 - 24
    top, base = y0 + 60, y1 - 36
    t1 = points[-1][0]
    # The axis covers the whole period (so 24 hours, 7 days and 30 days really look different); the line
    # starts where the player's history starts. It always covers at least an hour.
    t0 = min(axis_start if axis_start is not None else points[0][0], points[0][0], t1 - 3600)
    step = nice_step(max(hi - lo, 1) / 4)
    vmin = math.floor(lo / step) * step
    vmax = max(math.ceil(hi / step) * step, vmin + step)
    if vmin < 0 <= lo:
        vmin = 0

    def X(t):
        return left + (right - left) * (t - t0) / (t1 - t0)

    def Y(v):
        return base - (base - top) * (v - vmin) / (vmax - vmin)

    tick = vmin
    while tick <= vmax + 1e-9:
        gy = Y(tick)
        d.line(box(left, gy, right, gy), fill=GRID, width=s(1))
        text(d, left - 8, gy, short(tick), font("regular", 11), INK_3, anchor="rm")
        tick += step
    span = t1 - t0
    for i in range(6):
        t = t0 + span * i / 5
        text(d, X(t), base + 18, _time_label(t, span), font("regular", 11), INK_3)

    # Starting balance as a dashed reference line.
    sy = Y(start)
    x = left
    while x < right:
        d.line(box(x, sy, min(x + 6, right), sy), fill=INK_3, width=s(1))
        x += 11
    # Area under the line, then the line itself (as steps: a balance changes at an instant).
    pts = []
    for i, (t, b) in enumerate(points):
        if i:
            pts.append((X(t), Y(points[i - 1][1])))
        pts.append((X(t), Y(b)))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    area = [(s(px), s(py)) for px, py in pts] + [(s(pts[-1][0]), s(base)), (s(pts[0][0]), s(base))]
    od.polygon(area, fill=(*SERIES, 46))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
    d.line([(s(px), s(py)) for px, py in pts], fill=SERIES, width=s(2.5), joint="curve")

    # Peak, low and current value, labelled directly.
    peak = max(points, key=lambda p: p[1])
    low = min(points, key=lambda p: p[1])
    f = font("bold", 12)
    marks = [(peak, "Peak", -14)]
    if low[1] != peak[1]:
        marks.append((low, "Low", 16))
    for (t, b), label, dy in marks:
        px, py = X(t), Y(b)
        d.ellipse(box(px - 4, py - 4, px + 4, py + 4), fill=INK, outline=SURFACE, width=s(2))
        value = f"{label} {short(b)}"
        tw = text_width(d, value, f)
        lx = min(max(px, left + tw / 2), right - tw / 2)
        text(d, lx, min(max(py + dy, top - 6), base - 8), value, f, INK)
    ex, ey = X(t1), Y(end)
    d.ellipse(box(ex - 5, ey - 5, ex + 5, ey + 5), fill=SERIES, outline=INK, width=s(2))


def draw_sources(d, x0, y0, x1, y1, rows) -> None:
    panel(d, x0, y0, x1, y1, "Where the change came from")
    rows = [r for r in rows if r[1]]
    if not rows:
        return empty(d, (x0 + x1) / 2, (y0 + y1) / 2 + 14, "No balance changes in this period")
    _signed_bars(d, x0, y0, x1, y1, [(label, v, None) for label, v in rows])


def draw_games(d, x0, y0, x1, y1, rows) -> None:
    panel(d, x0, y0, x1, y1, "Profit by game", "games")
    if not rows:
        return empty(d, (x0 + x1) / 2, (y0 + y1) / 2 + 14)
    _signed_bars(d, x0, y0, x1, y1, rows)


def _signed_bars(d, x0, y0, x1, y1, rows) -> None:
    """Bars growing left (loss) or right (profit) from a shared zero line."""
    top = y0 + 54
    band = min(30, (y1 - 14 - top) / len(rows))
    thick = min(16, band - 8)
    label_w, count_w = 112, 40 if rows[0][2] is not None else 0
    zero_l, zero_r = x0 + 18 + label_w, x1 - 18 - count_w
    zero = (zero_l + zero_r) / 2
    half = (zero_r - zero_l) / 2 - 46
    biggest = max(abs(v) for _, v, _ in rows) or 1
    d.line(box(zero, top - 4, zero, top + band * len(rows) - 4), fill=GRID, width=s(1))
    f_label, f_val = font("regular", 13), font("bold", 12)
    for i, (name, v, count) in enumerate(rows):
        cy = top + band * i + band / 2
        text(d, x0 + 18, cy, fit(d, name, f_label, label_w - 8), f_label, INK_2, anchor="lm")
        length = max(2, half * abs(v) / biggest) if v else 0
        color = UP if v > 0 else DOWN
        if v >= 0:
            d.rounded_rectangle(box(zero, cy - thick / 2, zero + length, cy + thick / 2), radius=s(3), fill=color)
            text(d, zero + length + 6, cy, signed(v), f_val, INK, anchor="lm")
        else:
            d.rounded_rectangle(box(zero - length, cy - thick / 2, zero, cy + thick / 2), radius=s(3), fill=color)
            text(d, zero - length - 6, cy, signed(v), f_val, INK, anchor="rm")
        if count is not None:
            text(d, x1 - 18, cy, f"{count:,}", font("regular", 12), INK_3, anchor="rm")


def render_balance(info: BalanceInfo) -> bytes:
    img = Image.new("RGB", (W * K, H * K), SURFACE)
    d = ImageDraw.Draw(img)

    d.ellipse(box(PAD, 22, PAD + 14, 36), fill=BRAND)
    text(d, PAD + 24, 29, "JAX CASINO · PLAYER STATS", font("bold", 12), INK_3, anchor="lm")
    text(d, PAD, 62, fit(d, info.name, font("bold", 28), W - 2 * PAD - 220), font("bold", 28), INK, anchor="lm")
    f = font("bold", 13)
    pw = text_width(d, info.period, f) + 28
    d.rounded_rectangle(box(W - PAD - pw, 46, W - PAD, 76), radius=s(15), fill=PANEL, outline=GRID, width=s(1))
    text(d, W - PAD - pw / 2, 61, info.period, f, INK)

    draw_kpis(d, 96, info.kpis)
    draw_chart(img, d, PAD, 216, W - PAD, 520, info.points, info.axis_start)
    mid = W / 2
    draw_sources(d, PAD, 536, mid - 8, H - 46, info.sources)
    draw_games(d, mid + 8, 536, W - PAD, H - 46, info.games)
    if info.footer:
        text(d, PAD, H - 22, info.footer, font("regular", 12), INK_3, anchor="lm")

    out = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
