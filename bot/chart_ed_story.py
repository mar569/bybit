"""Режим графика «как у Ed»: зона → наклонная поддержка → путь → short-tool вперёд."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_position_boxes import draw_forward_short_projection
from .chart_range_breakdown_draw import (
    _draw_fade_top_resistance_story,
    _last_impulse_bar_index,
)
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

CHART_BG = "#0d1117"
CHART_TEXT = "#e6edf3"

ED_STORY_TRAILING = 0.46


def use_ed_story_chart(ta: TAAnalysisResult) -> bool:
    """Deep / signal / manual: RBR fade_top, await_break, retest."""
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return False
    return str(rbr.get("phase") or "") in {"fade_top", "await_break", "retest"}


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x_span(bars: list[KlineBar]) -> tuple[float, float]:
    i0 = max(0, len(bars) - min(len(bars), 120))
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    return x0, max(x1, x0 + 0.001)


def _draw_watch_entry_band(ax: plt.Axes, bars: list[KlineBar], rbr: dict) -> None:
    el, eh = rbr.get("entry_lo"), rbr.get("entry_hi")
    if not el or not eh or float(eh) <= float(el):
        return
    x0, x1 = _x_span(bars)
    lo, hi = float(el), float(eh)
    ax.add_patch(
        Rectangle(
            (x0, lo),
            x1 - x0,
            hi - lo,
            facecolor="#e6edf3",
            edgecolor="#e6edf3",
            alpha=0.10,
            linewidth=1.2,
            linestyle="--",
            zorder=2,
        )
    )
    ax.text(
        x0 + (x1 - x0) * 0.02,
        hi,
        f"  зона входа {fmt_price(lo)}–{fmt_price(hi)}  ",
        color=CHART_TEXT,
        fontsize=7.5,
        fontweight="bold",
        va="bottom",
        ha="left",
        zorder=5,
        bbox=dict(boxstyle="round,pad=0.2", facecolor=CHART_BG, edgecolor="#8b949e", alpha=0.92),
    )


def _draw_impulse_support_line(ax: plt.Axes, bars: list[KlineBar]) -> None:
    if len(bars) < 12:
        return
    imp_i = _last_impulse_bar_index(bars, lookback=18)
    seg_start = max(0, imp_i - 2)
    seg = bars[seg_start:]
    swing: list[tuple[int, float]] = []
    for j in range(1, len(seg) - 1):
        i = seg_start + j
        if seg[j].low <= seg[j - 1].low and seg[j].low <= seg[j + 1].low:
            swing.append((i, float(seg[j].low)))
    if len(swing) < 2:
        swing = [(imp_i, float(bars[imp_i].low)), (len(bars) - 1, float(bars[-1].low))]
    else:
        swing = swing[-4:]
    i0, p0 = swing[0]
    i1, p1 = swing[-1]
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, i1))
    ax.plot([x0, x1], [p0, p1], color="#58a6ff", linewidth=1.55, alpha=0.88, zorder=4)
    ax.text(
        x0, p0,
        "  поддержка после импульса  ",
        color="#58a6ff",
        fontsize=6.8,
        va="top",
        ha="left",
        zorder=5,
    )


def _draw_story_caption(ax: plt.Axes, ta: TAAnalysisResult, rbr: dict) -> None:
    phase = str(rbr.get("phase") or "")
    label = str(rbr.get("label_ru") or "").strip()
    if phase in {"fade_top", "await_break"}:
        line = "Смотрим реакцию у зоны — шорт только после отказа, не в импульс."
    elif phase == "retest":
        line = "Retest пола — шорт только после подтверждения."
    else:
        line = label or "Сценарий на графике."
    ax.text(
        0.5,
        0.97,
        line,
        transform=ax.transAxes,
        va="top",
        ha="center",
        color=CHART_TEXT,
        fontsize=7.4,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.95),
    )


def draw_ed_story_layers(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Минимум слоёв: зона, наклон, RBR-путь, forward short-tool."""
    if not bars:
        return
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return

    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    targets = [float(t) for t in (rbr.get("targets") or []) if t]
    phase = str(rbr.get("phase") or "")

    _draw_watch_entry_band(ax, bars, rbr)
    _draw_impulse_support_line(ax, bars)

    if phase in {"fade_top", "await_break"} and floor > 0 and ceil > floor:
        _draw_fade_top_resistance_story(
            ax, bars, floor=floor, ceil=ceil, targets=targets, ed_story=True,
        )
    else:
        from .chart_range_breakdown_draw import draw_range_breakdown_retest_path

        draw_range_breakdown_retest_path(ax, bars, ta)

    draw_forward_short_projection(ax, bars, ta)
    _draw_story_caption(ax, ta, rbr)
