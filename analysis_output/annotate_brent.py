"""Annotate Brent Cash 15m screenshot with zones, patterns, trade plan."""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

SRC = (
    r"C:\Users\User\.cursor\projects\d-PROJECTS-Bybit-bot\assets\\"
    r"c__Users_User_AppData_Roaming_Cursor_User_workspaceStorage_"
    r"7421d9986fe84b49bc115e3b3adcc429_images_image-b3488a61-d60c-4139-ad23-db9168fd2013.png"
)
OUT_DIR = r"d:\PROJECTS\Bybit_bot\analysis_output"

LEFT, RIGHT = 8, 955
VOL_TOP, VOL_BOT = 295, 365


def load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


FONT_SM = load_font(11)
FONT_MD = load_font(13)
FONT_TITLE = load_font(16)


def overlay_rect(base: Image.Image, xy: list[int], fill: tuple[int, ...]) -> Image.Image:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle(xy, fill=fill)
    return Image.alpha_composite(base, layer)


def draw_hline(
    d: ImageDraw.ImageDraw,
    y: int,
    color: tuple[int, ...],
    width: int = 1,
    dash: bool = False,
    x0: int = LEFT,
    x1: int = RIGHT,
) -> None:
    if not dash:
        d.line([(x0, y), (x1, y)], fill=color, width=width)
        return
    x = x0
    while x < x1:
        d.line([(x, y), (min(x + 8, x1), y)], fill=color, width=width)
        x += 14


def label_box(
    d: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fill: tuple[int, ...],
    text_fill: tuple[int, ...] = (255, 255, 255, 255),
    font: ImageFont.ImageFont = FONT_SM,
) -> None:
    x, y = xy
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 3
    d.rectangle([x, y, x + tw + 2 * pad, y + th + 2 * pad], fill=fill)
    d.text((x + pad, y + pad), text, fill=text_fill, font=font)


def title_bar(d: ImageDraw.ImageDraw, w: int, text: str) -> None:
    d.rectangle([0, 0, w, 22], fill=(15, 18, 28, 230))
    d.text((8, 3), text, fill=(240, 240, 245, 255), font=FONT_TITLE)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    im = Image.open(SRC).convert("RGBA")
    w, _h = im.size

    # ----- MAP 1: Zones + Liquidity -----
    img1 = im.copy()
    img1 = overlay_rect(img1, [LEFT, 42, RIGHT, 72], (220, 60, 60, 55))
    img1 = overlay_rect(img1, [LEFT, 95, RIGHT, 118], (255, 140, 60, 40))
    img1 = overlay_rect(img1, [LEFT, 148, RIGHT, 168], (60, 180, 100, 45))
    img1 = overlay_rect(img1, [LEFT, 185, RIGHT, 210], (60, 160, 220, 40))
    img1 = overlay_rect(img1, [LEFT, 245, RIGHT, 278], (40, 200, 120, 55))

    d1 = ImageDraw.Draw(img1)
    draw_hline(d1, 155, (255, 80, 80, 200), width=1, dash=True)
    label_box(d1, (760, 142), "PRICE ~106.65", (180, 40, 40, 230))
    label_box(
        d1,
        (12, 44),
        "SUPPLY / EQ HIGHS ~107.85-108.05  |  BSL (stops above)",
        (180, 40, 40, 230),
    )
    label_box(d1, (12, 97), "R2 mid-resistance ~107.15-107.35", (200, 100, 30, 230))
    label_box(d1, (12, 150), "S1 retest support ~106.40-106.55", (30, 130, 70, 230))
    label_box(
        d1,
        (12, 187),
        "S2 breakout origin / prior R->S ~105.95-106.25",
        (30, 110, 170, 230),
    )
    label_box(
        d1,
        (12, 250),
        "DEMAND + SSL SWEEP ~105.05-105.45  |  V-bottom liquidity grab",
        (20, 140, 80, 230),
    )

    d1.polygon([(900, 38), (910, 50), (890, 50)], fill=(255, 80, 80, 220))
    label_box(d1, (820, 18), "BSL ^", (200, 50, 50, 230), font=FONT_MD)
    d1.polygon([(220, 285), (210, 273), (230, 273)], fill=(50, 220, 120, 220))
    label_box(d1, (235, 270), "SSL v", (20, 140, 80, 230), font=FONT_MD)

    title_bar(d1, w, "BRENT CASH · 15m · Vantage  |  MAP 1/3: Zones · Liquidity · S/R")

    legend_y = 318
    d1.rectangle([8, legend_y, 620, 382], fill=(12, 14, 22, 210))
    d1.text((14, legend_y + 4), "LEGEND", fill=(255, 220, 100, 255), font=FONT_MD)
    d1.text(
        (14, legend_y + 22),
        "Red band = Supply / resistance (sellers defended)",
        fill=(255, 180, 180, 255),
        font=FONT_SM,
    )
    d1.text(
        (14, legend_y + 36),
        "Green band = Demand / support (buyers stepped in)",
        fill=(160, 255, 190, 255),
        font=FONT_SM,
    )
    d1.text(
        (14, legend_y + 50),
        "BSL = buy-side liquidity (stops above highs)  |  SSL = sell-side liquidity (stops below lows)",
        fill=(200, 210, 230, 255),
        font=FONT_SM,
    )

    path1 = os.path.join(OUT_DIR, "brent_15m_map1_zones_liquidity.png")
    img1.convert("RGB").save(path1, quality=95)
    print("saved", path1)

    # ----- MAP 2: Patterns + Volume -----
    img2 = im.copy()
    layer = Image.new("RGBA", img2.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    dl.line([(40, 70), (420, 160)], fill=(255, 200, 60, 180), width=2)
    dl.line([(80, 250), (400, 230)], fill=(255, 200, 60, 180), width=2)
    dl.polygon([(40, 70), (420, 160), (400, 230), (80, 250)], fill=(255, 200, 60, 35))
    img2 = Image.alpha_composite(img2, layer)

    layer = Image.new("RGBA", img2.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    dl.line([(520, 230), (880, 55)], fill=(80, 220, 140, 200), width=2)
    dl.line([(540, 270), (920, 110)], fill=(80, 220, 140, 160), width=2)
    dl.polygon([(520, 230), (880, 55), (920, 110), (540, 270)], fill=(80, 220, 140, 30))
    img2 = Image.alpha_composite(img2, layer)

    img2 = overlay_rect(img2, [780, 95, 940, 170], (180, 120, 255, 40))

    d2 = ImageDraw.Draw(img2)
    d2.ellipse([175, 248, 230, 285], outline=(50, 255, 150, 255), width=2)
    label_box(d2, (100, 228), "1 LIQUIDITY SWEEP (SSL)", (20, 150, 90, 230))

    d2.ellipse([455, 215, 500, 250], outline=(80, 200, 255, 255), width=2)
    d2.ellipse([500, 225, 545, 255], outline=(80, 200, 255, 255), width=2)
    label_box(d2, (430, 198), "2 MICRO DB / SPRING", (30, 120, 180, 230))

    d2.line([(560, 200), (720, 90)], fill=(80, 255, 140, 255), width=3)
    d2.polygon([(720, 90), (700, 95), (710, 112)], fill=(80, 255, 140, 255))
    label_box(d2, (600, 130), "3 IMPULSE BOS UP", (20, 140, 80, 230))

    d2.ellipse([850, 42, 920, 78], outline=(255, 80, 80, 255), width=2)
    label_box(d2, (760, 24), "4 EQ HIGH / SUPPLY", (180, 40, 40, 230))
    label_box(d2, (790, 175), "5 FLAG / PULLBACK", (120, 80, 180, 230))
    label_box(d2, (140, 100), "DESCENDING WEDGE / CHANNEL", (180, 140, 20, 230))

    d2.rectangle([8, VOL_TOP, RIGHT, VOL_BOT], outline=(255, 220, 100, 120), width=1)
    for vx, txt in (
        (95, "VOL dump"),
        (200, "VOL sweep"),
        (560, "VOL impulse"),
        (860, "VOL fade"),
    ):
        d2.line([(vx, VOL_TOP + 5), (vx, VOL_BOT - 5)], fill=(255, 220, 100, 160), width=1)
        label_box(
            d2,
            (vx - 20, VOL_TOP + 2),
            txt,
            (40, 35, 10, 220),
            text_fill=(255, 230, 120, 255),
        )

    d2.rectangle([560, 250, 950, 310], fill=(12, 14, 22, 220))
    d2.text((568, 254), "SCENARIO (local 15m)", fill=(255, 220, 100, 255), font=FONT_MD)
    d2.text(
        (568, 272),
        "BASE: hold S1 106.40-55 -> retest R2 107.15 / supply 107.85",
        fill=(200, 255, 210, 255),
        font=FONT_SM,
    )
    d2.text(
        (568, 286),
        "ALT: lose 106.40 -> magnet S2 106.00 / demand 105.20",
        fill=(255, 200, 180, 255),
        font=FONT_SM,
    )
    d2.text(
        (568, 300),
        "INVALID long: 15m close < ~105.90 (breakout origin)",
        fill=(255, 160, 160, 255),
        font=FONT_SM,
    )

    title_bar(d2, w, "BRENT CASH · 15m · Vantage  |  MAP 2/3: Patterns · Volume · Scenario")

    d2.rectangle([8, 318, 540, 382], fill=(12, 14, 22, 210))
    d2.text((14, 322), "PATTERNS SEEN", fill=(255, 220, 100, 255), font=FONT_MD)
    d2.text(
        (14, 340),
        "1 Sweep low -> V recovery   2 Micro double-bottom before BOS",
        fill=(210, 220, 235, 255),
        font=FONT_SM,
    )
    d2.text(
        (14, 354),
        "3 Impulsive break of structure   4 Equal highs / supply   5 Bull flag pullback",
        fill=(210, 220, 235, 255),
        font=FONT_SM,
    )
    d2.text(
        (14, 368),
        "Volume: spikes on dump/sweep/impulse; fading on current pullback",
        fill=(180, 200, 160, 255),
        font=FONT_SM,
    )

    path2 = os.path.join(OUT_DIR, "brent_15m_map2_patterns_volume.png")
    img2.convert("RGB").save(path2, quality=95)
    print("saved", path2)

    # ----- MAP 3: Trade plan -----
    img3 = im.copy()
    img3 = overlay_rect(img3, [LEFT, 148, RIGHT, 168], (60, 200, 120, 50))
    img3 = overlay_rect(img3, [LEFT, 210, RIGHT, 225], (220, 60, 60, 50))
    img3 = overlay_rect(img3, [LEFT, 95, RIGHT, 112], (60, 140, 255, 45))
    img3 = overlay_rect(img3, [LEFT, 42, RIGHT, 68], (180, 80, 255, 45))

    d3 = ImageDraw.Draw(img3)
    draw_hline(d3, 155, (80, 255, 140, 220), width=2)
    draw_hline(d3, 217, (255, 80, 80, 220), width=2)
    draw_hline(d3, 103, (80, 160, 255, 220), width=2)
    draw_hline(d3, 55, (200, 120, 255, 220), width=2)

    label_box(d3, (680, 140), "ENTRY ZONE ~106.40-106.55", (30, 140, 80, 235))
    label_box(d3, (680, 205), "STOP ~105.85-105.95", (160, 40, 40, 235))
    label_box(d3, (680, 88), "TP1 ~107.15-107.30", (30, 100, 180, 235))
    label_box(d3, (680, 44), "TP2 ~107.85-108.00", (120, 60, 180, 235))

    d3.line([(650, 155), (650, 217)], fill=(255, 100, 100, 200), width=2)
    d3.line([(670, 155), (670, 103)], fill=(100, 180, 255, 200), width=2)
    label_box(d3, (560, 175), "RISK", (160, 40, 40, 230))
    label_box(d3, (520, 115), "REWARD->TP1 ~1:1.2", (30, 100, 180, 230))

    title_bar(
        d3,
        w,
        "BRENT CASH · 15m · Vantage  |  MAP 3/3: Trade plan (WAIT for hold/reclaim)",
    )

    d3.rectangle([8, 318, 720, 382], fill=(12, 14, 22, 210))
    d3.text((14, 322), "PLAN — not a market chase", fill=(255, 220, 100, 255), font=FONT_MD)
    d3.text(
        (14, 340),
        "Bias local: bullish after BOS, but under supply -> WAIT / limit at S1",
        fill=(210, 220, 235, 255),
        font=FONT_SM,
    )
    d3.text(
        (14, 354),
        "Long only if 15m holds/reclaims 106.40-55 with higher low; R:R to TP2 ~1:2+",
        fill=(200, 255, 210, 255),
        font=FONT_SM,
    )
    d3.text(
        (14, 368),
        "If break <105.90 -> cancel long, look demand 105.05-45 for next reaction",
        fill=(255, 190, 170, 255),
        font=FONT_SM,
    )

    path3 = os.path.join(OUT_DIR, "brent_15m_map3_trade_plan.png")
    img3.convert("RGB").save(path3, quality=95)
    print("saved", path3)
    print("done")


if __name__ == "__main__":
    main()
