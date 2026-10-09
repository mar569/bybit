"""Полный визуальный разбор на PNG: тренды, паттерны, план (MAP-style), без «сплошных» RBR-коробок."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_display_policy import (
    chart_teaching_tags_enabled,
    chart_trade_plan_on_chart_enabled,
    ed_chart_rich_analysis_enabled,
)
from .chart_ed_story import _visible_x_span
from .chart_label_layout import LabelBoard
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

_PLAN_GREEN = (30, 140, 80, 200)
_PLAN_RED = (248, 81, 73, 190)
_PLAN_BLUE = (88, 166, 255, 170)
_PLAN_GOLD = (227, 179, 65, 160)


def _draw_plan_horizontal_bands(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    """Полосы ENTRY / STOP / TP на всю видимую ширину (как MAP 3/3)."""
    from .chart_plan_display import build_display_plan
    from .chart_position_boxes import plan_for_display
    from .plan_staleness import plan_is_stale

    if not chart_trade_plan_on_chart_enabled() or plan_is_stale(ta) or not bars:
        return False
    raw = plan_for_display(ta)
    if raw is None:
        return False
    side, entry, entry_lo, entry_hi, stop, tp = raw
    disp = build_display_plan(
        side=side,
        entry=entry,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        tp=tp,
    )
    x0, x1 = _visible_x_span(ax, bars)
    w = max(x1 - x0, 0.001)
    drawn = False

    def band(y0: float, y1: float, rgba: tuple[int, ...], label: str) -> None:
        nonlocal drawn
        lo, hi = min(y0, y1), max(y0, y1)
        if hi <= lo:
            return
        ax.add_patch(
            Rectangle((x0, lo), w, hi - lo, facecolor=tuple(c / 255 for c in rgba[:3]), alpha=rgba[3] / 255, zorder=2)
        )
        if chart_teaching_tags_enabled():
            ax.text(x0 + w * 0.01, hi, f"  {label}  ", color="#e6edf3", fontsize=7, fontweight="bold", va="bottom", zorder=7)
        drawn = True

    el, eh = float(disp.entry_lo), float(disp.entry_hi)
    if side == "short":
        band(el, eh, _PLAN_GOLD, f"ENTRY {fmt_price(el)}–{fmt_price(eh)}")
        if stop > eh:
            band(eh, stop, _PLAN_RED, f"STOP ~{disp.stop_label}")
        if tp < el:
            band(tp, el, _PLAN_BLUE, f"TP1 ~{disp.tp_label}")
    else:
        band(el, eh, _PLAN_GOLD, f"ENTRY {fmt_price(el)}–{fmt_price(eh)}")
        if stop < el:
            band(stop, el, _PLAN_RED, f"STOP ~{disp.stop_label}")
        if tp > eh:
            band(eh, tp, _PLAN_BLUE, f"TP1 ~{disp.tp_label}")
    return drawn


def _draw_structure_levels(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Пол/потолок/триггеры — линии, без заливки на весь экран."""
    from .range_breakdown_retest import get_rbr_from_ta

    x0, x1 = _visible_x_span(ax, bars)
    rbr = get_rbr_from_ta(ta)
    if rbr:
        floor = float(rbr.get("range_bottom") or 0)
        ceil = float(rbr.get("range_top") or 0)
        if floor > 0:
            ax.hlines(floor, x0, x1, colors="#3fb950", linewidth=1.2, alpha=0.85, zorder=4)
            if chart_teaching_tags_enabled():
                ax.text(x0, floor, f"  пол {fmt_price(floor)}  ", color="#3fb950", fontsize=6.8, va="top", zorder=5)
        if ceil > 0:
            ax.hlines(ceil, x0, x1, colors="#f0c040", linewidth=1.2, alpha=0.85, zorder=4)
            if chart_teaching_tags_enabled():
                ax.text(x0, ceil, f"  потолок {fmt_price(ceil)}  ", color="#f0c040", fontsize=6.8, va="bottom", zorder=5)
        return
    for price, col, tag in (
        (getattr(ta, "breakout_level", None), "#f0c040", "сопр"),
        (getattr(ta, "breakdown_level", None), "#3fb950", "подд"),
    ):
        if not price or float(price) <= 0:
            continue
        p = float(price)
        ax.hlines(p, x0, x1, colors=col, linewidth=1.2, alpha=0.8, zorder=4)
        if chart_teaching_tags_enabled():
            ax.text(x0, p, f"  {tag} {fmt_price(p)}  ", color=col, fontsize=6.8, va="center", zorder=5)


def draw_rich_analysis_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    mode: str,
    interval_minutes: int = 15,
) -> LabelBoard:
    """Единый PRO-разбор для /ta и сигналов."""
    board = LabelBoard()
    if not bars or not ed_chart_rich_analysis_enabled():
        return board

    from .chart_rbr_refresh import refresh_rbr_for_chart

    ta = refresh_rbr_for_chart(ta, bars)

    try:
        from .chart_renderer import _draw_consolidation_box, _draw_extended_trend_lines

        _draw_consolidation_box(ax, bars, ta)
        _draw_extended_trend_lines(ax, bars, ta)
    except Exception:
        logger.debug("consolidation/trend layers failed", exc_info=True)

    _draw_structure_levels(ax, bars, ta)
    has_plan_bands = _draw_plan_horizontal_bands(ax, bars, ta)

    from .chart_ed_minimal import draw_pro_teaching_layers

    draw_pro_teaching_layers(ax, bars, ta, mode=mode, compact_rbr=False)

    if not has_plan_bands:
        from .range_breakdown_retest import get_rbr_from_ta

        if get_rbr_from_ta(ta):
            try:
                from .chart_range_breakdown_draw import draw_range_breakdown_retest_path

                draw_range_breakdown_retest_path(ax, bars, ta)
            except Exception:
                logger.debug("RBR path skipped", exc_info=True)

    if chart_trade_plan_on_chart_enabled():
        try:
            from .chart_position_boxes import draw_forward_plan_boxes

            draw_forward_plan_boxes(ax, bars, ta, use_xlim=True)
        except Exception:
            logger.debug("forward plan boxes failed", exc_info=True)

    if chart_teaching_tags_enabled():
        from .chart_teaching_tags import story_banner_line

        line = story_banner_line(ta, mode=mode if mode != "legacy_manual" else "ed_story")
        ax.text(
            0.5,
            0.04,
            line,
            transform=ax.transAxes,
            va="bottom",
            ha="center",
            color="#e6edf3",
            fontsize=7.0,
            zorder=12,
            bbox=dict(boxstyle="round,pad=0.32", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.96),
        )
    return board
