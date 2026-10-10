"""Чистый PNG: S/R — синяя/красная линия 2px или прямоугольник, без жёлтых меток."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .ta_analysis import TAAnalysisResult, fmt_price

SUPPORT_LINE = "#58a6ff"
RESIST_LINE = "#f85149"
SUPPORT_FILL = "#58a6ff"
RESIST_FILL = "#f85149"
LINE_W = 2.0


@dataclass(frozen=True)
class _SrLevel:
    price: float
    label: str
    side: str  # support | resistance
    priority: int


def _x(bars: list[KlineBar], idx: int) -> float:
    idx = max(0, min(idx, len(bars) - 1))
    dt = datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)
    return mdates.date2num(dt)


def _visible_x(bars: list[KlineBar]) -> tuple[float, float]:
    i0 = max(0, len(bars) - min(len(bars), 96))
    return _x(bars, i0), _x(bars, len(bars) - 1)


def _collect_levels(bars: list[KlineBar], ta: TAAnalysisResult) -> list[_SrLevel]:
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    out: list[_SrLevel] = []

    def add(price: float | None, label: str, side: str, priority: int) -> None:
        if price is None or float(price) <= 0 or cur <= 0:
            return
        p = float(price)
        if abs(p - cur) / cur > 0.22:
            return
        out.append(_SrLevel(p, label.strip(), side, priority))

    add(getattr(ta, "breakdown_level", None), "поддержка", "support", 95)
    add(getattr(ta, "nearest_support", None), "S", "support", 88)
    add(getattr(ta, "breakout_level", None), "сопротивление", "resistance", 95)
    add(getattr(ta, "nearest_resistance", None), "R", "resistance", 88)

    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot:
            add(bot, "низ range", "support", 82)
            add(top, "верх range", "resistance", 82)

    try:
        from .chart_reference_levels import session_reference_levels

        for ref in session_reference_levels(bars):
            if ref.kind in {"daily_low", "prev_daily_low", "weekly_low"}:
                add(ref.price, ref.label, "support", 70 if ref.priority >= 85 else 55)
            elif ref.kind in {"daily_high", "prev_daily_high", "weekly_high"}:
                add(ref.price, ref.label, "resistance", 70 if ref.priority >= 85 else 55)
    except Exception:
        pass

    if cur > 0:
        tol = cur * 0.0035
        merged: list[_SrLevel] = []
        for lv in sorted(out, key=lambda x: (-x.priority, x.price)):
            if any(abs(lv.price - m.price) <= tol for m in merged):
                continue
            merged.append(lv)
        out = merged

    supports = sorted([l for l in out if l.side == "support"], key=lambda x: -x.priority)[:3]
    resists = sorted([l for l in out if l.side == "resistance"], key=lambda x: -x.priority)[:3]
    return supports + resists


def _label(ax: plt.Axes, x1: float, price: float, text: str, color: str, *, cur: float) -> None:
    ax.text(
        x1,
        price,
        f" {text} {fmt_price(price)}",
        color=color,
        fontsize=7,
        fontweight="bold",
        va="bottom" if price >= cur else "top",
        ha="right",
        zorder=4,
        bbox=dict(
            boxstyle="round,pad=0.15",
            facecolor="#0d1117",
            edgecolor=color,
            alpha=0.9,
            linewidth=0.5,
        ),
    )


def draw_clean_sr_chart(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
) -> None:
    """Только S/R: 2px синий support, 2px красный resistance; range — полупрозрачный прямоугольник."""
    if not bars:
        return
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    x0, x1 = _visible_x(bars)
    levels = _collect_levels(bars, ta)

    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot and cur > 0 and bot <= cur * 1.12 and top >= cur * 0.88:
            ax.add_patch(
                Rectangle(
                    (x0, bot),
                    max(x1 - x0, 0.001),
                    top - bot,
                    facecolor="#8b949e",
                    edgecolor="#6e7681",
                    alpha=0.07,
                    linewidth=0.8,
                    linestyle="--",
                    zorder=1,
                )
            )
            ax.hlines(bot, x0, x1, colors=SUPPORT_LINE, linewidth=LINE_W, alpha=0.95, zorder=3)
            ax.hlines(top, x1 - (x1 - x0) * 0.35, x1, colors=RESIST_LINE, linewidth=LINE_W, alpha=0.95, zorder=3)

    for lv in levels:
        color = SUPPORT_LINE if lv.side == "support" else RESIST_LINE
        ax.hlines(lv.price, x0, x1, colors=color, linewidth=LINE_W, alpha=0.92, zorder=3)
        _label(ax, x1, lv.price, lv.label, color, cur=cur)

    trig = str(getattr(ta, "setup_trigger", "") or getattr(ta, "reading_seek_label", "") or "").strip()
    if trig and len(trig) < 100:
        ax.text(
            0.02,
            0.04,
            trig[:90],
            transform=ax.transAxes,
            color="#c9d1d9",
            fontsize=6.8,
            ha="left",
            va="bottom",
            zorder=5,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#161b22", edgecolor="#30363d", alpha=0.92),
        )
