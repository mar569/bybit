"""Единая профессиональная отрисовка PNG для любого символа (crypto perp и др.).

Один стек слоёв из ChartSpec: паттерн → тренды → уровни → фаза RBR.
Без дубля teaching/RBR-path/legacy verdict-боксов.
"""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .chart_display_policy import (
    chart_teaching_tags_enabled,
    chart_trade_plan_on_chart_enabled,
)
from .core.playbook.chart_spec import ChartSpec
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)


def _draw_trend_lines_limited(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult, *, max_lines: int = 2) -> int:
    lines = list(getattr(ta, "trend_lines", None) or [])
    if not lines or not bars:
        return 0
    from .chart_renderer import _draw_extended_trend_lines

    subset = lines[: max(1, max_lines)]
    if len(subset) == len(lines):
        _draw_extended_trend_lines(ax, bars, ta)
        return len(subset)
    class _Wrap:
        __slots__ = ("_ta", "_lines")

        def __init__(self, base: TAAnalysisResult, tl: list) -> None:
            self._ta = base
            self._lines = tl

        def __getattr__(self, name: str):
            if name == "trend_lines":
                return self._lines
            return getattr(self._ta, name)

    _draw_extended_trend_lines(ax, bars, _Wrap(ta, subset))
    return len(subset)


def _draw_ta_structure_levels(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    from .chart_ed_story import _visible_x_span
    from .chart_level_labels import level_tag

    x0, x1 = _visible_x_span(ax, bars)
    n = 0
    for attr, kind, color in (
        ("breakout_level", "break_up", "#f0c040"),
        ("breakdown_level", "break_down", "#3fb950"),
    ):
        p = float(getattr(ta, attr, 0) or 0)
        if p <= 0:
            continue
        ax.hlines(p, x0, x1, colors=color, linewidth=1.25, alpha=0.85, zorder=4)
        if chart_teaching_tags_enabled():
            ax.text(x0, p, f"  {level_tag(kind, p)}  ", color=color, fontsize=6.8, va="center", zorder=5)
        n += 1
    if bool(getattr(ta, "post_pump", False)) and bars:
        hi = max(float(b.high) for b in bars[-min(24, len(bars)) :])
        ax.hlines(hi, x0, x1, colors="#f85149", linewidth=1.1, alpha=0.75, linestyle="--", zorder=4)
        if chart_teaching_tags_enabled():
            from .chart_level_labels import level_tag as lt

            ax.text(x0, hi, f"  local H {lt('structure', hi)}  ", color="#f85149", fontsize=6.8, va="bottom", zorder=5)
        n += 1
    return n


def _draw_primary_pattern(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    if not getattr(ta, "reading_accept_pattern", True):
        return False
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if not primary and not patterns:
        return False
    try:
        from .chart_pattern_draw import draw_chart_patterns
        from .pattern_specs import MIN_DRAW_CONFIDENCE

        draw_chart_patterns(
            ax,
            bars,
            patterns,
            max_patterns=1,
            min_confidence=max(0.68, float(MIN_DRAW_CONFIDENCE)),
            force_primary=primary,
            draw_target_labels=False,
        )
        return True
    except Exception:
        logger.debug("primary pattern draw skipped", exc_info=True)
        return False


def _draw_rbr_phase_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult, spec: ChartSpec) -> None:
    rbr = get_rbr_from_ta(ta)
    if not rbr or not spec.rbr_phase:
        return
    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    if floor <= 0 or ceil <= floor:
        return
    try:
        from .chart_ed_story import _draw_consolidation_context

        _draw_consolidation_context(ax, bars, ta, floor=floor, ceil=ceil)
    except Exception:
        logger.debug("consolidation context skipped", exc_info=True)

    phase = spec.rbr_phase
    if phase in {"retest", "broken"}:
        try:
            from .chart_range_breakdown_draw import draw_range_breakdown_retest_path

            draw_range_breakdown_retest_path(ax, bars, ta)
        except Exception:
            logger.debug("RBR retest path skipped", exc_info=True)


def _draw_plan_horizontal_bands(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    from matplotlib.patches import Rectangle

    from .chart_plan_display import build_display_plan
    from .chart_position_boxes import plan_for_display
    from .plan_staleness import plan_is_stale
    from .ta_analysis import fmt_price

    if not chart_trade_plan_on_chart_enabled() or plan_is_stale(ta) or not bars:
        return False
    raw = plan_for_display(ta)
    if raw is None:
        return False
    side, _entry, entry_lo, entry_hi, stop, tp = raw
    disp = build_display_plan(
        side=side,
        entry=raw[1],
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        tp=tp,
    )
    from .chart_ed_story import _visible_x_span

    x0, x1 = _visible_x_span(ax, bars)
    w = max(x1 - x0, 0.001)

    def band(y0: float, y1: float, rgb: tuple[int, int, int], alpha: float, label: str) -> None:
        lo, hi = min(y0, y1), max(y0, y1)
        if hi <= lo:
            return
        ax.add_patch(
            Rectangle((x0, lo), w, hi - lo, facecolor=tuple(c / 255 for c in rgb), alpha=alpha, zorder=2)
        )
        if chart_teaching_tags_enabled():
            ax.text(x0 + w * 0.01, hi, f"  {label}  ", color="#e6edf3", fontsize=7, fontweight="bold", va="bottom", zorder=7)

    el, eh = float(disp.entry_lo), float(disp.entry_hi)
    if side == "short":
        band(el, eh, (227, 179, 65), 0.28, f"ENTRY {fmt_price(el)}–{fmt_price(eh)}")
        if stop > eh:
            band(eh, stop, (248, 81, 73), 0.32, f"STOP ~{disp.stop_label}")
        if tp < el:
            band(tp, el, (88, 166, 255), 0.28, f"TP1 ~{disp.tp_label}")
    else:
        band(el, eh, (227, 179, 65), 0.28, f"ENTRY {fmt_price(el)}–{fmt_price(eh)}")
        if stop < el:
            band(stop, el, (248, 81, 73), 0.32, f"STOP ~{disp.stop_label}")
        if tp > eh:
            band(eh, tp, (88, 166, 255), 0.28, f"TP1 ~{disp.tp_label}")
    return True


def _draw_trade_plan_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not chart_trade_plan_on_chart_enabled():
        return
    from .chart_plan_chart_gate import plan_ok_to_draw_on_chart

    if not plan_ok_to_draw_on_chart(ta):
        return
    try:
        if not _draw_plan_horizontal_bands(ax, bars, ta):
            from .chart_position_boxes import draw_forward_plan_boxes

            draw_forward_plan_boxes(ax, bars, ta, use_xlim=True)
    except Exception:
        logger.debug("trade plan mpl skipped", exc_info=True)


def draw_pro_visual_mpl(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    spec: ChartSpec,
    *,
    interval_minutes: int = 15,
) -> int:
    """Returns approximate layer count (for TV parity logging)."""
    if not bars:
        return 0
    drawn = 0

    try:
        from .chart_market_read import draw_swing_structure_mpl

        drawn += draw_swing_structure_mpl(ax, bars, ta)
    except Exception:
        logger.debug("swing structure skipped", exc_info=True)

    if spec.show_primary_pattern:
        if _draw_primary_pattern(ax, bars, ta):
            drawn += 1

    drawn += _draw_trend_lines_limited(ax, bars, ta, max_lines=2)

    from .core.playbook.chart_mpl_spec import draw_mpl_spec_core

    rbr = get_rbr_from_ta(ta)
    scenario_rbr = bool(spec.rbr_phase and rbr)
    drawn += max(draw_mpl_spec_core(ax, bars, ta, spec, levels_only=scenario_rbr), 0)

    if not scenario_rbr:
        drawn += _draw_ta_structure_levels(ax, bars, ta)

    if scenario_rbr:
        _draw_rbr_phase_mpl(ax, bars, ta, spec)
        drawn += 2
    elif spec.show_smc_fvg:
        try:
            from .chart_education_visual import draw_education_visuals_mpl

            draw_education_visuals_mpl(ax, bars, ta)
            drawn += 1
        except Exception:
            logger.debug("smc mpl skipped", exc_info=True)

    if spec.show_trade_plan:
        _draw_trade_plan_mpl(ax, bars, ta)

    return max(drawn, 1)
