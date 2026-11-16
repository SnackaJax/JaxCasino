"""Draws the Spin Frenzy Activity art for the Discord Developer Portal (Activities -> Art Assets).

    python activity/assets/make_assets.py

Writes cover.png (cover art, with the logo) and background.png (no text, calmer, for behind other content),
both 1024x576 (16:9). Everything is drawn here from shapes and Windows fonts, so the art is original.
The colors match activity/static/theme.css; change them below, and the wheel layout comes from the game itself.
"""
import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from activity.engine import WHEEL  # noqa: E402  (the real wheel order)

SS = 2                      # drawn at 2x, then scaled down for smooth edges
OUT_W, OUT_H = 1024, 576
W, H = OUT_W * SS, OUT_H * SS

FONTS = "C:/Windows/Fonts/"
F_BLACK = FONTS + "seguibl.ttf"      # Segoe UI Black
F_BOLD = FONTS + "segoeuib.ttf"
F_SYMBOL = FONTS + "seguisym.ttf"

SPOT = {"1": "#2dd4bf", "2": "#f59e0b", "5": "#84cc16", "10": "#f43f5e", "coin": "#facc15", "grid": "#10b981",
        "drop": "#38bdf8", "frenzy": "#a855f7"}
LABEL = {"1": "1", "2": "2", "5": "5", "10": "10", "coin": "COIN", "grid": "GRID", "drop": "DROP", "frenzy": "FRENZY"}
CYAN, MAGENTA, GOLD, GOLD_LIGHT, GOLD_DARK = "#45e3ff", "#ff4fd8", "#f5b700", "#fff1a8", "#8a5a00"
CHIP_COLORS = [("#16a34a", "100"), ("#2563eb", "50"), ("#9333ea", "500"), ("#ea580c", "1K"), ("#db2777", "5K"),
               ("#0891b2", "25K"), ("#111827", "100K"), ("#dc2626", "10")]


def rgb(c, a=255):
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


def mix(c1, c2, t):
    a, b = rgb(c1), rgb(c2)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)


def font(path, px):
    return ImageFont.truetype(path, int(px))


def layer():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def over(base, top):
    base.alpha_composite(top)


def glow(base, lay, radius, strength=1):
    """Adds a blurred copy of `lay` (a glow), `strength` times."""
    g = lay.filter(ImageFilter.GaussianBlur(radius))
    for _ in range(strength):
        base.alpha_composite(g)


def radial_alpha(size, inner=1.0, outer=0.0, power=1.6):
    """An L-mode mask: `inner` at the centre fading to `outer` at the edge."""
    w, h = size
    y, x = np.ogrid[-1:1:complex(0, h), -1:1:complex(0, w)]
    d = np.clip(np.sqrt(x * x + y * y), 0, 1) ** power
    a = inner + (outer - inner) * d
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L")


def blob(color, size, alpha=1.0, power=1.6):
    img = Image.new("RGBA", size, rgb(color))
    img.putalpha(radial_alpha(size, alpha, 0, power))
    return img


def vertical_gradient(size, top, bottom):
    w, h = size
    t = np.linspace(0, 1, h)[:, None, None]
    a, b = np.array(rgb(top), float), np.array(rgb(bottom), float)
    arr = (a + (b - a) * t) * np.ones((1, w, 1))
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


# ------------------------------------------------------------------ scene pieces
def backdrop(seed):
    """Dark plum room, art-deco lattice wallpaper, coloured light pools."""
    random.seed(seed)
    img = vertical_gradient((W, H), "#1a0b33", "#07040f")
    # lattice wallpaper
    wall = layer()
    d = ImageDraw.Draw(wall)
    step = 90 * SS
    for k in range(-H // step - 2, W // step + 3):
        x = k * step
        d.line([(x, 0), (x + H * 0.6, H)], fill=rgb(GOLD, 22), width=2 * SS)
        d.line([(x, 0), (x - H * 0.6, H)], fill=rgb(GOLD, 22), width=2 * SS)
    for k in range(0, W // step + 2):
        for j in range(0, H // (step // 2) + 2):
            cx, cy = k * step + (step / 2 if j % 2 else 0), j * step * 0.83
            d.ellipse([cx - 3 * SS, cy - 3 * SS, cx + 3 * SS, cy + 3 * SS], fill=rgb(GOLD, 40))
    over(img, wall)
    # light pools
    for color, (x, y), r, a in [(MAGENTA, (0.18, 0.35), 0.55, 0.45), (CYAN, (0.85, 0.25), 0.5, 0.35),
                                ("#7c3aed", (0.55, 0.55), 0.6, 0.5), (GOLD, (0.5, 0.95), 0.6, 0.25)]:
        size = (int(W * r * 2), int(W * r * 1.3))
        b = blob(color, size, a, 1.3)
        img.alpha_composite(b, (int(W * x - size[0] / 2), int(H * y - size[1] / 2)))
    return img


def spotlights(img, beams):
    lay = layer()
    d = ImageDraw.Draw(lay)
    for color, x_top, x_bottom, width, alpha in beams:
        xt, xb, w = x_top * W, x_bottom * W, width * W
        d.polygon([(xt - 8 * SS, -10), (xt + 8 * SS, -10), (xb + w, H), (xb - w, H)], fill=rgb(color, alpha))
    glow(img, lay, 40 * SS)


def bokeh(img, count, seed, area=(0, 0, 1, 1), blur=6):
    random.seed(seed)
    lay = layer()
    d = ImageDraw.Draw(lay)
    colors = [GOLD, GOLD_LIGHT, MAGENTA, CYAN, "#ffffff", "#a78bfa"]
    x0, y0, x1, y1 = area
    for _ in range(count):
        r = random.uniform(5, 30) * SS
        x, y = random.uniform(x0, x1) * W, random.uniform(y0, y1) * H
        c = random.choice(colors)
        d.ellipse([x - r, y - r, x + r, y + r], fill=rgb(c, random.randint(30, 95)),
                  outline=rgb(c, random.randint(60, 140)), width=max(1, int(r / 8)))
    over(img, lay.filter(ImageFilter.GaussianBlur(blur * SS)))


def marquee(img, inset=16, spacing=38, radius=26, phase=0):
    """Casino marquee bulbs around the edge of the picture."""
    inset, spacing, radius = inset * SS, spacing * SS, radius * SS
    x0, y0, x1, y1 = inset, inset, W - inset, H - inset
    pts = []
    per = 2 * ((x1 - x0) + (y1 - y0))
    n = int(per / spacing)
    for i in range(n):
        t = i / n * per
        if t < x1 - x0:
            pts.append((x0 + t, y0))
        elif t < (x1 - x0) + (y1 - y0):
            pts.append((x1, y0 + t - (x1 - x0)))
        elif t < 2 * (x1 - x0) + (y1 - y0):
            pts.append((x1 - (t - (x1 - x0) - (y1 - y0)), y1))
        else:
            pts.append((x0, y1 - (t - 2 * (x1 - x0) - (y1 - y0))))
    frame = layer()
    fd = ImageDraw.Draw(frame)
    fd.rounded_rectangle([x0 - 9 * SS, y0 - 9 * SS, x1 + 9 * SS, y1 + 9 * SS], radius=radius, outline=rgb(GOLD_DARK),
                         width=12 * SS)
    fd.rounded_rectangle([x0 - 9 * SS, y0 - 9 * SS, x1 + 9 * SS, y1 + 9 * SS], radius=radius, outline=rgb(GOLD, 200),
                         width=3 * SS)
    over(img, frame)
    bulbs, halo = layer(), layer()
    bd, hd = ImageDraw.Draw(bulbs), ImageDraw.Draw(halo)
    for i, (x, y) in enumerate(pts):
        on = (i + phase) % 3 != 0
        r = 5.5 * SS
        color = "#fff6d0" if on else "#c58b1a"
        if on:
            hd.ellipse([x - r * 3, y - r * 3, x + r * 3, y + r * 3], fill=rgb("#ffd36b", 150))
        bd.ellipse([x - r, y - r, x + r, y + r], fill=rgb(color))
        bd.ellipse([x - r * 0.45, y - r * 0.6, x + r * 0.1, y - r * 0.1], fill=(255, 255, 255, 220 if on else 90))
    glow(img, halo, 8 * SS, 2)
    over(img, bulbs)


def wheel(radius, rotation_deg=0.0):
    """The Spin Frenzy wheel (real segment order) with rim, bulbs, hub and pointer, on its own layer."""
    R = int(radius)
    pad = int(R * 0.28)
    size = 2 * (R + pad)
    c = size / 2
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    n = len(WHEEL)
    seg = 360 / n
    # back glow
    img.alpha_composite(blob("#c084fc", (size, size), 0.75, 1.1))
    d = ImageDraw.Draw(img)
    rim_r = R * 1.1
    d.ellipse([c - rim_r * 1.03, c - rim_r * 1.03, c + rim_r * 1.03, c + rim_r * 1.03], fill=rgb(GOLD_DARK))
    d.ellipse([c - rim_r, c - rim_r, c + rim_r, c + rim_r], fill=rgb("#2a1f6b"))
    # rim gradient ring
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    for k in range(18):
        t = k / 17
        rr = rim_r - (rim_r - R) * t
        rd.ellipse([c - rr, c - rr, c + rr, c + rr], outline=mix("#6d5cff", "#1b1450", t), width=3 * SS)
    img.alpha_composite(ring)
    # segments
    for i, spot in enumerate(WHEEL):
        a0 = -90 + rotation_deg + i * seg
        d.pieslice([c - R, c - R, c + R, c + R], a0, a0 + seg, fill=rgb(SPOT[spot]))
    # separators
    for i in range(n):
        a = math.radians(-90 + rotation_deg + i * seg)
        d.line([(c, c), (c + math.cos(a) * R, c + math.sin(a) * R)], fill=(0, 0, 0, 90), width=max(1, SS))
    # depth: darken toward the centre, sheen on top
    shade = Image.new("RGBA", (size, size), (10, 4, 30, 255))
    m = radial_alpha((2 * R, 2 * R), 0.7, 0.0, 0.9)
    mask = Image.new("L", (size, size), 0)
    mask.paste(m, (int(c - R), int(c - R)))
    circle = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle).ellipse([c - R, c - R, c + R, c + R], fill=255)
    shade.putalpha(ImageChops.multiply(mask, circle))
    img.alpha_composite(shade)
    sheen = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    sm = Image.new("L", (size, size), 0)
    ImageDraw.Draw(sm).ellipse([c - R * 0.95, c - R * 1.0, c + R * 0.95, c + R * 0.2], fill=34)
    sheen.putalpha(ImageChops.multiply(sm.filter(ImageFilter.GaussianBlur(R * 0.08)), circle))
    img.alpha_composite(sheen)
    # labels, reading outward along each segment
    for i, spot in enumerate(WHEEL):
        mid = -90 + rotation_deg + (i + 0.5) * seg
        is_num = spot in ("1", "2", "5", "10")
        f = font(F_BLACK, R * (0.085 if is_num else 0.05))
        text = LABEL[spot]
        bbox = f.getbbox(text)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        t = Image.new("RGBA", (tw + 8 * SS, th + 8 * SS), (0, 0, 0, 0))
        td = ImageDraw.Draw(t)
        td.text((4 * SS - bbox[0], 4 * SS - bbox[1]), text, font=f, fill=(255, 255, 255, 255),
                stroke_width=max(1, SS), stroke_fill=(0, 0, 0, 120))
        upside_down = 90 < mid % 360 < 270      # left half: turn the text so it isn't upside down
        t = t.rotate(-mid + (180 if upside_down else 0), expand=True, resample=Image.BICUBIC)
        dist = R * 0.94 - tw / 2 - 2 * SS
        x = c + math.cos(math.radians(mid)) * dist - t.width / 2
        y = c + math.sin(math.radians(mid)) * dist - t.height / 2
        img.alpha_composite(t, (int(x), int(y)))
    # rim bulbs
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for i in range(n):
        a = math.radians(-90 + rotation_deg + i * seg)
        x, y = c + math.cos(a) * R * 1.05, c + math.sin(a) * R * 1.05
        r = R * 0.017
        on = i % 3 != 0
        if on:
            hd.ellipse([x - r * 3, y - r * 3, x + r * 3, y + r * 3], fill=rgb("#fff3b0", 170))
        d.ellipse([x - r, y - r, x + r, y + r], fill=rgb("#fff7d6" if on else "#8f80ff"))
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(R * 0.012)))
    # hub
    hr = R * 0.25
    d.ellipse([c - hr * 1.12, c - hr * 1.12, c + hr * 1.12, c + hr * 1.12], fill=rgb(GOLD))
    d.ellipse([c - hr * 1.05, c - hr * 1.05, c + hr * 1.05, c + hr * 1.05], fill=rgb(GOLD_DARK))
    hub = Image.new("RGBA", (int(hr * 2), int(hr * 2)), rgb("#3b2a9f"))
    hub.putalpha(radial_alpha(hub.size, 1, 1, 1))
    hub_grad = vertical_gradient(hub.size, "#5442d6", "#120e38")
    hub_mask = Image.new("L", hub.size, 0)
    ImageDraw.Draw(hub_mask).ellipse([0, 0, hub.size[0] - 1, hub.size[1] - 1], fill=255)
    hub_grad.putalpha(hub_mask)
    img.alpha_composite(hub_grad, (int(c - hr), int(c - hr)))
    d.ellipse([c - hr, c - hr, c + hr, c + hr], outline=rgb(MAGENTA), width=int(R * 0.014))
    for text, color, dy in (("SPIN", CYAN, -0.33), ("FRENZY", MAGENTA, 0.33)):
        f = font(F_BLACK, hr * 0.36)
        d.text((c, c + hr * dy), text, font=f, fill=rgb(color), anchor="mm")
    # pointer at the top
    pr = R * 0.09
    py = c - rim_r - pr * 0.1
    tri = [(c, py + pr * 1.4), (c - pr, py - pr * 0.4), (c + pr, py - pr * 0.4)]
    sh = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(x + 3 * SS, y + 5 * SS) for x, y in tri], fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4 * SS)))
    d.polygon(tri, fill=rgb(GOLD_LIGHT), outline=rgb(GOLD_DARK), width=2 * SS)
    d.polygon([(c, py + pr * 1.1), (c - pr * 0.45, py), (c, py)], fill=rgb("#ffffff", 120))
    return img


def sunburst(img, cx, cy, radius, rays=28, color="#ffd36b", alpha=60, rotation=0.0):
    """Light rays fanning out from behind the wheel, fading with distance."""
    lay = layer()
    d = ImageDraw.Draw(lay)
    for k in range(rays):
        a0 = math.radians(rotation + k * 360 / rays)
        a1 = math.radians(rotation + (k + 0.5) * 360 / rays)
        d.polygon([(cx, cy), (cx + math.cos(a0) * radius, cy + math.sin(a0) * radius),
                   (cx + math.cos(a1) * radius, cy + math.sin(a1) * radius)], fill=rgb(color, alpha))
    fade = Image.new("L", (W, H), 0)
    m = radial_alpha((int(radius * 2), int(radius * 2)), 1.0, 0.0, 1.0)
    fade.paste(m, (int(cx - radius), int(cy - radius)))
    lay.putalpha(ImageChops.multiply(lay.getchannel("A"), fade))
    over(img, lay.filter(ImageFilter.GaussianBlur(3 * SS)))


def pedestal(img, cx, top, width, height):
    """Gold stand the wheel sits on."""
    lay = layer()
    d = ImageDraw.Draw(lay)
    neck_w = width * 0.22
    d.polygon([(cx - neck_w / 2, top), (cx + neck_w / 2, top), (cx + width / 2, top + height),
               (cx - width / 2, top + height)], fill=rgb(GOLD_DARK))
    g = vertical_gradient((W, H), GOLD_LIGHT, "#9a6400")
    m = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(m)
    md.polygon([(cx - neck_w / 2 + 3 * SS, top), (cx + neck_w / 2 - 3 * SS, top),
                (cx + width / 2 - 6 * SS, top + height - 8 * SS), (cx - width / 2 + 6 * SS, top + height - 8 * SS)],
               fill=255)
    g.putalpha(m)
    lay.alpha_composite(g)
    d.rounded_rectangle([cx - width / 2 - 10 * SS, top + height - 12 * SS, cx + width / 2 + 10 * SS,
                         top + height + 8 * SS], radius=8 * SS, fill=rgb("#5a3200"), outline=rgb(GOLD), width=2 * SS)
    sh = layer()
    ImageDraw.Draw(sh).ellipse([cx - width * 0.75, top + height - 4 * SS, cx + width * 0.75, top + height + 26 * SS],
                               fill=(0, 0, 0, 160))
    over(img, sh.filter(ImageFilter.GaussianBlur(10 * SS)))
    over(img, lay)


def chip_face(diam, color, value):
    """A casino chip seen from above."""
    s = int(diam)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = s / 2, s / 2 - 1
    d.ellipse([c - r, c - r, c + r, c + r], fill=rgb(color))
    for k in range(8):   # edge inserts
        a = math.radians(k * 45 + 22.5)
        w = math.radians(11)
        pts = [(c + math.cos(a - w) * r, c + math.sin(a - w) * r), (c + math.cos(a + w) * r, c + math.sin(a + w) * r),
               (c + math.cos(a + w) * r * 0.74, c + math.sin(a + w) * r * 0.74),
               (c + math.cos(a - w) * r * 0.74, c + math.sin(a - w) * r * 0.74)]
        d.polygon(pts, fill=(255, 255, 255, 235))
    ir = r * 0.62
    d.ellipse([c - ir, c - ir, c + ir, c + ir], fill=mix(color, "#000000", 0.18))
    for k in range(24):  # dashed inner ring
        a0 = k * 15
        d.arc([c - ir * 0.9, c - ir * 0.9, c + ir * 0.9, c + ir * 0.9], a0, a0 + 8, fill=(255, 255, 255, 200),
              width=max(1, int(s * 0.018)))
    f = font(F_BLACK, ir * (0.62 if len(value) <= 3 else 0.5))
    d.text((c, c + s * 0.01), value, font=f, fill=(255, 255, 255, 255), anchor="mm")
    # light from the top-left
    shine = Image.new("RGBA", (s, s), (255, 255, 255, 0))
    sm = Image.new("L", (s, s), 0)
    ImageDraw.Draw(sm).ellipse([c - r * 0.9, c - r * 0.95, c + r * 0.5, c + r * 0.2], fill=60)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([c - r, c - r, c + r, c + r], fill=255)
    shine.putalpha(ImageChops.multiply(sm.filter(ImageFilter.GaussianBlur(s * 0.08)), mask))
    img.alpha_composite(shine)
    return img


def chip_stack(diam, color, value, count, tilt=0.42):
    """A stack of chips seen at an angle (face squashed, edges stacked)."""
    face = chip_face(diam, color, value)
    fh = max(2, int(diam * tilt))
    face = face.resize((int(diam), fh), Image.LANCZOS)
    thick = max(2, int(diam * 0.075))
    height = fh + thick * count + 6 * SS
    img = Image.new("RGBA", (int(diam), height), (0, 0, 0, 0))
    edge = Image.new("RGBA", (int(diam), fh), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)
    ed.ellipse([0, 0, diam - 1, fh - 1], fill=mix(color, "#000000", 0.35))
    for k in range(10):  # white stripes on the edge
        x = diam * (0.08 + k * 0.095)
        ed.rectangle([x, fh * 0.45, x + diam * 0.035, fh], fill=(235, 235, 235, 200))
    for i in range(count):
        y = height - fh - i * thick
        img.alpha_composite(edge, (0, y - 0))
    img.alpha_composite(face, (0, height - fh - (count - 1) * thick - thick))
    return img


def coin(diam, tilt=1.0):
    s = int(diam)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = s / 2, s / 2 - 1
    d.ellipse([c - r, c - r, c + r, c + r], fill=rgb(GOLD_DARK))
    g = vertical_gradient((s, s), GOLD_LIGHT, "#c98a00")
    m = Image.new("L", (s, s), 0)
    ImageDraw.Draw(m).ellipse([c - r * 0.92, c - r * 0.92, c + r * 0.92, c + r * 0.92], fill=255)
    g.putalpha(m)
    img.alpha_composite(g)
    d.ellipse([c - r * 0.72, c - r * 0.72, c + r * 0.72, c + r * 0.72], outline=rgb("#9a6b00"), width=max(1, s // 30))
    star = []
    for k in range(10):   # embossed star
        a = math.radians(-90 + k * 36)
        rr = r * (0.45 if k % 2 == 0 else 0.19)
        star.append((c + math.cos(a) * rr, c + math.sin(a) * rr))
    d.polygon(star, fill=rgb("#b07800"))
    if tilt != 1.0:
        img = img.resize((s, max(2, int(s * tilt))), Image.LANCZOS)
    return img


def card(w, rank, suit, red):
    h = int(w * 1.4)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(w * 0.09), fill=(250, 248, 240, 255),
                        outline=(200, 195, 180, 255), width=max(1, SS))
    color = (200, 20, 50, 255) if red else (20, 20, 30, 255)
    d.text((w * 0.1, h * 0.05), rank, font=font(F_BLACK, w * 0.22), fill=color)
    d.text((w * 0.12, h * 0.24), suit, font=font(F_SYMBOL, w * 0.17), fill=color)
    d.text((w / 2, h * 0.55), suit, font=font(F_SYMBOL, w * 0.55), fill=color, anchor="mm")
    return img


def shadowed(base, obj, x, y, angle=0.0, blur=6, offset=(6, 10), alpha=150):
    if angle:
        obj = obj.rotate(angle, expand=True, resample=Image.BICUBIC)
    sh = Image.new("RGBA", obj.size, (0, 0, 0, 0))
    sh.putalpha(obj.getchannel("A").point(lambda v: v * alpha // 255))
    big = Image.new("RGBA", (obj.width + 80 * SS, obj.height + 80 * SS), (0, 0, 0, 0))
    big.alpha_composite(sh, (40 * SS, 40 * SS))
    big = big.filter(ImageFilter.GaussianBlur(blur * SS))
    base.alpha_composite(big, (int(x - 40 * SS + offset[0] * SS), int(y - 40 * SS + offset[1] * SS)))
    base.alpha_composite(obj, (int(x), int(y)))


def sparkle(base, x, y, size, color="#ffffff"):
    lay = layer()
    d = ImageDraw.Draw(lay)
    s = size
    d.polygon([(x, y - s), (x + s * 0.16, y - s * 0.16), (x + s, y), (x + s * 0.16, y + s * 0.16), (x, y + s),
               (x - s * 0.16, y + s * 0.16), (x - s, y), (x - s * 0.16, y - s * 0.16)], fill=rgb(color, 240))
    d.ellipse([x - s * 0.5, y - s * 0.5, x + s * 0.5, y + s * 0.5], fill=rgb(color, 70))
    glow(base, lay, s * 0.25, 2)
    over(base, lay)


def table(img, top_frac):
    """Curved casino table: felt with a padded rail and a gold trim, across the bottom."""
    cx, ry = W / 2, H * 0.75
    cy = H * top_frac + ry
    rx = W * 0.95
    rail = layer()
    d = ImageDraw.Draw(rail)
    d.ellipse([cx - rx - 26 * SS, cy - ry - 26 * SS, cx + rx + 26 * SS, cy + ry + 26 * SS], fill=rgb("#3a1408"))
    d.ellipse([cx - rx - 26 * SS, cy - ry - 26 * SS, cx + rx + 26 * SS, cy + ry + 26 * SS], outline=rgb(GOLD),
              width=3 * SS)
    d.ellipse([cx - rx - 12 * SS, cy - ry - 14 * SS, cx + rx + 12 * SS, cy + ry + 14 * SS], outline=rgb("#6b2a10"),
              width=8 * SS)
    shadow_l = layer()
    ImageDraw.Draw(shadow_l).ellipse([cx - rx - 26 * SS, cy - ry - 40 * SS, cx + rx + 26 * SS, cy + ry],
                                     fill=(0, 0, 0, 170))
    over(img, shadow_l.filter(ImageFilter.GaussianBlur(14 * SS)))
    over(img, rail)
    felt = vertical_gradient((W, H), "#0f6b4a", "#03261a")
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
    felt.putalpha(m)
    over(img, felt)
    # felt texture + a printed gold line
    noise = (np.random.default_rng(7).random((H, W)) * 26).astype(np.uint8)
    tex = Image.fromarray(noise, "L")
    tex_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tex_l.putalpha(ImageChops.multiply(tex, m))
    over(img, tex_l)
    line = layer()
    ImageDraw.Draw(line).ellipse([cx - rx * 0.86, cy - ry * 0.86 + 30 * SS, cx + rx * 0.86, cy + ry * 0.86],
                                 outline=rgb(GOLD, 110), width=2 * SS)
    over(img, line)
    return cx, cy, rx, ry


def vignette(img, strength=0.75):
    v = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    v.putalpha(ImageChops.invert(radial_alpha((W, H), 1.0, 0.0, 2.2)).point(lambda a: int(a * strength)))
    over(img, v)


def italic(img, shear=0.2):
    w, h = img.size
    extra = int(h * shear)
    return img.transform((w + extra, h), Image.AFFINE, (1, shear, -extra, 0, 1, 0), resample=Image.BICUBIC)


def logo_word(text, px, top, bottom, outline="#1a0630"):
    f = font(F_BLACK, px)
    bbox = f.getbbox(text)
    pad = int(px * 0.25)
    w, h = bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((pad - bbox[0], pad - bbox[1]), text, font=f, fill=255)
    stroke = Image.new("L", (w, h), 0)
    ImageDraw.Draw(stroke).text((pad - bbox[0], pad - bbox[1]), text, font=f, fill=255,
                                stroke_width=int(px * 0.07))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    edge = Image.new("RGBA", (w, h), rgb(outline))
    edge.putalpha(stroke)
    out.alpha_composite(edge)
    fill = vertical_gradient((w, h), top, bottom)
    fill.putalpha(mask)
    out.alpha_composite(fill)
    # glossy highlight on the upper half of the letters
    hl = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    hm = Image.new("L", (w, h), 0)
    ImageDraw.Draw(hm).rectangle([0, 0, w, pad + (bbox[3] - bbox[1]) * 0.42], fill=70)
    hl.putalpha(ImageChops.multiply(hm, mask))
    out.alpha_composite(hl)
    return italic(out)


def place_logo(img, cx, top, scale=1.0):
    spin = logo_word("SPIN", 112 * SS * scale, "#e9fdff", CYAN)
    frenzy = logo_word("FRENZY", 112 * SS * scale, "#ffd1f6", MAGENTA)
    gap = int(-34 * SS * scale)
    total_h = spin.height + frenzy.height + gap
    # coloured glow behind the words
    g = layer()
    g.alpha_composite(spin, (int(cx - spin.width / 2 - 20 * SS * scale), int(top)))
    g.alpha_composite(frenzy, (int(cx - frenzy.width / 2), int(top + spin.height + gap)))
    tint = Image.new("RGBA", (W, H), rgb(MAGENTA))
    tint.putalpha(g.getchannel("A"))
    glow(img, tint, 22 * SS, 2)
    over(img, g)
    return top + total_h


def tagline(img, cx, y, text, px):
    f = font(F_BOLD, px)
    spaced = " ".join(text)
    bbox = f.getbbox(spaced)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    padx, pady = px * 0.9, px * 0.45
    lay = layer()
    d = ImageDraw.Draw(lay)
    box = [cx - w / 2 - padx, y, cx + w / 2 + padx, y + h + pady * 2]
    d.rounded_rectangle(box, radius=(h + pady * 2) / 2, fill=(20, 8, 40, 220), outline=rgb(GOLD), width=2 * SS)
    d.text((cx, y + pady + h / 2), spaced, font=f, fill=rgb(GOLD_LIGHT), anchor="mm")
    over(img, lay)


# ------------------------------------------------------------------ the two images
def cover():
    img = backdrop(1)
    spotlights(img, [(MAGENTA, 0.12, 0.05, 0.12, 60), (CYAN, 0.38, 0.55, 0.1, 45), ("#ffd36b", 0.72, 0.74, 0.16, 55),
                     ("#a78bfa", 0.95, 0.98, 0.1, 50)])
    bokeh(img, 70, 2, blur=5)
    bokeh(img, 25, 3, blur=2)
    wcx, wcy, wr = W * 0.735, H * 0.455, 178 * SS
    sunburst(img, wcx, wcy, wr * 2.6, rays=30, alpha=46, rotation=3)
    tcx, tcy, rx, ry = table(img, 0.79)
    # the wheel on the right, on a gold stand at the back of the table
    pedestal(img, wcx, wcy + wr * 0.9, wr * 1.05, wr * 0.5)
    wl = wheel(wr, rotation_deg=4)
    img.alpha_composite(wl, (int(wcx - wl.width / 2), int(wcy - wl.height / 2)))
    # logo and tagline on the left
    bottom = place_logo(img, W * 0.29, H * 0.08)
    tagline(img, W * 0.285, bottom + 6 * SS, "LIVE WHEEL GAME SHOW", 17 * SS)
    # things on the table
    random.seed(11)
    stacks = [(0.06, 0.80, 0, 7), (0.13, 0.84, 1, 4), (0.205, 0.795, 2, 9), (0.86, 0.86, 3, 6), (0.93, 0.81, 4, 3),
              (0.40, 0.89, 5, 5)]
    for x, y, ci, count in stacks:
        color, value = CHIP_COLORS[ci]
        st = chip_stack(64 * SS, color, value, count)
        shadowed(img, st, W * x - st.width / 2, H * y - st.height + 20 * SS, blur=5, offset=(5, 6))
    for x, y, ci, ang in [(0.30, 0.88, 6, 12), (0.52, 0.93, 0, -20), (0.63, 0.90, 7, 30), (0.75, 0.95, 2, -8)]:
        color, value = CHIP_COLORS[ci]
        ch = chip_face(56 * SS, color, value).resize((56 * SS, int(56 * SS * 0.45)), Image.LANCZOS)
        shadowed(img, ch, W * x, H * y, angle=ang, blur=4, offset=(4, 5))
    for x, y in [(0.47, 0.86), (0.495, 0.875), (0.56, 0.95), (0.70, 0.88), (0.235, 0.93)]:
        cn = coin(40 * SS, tilt=0.5)
        shadowed(img, cn, W * x, H * y, angle=random.uniform(-15, 15), blur=3, offset=(3, 4))
    shadowed(img, card(70 * SS, "A", "♠", False), W * 0.30, H * 0.765, angle=-14, blur=6)
    shadowed(img, card(70 * SS, "K", "♥", True), W * 0.335, H * 0.75, angle=9, blur=6)
    # coins flying through the air, slightly motion-blurred
    for x, y, d_, ang, tilt in [(0.505, 0.30, 34, 25, 0.8), (0.545, 0.12, 26, -30, 0.55), (0.955, 0.35, 30, 40, 0.7),
                                (0.47, 0.60, 22, -10, 0.9)]:
        cn = coin(d_ * SS, tilt=tilt).rotate(ang, expand=True, resample=Image.BICUBIC)
        cn = cn.filter(ImageFilter.GaussianBlur(0.6 * SS))
        shadowed(img, cn, W * x, H * y, blur=8, offset=(10, 22), alpha=70)
    # airborne chip
    fl = chip_face(46 * SS, "#db2777", "5K").resize((46 * SS, 30 * SS), Image.LANCZOS).rotate(-28, expand=True,
                                                                                              resample=Image.BICUBIC)
    shadowed(img, fl, W * 0.07, H * 0.5, blur=8, offset=(10, 22), alpha=70)
    # sparkles
    for x, y, s in [(0.53, 0.18, 16), (0.93, 0.13, 12), (0.62, 0.62, 10), (0.08, 0.12, 11), (0.47, 0.06, 8),
                    (0.88, 0.55, 14), (0.42, 0.47, 9)]:
        sparkle(img, W * x, H * y, s * SS)
    vignette(img, 0.6)
    marquee(img)
    return img


def background():
    img = backdrop(5)
    spotlights(img, [(MAGENTA, 0.2, 0.1, 0.14, 55), ("#ffd36b", 0.5, 0.5, 0.2, 50), (CYAN, 0.8, 0.9, 0.14, 50)])
    bokeh(img, 110, 6, blur=9)
    wcx, wcy, wr = W / 2, H * 0.455, 196 * SS
    sunburst(img, wcx, wcy, wr * 3, rays=36, alpha=40)
    table(img, 0.8)
    # big wheel in the middle, softened so text on top of it stays readable
    pedestal(img, wcx, wcy + wr * 0.9, wr * 1.05, wr * 0.45)
    wl = wheel(wr, rotation_deg=-3).filter(ImageFilter.GaussianBlur(1.2 * SS))
    img.alpha_composite(wl, (int(wcx - wl.width / 2), int(wcy - wl.height / 2)))
    random.seed(21)
    blurred = layer()
    for x, y, ci, count in [(0.1, 0.86, 0, 6), (0.2, 0.9, 2, 3), (0.82, 0.88, 4, 7), (0.91, 0.84, 1, 4)]:
        color, value = CHIP_COLORS[ci]
        st = chip_stack(70 * SS, color, value, count)
        shadowed(blurred, st, W * x - st.width / 2, H * y - st.height + 20 * SS, blur=5)
    for x, y in [(0.3, 0.93), (0.66, 0.92), (0.72, 0.95)]:
        shadowed(blurred, coin(42 * SS, 0.5), W * x, H * y, angle=random.uniform(-20, 20), blur=3)
    over(img, blurred.filter(ImageFilter.GaussianBlur(2.5 * SS)))   # shallow depth of field
    dim = Image.new("RGBA", (W, H), (8, 4, 20, 60))
    over(img, dim)
    for x, y, s in [(0.25, 0.2, 10), (0.76, 0.16, 12), (0.68, 0.7, 8), (0.3, 0.66, 9)]:
        sparkle(img, W * x, H * y, s * SS)
    vignette(img, 0.7)
    marquee(img, phase=1)
    return img


def save(img, name):
    out = img.resize((OUT_W, OUT_H), Image.LANCZOS).convert("RGB")
    path = os.path.join(HERE, name)
    out.save(path, optimize=True)
    print(f"{path}  {out.size[0]}x{out.size[1]}  {os.path.getsize(path) / 1024:.0f} KB")


if __name__ == "__main__":
    save(cover(), "cover.png")
    save(background(), "background.png")
