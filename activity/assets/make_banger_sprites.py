"""Draws the Banger Bash symbol sprites for the Activity page, from the same drawings the chat version uses
(utils/banger_image.py), so both look the same.

    python -m activity.assets.make_banger_sprites

Writes activity/static/banger/<symbol>.png (one cell, drawn at 2x for sharp screens), <symbol>_blur.png (the
same symbol smeared for spinning reels, no glow) and <symbol>_big.png (paying symbols at 3x3 cells, for Mega
Banger). Run it again after changing a symbol drawing.
"""
import os
import sys

from PIL import Image, ImageChops

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from utils import banger_engine as E  # noqa: E402
from utils import banger_image as I  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "banger")


def blurred(tile: Image.Image) -> Image.Image:
    w, h = tile.size
    pad = int(h * 0.35)
    canvas = Image.new("RGBA", (w, h + pad * 2), (0, 0, 0, 0))
    canvas.alpha_composite(tile, (0, pad))
    smear = canvas
    steps = 12
    for k in range(1, steps):
        shift = int((k / steps - 0.5) * pad * 1.6)
        smear = Image.blend(smear, ImageChops.offset(canvas, 0, shift), 1 / (k + 1))
    return smear.crop((0, pad, w, pad + h))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for sym in list(E.SYMBOLS) + [E.MYSTERY]:
        tile = I.symbol_tile(sym)
        tile.save(os.path.join(OUT, f"{sym}.png"), optimize=True)
        blurred(I.symbol_tile(sym, glow=False)).save(os.path.join(OUT, f"{sym}_blur.png"), optimize=True)
        if sym in E.PAYING:
            I.symbol_tile(sym, I.CW * 3, I.CH * 3).save(os.path.join(OUT, f"{sym}_big.png"), optimize=True)
        print("drew", sym)


if __name__ == "__main__":
    main()
