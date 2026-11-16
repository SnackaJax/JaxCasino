"""Draws the Cosmic Lab slot machine as a PNG with Pillow.

Symbols are drawn with shapes (no emoji font needed), once per size, and cached. Everything is drawn at 2x
and scaled down for smooth edges. The functions take plain data so they can run in a worker thread.
"""
import io
import math
import random
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from utils import slot_engine as E
from utils.table_image import K, font

CW, CH = 120, 108           # cell size
GX, GY = 60, 92             # grid top-left
GW, GH = CW * E.REELS, CH * E.ROWS
W = GX * 2 + GW
H = GY + GH + 70

BG_TOP = (12, 10, 34)
BG_BOTTOM = (28, 12, 52)
FRAME = (92, 74, 160)
REEL_BG = (20, 18, 44)
CELL_BG = (34, 30, 66)
CELL_EDGE = (52, 46, 92)
WHITE = (245, 245, 250)
GREY = (150, 150, 175)
GOLD = (250, 200, 70)
PURPLE = (170, 90, 255)
CYAN = (70, 220, 255)

LINE_COLORS = [
    (255, 90, 90), (90, 200, 255), (120, 240, 120), (255, 200, 60), (255, 120, 220),
    (160, 130, 255), (255, 150, 70), (80, 240, 210), (230, 230, 90), (255, 110, 150),
    (110, 170, 255), (190, 255, 110), (255, 170, 120), (200, 120, 255), (110, 255, 170),
    (255, 230, 150), (150, 220, 255), (255, 140, 190), (180, 200, 120), (240, 170, 255),
]


@dataclass
class SlotFrame:
    grid: list[list[str]]
    stopped: int = E.REELS                      # reels 0..stopped-1 are shown stopped, the rest spin
    lines: list[int] = field(default_factory=list)          # winning paylines to draw
    win_cells: set[tuple[int, int]] = field(default_factory=set)
    sticky: set[tuple[int, int]] = field(default_factory=set)
    scatter_cells: set[tuple[int, int]] = field(default_factory=set)  # comets that started/added free spins
    new_wilds: set[tuple[int, int]] = field(default_factory=set)      # wilds that just landed in free spins
    bet: int = 0
    win: int = 0
    free: tuple[int, int] | None = None         # (spin number, total spins)
    message: str = ""
    spin_seed: int = 0


def P(v: float) -> int:
    return int(round(v * K))


def rot_poly(cx, cy, rx, ry, angle_deg, n=28):
    a = math.radians(angle_deg)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)))
    return pts


# ----------------------------------------------------------------------------- symbols
# Each draws a symbol centred in a tile of size (w, h), in tile pixels (already scaled by K).
def _battery(d, w, h):
    cx, cy = w / 2, h / 2
    bw, bh = w * 0.34, h * 0.62
    d.rounded_rectangle((cx - bw * 0.3, cy - bh / 2 - h * 0.06, cx + bw * 0.3, cy - bh / 2 + 2), radius=P(3),
                        fill=(190, 190, 205))
    d.rounded_rectangle((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2), radius=P(8), fill=(60, 64, 80),
                        outline=(200, 205, 220), width=P(3))
    pad = P(6)
    seg = (bh - pad * 2 - P(8)) / 3
    for i in range(3):
        y1 = cy + bh / 2 - pad - i * (seg + P(4))
        d.rounded_rectangle((cx - bw / 2 + pad, y1 - seg, cx + bw / 2 - pad, y1), radius=P(3),
                            fill=(80, 230, 110) if i < 3 else (40, 50, 50))
    d.polygon([(cx + P(4), cy - bh * 0.32), (cx - P(9), cy + P(2)), (cx, cy + P(2)), (cx - P(4), cy + bh * 0.3),
               (cx + P(9), cy - P(4)), (cx, cy - P(4))], fill=(255, 255, 255))


def _tube(d, w, h):
    cx, cy = w / 2, h / 2
    tw, th = w * 0.2, h * 0.64
    top, bottom = cy - th / 2, cy + th / 2
    glass = (200, 235, 255)
    liquid = (240, 70, 200)
    # liquid
    lq = top + th * 0.42
    d.rectangle((cx - tw / 2, lq, cx + tw / 2, bottom - tw / 2), fill=liquid)
    d.ellipse((cx - tw / 2, bottom - tw, cx + tw / 2, bottom), fill=liquid)
    d.ellipse((cx - tw / 2, lq - P(4), cx + tw / 2, lq + P(4)), fill=(255, 130, 230))
    for bx, by, br in ((-0.18, 0.62, 5), (0.15, 0.75, 4), (-0.05, 0.86, 3), (0.2, 0.55, 3)):
        x, y = cx + bx * tw * 2, top + by * th
        d.ellipse((x - P(br), y - P(br), x + P(br), y + P(br)), outline=(255, 210, 245), width=P(2))
    # glass outline
    d.line((cx - tw / 2, top, cx - tw / 2, bottom - tw / 2), fill=glass, width=P(3))
    d.line((cx + tw / 2, top, cx + tw / 2, bottom - tw / 2), fill=glass, width=P(3))
    d.arc((cx - tw / 2, bottom - tw, cx + tw / 2, bottom), 0, 180, fill=glass, width=P(3))
    d.rounded_rectangle((cx - tw / 2 - P(6), top - P(5), cx + tw / 2 + P(6), top + P(4)), radius=P(3), fill=glass)
    d.line((cx - tw / 2 + P(6), top + P(10), cx - tw / 2 + P(6), lq - P(6)), fill=(255, 255, 255), width=P(3))


def _magnet(d, w, h):
    cx, cy = w / 2, h / 2 + h * 0.04
    r = w * 0.24
    thick = P(17)
    red = (230, 50, 60)
    d.arc((cx - r, cy - r * 0.2 - r, cx + r, cy - r * 0.2 + r), 0, 180, fill=red, width=thick)
    leg_top = cy - h * 0.3
    for side in (-1, 1):
        x0 = cx + side * r - (thick if side == 1 else 0)
        x1 = x0 + thick
        d.rectangle((x0, leg_top + P(14), x1, cy - r * 0.2), fill=red)
        d.rectangle((x0, leg_top, x1, leg_top + P(14)), fill=(215, 220, 230))
    d.arc((cx - r + P(5), cy - r * 0.2 - r + P(5), cx + r - P(5), cy - r * 0.2 + r - P(5)), 30, 80,
          fill=(255, 140, 140), width=P(3))


def _bolt(d, w, h):
    cx, cy = w / 2, h / 2
    s = h * 0.36
    pts = [(cx + s * 0.25, cy - s), (cx - s * 0.55, cy + s * 0.12), (cx - s * 0.02, cy + s * 0.12),
           (cx - s * 0.3, cy + s), (cx + s * 0.58, cy - s * 0.18), (cx + s * 0.05, cy - s * 0.18)]
    d.polygon(pts, fill=(255, 215, 40), outline=(255, 140, 20), width=P(3))
    d.line([(cx + s * 0.18, cy - s * 0.78), (cx - s * 0.32, cy + s * 0.02)], fill=(255, 250, 200), width=P(3))


def _planet(d, w, h):
    cx, cy = w / 2, h / 2
    r = h * 0.26
    ring = (240, 210, 150)
    box = (cx - r * 1.85, cy - r * 0.55, cx + r * 1.85, cy + r * 0.55)
    d.arc(box, 180, 360, fill=ring, width=P(5))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(235, 130, 60))
    for i, (dy, hh, col) in enumerate(((-0.45, 0.16, (250, 175, 100)), (0.05, 0.2, (200, 95, 45)),
                                       (0.5, 0.14, (250, 165, 90)))):
        y = cy + dy * r
        half = math.sqrt(max(0.0, r * r - (dy * r) ** 2))
        d.rectangle((cx - half + P(2), y - hh * r / 2, cx + half - P(2), y + hh * r / 2), fill=col)
    d.ellipse((cx - r * 0.6, cy - r * 0.7, cx - r * 0.2, cy - r * 0.35), fill=(255, 200, 150))
    d.arc(box, 0, 180, fill=ring, width=P(5))


def _robot(d, w, h):
    cx, cy = w / 2, h / 2 + h * 0.06
    hw, hh = w * 0.4, h * 0.44
    d.line((cx, cy - hh / 2, cx, cy - hh / 2 - h * 0.13), fill=(200, 205, 220), width=P(3))
    d.ellipse((cx - P(6), cy - hh / 2 - h * 0.13 - P(6), cx + P(6), cy - hh / 2 - h * 0.13 + P(6)),
              fill=(255, 70, 70))
    for side in (-1, 1):
        x = cx + side * hw / 2
        d.rounded_rectangle((x - P(6) if side == 1 else x - P(6), cy - P(10), x + P(6), cy + P(10)), radius=P(3),
                            fill=(130, 140, 165))
    d.rounded_rectangle((cx - hw / 2, cy - hh / 2, cx + hw / 2, cy + hh / 2), radius=P(10), fill=(175, 185, 210),
                        outline=(225, 230, 245), width=P(3))
    for side in (-1, 1):
        ex = cx + side * hw * 0.22
        d.ellipse((ex - P(9), cy - hh * 0.2 - P(9), ex + P(9), cy - hh * 0.2 + P(9)), fill=(25, 30, 50))
        d.ellipse((ex - P(5), cy - hh * 0.2 - P(5), ex + P(5), cy - hh * 0.2 + P(5)), fill=CYAN)
    my = cy + hh * 0.22
    d.rounded_rectangle((cx - hw * 0.28, my - P(6), cx + hw * 0.28, my + P(6)), radius=P(3), fill=(40, 45, 65))
    for i in range(1, 4):
        x = cx - hw * 0.28 + i * hw * 0.56 / 4
        d.line((x, my - P(6), x, my + P(6)), fill=(150, 160, 185), width=P(2))


def _ufo(d, w, h):
    cx, cy = w / 2, h / 2 + h * 0.04
    sw, sh = w * 0.38, h * 0.12
    d.polygon([(cx - sw * 0.45, cy + sh), (cx + sw * 0.45, cy + sh), (cx + sw * 0.75, cy + h * 0.36),
               (cx - sw * 0.75, cy + h * 0.36)], fill=(120, 255, 170, 70))
    d.ellipse((cx - sw * 0.5, cy - sh * 2.6, cx + sw * 0.5, cy + sh * 0.6), fill=(110, 220, 255),
              outline=(200, 245, 255), width=P(2))
    d.ellipse((cx - sw * 0.3, cy - sh * 2.2, cx - sw * 0.05, cy - sh * 1.3), fill=(220, 250, 255))
    d.ellipse((cx - sw, cy - sh, cx + sw, cy + sh), fill=(150, 160, 185), outline=(220, 225, 240), width=P(3))
    for i in range(5):
        x = cx - sw * 0.7 + i * sw * 0.35
        d.ellipse((x - P(4), cy - P(3), x + P(4), cy + P(5)), fill=GOLD)


def _alien(d, w, h):
    cx, cy = w / 2, h / 2
    green = (110, 230, 90)
    d.ellipse((cx - w * 0.22, cy - h * 0.36, cx + w * 0.22, cy + h * 0.12), fill=green)
    d.polygon([(cx - w * 0.2, cy - h * 0.04), (cx + w * 0.2, cy - h * 0.04), (cx + w * 0.06, cy + h * 0.3),
               (cx - w * 0.06, cy + h * 0.3)], fill=green)
    d.ellipse((cx - w * 0.07, cy + h * 0.22, cx + w * 0.07, cy + h * 0.33), fill=green)
    for side in (-1, 1):
        d.polygon(rot_poly(cx + side * w * 0.095, cy - h * 0.04, w * 0.085, h * 0.05, side * 28), fill=(15, 15, 25))
        d.ellipse((cx + side * w * 0.09 - P(3) - side * P(3), cy - h * 0.07 - P(3),
                   cx + side * w * 0.09 + P(3) - side * P(3), cy - h * 0.07 + P(3)), fill=(255, 255, 255))
    d.ellipse((cx - w * 0.12, cy - h * 0.3, cx - w * 0.02, cy - h * 0.2), fill=(170, 250, 150))
    d.arc((cx - P(7), cy + h * 0.12, cx + P(7), cy + h * 0.2), 20, 160, fill=(40, 90, 40), width=P(2))


def _label(d, w, y, value, fill, outline):
    f = font("bold", 17)
    d.text((w / 2, y), value, font=f, fill=fill, anchor="mm", stroke_width=P(3), stroke_fill=outline)


def _wild(d, w, h):
    cx, cy = w / 2, h / 2 - h * 0.08
    for i, col in enumerate([(120, 40, 220), (150, 70, 255), (90, 160, 255), (80, 220, 255), (200, 240, 255)]):
        r = h * (0.32 - i * 0.055)
        start = i * 70
        d.arc((cx - r, cy - r, cx + r, cy + r), start, start + 260, fill=col, width=P(6))
    d.ellipse((cx - P(6), cy - P(6), cx + P(6), cy + P(6)), fill=(255, 255, 255))
    _label(d, w, h * 0.84, "WILD", (255, 255, 255), (120, 40, 220))


def _comet(d, w, h):
    hx, hy = w * 0.62, h * 0.36
    for i in range(14, 0, -1):
        t = i / 14
        x, y = hx - t * w * 0.42, hy + t * h * 0.32
        r = h * 0.15 * (1 - t * 0.75)
        a = int(200 * (1 - t))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 150 + int(80 * (1 - t)), 60, a))
    r = h * 0.15
    d.ellipse((hx - r, hy - r, hx + r, hy + r), fill=(255, 220, 120))
    d.ellipse((hx - r * 0.6, hy - r * 0.6, hx + r * 0.6, hy + r * 0.6), fill=(255, 255, 230))
    _label(d, w, h * 0.84, "BONUS", (255, 230, 120), (180, 60, 20))


DRAW = {"battery": _battery, "tube": _tube, "magnet": _magnet, "bolt": _bolt, "planet": _planet,
        "robot": _robot, "ufo": _ufo, "alien": _alien, E.WILD: _wild, E.SCATTER: _comet}
GLOW = {"alien": (255, 80, 90), "ufo": (255, 215, 70), "robot": (80, 230, 120), "planet": (235, 140, 60),
        E.WILD: (170, 90, 255), E.SCATTER: (255, 170, 60)}
# The three highest symbols sit on a coloured cell: (fill, edge).
SYMBOL_BG = {"alien": ((92, 22, 36), (170, 50, 66)), "ufo": ((84, 70, 14), (170, 145, 40)),
             "robot": ((20, 76, 40), (50, 150, 85))}
NEW_WILD = (90, 240, 255)


@lru_cache(maxsize=32)
def symbol_tile(sym: str) -> Image.Image:
    """The symbol on a transparent tile of one cell (at K scale)."""
    w, h = P(CW), P(CH)
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if sym in GLOW:
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        r = h * 0.3
        gd.ellipse((w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r), fill=(*GLOW[sym], 90))
        tile.alpha_composite(glow.filter(ImageFilter.GaussianBlur(P(10))))
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
    # Motion blur: average copies shifted down a little further each time.
    steps, span = 14, h * 0.55
    smear = col
    for k in range(1, steps):
        smear = Image.blend(smear, ImageChops.offset(col, 0, int(k * span / steps)), 1 / (k + 1))
    offset = rng.randrange(h)
    return smear.crop((0, offset, w, offset + h * E.ROWS))


@lru_cache(maxsize=1)
def background() -> Image.Image:
    w, h = P(W), P(H)
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 2):
        t = y / h
        col = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
        d.rectangle((0, y, w, y + 2), fill=col)
    rng = random.Random(7)
    for _ in range(140):
        x, y, r = rng.random() * w, rng.random() * h, rng.choice((1, 1, 1, 2, 2, 3))
        a = rng.randint(80, 220)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, a))
    # reel frame
    d.rounded_rectangle((P(GX - 12), P(GY - 12), P(GX + GW + 12), P(GY + GH + 12)), radius=P(18),
                        fill=REEL_BG, outline=FRAME, width=P(4))
    title = font("bold", 34)
    d.text((P(W / 2), P(40)), "COSMIC LAB", font=title, fill=GOLD, anchor="mm",
           stroke_width=P(3), stroke_fill=(110, 50, 10))
    return img


def cell_box(r: int, row: int, inset: float = 4):
    x, y = GX + r * CW, GY + row * CH
    return (P(x + inset), P(y + inset), P(x + CW - inset), P(y + CH - inset))


def cell_center(r: int, row: int) -> tuple[int, int]:
    return P(GX + r * CW + CW / 2), P(GY + row * CH + CH / 2)


def _pill(d, x, y, value, f, fg, bg, outline=None, anchor="m"):
    tw = d.textlength(value, font=f)
    w, h = tw + P(28), P(32)
    x0 = x - w / 2 if anchor == "m" else (x - w if anchor == "r" else x)
    d.rounded_rectangle((x0, y - h / 2, x0 + w, y + h / 2), radius=h / 2, fill=bg,
                        outline=outline, width=P(2) if outline else 0)
    d.text((x0 + w / 2, y), value, font=f, fill=fg, anchor="mm")


def _line_badges(d, lit: set[int]):
    """Payline numbers down both sides of the reels, next to the row each line starts and ends on."""
    f = font("bold", 11)
    per_side = {0: {}, 1: {}}
    for i, line in enumerate(E.PAYLINES):
        per_side[0].setdefault(line[0], []).append(i)
        per_side[1].setdefault(line[-1], []).append(i)
    for side, rows in per_side.items():
        x = GX - 34 if side == 0 else GX + GW + 34
        for row, ids in rows.items():
            gap = CH / (len(ids) + 1)
            for j, i in enumerate(ids):
                y = GY + row * CH + gap * (j + 1)
                on = i in lit
                col = LINE_COLORS[i]
                bg = col if on else (40, 36, 70)
                d.rounded_rectangle((P(x - 13), P(y - 9), P(x + 13), P(y + 9)), radius=P(5), fill=bg,
                                    outline=col, width=P(2))
                d.text((P(x), P(y)), str(i + 1), font=f, fill=(20, 15, 30) if on else col, anchor="mm")


def render(frame: SlotFrame) -> bytes:
    img = background().copy()
    d = ImageDraw.Draw(img)
    highlight = frame.win_cells | frame.scatter_cells
    dim = bool(highlight) and frame.stopped >= E.REELS

    for r in range(E.REELS):
        if r >= frame.stopped:
            col = spinning_column((frame.spin_seed + r) % 8)
            img.alpha_composite(col, (P(GX + r * CW), P(GY)))
            for row in range(E.ROWS):  # sticky wilds stay put while the reel spins
                if (r, row) in frame.sticky:
                    d.rounded_rectangle(cell_box(r, row), radius=P(12), fill=(48, 34, 86), outline=GOLD, width=P(3))
                    img.alpha_composite(symbol_tile(E.WILD), (P(GX + r * CW), P(GY + row * CH)))
            continue
        for row in range(E.ROWS):
            sym = frame.grid[r][row]
            box = cell_box(r, row)
            sticky = (r, row) in frame.sticky
            new = (r, row) in frame.new_wilds
            fill, edge, width = (*SYMBOL_BG.get(sym, (CELL_BG, CELL_EDGE)), P(2))
            if sticky:
                fill, edge, width = (48, 34, 86), GOLD, P(3)
            if new:
                fill, edge, width = (18, 58, 92), NEW_WILD, P(4)
            d.rounded_rectangle(box, radius=P(12), fill=fill, outline=edge, width=width)
            img.alpha_composite(symbol_tile(sym), (P(GX + r * CW), P(GY + row * CH)))
            if dim and (r, row) not in highlight and not sticky and not new:
                shade = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (10, 8, 25, 150))
                img.alpha_composite(shade, (box[0], box[1]))
    for r in range(1, E.REELS):  # reel separators
        x = P(GX + r * CW)
        d.line((x, P(GY), x, P(GY + GH)), fill=(14, 12, 32), width=P(2))

    if frame.new_wilds and frame.stopped >= E.REELS:
        # Wilds that just landed in free spins: a bright glow, a thick border and a NEW tag.
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for (r, row) in frame.new_wilds:
            gd.rounded_rectangle(cell_box(r, row, -2), radius=P(14), outline=(*NEW_WILD, 255), width=P(12))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(P(9))))
        tag_f = font("bold", 12)
        for (r, row) in frame.new_wilds:
            d.rounded_rectangle(cell_box(r, row, 3), radius=P(12), outline=(235, 255, 255), width=P(3))
            cx = GX + r * CW + CW / 2
            ty = GY + row * CH + 14
            d.rounded_rectangle((P(cx - 24), P(ty - 9), P(cx + 24), P(ty + 9)), radius=P(9), fill=NEW_WILD,
                                outline=(255, 255, 255), width=P(2))
            d.text((P(cx), P(ty)), "NEW", font=tag_f, fill=(10, 30, 50), anchor="mm")

    if frame.stopped >= E.REELS and (frame.lines or highlight):
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for (r, row) in frame.scatter_cells:
            gd.rounded_rectangle(cell_box(r, row, 2), radius=P(12), outline=(255, 170, 60, 255), width=P(6))
        for i in frame.lines:
            pts = [cell_center(r, E.PAYLINES[i][r]) for r in range(E.REELS)]
            gd.line(pts, fill=(*LINE_COLORS[i], 255), width=P(8), joint="curve")
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(P(5))))
        lines = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lines)
        for i in frame.lines:
            pts = [cell_center(r, E.PAYLINES[i][r]) for r in range(E.REELS)]
            ld.line(pts, fill=(15, 10, 25, 220), width=P(8), joint="curve")
            ld.line(pts, fill=(*LINE_COLORS[i], 255), width=P(4), joint="curve")
        for (r, row) in frame.win_cells:
            ld.rounded_rectangle(cell_box(r, row, 3), radius=P(12), outline=(255, 245, 200, 255), width=P(3))
        for (r, row) in frame.scatter_cells:
            ld.rounded_rectangle(cell_box(r, row, 3), radius=P(12), outline=(255, 200, 90, 255), width=P(3))
        img.alpha_composite(lines)

    _line_badges(d, set(frame.lines))

    f = font("bold", 16)
    yb = P(40)
    _pill(d, P(GX - 12), yb, f"BET {frame.bet:,}", f, WHITE, (40, 36, 80), FRAME, anchor="l")
    win_bg = (40, 110, 60) if frame.win else (40, 36, 80)
    _pill(d, P(GX + GW + 12), yb, f"WIN {frame.win:,}", f, WHITE, win_bg, (110, 220, 140) if frame.win else FRAME,
          anchor="r")
    yf = P(GY + GH + 42)
    if frame.free:
        n, total = frame.free
        _pill(d, P(GX - 12), yf, f"FREE SPIN {n} / {total}", f, WHITE, (90, 40, 160), PURPLE, anchor="l")
    if frame.message:
        mf = font("bold", 18)
        x = P(W / 2 + (60 if frame.free else 0))
        color = NEW_WILD if frame.new_wilds and not frame.lines else (
            GOLD if frame.win or frame.scatter_cells or frame.new_wilds else GREY)
        d.text((x, yf), frame.message, font=mf, fill=color,
               anchor="mm", stroke_width=P(2), stroke_fill=(20, 10, 30))

    out = img.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


@lru_cache(maxsize=1)
def render_paylines() -> bytes:
    """All 20 paylines as small diagrams."""
    cols, cell, gap = 5, 15, 26
    mw, mh = cell * E.REELS, cell * E.ROWS
    w = cols * mw + (cols + 1) * gap
    rows = math.ceil(E.LINES / cols)
    h = 60 + rows * (mh + 30) + gap
    img = Image.new("RGB", (P(w), P(h)), BG_TOP)
    d = ImageDraw.Draw(img)
    d.text((P(w / 2), P(30)), "COSMIC LAB · 20 PAYLINES", font=font("bold", 20), fill=GOLD, anchor="mm")
    f = font("bold", 12)
    for i, line in enumerate(E.PAYLINES):
        cx = gap + (i % cols) * (mw + gap)
        cy = 60 + (i // cols) * (mh + 30) + 16
        col = LINE_COLORS[i]
        d.text((P(cx + mw / 2), P(cy - 9)), f"Line {i + 1}", font=f, fill=col, anchor="mm")
        for r in range(E.REELS):
            for row in range(E.ROWS):
                on = line[r] == row
                x0, y0 = cx + r * cell, cy + row * cell
                d.rectangle((P(x0 + 1), P(y0 + 1), P(x0 + cell - 1), P(y0 + cell - 1)),
                            fill=col if on else (40, 36, 70))
    buf = io.BytesIO()
    img.resize((w, h), Image.LANCZOS).save(buf, "PNG")
    return buf.getvalue()
