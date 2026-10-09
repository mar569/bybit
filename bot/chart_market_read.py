"""Структура на PNG: последние swing H/L (все символы, не только RBR)."""
from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .chart_display_policy import chart_teaching_tags_enabled
from .chart_ed_story import _visible_x_span
from .ta_analysis import TAAnalysisResult, find_swing_points, fmt_price


@dataclass(frozen=True)
class SwingMark:
    price: float
    kind: str  # high | low
    index: int


def recent_swings(bars: list[KlineBar], *, max_highs: int = 2, max_lows: int = 2) -> list[SwingMark]:
    if len(bars) < 12:
        return []
    raw = find_swing_points(bars[-min(len(bars), 192) :], window=2)
    if not raw:
        return []
    offset = len(bars) - min(len(bars), 192)
    highs = [SwingMark(s.price, "high", s.index + offset) for s in raw if s.kind == "high"]
    lows = [SwingMark(s.price, "low", s.index + offset) for s in raw if s.kind == "low"]
    highs.sort(key=lambda s: s.index)
    lows.sort(key=lambda s: s.index)
    out: list[SwingMark] = highs[-max_highs:] + lows[-max_lows:]
    return out


def _near(price: float, level: float) -> bool:
    if level <= 0:
        return False
    return abs(price - level) / level <= 0.0012


def draw_swing_structure_mpl(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    skip_near: list[float] | None = None,
) -> int:
    if not bars:
        return 0
    skip = [float(x) for x in (skip_near or []) if x and float(x) > 0]
    rbr_floor = rbr_ceil = 0.0
    from .range_breakdown_retest import get_rbr_from_ta

    rbr = get_rbr_from_ta(ta)
    if rbr:
        rbr_floor = float(rbr.get("range_bottom") or 0)
        rbr_ceil = float(rbr.get("range_top") or 0)
        skip.extend(x for x in (rbr_floor, rbr_ceil) if x > 0)

    marks = recent_swings(bars)
    if not marks:
        return 0
    x0, x1 = _visible_x_span(ax, bars)
    drawn = 0
    hi_n = lo_n = 0
    for m in marks:
        if any(_near(m.price, s) for s in skip):
            continue
        if m.kind == "high":
            hi_n += 1
            if hi_n > 2:
                continue
            color, tag = "#f85149", f"H{hi_n} {fmt_price(m.price)}"
        else:
            lo_n += 1
            if lo_n > 2:
                continue
            color, tag = "#3fb950", f"L{lo_n} {fmt_price(m.price)}"
        ax.hlines(m.price, x0, x1, colors=color, linewidth=0.95, alpha=0.55, linestyles=":", zorder=3)
        if chart_teaching_tags_enabled():
            ax.text(x1, m.price, f"  {tag}  ", color=color, fontsize=6.5, va="center", ha="right", zorder=4)
        drawn += 1
    return drawn


def draw_swing_structure_tv(ax: plt.Axes, mapper: object, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    if not bars:
        return 0
    marks = recent_swings(bars)
    if not marks:
        return 0
    drawn = 0
    hi_n = lo_n = 0
    for m in marks:
        if not mapper.price_visible(m.price):  # type: ignore[attr-defined]
            continue
        if m.kind == "high":
            hi_n += 1
            if hi_n > 2:
                continue
            color, tag = "#f85149", f"H{hi_n}"
        else:
            lo_n += 1
            if lo_n > 2:
                continue
            color, tag = "#3fb950", f"L{lo_n}"
        mapper.hline(ax, m.price, color=color, lw=0.9, alpha=0.5, ls=":")  # type: ignore[attr-defined]
        if chart_teaching_tags_enabled():
            ax.text(
                mapper.x_start + 0.992,  # type: ignore[attr-defined]
                mapper.y(m.price),  # type: ignore[attr-defined]
                f" {tag} {fmt_price(m.price)} ",
                color=color,
                fontsize=6,
                ha="right",
                va="center",
                zorder=5,
            )
        drawn += 1
    return drawn
