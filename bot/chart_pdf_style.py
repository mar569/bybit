"""Отрисовка по материалам docs/.cursor_pdf_pages: один сетап на график.

Приоритет: паттерн (линии + вход/SL/цель) → SMC (BOS, свип, OB) → range (поддержка/сопр.) → минимум swing.
Без RBR fade_top / synthetic ghost / каши слоёв.
"""
from __future__ import annotations

import logging
from typing import Literal

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_display_policy import chart_teaching_tags_enabled, ed_pdf_chart_style_enabled
from .chart_ed_story import _visible_x_span
from .chart_level_labels import level_tag
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

PdfChartMode = Literal["pattern", "smc", "range", "structure"]


def sanitize_rbr_for_pdf(ta: TAAnalysisResult, bars: list[KlineBar] | None) -> TAAnalysisResult:
    """Убрать synthetic RBR, если цена не у края range (как в PDF — без «отказ у потолка» в середине)."""
    if not ed_pdf_chart_style_enabled():
        return ta
    rbr = get_rbr_from_ta(ta)
    if not rbr or not bars:
        return ta
    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    if floor <= 0 or ceil <= floor or cur <= 0:
        return ta
    pos = (cur - floor) / (ceil - floor)
    phase = str(rbr.get("phase") or "")
    drop = bool(rbr.get("synthetic")) or phase == "fade_top"
    if drop and phase == "fade_top" and pos < 0.72:
        mm = dict(getattr(ta, "market_metrics", None) or {})
        mm.pop("range_breakdown_retest", None)
        ta.market_metrics = mm
        return ta
    if drop and phase == "await_break" and pos > 0.35 and pos < 0.65:
        mm = dict(getattr(ta, "market_metrics", None) or {})
        mm.pop("range_breakdown_retest", None)
        ta.market_metrics = mm
    return ta


def resolve_pdf_chart_mode(ta: TAAnalysisResult, bars: list[KlineBar]) -> PdfChartMode:
    if not ed_pdf_chart_style_enabled():
        return "structure"
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if getattr(ta, "reading_accept_pattern", True) and (primary or patterns):
        conf = float(getattr(primary, "confidence", 0) or 0) if primary else 0.0
        if primary and conf >= 0.58:
            return "pattern"
        if patterns:
            best = max(patterns, key=lambda p: float(getattr(p, "confidence", 0) or 0))
            if float(getattr(best, "confidence", 0) or 0) >= 0.62:
                return "pattern"

    smc = getattr(ta, "smc", None)
    if smc is not None:
        if getattr(smc, "structure_break", False) or getattr(smc, "liquidity_sweep", False):
            return "smc"
        if list(getattr(smc, "order_blocks", None) or [])[-1:]:
            return "smc"
        if list(getattr(smc, "markers", None) or []):
            return "smc"

    if getattr(ta, "consolidation", None) is not None:
        return "range"
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > 0 and brdn > 0 and brk > brdn:
        return "range"
    return "structure"


def _draw_range_pdf(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    cons = getattr(ta, "consolidation", None)
    x0, x1 = _visible_x_span(ax, bars)
    w = max(x1 - x0, 0.001)
    top = bot = 0.0
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
    else:
        bot = float(getattr(ta, "breakdown_level", 0) or 0)
        top = float(getattr(ta, "breakout_level", 0) or 0)
    if top <= bot:
        return 0
    ax.add_patch(
        Rectangle(
            (x0, bot),
            w,
            top - bot,
            facecolor="#8b949e",
            alpha=0.07,
            edgecolor="#484f58",
            linewidth=0.8,
            linestyle="--",
            zorder=1,
        )
    )
    ax.hlines(top, x0, x1, colors="#f0c040", linewidth=1.2, alpha=0.9, zorder=4)
    ax.hlines(bot, x0, x1, colors="#3fb950", linewidth=1.2, alpha=0.9, zorder=4)
    if chart_teaching_tags_enabled():
        ax.text(x0, top, f"  {level_tag('break_up', top)}  ", color="#f0c040", fontsize=7, va="bottom", zorder=5)
        ax.text(x0, bot, f"  {level_tag('break_down', bot)}  ", color="#3fb950", fontsize=7, va="top", zorder=5)
    return 2


def _draw_pattern_pdf(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    from .chart_pattern_draw import draw_chart_patterns
    from .pattern_specs import MIN_DRAW_CONFIDENCE

    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    draw_chart_patterns(
        ax,
        bars,
        patterns,
        max_patterns=1,
        min_confidence=max(0.58, float(MIN_DRAW_CONFIDENCE) - 0.08),
        force_primary=primary,
        draw_target_labels=True,
    )
    return 3 if primary or patterns else 0


def _draw_smc_pdf(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    from .chart_education_visual import draw_order_blocks_mpl, draw_smc_visuals_mpl

    draw_smc_visuals_mpl(ax, bars, ta)
    draw_order_blocks_mpl(ax, bars, ta)
    return 2


def _draw_structure_minimal(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    from .chart_market_read import draw_swing_structure_mpl

    n = draw_swing_structure_mpl(ax, bars, ta)
    x0, x1 = _visible_x_span(ax, bars)
    for attr, kind, color in (("breakout_level", "break_up", "#f0c040"), ("breakdown_level", "break_down", "#3fb950")):
        p = float(getattr(ta, attr, 0) or 0)
        if p <= 0:
            continue
        ax.hlines(p, x0, x1, colors=color, linewidth=1.15, alpha=0.82, zorder=4)
        if chart_teaching_tags_enabled():
            ax.text(x0, p, f"  {level_tag(kind, p)}  ", color=color, fontsize=6.8, va="center", zorder=5)
        n += 1
    return max(n, 1)


def draw_pdf_style_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    if not bars:
        return 0
    ta = sanitize_rbr_for_pdf(ta, bars)
    mode = resolve_pdf_chart_mode(ta, bars)
    if mode == "pattern":
        return max(_draw_pattern_pdf(ax, bars, ta), 1)
    if mode == "smc":
        return max(_draw_smc_pdf(ax, bars, ta), 1)
    if mode == "range":
        return max(_draw_range_pdf(ax, bars, ta), 1)
    return _draw_structure_minimal(ax, bars, ta)


def draw_pdf_style_tv(ax: plt.Axes, mapper: object, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    ta = sanitize_rbr_for_pdf(ta, bars)
    mode = resolve_pdf_chart_mode(ta, bars)
    layers = 0
    if mode == "pattern":
        from .chart_education_visual import draw_patterns_tv_full

        draw_patterns_tv_full(ax, mapper, bars, ta)
        layers = 3
    elif mode == "smc":
        from .chart_education_visual import draw_education_visuals_tv

        draw_education_visuals_tv(mapper, ax, ta)
        layers = 2
    elif mode == "range":
        cons = getattr(ta, "consolidation", None)
        top = float(getattr(cons, "top", 0) or getattr(ta, "breakout_level", 0) or 0)
        bot = float(getattr(cons, "bottom", 0) or getattr(ta, "breakdown_level", 0) or 0)
        if top > bot:
            i0 = getattr(mapper, "vis_start", 0)
            mapper.rect(ax, i0, len(mapper.bars) - 1, bot, top, color="#8b949e", alpha=0.08)  # type: ignore[attr-defined]
            mapper.hline(ax, top, color="#f0c040", lw=1.2, alpha=0.88)  # type: ignore[attr-defined]
            mapper.hline(ax, bot, color="#3fb950", lw=1.2, alpha=0.88)  # type: ignore[attr-defined]
            layers = 2
    else:
        from .chart_tv_pro_overlay import _draw_observation_baseline_tv

        layers = _draw_observation_baseline_tv(ax, mapper, ta)
    return max(layers, 1)
