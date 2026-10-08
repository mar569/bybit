"""Учебная графика бота: SMC, манипуляции, ликвидность, BOS — без простыни текста."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_analysis_text import structure_break_label_ru
from .chart_display_policy import chart_teaching_tags_enabled, ed_chart_visual_only
from .ta_analysis import TAAnalysisResult, fmt_price


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x_mpl(bars: list[KlineBar], idx: int) -> float:
    return mdates.date2num(_idx_to_date(bars, idx))


def _x_span(bars: list[KlineBar], *, tail: int | None = None) -> tuple[float, float]:
    tail = tail or min(120, len(bars))
    i0 = max(0, len(bars) - tail)
    x0 = _x_mpl(bars, i0)
    x1 = _x_mpl(bars, len(bars) - 1)
    return x0, x1


def draw_smc_visuals_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None or not bars:
        return
    show_text = chart_teaching_tags_enabled() or not ed_chart_visual_only()
    x0, x1 = _x_span(bars, tail=min(80, len(bars)))
    width = max(x1 - x0, 0.001)

    for gap in list(getattr(smc, "fvgs", None) or [])[-2:]:
        color = "#3ddc84" if getattr(gap, "direction", "") == "bullish" else "#ff6b6b"
        xg0 = _x_mpl(bars, max(0, gap.start_idx))
        xg1 = _x_mpl(bars, min(len(bars) - 1, gap.end_idx))
        ax.add_patch(
            Rectangle(
                (xg0, gap.bottom),
                max(xg1 - xg0, width * 0.02),
                gap.top - gap.bottom,
                facecolor=color,
                edgecolor=color,
                alpha=0.16,
                linewidth=0.6,
                zorder=2,
            )
        )

    if getattr(smc, "discount_zone", None):
        lo, hi = smc.discount_zone
        ax.add_patch(
            Rectangle((x0, lo), width, hi - lo, facecolor="#3fb950", edgecolor="#3fb950", alpha=0.10, zorder=1)
        )
        if show_text:
            ax.text(x0, hi, "  спрос", color="#3fb950", fontsize=6.5, va="bottom")

    if getattr(smc, "premium_zone", None):
        lo, hi = smc.premium_zone
        ax.add_patch(
            Rectangle((x0, lo), width, hi - lo, facecolor="#f85149", edgecolor="#f85149", alpha=0.10, zorder=1)
        )
        if show_text:
            ax.text(x0, hi, "  предложение", color="#f85149", fontsize=6.5, va="bottom")

    if getattr(smc, "structure_break_level", None):
        lv = float(smc.structure_break_level)
        kind = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"))
        ax.axhline(lv, color="#f0c040", linestyle="-.", linewidth=1.1, alpha=0.88, zorder=4)
        if show_text:
            ax.text(x1, lv, f" {kind}", color="#f0c040", fontsize=6.8, va="bottom")

    for lv in list(getattr(smc, "liquidity_levels", None) or [])[:5]:
        ls = ":" if "daily" in getattr(lv, "kind", "") or "weekly" in getattr(lv, "kind", "") else "--"
        ax.axhline(float(lv.price), color="#8899aa", linestyle=ls, linewidth=0.7, alpha=0.55, zorder=3)

    for marker in list(getattr(smc, "markers", None) or [])[:6]:
        if marker.index >= len(bars):
            continue
        ts = _x_mpl(bars, marker.index)
        kind = str(getattr(marker, "kind", "") or "")
        if kind == "sweep":
            color = "#ffd33d"
            ax.plot(ts, marker.price, marker="o", mfc="none", mec=color, ms=10, mew=2.0, zorder=7)
        elif kind in {"bos", "expansion", "mss"}:
            color = "#58a6ff" if getattr(marker, "direction", "") == "long" else "#ff7b72"
            ax.plot(ts, marker.price, marker="*", color=color, ms=9, linestyle="None", zorder=7)
        elif kind in {"equal_highs", "equal_lows"}:
            ax.axhline(float(marker.price), color="#d2a8ff", linestyle=":", linewidth=0.8, alpha=0.7, zorder=3)
        if show_text and getattr(marker, "label", ""):
            ax.text(ts, marker.price, f" {marker.label[:12]}", color="#c9d1d9", fontsize=6, va="bottom")


def draw_order_blocks_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None or not bars:
        return
    show_text = chart_teaching_tags_enabled() or not ed_chart_visual_only()
    x0, x1 = _x_span(bars, tail=min(100, len(bars)))
    for ob in list(getattr(smc, "order_blocks", None) or [])[-2:]:
        try:
            top, bot = float(ob.top), float(ob.bottom)
        except (TypeError, ValueError, AttributeError):
            continue
        if top <= bot:
            continue
        col = "#3fb950" if getattr(ob, "direction", "") == "bullish" else "#f85149"
        ax.add_patch(
            Rectangle((x0, bot), x1 - x0, top - bot, facecolor=col, edgecolor=col, alpha=0.13, linewidth=0.8, zorder=2)
        )
        if show_text:
            tag = "OB↑" if col == "#3fb950" else "OB↓"
            ax.text(x0, top, f"  {tag}", color=col, fontsize=6.5, va="bottom")


def draw_consolidation_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    cons = getattr(ta, "consolidation", None)
    if cons is None or not bars:
        return
    top, bot = float(cons.top), float(cons.bottom)
    if top <= bot:
        return
    x0, x1 = _x_span(bars)
    ax.add_patch(
        Rectangle((x0, bot), x1 - x0, top - bot, facecolor="#8b949e", edgecolor="#8b949e", alpha=0.11, zorder=1)
    )


def draw_education_visuals_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    draw_consolidation_mpl(ax, bars, ta)
    draw_smc_visuals_mpl(ax, bars, ta)
    draw_order_blocks_mpl(ax, bars, ta)


def draw_smc_visuals_tv(mapper, ax: plt.Axes, ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None:
        return
    show_tags = chart_teaching_tags_enabled()
    i0 = max(0, len(mapper.bars) - min(len(mapper.bars), 80))

    for gap in list(getattr(smc, "fvgs", None) or [])[-2:]:
        color = "#3ddc84" if getattr(gap, "direction", "") == "bullish" else "#ff6b6b"
        mapper.rect(
            ax,
            max(0, gap.start_idx),
            min(len(mapper.bars) - 1, gap.end_idx),
            gap.bottom,
            gap.top,
            color=color,
            alpha=0.16,
        )

    if getattr(smc, "discount_zone", None):
        lo, hi = smc.discount_zone
        mapper.rect(ax, i0, len(mapper.bars) - 1, lo, hi, color="#3fb950", alpha=0.10)
    if getattr(smc, "premium_zone", None):
        lo, hi = smc.premium_zone
        mapper.rect(ax, i0, len(mapper.bars) - 1, lo, hi, color="#f85149", alpha=0.10)

    if getattr(smc, "structure_break_level", None):
        lv = float(smc.structure_break_level)
        mapper.hline(ax, lv, color="#f0c040", lw=1.1, alpha=0.88, ls="-.")
        if show_tags:
            from .chart_analysis_text import structure_break_label_ru

            kind = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"))
            ax.text(mapper.x_start + 0.01, mapper.y(lv), f" {kind[:10]}", color="#f0c040", fontsize=6.5, va="bottom", zorder=8)

    for lv in list(getattr(smc, "liquidity_levels", None) or [])[:5]:
        mapper.hline(ax, float(lv.price), color="#8899aa", lw=0.7, alpha=0.5, ls=":")

    for marker in list(getattr(smc, "markers", None) or [])[:6]:
        if marker.index >= len(mapper.bars):
            continue
        kind = str(getattr(marker, "kind", "") or "")
        x, y = mapper.x(marker.index), mapper.y(marker.price)
        if kind == "sweep":
            ax.plot(x, y, marker="o", mfc="none", mec="#ffd33d", ms=9, mew=2.0, zorder=7)
            if show_tags:
                ax.text(x + 0.012, y, "SWEEP", color="#ffd33d", fontsize=6.5, va="center", zorder=8)
        elif kind in {"bos", "expansion", "mss"}:
            col = "#58a6ff" if getattr(marker, "direction", "") == "long" else "#ff7b72"
            ax.plot(x, y, marker="*", color=col, ms=8, linestyle="None", zorder=7)
            if show_tags:
                ax.text(x + 0.012, y, "BOS", color=col, fontsize=6.5, va="center", zorder=8)
        elif kind in {"equal_highs", "equal_lows"}:
            mapper.hline(ax, float(marker.price), color="#d2a8ff", lw=0.8, alpha=0.65, ls=":")


def draw_order_blocks_tv(mapper, ax: plt.Axes, ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None:
        return
    i0 = max(0, len(mapper.bars) - min(len(mapper.bars), 100))
    for ob in list(getattr(smc, "order_blocks", None) or [])[-2:]:
        try:
            top, bot = float(ob.top), float(ob.bottom)
        except (TypeError, ValueError, AttributeError):
            continue
        if top <= bot:
            continue
        col = "#3fb950" if getattr(ob, "direction", "") == "bullish" else "#f85149"
        mapper.rect(ax, i0, len(mapper.bars) - 1, bot, top, color=col, alpha=0.13)


def draw_consolidation_tv(mapper, ax: plt.Axes, ta: TAAnalysisResult) -> None:
    cons = getattr(ta, "consolidation", None)
    if cons is None:
        return
    top, bot = float(cons.top), float(cons.bottom)
    if top <= bot:
        return
    i0 = max(0, len(mapper.bars) - min(len(mapper.bars), 120))
    mapper.rect(ax, i0, len(mapper.bars) - 1, bot, top, color="#8b949e", alpha=0.11)


@contextlib.contextmanager
def _tv_pattern_x_coords(mapper):
    import bot.chart_pattern_draw as cpd

    def _x_at_tv(bars: list[KlineBar], idx: int) -> float:
        return mapper.x(idx)

    old = cpd._x_at
    cpd._x_at = _x_at_tv
    try:
        yield
    finally:
        cpd._x_at = old


def draw_patterns_tv_full(ax: plt.Axes, mapper, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    from .pattern_specs import MIN_DRAW_CONFIDENCE

    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if not getattr(ta, "reading_accept_pattern", True) or not (primary or patterns):
        return
    from .chart_pattern_draw import draw_chart_patterns

    with _tv_pattern_x_coords(mapper):
        draw_chart_patterns(
            ax,
            bars,
            patterns,
            max_patterns=1,
            min_confidence=max(0.65, float(MIN_DRAW_CONFIDENCE) - 0.03),
            force_primary=primary,
            draw_target_labels=False,
        )


def draw_education_visuals_tv(mapper, ax: plt.Axes, ta: TAAnalysisResult) -> None:
    draw_consolidation_tv(mapper, ax, ta)
    draw_smc_visuals_tv(mapper, ax, ta)
    draw_order_blocks_tv(mapper, ax, ta)
