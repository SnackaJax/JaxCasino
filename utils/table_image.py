"""Draws the poker table (and a player's private hand) as PNG images with Pillow.

Everything is drawn at 2x and downscaled for smooth edges. The functions take plain data
(no Discord objects) so they can run in a worker thread.
"""
import io
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

K = 2                       # supersampling factor
W, H = 1000, 640            # output size
CX, CY = 500, 320           # table centre
SEAT_RX, SEAT_RY = 380, 245  # ellipse the seats sit on
PW, PH = 220, 96            # seat panel size

BG = (14, 18, 26)
RAIL = (74, 46, 24)
RAIL_EDGE = (42, 25, 12)
FELT = (14, 104, 62)
FELT_LINE = (36, 132, 86)
PANEL = (28, 34, 46)
PANEL_DIM = (22, 25, 31)
PANEL_EDGE = (62, 72, 92)
WHITE = (245, 245, 245)
GREY = (140, 146, 156)
GOLD = (245, 196, 72)
GREEN = (60, 200, 110)
RED = (220, 50, 60)
CARD_RED = (205, 30, 45)
CARD_BLACK = (25, 25, 30)
BACK = (160, 25, 45)
BACK_PATTERN = (190, 55, 72)

Card = tuple[str, str]  # (rank name, suit symbol), e.g. ("10", "♥")

FONT_DIRS = [r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF",
             "/usr/share/fonts/dejavu", "/Library/Fonts", "/System/Library/Fonts/Supplemental"]
FONT_FILES = {
    "bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
    "regular": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Arial.ttf"],
    "symbol": ["seguisym.ttf", "DejaVuSans.ttf", "Arial Unicode.ttf"],
}


@dataclass
class SeatInfo:
    name: str
    stack: int
    bet: int = 0
    cards: list[Card] = field(default_factory=list)  # face-up cards (showdown)
    hidden: int = 0         # number of face-down cards
    turn: bool = False
    dealer: bool = False
    folded: bool = False
    all_in: bool = False
    won: int = 0
    status: str = ""        # extra label, e.g. a hand name, "Next hand", "Leaving"


@dataclass
class TableInfo:
    seats: list[SeatInfo]
    board: list[Card]
    pot: int
    street: str = ""        # shown next to the pot, e.g. "FLOP"
    hand_no: int = 0
    sb: int = 0
    bb: int = 0
    center_title: str = ""  # shown in the middle when there is no board (waiting)
    center_sub: str = ""
    result: str = ""        # shown under the board after a hand


# --------------------------------------------------------------------------- helpers
def short_amount(n: int) -> str:
    """An amount for a picture: in full below a million (25,000), short from there (2.5M, 487.5M, 1B, 2.5T)."""
    if n >= 1_000_000:
        for d, u in ((10 ** 12, "T"), (10 ** 9, "B"), (10 ** 6, "M")):
            x = n / d
            t = round(x, 2 if x < 10 else 1)
            if t >= 1:
                return f"{t:g}{u}" if t < 1e5 else f"{round(t):,}{u}"
    return f"{n:,}"


@lru_cache(maxsize=64)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_FILES[kind]:
        for folder in FONT_DIRS:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size * K)
    return ImageFont.load_default(size * K)


def s(v: float) -> int:
    return int(round(v * K))


def box(x0, y0, x1, y1):
    return (s(x0), s(y0), s(x1), s(y1))


def text(d: ImageDraw.ImageDraw, x, y, value: str, f, fill, anchor="mm") -> None:
    d.text((s(x), s(y)), value, font=f, fill=fill, anchor=anchor)


def text_width(d: ImageDraw.ImageDraw, value: str, f) -> float:
    return d.textlength(value, font=f) / K


def fit(d: ImageDraw.ImageDraw, value: str, f, max_w: float) -> str:
    if text_width(d, value, f) <= max_w:
        return value
    while value and text_width(d, value + "…", f) > max_w:
        value = value[:-1]
    return value + "…"


def short(n: int) -> str:
    if n >= 10_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 10_000_000:
        return f"{n / 1e6:.1f}M"
    return f"{n:,}"


def pill(d: ImageDraw.ImageDraw, x, y, value: str, f, fg, bg, pad=12, h=28, outline=None) -> None:
    w = text_width(d, value, f) + pad * 2
    d.rounded_rectangle(box(x - w / 2, y - h / 2, x + w / 2, y + h / 2), radius=s(h / 2), fill=bg,
                        outline=outline, width=s(2) if outline else 0)
    text(d, x, y, value, f, fg)


# --------------------------------------------------------------------------- cards / chips
def draw_card(d: ImageDraw.ImageDraw, x, y, w, h, card: Card | None) -> None:
    """Card with its top-left corner at (x, y). None draws a card back."""
    r = max(4, w * 0.12)
    if card is None:
        d.rounded_rectangle(box(x, y, x + w, y + h), radius=s(r), fill=BACK, outline=WHITE, width=s(2))
        inset = max(4, w * 0.12)
        step = max(6, w / 5)
        yy = y + inset + step / 2
        while yy < y + h - inset:
            xx = x + inset + step / 2
            while xx < x + w - inset:
                q = step * 0.3
                d.polygon([(s(xx), s(yy - q)), (s(xx + q), s(yy)), (s(xx), s(yy + q)), (s(xx - q), s(yy))],
                          fill=BACK_PATTERN)
                xx += step
            yy += step
        return
    rank, suit = card
    color = CARD_RED if suit in "♥♦" else CARD_BLACK
    d.rounded_rectangle(box(x, y, x + w, y + h), radius=s(r), fill=WHITE, outline=(190, 190, 195), width=s(1.5))
    rank_f = font("bold", max(10, int(h * 0.30)))
    small_suit = font("symbol", max(9, int(h * 0.22)))
    big_suit = font("symbol", max(12, int(h * 0.46)))
    text(d, x + w * 0.08, y + h * 0.05, rank, rank_f, color, anchor="lt")
    text(d, x + w * 0.10, y + h * 0.36, suit, small_suit, color, anchor="lt")
    text(d, x + w * 0.62, y + h * 0.68, suit, big_suit, color)


def draw_empty_slot(d: ImageDraw.ImageDraw, x, y, w, h) -> None:
    d.rounded_rectangle(box(x, y, x + w, y + h), radius=s(w * 0.12), outline=FELT_LINE, width=s(2))


def draw_chip_bet(d: ImageDraw.ImageDraw, x, y, amount: int) -> None:
    f = font("bold", 17)
    label = short(amount)
    lw = text_width(d, label, f)
    total = 26 + 6 + lw + 16
    left = x - total / 2
    cx = left + 13
    d.ellipse(box(cx - 13, y - 13, cx + 13, y + 13), fill=RED, outline=WHITE, width=s(2))
    for k in range(6):
        a = math.radians(k * 60)
        d.line(box(cx + 9 * math.cos(a), y + 9 * math.sin(a), cx + 12 * math.cos(a), y + 12 * math.sin(a)),
               fill=WHITE, width=s(3))
    d.ellipse(box(cx - 6, y - 6, cx + 6, y + 6), fill=(240, 220, 220))
    d.rounded_rectangle(box(left + 30, y - 13, left + total, y + 13), radius=s(13), fill=(10, 50, 30))
    text(d, left + 30 + (total - 30) / 2, y, label, f, WHITE)


# --------------------------------------------------------------------------- seats
def seat_positions(n: int) -> list[tuple[float, float]]:
    n = max(n, 1)
    out = []
    for i in range(n):
        a = math.radians(90 + i * 360 / max(n, 2))
        out.append((CX + SEAT_RX * math.cos(a), CY + SEAT_RY * math.sin(a)))
    return out


def draw_seat(img: Image.Image, d: ImageDraw.ImageDraw, sx: float, sy: float, seat: SeatInfo) -> None:
    x, y = sx - PW / 2, sy - PH / 2
    dimmed = seat.folded

    if seat.turn:  # soft gold glow behind the active player
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box(x - 8, y - 8, x + PW + 8, y + PH + 8), radius=s(20), fill=200)
        mask = mask.filter(ImageFilter.GaussianBlur(s(10)))
        img.paste(Image.new("RGB", img.size, GOLD), mask=mask)

    edge = GOLD if seat.turn else GREEN if seat.won else PANEL_EDGE
    d.rounded_rectangle(box(x, y, x + PW, y + PH), radius=s(14), fill=PANEL_DIM if dimmed else PANEL,
                        outline=edge, width=s(3 if (seat.turn or seat.won) else 2))

    # Cards (or an initial badge when the player has no cards)
    cw, ch = 44, 62
    cy0 = y + (PH - ch) / 2
    if seat.cards:
        draw_card(d, x + 6, cy0, cw - 2, ch, seat.cards[0])
        draw_card(d, x + 48, cy0, cw - 2, ch, seat.cards[1] if len(seat.cards) > 1 else None)
    elif seat.hidden:
        draw_card(d, x + 10, cy0, cw, ch, None)
        draw_card(d, x + 34, cy0 + 2, cw, ch, None)
    else:
        bx, by = x + 44, y + PH / 2
        d.ellipse(box(bx - 28, by - 28, bx + 28, by + 28), fill=(46, 54, 70) if not dimmed else (34, 38, 46))
        initial = (seat.name.strip()[:1] or "?").upper()
        text(d, bx, by, initial, font("bold", 26), GREY if dimmed else WHITE)

    # Name and stack
    tx = x + 96
    name_f = font("bold", 21)
    text(d, tx, y + 30, fit(d, seat.name, name_f, PW - 104), name_f, GREY if dimmed else WHITE, anchor="lm")
    text(d, tx, y + 62, short(seat.stack), font("bold", 21), GREY if dimmed else GOLD, anchor="lm")

    # Status ribbon under the panel
    small = font("bold", 14)
    if seat.won:
        label = f"WON {short(seat.won)}" + (f" · {seat.status}" if seat.status else "")
        pill(d, sx, y + PH, label, small, (10, 30, 15), GREEN, h=24)
    elif seat.turn:
        pill(d, sx, y + PH, "TO ACT", small, (40, 28, 0), GOLD, h=24)
    elif seat.folded:
        pill(d, sx, y + PH, "FOLDED", small, WHITE, (70, 74, 82), h=24)
    elif seat.all_in:
        pill(d, sx, y + PH, "ALL-IN", small, WHITE, RED, h=24)
    elif seat.status:
        pill(d, sx, y + PH, seat.status, small, WHITE, (52, 60, 78), h=24)

    if seat.dealer:
        bx, by = x + PW - 6, y + 6
        d.ellipse(box(bx - 14, by - 14, bx + 14, by + 14), fill=WHITE, outline=(120, 120, 120), width=s(2))
        text(d, bx, by + 1, "D", font("bold", 16), CARD_BLACK)

    if seat.bet:
        ux, uy = CX - sx, CY - sy
        length = math.hypot(ux, uy) or 1
        ux, uy = ux / length, uy / length
        offset = abs(ux) * (PW / 2 + 48) + abs(uy) * (PH / 2 + 30)
        draw_chip_bet(d, sx + ux * offset, sy + uy * offset, seat.bet)


# --------------------------------------------------------------------------- table
def render_table(info: TableInfo) -> bytes:
    img = Image.new("RGB", (W * K, H * K), BG)
    d = ImageDraw.Draw(img)

    # Table: rail, felt and an inner line
    d.ellipse(box(CX - 462, CY - 266, CX + 462, CY + 266), fill=RAIL_EDGE)
    d.ellipse(box(CX - 455, CY - 258, CX + 455, CY + 258), fill=RAIL)
    d.ellipse(box(CX - 430, CY - 234, CX + 430, CY + 234), fill=FELT)
    d.ellipse(box(CX - 395, CY - 202, CX + 395, CY + 202), outline=FELT_LINE, width=s(2))

    # Header
    text(d, 16, 18, "TEXAS HOLD'EM", font("bold", 18), GREY, anchor="lm")
    right = f"Blinds {short(info.sb)}/{short(info.bb)}" + (f"  ·  Hand #{info.hand_no}" if info.hand_no else "")
    text(d, W - 16, 18, right, font("bold", 18), GREY, anchor="rm")

    # Centre: board, pot, result or waiting message
    if info.board or info.street:
        cw, ch, gap = 64, 90, 8
        total = 5 * cw + 4 * gap
        x0 = CX - total / 2
        for i in range(5):
            x = x0 + i * (cw + gap)
            if i < len(info.board):
                draw_card(d, x, CY - ch / 2, cw, ch, info.board[i])
            else:
                draw_empty_slot(d, x, CY - ch / 2, cw, ch)
        label = (f"{info.street}  ·  " if info.street else "") + f"POT {short(info.pot)}"
        pill(d, CX, CY - 78, label, font("bold", 18), GOLD, (8, 60, 36), h=32, pad=16)
        if info.result:
            f = font("bold", 18)
            pill(d, CX, CY + 76, fit(d, info.result, f, 560), f, WHITE, (8, 60, 36), h=32, pad=16, outline=GREEN)
    else:
        text(d, CX, CY - 16, info.center_title, font("bold", 32), WHITE)
        if info.center_sub:
            text(d, CX, CY + 24, info.center_sub, font("regular", 19), (200, 225, 210))

    for (sx, sy), seat in zip(seat_positions(len(info.seats)), info.seats):
        draw_seat(img, d, sx, sy, seat)

    out = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- private hand
def render_hand(hole: list[Card], board: list[Card], best: str = "", folded: bool = False) -> bytes:
    """A player's own two cards, the board, and their best hand. Shown only to them."""
    w, h = 640, 210
    img = Image.new("RGB", (w * K, h * K), FELT)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box(4, 4, w - 4, h - 4), radius=s(18), outline=FELT_LINE, width=s(3))

    text(d, 24, 26, "YOUR HAND", font("bold", 16), (200, 225, 210), anchor="lm")
    for i, card in enumerate(hole[:2]):
        draw_card(d, 24 + i * 84, 44, 76, 106, card)

    text(d, 210, 26, "BOARD", font("bold", 16), (200, 225, 210), anchor="lm")
    for i in range(5):
        x = 210 + i * 84
        if i < len(board):
            draw_card(d, x, 54, 76, 96, board[i])
        else:
            draw_empty_slot(d, x, 54, 76, 96)

    if folded:
        label, color = "You folded this hand", GREY
    elif best:
        label, color = f"Best hand: {best}", GOLD
    else:
        label, color = "Waiting for the flop", (200, 225, 210)
    text(d, w / 2, 182, label, font("bold", 20), color)

    out = img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
