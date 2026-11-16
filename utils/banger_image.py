"""Draws Banger Bash as PNGs with Pillow: the reels, the Grill Bonus ladder and the bonus games.

Symbols are drawn with shapes (no emoji font or image files), cached per size, and drawn at 2x then scaled
down for smooth edges. The same symbol drawings are exported as sprites for the Activity
(python -m activity.assets.make_banger_sprites). The functions take plain data so they can run in a thread.
"""
from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from utils import banger_engine as E
from utils.table_image import K, font

CW, CH = 140, 124
GX, GY = 60, 104
GW, GH = CW * E.REELS, CH * E.ROWS
W = GX * 2 + GW
H = GY + GH + 74

SKY_TOP = (24, 16, 44)
SKY_BOTTOM = (96, 38, 22)
FREE_TOP = (60, 12, 8)
FREE_BOTTOM = (150, 50, 10)
WOOD = (132, 80, 40)
WOOD_DARK = (74, 42, 20)
REEL_BG = (30, 20, 16)
CELL_BG = (56, 36, 26)
CELL_EDGE = (92, 62, 40)
WHITE = (250, 245, 235)
GREY = (190, 170, 150)
MUSTARD = (255, 200, 40)
KETCHUP = (215, 40, 35)
FIRE = (255, 130, 30)
GREEN = (40, 110, 50)

LINE_COLORS = [
    (255, 90, 90), (90, 200, 255), (120, 240, 120), (255, 200, 60), (255, 120, 220),
    (160, 130, 255), (255, 150, 70), (80, 240, 210), (230, 230, 90), (255, 110, 150),
    (110, 170, 255), (190, 255, 110), (255, 170, 120), (200, 120, 255), (110, 255, 170),
    (255, 230, 150), (150, 220, 255), (255, 140, 190), (180, 200, 120), (240, 170, 255),
]


def P(v: float) -> int:
    return int(round(v * K))


def rot_poly(cx, cy, rx, ry, angle_deg, n=36):
    a = math.radians(angle_deg)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)))
    return pts


def _label(d, w, h, y, text, fill, outline, size=17):
    s = size * min(w / P(CW), h / P(CH))
    d.text((w / 2, y), text, font=font("bold", max(8, round(s))), fill=fill, anchor="mm",
           stroke_width=max(1, round(P(3) * s / size)), stroke_fill=outline)


# ----------------------------------------------------------------------------- symbols
# Each draws a symbol centred in a tile of size (w, h), in tile pixels (already scaled by K).
def _q(w, h, v):
    """Scales a line width or small radius (given for a normal cell) to this tile size."""
    return max(1, v * min(w / P(CW), h / P(CH)))


def _bottle(d, w, h, body, edge, cap, mark):
    cx, cy = w / 2, h / 2 + h * 0.04
    bw = w * 0.24
    top, bot = cy - h * 0.12, cy + h * 0.34
    d.polygon([(cx - bw / 2 + _q(w, h, P(3)), top + _q(w, h, P(8))), (cx - bw * 0.2, top - h * 0.1),
               (cx + bw * 0.2, top - h * 0.1), (cx + bw / 2 - _q(w, h, P(3)), top + _q(w, h, P(8)))],
              fill=body, outline=edge)
    d.rounded_rectangle((cx - bw / 2, top, cx + bw / 2, bot), radius=_q(w, h, P(11)), fill=body, outline=edge,
                        width=round(_q(w, h, P(3))))
    d.rounded_rectangle((cx - bw * 0.25, top - h * 0.19, cx + bw * 0.25, top - h * 0.09), radius=_q(w, h, P(4)),
                        fill=cap)
    n = _q(w, h, P(5))
    d.polygon([(cx - n, top - h * 0.19), (cx + n, top - h * 0.19), (cx + n * 0.4, top - h * 0.31),
               (cx - n * 0.4, top - h * 0.31)], fill=cap)
    d.ellipse((cx - bw * 0.38, cy + h * 0.02, cx + bw * 0.38, cy + h * 0.24), fill=(250, 240, 215), outline=edge,
              width=round(_q(w, h, P(2))))
    mark(d, cx, cy + h * 0.13, h * 0.055)
    d.line((cx - bw * 0.3, top + _q(w, h, P(10)), cx - bw * 0.3, cy - _q(w, h, P(2))), fill=(255, 255, 255),
           width=round(_q(w, h, P(4))))


def _ketchup(d, w, h):
    def tomato(d, x, y, r):
        d.ellipse((x - r, y - r, x + r, y + r), fill=KETCHUP)
        d.polygon([(x - r * 0.6, y - r * 0.9), (x, y - r * 0.4), (x + r * 0.6, y - r * 0.9), (x, y - r * 1.3)],
                  fill=(60, 150, 50))
    _bottle(d, w, h, (215, 35, 35), (130, 15, 15), (245, 245, 240), tomato)


def _mustard(d, w, h):
    def seed(d, x, y, r):
        for dx in (-1.1, 0, 1.1):
            d.ellipse((x + dx * r - r * 0.5, y - r * 0.5, x + dx * r + r * 0.5, y + r * 0.5), fill=(200, 140, 20))
    _bottle(d, w, h, (250, 195, 30), (160, 110, 10), (200, 50, 40), seed)


def _pickle(d, w, h):
    cx, cy = w / 2, h / 2
    d.polygon(rot_poly(cx, cy, w * 0.3, h * 0.16, -35), fill=(105, 165, 55), outline=(55, 100, 25))
    d.polygon(rot_poly(cx, cy, w * 0.3, h * 0.16, -35), outline=(55, 100, 25), width=round(_q(w, h, P(3))))
    rng = random.Random(3)
    a = math.radians(-35)
    r = _q(w, h, P(3))
    for _ in range(14):
        t, s = rng.uniform(-0.8, 0.8), rng.uniform(-0.6, 0.6)
        x = cx + t * w * 0.3 * math.cos(a) - s * h * 0.16 * math.sin(a)
        y = cy + t * w * 0.3 * math.sin(a) + s * h * 0.16 * math.cos(a)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(70, 125, 35))
    d.polygon(rot_poly(cx - w * 0.04, cy - h * 0.05, w * 0.18, h * 0.04, -35), fill=(170, 215, 110))


def _onion(d, w, h):
    cx, cy = w / 2, h / 2
    for (ox, oy) in ((-0.09, 0.07), (0.1, -0.07)):
        x, y, r = cx + ox * w, cy + oy * h, h * 0.21
        d.ellipse((x - r, y - r, x + r, y + r), outline=(160, 95, 30), width=round(_q(w, h, P(16))))
        i = _q(w, h, P(2))
        d.ellipse((x - r + i, y - r + i, x + r - i, y + r - i), outline=(225, 160, 65), width=round(_q(w, h, P(11))))
        i = _q(w, h, P(5))
        d.arc((x - r + i, y - r + i, x + r - i, y + r - i), 200, 290, fill=(255, 215, 130),
              width=round(_q(w, h, P(4))))
        rng = random.Random(int(ox * 100))
        dot = _q(w, h, P(2))
        for _ in range(10):
            a = rng.uniform(0, 2 * math.pi)
            px, py = x + math.cos(a) * (r - _q(w, h, P(7))), y + math.sin(a) * (r - _q(w, h, P(7)))
            d.ellipse((px - dot, py - dot, px + dot, py + dot), fill=(180, 110, 40))


def _chorizo(d, w, h):
    # Two links end to end in a gentle arch, tied with string where they meet.
    cx, cy = w / 2, h / 2 + h * 0.02
    for side in (-1, 1):
        x, y = cx + side * w * 0.19, cy + h * 0.05
        ang = side * 18
        d.polygon(rot_poly(x, y, w * 0.2, h * 0.12, ang), fill=(175, 40, 35), outline=(100, 18, 18))
        d.polygon(rot_poly(x, y, w * 0.2, h * 0.12, ang), outline=(100, 18, 18), width=round(_q(w, h, P(3))))
        rng = random.Random(side + 5)
        a, b = _q(w, h, P(3)), _q(w, h, P(2))
        for _ in range(10):
            px, py = x + rng.uniform(-w * 0.12, w * 0.12), y + rng.uniform(-h * 0.05, h * 0.05)
            d.ellipse((px - a, py - b, px + a, py + b), fill=(235, 175, 160))
        d.polygon(rot_poly(x - side * w * 0.01, y - h * 0.06, w * 0.11, h * 0.022, ang), fill=(225, 100, 90))
    kx, ky = cx, cy - h * 0.01
    lw = round(_q(w, h, P(3)))
    d.line((kx, ky, kx - w * 0.05, ky - h * 0.13), fill=(235, 220, 180), width=lw)
    d.line((kx, ky, kx + w * 0.04, ky - h * 0.14), fill=(235, 220, 180), width=lw)
    a, b = _q(w, h, P(8)), _q(w, h, P(7))
    d.ellipse((kx - a, ky - b, kx + a, ky + b), fill=(235, 220, 180), outline=(150, 130, 90))


def _brat(d, w, h):
    cx, cy = w / 2, h / 2
    ang = -25
    d.polygon(rot_poly(cx, cy, w * 0.36, h * 0.14, ang), fill=(220, 170, 115), outline=(140, 90, 50))
    d.polygon(rot_poly(cx, cy, w * 0.36, h * 0.14, ang), outline=(140, 90, 50), width=round(_q(w, h, P(3))))
    a = math.radians(ang)
    pa = a + math.pi / 2
    off = _q(w, h, P(3))
    for t in (-0.45, -0.15, 0.15, 0.45):
        x, y = cx + t * w * 0.36 * math.cos(a), cy + t * w * 0.36 * math.sin(a)
        L = h * 0.09
        d.line((x - L * math.cos(pa) + off, y - L * math.sin(pa), x + L * math.cos(pa) + off,
                y + L * math.sin(pa)), fill=(95, 55, 25), width=round(_q(w, h, P(6))))
    d.polygon(rot_poly(cx - w * 0.03, cy - h * 0.07, w * 0.22, h * 0.03, ang), fill=(245, 210, 165))


def _hotdog(d, w, h):
    cx, cy = w / 2, h / 2 + h * 0.03
    bun, bun_edge = (230, 170, 90), (160, 100, 40)
    lw = round(_q(w, h, P(3)))
    d.chord((cx - w * 0.35, cy - h * 0.24, cx + w * 0.35, cy + h * 0.08), 180, 360, fill=bun, outline=bun_edge,
            width=lw)
    d.rounded_rectangle((cx - w * 0.42, cy - h * 0.075, cx + w * 0.42, cy + h * 0.075), radius=h * 0.075,
                        fill=(195, 70, 45), outline=(120, 40, 25), width=lw)
    pts = [(cx - w * 0.3 + i * w * 0.06, cy + (-1 if i % 2 else 1) * h * 0.035) for i in range(11)]
    d.line(pts, fill=MUSTARD, width=round(_q(w, h, P(5))), joint="curve")
    d.chord((cx - w * 0.36, cy - h * 0.04, cx + w * 0.36, cy + h * 0.3), 0, 180, fill=bun, outline=bun_edge,
            width=lw)
    d.arc((cx - w * 0.28, cy + h * 0.0, cx + w * 0.28, cy + h * 0.22), 30, 150, fill=(250, 210, 140),
          width=round(_q(w, h, P(4))))


def _king(d, w, h):
    cx, cy = w / 2, h / 2 + h * 0.05
    bw = w * 0.15
    lw = round(_q(w, h, P(3)))
    d.rounded_rectangle((cx - bw, cy - h * 0.22, cx + bw, cy + h * 0.36), radius=bw, fill=(205, 85, 55),
                        outline=(120, 45, 25), width=lw)
    d.line((cx - bw * 0.55, cy - h * 0.12, cx - bw * 0.55, cy + h * 0.24), fill=(240, 150, 115),
           width=round(_q(w, h, P(5))))
    gy = cy - h * 0.05
    for side in (-1, 1):
        x0 = cx + side * w * 0.015
        x1 = cx + side * w * 0.135
        d.rounded_rectangle((min(x0, x1), gy - h * 0.04, max(x0, x1), gy + h * 0.045), radius=_q(w, h, P(6)),
                            fill=(20, 20, 30))
        hx = (x0 + x1) / 2 - _q(w, h, P(4))
        d.line((hx, gy - h * 0.02, hx + _q(w, h, P(6)), gy - h * 0.02), fill=(120, 200, 255), width=lw)
    d.line((cx - w * 0.02, gy - h * 0.02, cx + w * 0.02, gy - h * 0.02), fill=(20, 20, 30), width=lw)
    d.arc((cx - w * 0.07, cy + h * 0.0, cx + w * 0.07, cy + h * 0.12), 20, 160, fill=(90, 25, 15),
          width=round(_q(w, h, P(4))))
    y0, y1 = cy - h * 0.19, cy - h * 0.4
    d.polygon([(cx - w * 0.12, y0), (cx - w * 0.13, y1 + h * 0.05), (cx - w * 0.06, y1 + h * 0.12), (cx, y1),
               (cx + w * 0.06, y1 + h * 0.12), (cx + w * 0.13, y1 + h * 0.05), (cx + w * 0.12, y0)],
              fill=(255, 205, 50), outline=(170, 110, 20))
    j = _q(w, h, P(5))
    d.ellipse((cx - j, y0 - h * 0.08, cx + j, y0 - h * 0.08 + j * 2), fill=KETCHUP)
    j = _q(w, h, P(4))
    for side in (-1, 1):
        x = cx + side * w * 0.13
        d.ellipse((x - j, y1 + h * 0.05 - j, x + j, y1 + h * 0.05 + j), fill=(255, 240, 160))


def flame(d, cx, by, wd, ht, col):
    pts = [(cx - wd * 0.5, by - ht * 0.3), (cx - wd * 0.38, by - ht * 0.62), (cx - wd * 0.18, by - ht * 0.5),
           (cx - wd * 0.06, by - ht), (cx + wd * 0.16, by - ht * 0.58), (cx + wd * 0.3, by - ht * 0.78),
           (cx + wd * 0.5, by - ht * 0.3), (cx + wd * 0.32, by - ht * 0.02), (cx - wd * 0.32, by - ht * 0.02)]
    d.polygon(pts, fill=col)
    d.ellipse((cx - wd * 0.5, by - ht * 0.5, cx + wd * 0.5, by), fill=col)


def _wild(d, w, h):
    cx, by = w / 2, h * 0.7
    flame(d, cx, by, w * 0.42, h * 0.6, (220, 50, 25))
    flame(d, cx, by - h * 0.02, w * 0.31, h * 0.45, FIRE)
    flame(d, cx, by - h * 0.03, w * 0.18, h * 0.27, (255, 225, 90))
    _label(d, w, h, h * 0.84, "WILD", (255, 245, 210), (170, 40, 10), 19)


def _grill(d, w, h):
    cx, cy = w / 2, h / 2 - h * 0.04
    for sx, sy, r in ((-0.05, -0.28, 0.07), (0.05, -0.36, 0.06), (-0.02, -0.43, 0.045)):
        x, y, rr = cx + sx * w, cy + sy * h, r * h
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(210, 210, 215, 150))
    lw = round(_q(w, h, P(5)))
    for side in (-1, 1):
        d.line((cx + side * w * 0.12, cy + h * 0.15, cx + side * w * 0.2, cy + h * 0.32), fill=(70, 70, 78), width=lw)
    d.chord((cx - w * 0.28, cy - h * 0.22, cx + w * 0.28, cy + h * 0.24), 0, 180, fill=(38, 38, 44),
            outline=(110, 110, 120), width=round(_q(w, h, P(3))))
    for side in (-1, 1):
        flame(d, cx + side * w * 0.1, cy + _q(w, h, P(2)), w * 0.1, h * 0.12, FIRE)
    d.line((cx - w * 0.3, cy, cx + w * 0.3, cy), fill=(170, 170, 180), width=round(_q(w, h, P(4))))
    for x in (-0.13, 0.0, 0.13):
        xx = cx + x * w
        d.rounded_rectangle((xx - w * 0.055, cy - h * 0.11, xx + w * 0.055, cy - 1), radius=_q(w, h, P(7)),
                            fill=(195, 75, 45), outline=(110, 40, 20), width=round(_q(w, h, P(2))))
        d.line((xx - _q(w, h, P(4)), cy - h * 0.085, xx + _q(w, h, P(4)), cy - h * 0.03), fill=(90, 40, 20),
               width=round(_q(w, h, P(3))))
    _label(d, w, h, h * 0.86, "BONUS", (255, 225, 120), (150, 40, 10), 18)


def _bun(d, w, h):
    """Mystery Bun: a closed bun with a question mark, shown until the buns reveal their symbol."""
    cx, cy = w / 2, h / 2
    d.chord((cx - w * 0.34, cy - h * 0.3, cx + w * 0.34, cy + h * 0.22), 180, 360, fill=(235, 175, 95),
            outline=(160, 100, 40), width=round(_q(w, h, P(3))))
    d.rounded_rectangle((cx - w * 0.34, cy - h * 0.06, cx + w * 0.34, cy + h * 0.22), radius=_q(w, h, P(14)),
                        fill=(225, 160, 80), outline=(160, 100, 40), width=round(_q(w, h, P(3))))
    rng = random.Random(11)
    r = _q(w, h, P(2.5))
    for _ in range(9):
        x, y = cx + rng.uniform(-0.25, 0.25) * w, cy + rng.uniform(-0.25, -0.12) * h
        d.ellipse((x - r * 1.4, y - r, x + r * 1.4, y + r), fill=(255, 240, 200))
    d.text((cx, cy + h * 0.07), "?", font=font("bold", max(8, round(30 * min(w / P(CW), h / P(CH))))),
           fill=(110, 50, 15), anchor="mm")


DRAW = {"ketchup": _ketchup, "mustard": _mustard, "pickle": _pickle, "onion": _onion, "chorizo": _chorizo,
        "brat": _brat, "hotdog": _hotdog, "king": _king, E.WILD: _wild, E.SCATTER: _grill, E.MYSTERY: _bun}
GLOW = {"king": (255, 200, 60), "hotdog": (255, 90, 60), "brat": (255, 160, 70), E.WILD: (255, 120, 30),
        E.SCATTER: (255, 170, 60), E.MYSTERY: (255, 210, 120)}
SYMBOL_BG = {"king": ((90, 66, 14), (190, 150, 40)), "hotdog": ((96, 26, 22), (180, 60, 50)),
             "brat": ((86, 50, 18), (170, 110, 50))}


@lru_cache(maxsize=64)
def symbol_tile(sym: str, w: int = CW, h: int = CH, glow: bool = True) -> Image.Image:
    """The symbol on a transparent tile of w x h (in output pixels; the tile itself is K times larger)."""
    w, h = P(w), P(h)
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if glow and sym in GLOW:
        g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        r = min(w, h) * 0.32
        ImageDraw.Draw(g).ellipse((w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r), fill=(*GLOW[sym], 95))
        tile.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(10) * w / P(CW))))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    DRAW[sym](ImageDraw.Draw(layer), w, h)
    tile.alpha_composite(layer)
    return tile


@lru_cache(maxsize=8)
def spinning_column(variant: int) -> Image.Image:
    """A reel in motion: random symbols smeared vertically."""
    rng = random.Random(variant)
    w, h = P(CW), P(CH)
    n = E.ROWS + 2
    col = Image.new("RGBA", (w, h * n), (*CELL_BG, 255))
    for i in range(n):
        col.alpha_composite(symbol_tile(rng.choice(E.PAYING + [E.WILD])), (0, i * h))
    steps, span = 14, h * 0.6
    smear = col
    for k in range(1, steps):
        smear = Image.blend(smear, ImageChops.offset(col, 0, int(k * span / steps)), 1 / (k + 1))
    offset = rng.randrange(h)
    return smear.crop((0, offset, w, offset + h * E.ROWS))


# ----------------------------------------------------------------------------- backdrop and helpers
@lru_cache(maxsize=8)
def sky(w: int, h: int, free: bool = False) -> Image.Image:
    top, bottom = (FREE_TOP, FREE_BOTTOM) if free else (SKY_TOP, SKY_BOTTOM)
    img = Image.new("RGBA", (P(w), P(h)))
    d = ImageDraw.Draw(img)
    for y in range(0, P(h), 2):
        t = y / P(h)
        d.rectangle((0, y, P(w), y + 2), fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    rng = random.Random(4)
    for _ in range(50):
        x, y = rng.random() * P(w), rng.random() * P(h) * 0.35
        r = rng.choice((1, 1, 2))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 245, 220, rng.randint(60, 160)))
    for x in range(0, w, 26):   # fence
        d.polygon([(P(x + 2), P(h)), (P(x + 2), P(h - 46)), (P(x + 12), P(h - 54)), (P(x + 22), P(h - 46)),
                   (P(x + 22), P(h))], fill=(58, 28, 18))
    d.rectangle((0, P(h - 34), P(w), P(h - 28)), fill=(46, 22, 14))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))   # string lights
    gd = ImageDraw.Draw(glow)
    for x0, x1 in ((0, w / 2), (w / 2, w)):
        pts = [(x0 + (x1 - x0) * t / 20, 10 + 26 * math.sin(math.pi * t / 20)) for t in range(21)]
        d.line([(P(x), P(y)) for x, y in pts], fill=(30, 20, 20), width=P(2))
        for i, (x, y) in enumerate(pts[1:-1:2]):
            col = [(255, 200, 80), (255, 110, 80), (120, 220, 255), (150, 255, 140)][i % 4]
            gd.ellipse((P(x - 9), P(y - 3), P(x + 9), P(y + 15)), fill=(*col, 140))
            d.ellipse((P(x - 4), P(y + 2), P(x + 4), P(y + 12)), fill=col)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(P(6))))
    return img


def title(d, x, y, text, size=36):
    d.text((P(x), P(y)), text, font=font("bold", size), fill=MUSTARD, anchor="mm", stroke_width=P(4),
           stroke_fill=(150, 25, 15))


def pill(d, x, y, value, fg, bg, outline=None, anchor="m", size=16):
    f = font("bold", size)
    tw = d.textlength(value, font=f)
    w, h = tw + P(28), P(32)
    x0 = x - w / 2 if anchor == "m" else (x - w if anchor == "r" else x)
    d.rounded_rectangle((x0, y - h / 2, x0 + w, y + h / 2), radius=h / 2, fill=bg, outline=outline,
                        width=P(2) if outline else 0)
    d.text((x0 + w / 2, y), value, font=f, fill=fg, anchor="mm")


def glow_box(img, box, color, width=14, blur=8, radius=16):
    g = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(g).rounded_rectangle(box, radius=P(radius), outline=(*color, 255), width=P(width))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(blur))))


def banner(img, d, cx, cy, text, size=26, fill=(150, 30, 10)):
    f = font("bold", size)
    tw = d.textlength(text, font=f)
    g = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(g).rounded_rectangle((cx - tw / 2 - P(40), cy - P(34), cx + tw / 2 + P(40), cy + P(34)),
                                        radius=P(20), fill=(255, 120, 20, 200))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(12))))
    d.rounded_rectangle((cx - tw / 2 - P(30), cy - P(28), cx + tw / 2 + P(30), cy + P(28)), radius=P(16),
                        fill=fill, outline=MUSTARD, width=P(4))
    d.text((cx, cy), text, font=f, fill=WHITE, anchor="mm", stroke_width=P(2), stroke_fill=(80, 10, 0))


def to_png(img: Image.Image, w: int, h: int) -> bytes:
    out = img.convert("RGB").resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def cell_box(r, row, inset=4):
    x, y = GX + r * CW, GY + row * CH
    return (P(x + inset), P(y + inset), P(x + CW - inset), P(y + CH - inset))


def cell_center(r, row):
    return P(GX + r * CW + CW / 2), P(GY + row * CH + CH / 2)


# ----------------------------------------------------------------------------- the reels
@dataclass
class Frame:
    grid: list[list[str]]
    stopped: int = E.REELS                      # reels 0..stopped-1 are shown stopped, the rest spin
    lines: list[int] = field(default_factory=list)
    win_cells: set = field(default_factory=set)
    lit: set = field(default_factory=set)       # cells that just turned wild (Flame Wilds, Hot Reels)
    grill_cells: set = field(default_factory=set)
    mega: tuple | None = None                   # (first reel, size, symbol)
    links: list = field(default_factory=list)   # Twin Links wilds
    banner: str = ""
    bet: int = 0
    win: int = 0
    message: str = ""
    free: tuple | None = None                   # (spin number, total)
    free_name: str = ""
    mult: int | None = None
    respin: int | None = None                   # a reel spinning again (Second Helping)
    spin_seed: int = 0


def render(frame: Frame) -> bytes:
    free = frame.free is not None
    img = sky(W, H, free).copy()
    d = ImageDraw.Draw(img)
    frame_col = FIRE if free else WOOD
    d.rounded_rectangle((P(GX - 14), P(GY - 14), P(GX + GW + 14), P(GY + GH + 14)), radius=P(18), fill=REEL_BG,
                        outline=frame_col, width=P(7))
    title(d, W / 2, 46, frame.free_name.upper() if free and frame.free_name else "BANGER BASH")
    highlight = set(frame.win_cells) | set(frame.lit) | set(frame.grill_cells)
    done = frame.stopped >= E.REELS
    dim = bool(highlight) and done
    mega_cells = set()
    if frame.mega and done:
        r0, size, _ = frame.mega
        mega_cells = {(r, row) for r in range(r0, r0 + size) for row in range(size)}
    links = {tuple(c) for c in frame.links}
    for r in range(E.REELS):
        if r >= frame.stopped or r == frame.respin:
            img.alpha_composite(spinning_column((frame.spin_seed + r) % 8), (P(GX + r * CW), P(GY)))
            continue
        for row in range(E.ROWS):
            if (r, row) in mega_cells:
                continue
            sym = frame.grid[r][row]
            fill, edge = SYMBOL_BG.get(sym, (CELL_BG, CELL_EDGE))
            width = P(2)
            if (r, row) in frame.lit:
                fill, edge, width = (110, 40, 14), FIRE, P(4)
            if (r, row) in links:
                fill, edge, width = (110, 40, 14), MUSTARD, P(4)
            d.rounded_rectangle(cell_box(r, row), radius=P(12), fill=fill, outline=edge, width=width)
            img.alpha_composite(symbol_tile(sym), (P(GX + r * CW), P(GY + row * CH)))
            if dim and (r, row) not in highlight and (r, row) not in links:
                box = cell_box(r, row)
                img.alpha_composite(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (15, 8, 5, 150)),
                                    (box[0], box[1]))
    if mega_cells:
        r0, size, sym = frame.mega
        x0 = GX + r0 * CW
        box = (P(x0 + 4), P(GY + 4), P(x0 + CW * size - 4), P(GY + CH * size - 4))
        glow_box(img, box, (255, 200, 60))
        fill = SYMBOL_BG.get(sym, (CELL_BG, CELL_EDGE))[0]
        d.rounded_rectangle(box, radius=P(16), fill=fill, outline=(255, 220, 110), width=P(4))
        img.alpha_composite(symbol_tile(sym, CW * size, CH * size), (P(x0), P(GY)))
        pill(d, P(x0 + CW * size / 2), P(GY + 22), "MEGA BANGER", (60, 30, 0), MUSTARD, WHITE, size=14)
    for r in range(1, E.REELS):
        if not (mega_cells and frame.mega[0] < r < frame.mega[0] + frame.mega[1]):
            x = P(GX + r * CW)
            d.line((x, P(GY), x, P(GY + GH)), fill=(20, 12, 8), width=P(2))
    if done and (frame.lit or links):
        g = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(g)
        for c in frame.lit:
            gd.rounded_rectangle(cell_box(*c, -2), radius=P(14), outline=(*FIRE, 255), width=P(12))
        for c in links:
            gd.rounded_rectangle(cell_box(*c, -2), radius=P(14), outline=(*MUSTARD, 255), width=P(12))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(9))))
        if len(links) == 2:   # the chain between the Twin Links wilds
            (a, b) = sorted(links)
            p1, p2 = cell_center(*a), cell_center(*b)
            y = p1[1] - P(CH * 0.36)
            for i in range(7):
                x = p1[0] + (p2[0] - p1[0]) * (i + 0.5) / 7
                d.ellipse((x - P(9), y - P(6), x + P(9), y + P(6)), outline=MUSTARD, width=P(3))
            pill(d, (p1[0] + p2[0]) // 2, y, "2x LINK", (60, 30, 0), MUSTARD, WHITE, size=13)
    if done and frame.grill_cells:
        g = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(g)
        for c in frame.grill_cells:
            gd.rounded_rectangle(cell_box(*c, -2), radius=P(14), outline=(255, 170, 60, 255), width=P(12))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(9))))
    if done and frame.lines:
        g = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(g)
        ln = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(ln)
        for i in frame.lines:
            pts = [cell_center(r, E.PAYLINES[i][r]) for r in range(E.REELS)]
            gd.line(pts, fill=(*LINE_COLORS[i], 255), width=P(9), joint="curve")
            ld.line(pts, fill=(20, 10, 5, 220), width=P(8), joint="curve")
            ld.line(pts, fill=(*LINE_COLORS[i], 255), width=P(4), joint="curve")
        for c in frame.win_cells:
            if c not in mega_cells:
                ld.rounded_rectangle(cell_box(*c, 3), radius=P(12), outline=(255, 245, 200, 255), width=P(3))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(5))))
        img.alpha_composite(ln)
    if frame.banner and done:
        banner(img, d, P(W / 2), P(GY + GH / 2), frame.banner)
    pill(d, P(GX - 14), P(46), f"BET {frame.bet:,}", WHITE, (70, 40, 26), WOOD, anchor="l")
    pill(d, P(GX + GW + 14), P(46), f"WIN {frame.win:,}", WHITE, GREEN if frame.win else (70, 40, 26),
         (120, 230, 140) if frame.win else WOOD, anchor="r")
    yf = P(GY + GH + 44)
    if free:
        pill(d, P(GX - 14), yf, f"SPIN {frame.free[0]} / {frame.free[1]}", WHITE, (150, 50, 15), FIRE, anchor="l")
    if frame.mult:
        pill(d, P(GX + GW + 14), yf, f"MULTIPLIER {frame.mult}x", (60, 30, 0), MUSTARD, WHITE, anchor="r")
    if frame.message:
        d.text((P(W / 2 + (30 if free else 0)), yf), frame.message, font=font("bold", 18),
               fill=MUSTARD if (frame.win or frame.banner or highlight) else GREY, anchor="mm", stroke_width=P(2),
               stroke_fill=(30, 10, 5))
    return to_png(img, W, H)


# ----------------------------------------------------------------------------- Grill Bonus ladder
LW, LH = 820, 560


def render_ladder(tier: int, *, chance: float | None, gambles_left: int, stake: int, rtp: float,
                  note: str = "", lost: bool = False, flash: str = "") -> bytes:
    """The five features as rungs with the current one lit. `flash` is 'up' or 'down' right after a gamble."""
    img = sky(LW, LH).copy()
    d = ImageDraw.Draw(img)
    title(d, LW / 2, 46, "GRILL BONUS")
    x0, x1 = 200, 760
    top, gap = 92, 76
    d.rounded_rectangle((P(40), P(top - 10), P(180), P(top + gap * 5 - 6)), radius=P(16), fill=REEL_BG,
                        outline=WOOD, width=P(5))
    lit = 0 if lost else (tier + 1) * 12 // 5
    for i in range(12):   # thermometer
        t = i / 11
        col = (255, int(220 - 170 * t), int(60 - 40 * t))
        y = top + 20 + (1 - t) * (gap * 5 - 70)
        d.ellipse((P(110 - 12), P(y - 12), P(110 + 12), P(y + 12)), fill=col if i < lit else (60, 40, 30))
    d.text((P(110), P(top + gap * 5 - 30)), "HEAT", font=font("bold", 16), fill=MUSTARD, anchor="mm")
    for i, kind in enumerate(reversed(E.TIERS)):
        rung = len(E.TIERS) - 1 - i
        y = top + i * gap
        on = rung == tier and not lost
        nxt = chance is not None and rung == tier + 1 and not lost
        fill = (150, 50, 15) if on else (95, 55, 25) if nxt else (70, 40, 26)
        edge = MUSTARD if on else (FIRE if nxt else WOOD)
        box = (P(x0), P(y), P(x1), P(y + gap - 12))
        if on:
            glow_box(img, box, (255, 160, 40), radius=14)
        d.rounded_rectangle(box, radius=P(14), fill=fill, outline=edge, width=P(4 if on else 2))
        d.text((P(x0 + 22), P(y + 22)), f"{rung + 1}. {E.TIER_NAMES[kind].upper()}", font=font("bold", 20),
               fill=WHITE if on or nxt else GREY, anchor="lm")
        avg = E.credits(stake, E.FEATURE_EV[kind], rtp)
        d.text((P(x0 + 22), P(y + 46)), f"{E.TIER_TEXT[kind]} · avg {avg:,}", font=font("regular", 15),
               fill=(240, 220, 200) if on else GREY, anchor="lm")
        if on:
            label = {"up": "HEAT RISES!", "down": "DROPPED"}.get(flash, "YOUR FEATURE")
            pill(d, P(x1 - 16), P(y + (gap - 12) / 2), label, (60, 30, 0), MUSTARD, WHITE, anchor="r", size=14)
        elif nxt:
            pill(d, P(x1 - 16), P(y + (gap - 12) / 2), f"{chance:.0%} TO CLIMB", WHITE, (150, 50, 15), FIRE,
                 anchor="r", size=14)
    if lost:
        banner(img, d, P(LW / 2), P(top + gap * 2.5), "THE GRILL BURNT OUT!", fill=(90, 20, 10))
    if not note:
        note = (f"Take it · or turn up the heat ({gambles_left} left): climb a rung, or drop one"
                if chance is not None else "Take your feature!")
    d.text((P(LW / 2), P(LH - 70)), note, font=font("bold", 16), fill=MUSTARD, anchor="mm", stroke_width=P(2),
           stroke_fill=(30, 10, 5))
    return to_png(img, LW, LH)


# ----------------------------------------------------------------------------- Hot Potato
def render_potato(state: dict, stake: int, rtp: float, reveal: list | None = None, pick: int | None = None) -> bytes:
    """`reveal`: the three values of the round just played (None = burnt), with `pick` the chosen one."""
    w, h = 820, 520
    img = sky(w, h).copy()
    d = ImageDraw.Draw(img)
    title(d, w / 2, 46, "HOT POTATO")
    gx0, gy0, gx1, gy1 = 70, 96, 750, 400
    d.rounded_rectangle((P(gx0), P(gy0), P(gx1), P(gy1)), radius=P(24), fill=(28, 26, 28), outline=WOOD,
                        width=P(7))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    rng = random.Random(9)
    for _ in range(40):
        x, y = rng.uniform(gx0 + 30, gx1 - 30), rng.uniform(gy0 + 30, gy1 - 30)
        r = rng.uniform(10, 26)
        gd.ellipse((P(x - r), P(y - r), P(x + r), P(y + r)), fill=(255, rng.randint(60, 140), 20, 120))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(P(14))))
    for x in range(gx0 + 30, gx1 - 20, 34):
        d.line((P(x), P(gy0 + 14), P(x), P(gy1 - 14)), fill=(120, 120, 128), width=P(4))
    for i in range(3):
        cx = gx0 + (gx1 - gx0) * (i + 0.5) / 3
        val = reveal[i] if reveal else "?"
        tile = symbol_tile("brat", 200, 180)
        if reveal and val is None:
            tile = _burnt(tile)
        if reveal and i == pick:
            col = (255, 80, 40, 160) if val is None else (255, 200, 60, 150)
            g = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(g).ellipse((P(cx - 95), P(120), P(cx + 95), P(280)), fill=col)
            img.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(16))))
        elif reveal:
            tile = _faded(tile)
        img.alpha_composite(tile, (P(cx - 100), P(160)))
        if not reveal:
            pill(d, P(cx), P(330), "ABC"[i], WHITE, (70, 40, 26), WOOD, size=20)
        elif val is None:
            pill(d, P(cx), P(330), "BURNT!", WHITE, (120, 20, 10) if i == pick else (60, 30, 20), FIRE, size=18)
        else:
            text = f"+{E.credits(stake, val, rtp):,}"
            pill(d, P(cx), P(330), text, (60, 30, 0) if i == pick else GREY, MUSTARD if i == pick else (70, 40, 26),
                 WHITE if i == pick else WOOD, size=18)
    rnd = state["round"] if reveal else state["round"] + 1
    pill(d, P(gx0), P(445), f"ROUND {min(rnd, E.POTATO_ROUNDS)} / {E.POTATO_ROUNDS}", WHITE, (150, 50, 15), FIRE,
         anchor="l")
    pill(d, P(gx1), P(445), f"TOTAL {E.credits(stake, state['total'], rtp):,}", WHITE, GREEN, (120, 230, 140),
         anchor="r")
    msg = ("Round 1 is safe — pick any sausage!" if state["round"] == 0 and not reveal
           else "One of these three is burnt!" if not reveal else "")
    if msg:
        d.text((P(w / 2), P(445)), msg, font=font("bold", 17), fill=MUSTARD, anchor="mm", stroke_width=P(2),
               stroke_fill=(30, 10, 5))
    return to_png(img, w, h)


def _burnt(tile: Image.Image) -> Image.Image:
    r, g, b, a = tile.split()
    dark = Image.merge("RGBA", (r.point(lambda v: v * 0.25), g.point(lambda v: v * 0.18),
                                b.point(lambda v: v * 0.15), a))
    return dark


def _faded(tile: Image.Image) -> Image.Image:
    r, g, b, a = tile.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: v * 0.45)))


# ----------------------------------------------------------------------------- Picnic Pick
def render_picnic(state: dict, stake: int, rtp: float, last: int | None = None) -> bytes:
    w, h = 820, 560
    img = sky(w, h).copy()
    d = ImageDraw.Draw(img)
    title(d, w / 2, 46, "PICNIC PICK")
    cols, rows = 5, 3
    bw, bh, gap = 132, 112, 14
    x0 = (w - (cols * bw + (cols - 1) * gap)) / 2
    y0 = 92
    opened = {int(k): v for k, v in state["opened"].items()}
    every = state.get("all")
    for i in range(E.PICNIC_CELLS):
        x, y = x0 + (i % cols) * (bw + gap), y0 + (i // cols) * (bh + gap)
        box = (P(x), P(y), P(x + bw), P(y + bh))
        if i in opened or every:
            v = opened.get(i, every[i] if every else None)
            mine = i in opened
            if i == last:
                glow_box(img, box, (255, 80, 40) if v == "ants" else (255, 200, 60), radius=14)
            fill = (110, 30, 20) if v == "ants" else (60, 90, 40) if v == "double" else (90, 60, 25)
            if not mine:
                fill = (45, 30, 22)
            d.rounded_rectangle(box, radius=P(14), fill=fill, outline=MUSTARD if mine else WOOD_DARK, width=P(3))
            if v == "ants":
                _ants(d, x + bw / 2, y + bh / 2 - 6, mine)
                text = "ANTS!"
            elif v == "double":
                text = "DOUBLE"
            else:
                text = f"{E.credits(stake, v, rtp):,}"
            d.text((P(x + bw / 2), P(y + bh / 2 + (28 if v == "ants" else 0))), text,
                   font=font("bold", 22 if v != "ants" else 16),
                   fill=(WHITE if mine else GREY), anchor="mm", stroke_width=P(2), stroke_fill=(30, 10, 5))
        else:
            _hamper(d, x, y, bw, bh)
            d.text((P(x + 16), P(y + 16)), str(i + 1), font=font("bold", 15), fill=WHITE, anchor="mm",
                   stroke_width=P(2), stroke_fill=(30, 10, 5))
    yb = y0 + rows * (bh + gap) + 20
    pill(d, P(x0), P(yb), "DOUBLED x2" if state["doubled"] else "1 DOUBLER HIDDEN", (60, 30, 0) if state["doubled"]
         else WHITE, MUSTARD if state["doubled"] else (70, 40, 26), WHITE if state["doubled"] else WOOD, anchor="l")
    pill(d, P(w - x0), P(yb), f"TOTAL {E.credits(stake, state['total'], rtp):,}", WHITE, GREEN, (120, 230, 140),
         anchor="r")
    if not state["done"]:
        d.text((P(w / 2), P(yb)), "Find the prizes · avoid the 2 ant nests", font=font("bold", 16), fill=MUSTARD,
               anchor="mm", stroke_width=P(2), stroke_fill=(30, 10, 5))
    return to_png(img, w, h)


def _hamper(d, x, y, bw, bh):
    d.rounded_rectangle((P(x), P(y), P(x + bw), P(y + bh)), radius=P(14), fill=(70, 40, 26), outline=WOOD, width=P(3))
    bx0, by0, bx1, by1 = x + bw * 0.18, y + bh * 0.38, x + bw * 0.82, y + bh * 0.82
    d.arc((P(bx0 + 12), P(y + bh * 0.12), P(bx1 - 12), P(by0 + 24)), 180, 360, fill=(120, 70, 30), width=P(6))
    d.rounded_rectangle((P(bx0), P(by0), P(bx1), P(by1)), radius=P(8), fill=(190, 130, 60),
                        outline=(120, 70, 30), width=P(3))
    for k in range(1, 4):
        yy = by0 + (by1 - by0) * k / 4
        d.line((P(bx0 + 4), P(yy), P(bx1 - 4), P(yy)), fill=(150, 95, 40), width=P(2))
    d.rectangle((P(bx0), P(by0 + 8), P(bx1), P(by0 + 18)), fill=(215, 40, 35))
    for k in range(4):
        xx = bx0 + (bx1 - bx0) * (k + 0.5) / 4
        d.rectangle((P(xx - 4), P(by0 + 8), P(xx + 4), P(by0 + 18)), fill=(250, 240, 230))


def _ants(d, cx, cy, bright):
    col = (20, 10, 5) if bright else (60, 40, 30)
    rng = random.Random(5)
    for _ in range(5):
        x, y = cx + rng.uniform(-30, 30), cy + rng.uniform(-14, 10)
        for k in range(3):
            r = 3.6 - k * 0.6
            d.ellipse((P(x + k * 6 - r), P(y - r), P(x + k * 6 + r), P(y + r)), fill=col)
        for k in (-1, 1):
            d.line((P(x + 6), P(y), P(x + 6 + 4), P(y + k * 6)), fill=col, width=P(1))


# ----------------------------------------------------------------------------- Golden Grill
def render_golden(value: int, step: int | None, stake: int, rtp: float, done: bool) -> bytes:
    w, h = 820, 520
    img = sky(w, h, free=True).copy()
    d = ImageDraw.Draw(img)
    title(d, w / 2, 46, "GOLDEN GRILL")
    cx, cy = w / 2, 250
    g = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    heat = min(1.0, value / E.GOLDEN_MAX) ** 0.5
    gd.ellipse((P(cx - 230), P(cy - 150), P(cx + 230), P(cy + 150)),
               fill=(255, int(190 - 80 * heat), 40, 170 if not done else 90))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(P(40))))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i, (dx, s) in enumerate(((-150, 0.7), (150, 0.7), (-75, 0.9), (75, 0.9), (0, 1.1))):
        hgt = 120 * s * (0.6 + heat * 0.6) * (0.6 if done else 1)
        flame(ld, P(cx + dx), P(cy + 120), P(90 * s), P(hgt), (220, 60, 25, 220))
        flame(ld, P(cx + dx), P(cy + 120), P(60 * s), P(hgt * 0.7), (255, 150, 30, 230))
        flame(ld, P(cx + dx), P(cy + 120), P(30 * s), P(hgt * 0.4), (255, 230, 100, 240))
    img.alpha_composite(layer)
    d.rounded_rectangle((P(cx - 300), P(cy + 110), P(cx + 300), P(cy + 140)), radius=P(10), fill=(60, 60, 66),
                        outline=(140, 140, 150), width=P(3))
    d.text((P(cx), P(cy - 30)), f"{value}x", font=font("bold", 96), fill=(255, 225, 90), anchor="mm",
           stroke_width=P(6), stroke_fill=(120, 30, 10))
    if step and not done:
        pill(d, P(cx), P(cy + 50), f"+{step}x", (60, 30, 0), MUSTARD, WHITE, size=24)
    yb = 440
    pill(d, P(70), P(yb), f"MAX {E.GOLDEN_MAX}x", WHITE, (150, 50, 15), FIRE, anchor="l")
    pill(d, P(w - 70), P(yb), f"WIN {E.credits(stake, value, rtp):,}", WHITE, GREEN, (120, 230, 140), anchor="r")
    d.text((P(w / 2), P(yb)), "The grill has cooled!" if done else "Turning up the heat...", font=font("bold", 18),
           fill=MUSTARD, anchor="mm", stroke_width=P(2), stroke_fill=(30, 10, 5))
    return to_png(img, w, h)


# ----------------------------------------------------------------------------- paytable
@lru_cache(maxsize=4)
def render_symbols(rtp: float) -> bytes:
    """All symbols with their pays as times the total bet."""
    order = ["king", "hotdog", "brat", "chorizo", "onion", "pickle", "mustard", "ketchup", E.WILD, E.SCATTER]
    cols, tw, th = 5, 150, 200
    w, h = cols * tw + 60, 2 * th + 110
    img = sky(w, h).copy()
    d = ImageDraw.Draw(img)
    title(d, w / 2, 42, "BANGER BASH · PAYTABLE", 30)
    s = E.scale(rtp)
    for i, sym in enumerate(order):
        x, y = 30 + (i % cols) * tw, 84 + (i // cols) * th
        fill, edge = SYMBOL_BG.get(sym, (CELL_BG, CELL_EDGE))
        d.rounded_rectangle((P(x + 6), P(y), P(x + tw - 6), P(y + 130)), radius=P(14), fill=fill, outline=edge,
                            width=P(2))
        img.alpha_composite(symbol_tile(sym, tw - 12, 130), (P(x + 6), P(y)))
        d.text((P(x + tw / 2), P(y + 148)), E.SYMBOLS[sym][0], font=font("bold", 15), fill=WHITE, anchor="mm")
        if sym == E.WILD:
            text = "Stands in for all but Grill"
        elif sym == E.SCATTER:
            text = "3+ anywhere = Grill Bonus"
        else:
            text = " · ".join(f"{n}: {_mult(p * s / E.LINES)}" for n, p in zip((3, 4, 5), E.SYMBOLS[sym][2]))
        d.text((P(x + tw / 2), P(y + 170)), text, font=font("regular", 12), fill=GREY, anchor="mm")
    return to_png(img, w, h)


def _mult(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") + "x"


@lru_cache(maxsize=1)
def render_paylines() -> bytes:
    cols, cell, gap = 5, 16, 26
    mw, mh = cell * E.REELS, cell * E.ROWS
    w = cols * mw + (cols + 1) * gap
    rows = math.ceil(E.LINES / cols)
    h = 60 + rows * (mh + 30) + gap
    img = Image.new("RGB", (P(w), P(h)), SKY_TOP)
    d = ImageDraw.Draw(img)
    d.text((P(w / 2), P(30)), "20 PAYLINES", font=font("bold", 20), fill=MUSTARD, anchor="mm")
    f = font("bold", 12)
    for i, line in enumerate(E.PAYLINES):
        cx = gap + (i % cols) * (mw + gap)
        cy = 60 + (i // cols) * (mh + 30) + 16
        col = LINE_COLORS[i]
        d.text((P(cx + mw / 2), P(cy - 9)), f"Line {i + 1}", font=f, fill=col, anchor="mm")
        for r in range(E.REELS):
            for row in range(E.ROWS):
                x0, y0 = cx + r * cell, cy + row * cell
                d.rectangle((P(x0 + 1), P(y0 + 1), P(x0 + cell - 1), P(y0 + cell - 1)),
                            fill=col if line[r] == row else (40, 30, 50))
    buf = io.BytesIO()
    img.resize((w, h), Image.LANCZOS).save(buf, "PNG")
    return buf.getvalue()
