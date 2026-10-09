"""TV/chart rendering driven by ChartSpec (no duplicate legacy RBR lines)."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from .chart_draw import draw_playbook_rbr_phase_tv, draw_playbook_spec_tv
from .chart_spec import ChartSpec
from ...chart_display_policy import ed_chart_spec_layers_enabled
from ...bybit_klines import KlineBar
from ...ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)


def chart_spec_for_ta(ta: TAAnalysisResult, *, symbol: str = "") -> ChartSpec | None:
    if not ed_chart_spec_layers_enabled():
        return None
    try:
        from .cache import get_or_run_playbook

        return get_or_run_playbook(ta, symbol=symbol).chart_spec
    except Exception:
        logger.debug("chart_spec_for_ta failed", exc_info=True)
        return None


def try_draw_tv_playbook_layers(
    ax: plt.Axes,
    mapper: object,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    *,
    symbol: str = "",
) -> int | None:
    """If spec-mode on, draw all TV layers and return count; else None → legacy path."""
    spec = chart_spec_for_ta(ta, symbol=symbol)
    if spec is None:
        return None

    from ...chart_breakout_markers import collect_breakout_retest_events
    from ...chart_rbr_refresh import refresh_rbr_for_chart
    from ...range_breakdown_retest import get_rbr_from_ta
    from ...chart_tv_pro_overlay import (
        _draw_breakout_tv,
        _draw_context_zone_tv,
        _draw_observation_baseline_tv,
        _draw_sweep_tv,
        _draw_tv_story_banner,
        draw_primary_pattern_tv_layer,
    )

    ta = refresh_rbr_for_chart(ta, bars)
    spec = chart_spec_for_ta(ta, symbol=symbol) or spec
    rbr_active = bool(spec.rbr_phase and get_rbr_from_ta(ta))

    layers = draw_playbook_spec_tv(ax, mapper, ta)
    if rbr_active:
        try:
            from .chart_scenario_tv import draw_rbr_scenario_narrative_tv

            layers += draw_rbr_scenario_narrative_tv(ax, mapper, ta)
        except Exception:
            logger.debug("TV RBR scenario narrative skipped", exc_info=True)
    else:
        layers += draw_playbook_rbr_phase_tv(ax, mapper, ta, spec)

    if spec.show_primary_pattern:
        draw_primary_pattern_tv_layer(ax, mapper, ta)
        if getattr(ta, "primary_chart_pattern", None):
            layers += 1

    if spec.show_smc_fvg and not rbr_active:
        from ...chart_education_visual import draw_education_visuals_tv

        draw_education_visuals_tv(mapper, ax, ta)
        layers += 1

    if not rbr_active:
        _draw_sweep_tv(ax, mapper, ta)
        _draw_context_zone_tv(ax, mapper, ta)
        _draw_breakout_tv(ax, mapper, ta)
        if collect_breakout_retest_events(mapper.bars, ta, max_events=1):  # type: ignore[attr-defined]
            layers += 1
        layers += _draw_observation_baseline_tv(ax, mapper, ta)

    mode = "ed_story" if spec.rbr_phase else "observation"
    _draw_tv_story_banner(ax, ta, mode=mode)
    layers += 1
    return max(layers, 2)


def draw_mpl_playbook_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    interval_minutes: int = 15,
) -> object | None:
    """Matplotlib: глобальный PRO-стек (ChartSpec + chart_pro_visual)."""
    spec = chart_spec_for_ta(ta, symbol=symbol)
    if spec is None:
        return None

    from ...chart_label_layout import LabelBoard
    from ...chart_rbr_refresh import refresh_rbr_for_chart
    from ...chart_pro_visual import draw_pro_visual_mpl

    ta = refresh_rbr_for_chart(ta, bars)
    spec = chart_spec_for_ta(ta, symbol=symbol) or spec

    board = LabelBoard()
    draw_pro_visual_mpl(ax, bars, ta, spec, interval_minutes=interval_minutes)

    from ...chart_display_policy import chart_teaching_tags_enabled

    if chart_teaching_tags_enabled() and spec.rbr_phase:
        from ...chart_teaching_tags import story_banner_line

        line = story_banner_line(ta, mode="ed_story")
        if line:
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
