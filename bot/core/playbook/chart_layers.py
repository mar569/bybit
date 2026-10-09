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
    from ...chart_tv_pro_overlay import (
        _draw_breakout_tv,
        _draw_context_zone_tv,
        _draw_sweep_tv,
        _draw_tv_story_banner,
        draw_primary_pattern_tv_layer,
    )

    layers = draw_playbook_spec_tv(ax, mapper, ta)
    layers += draw_playbook_rbr_phase_tv(ax, mapper, ta, spec)

    if spec.show_primary_pattern:
        draw_primary_pattern_tv_layer(ax, mapper, ta)
        if getattr(ta, "primary_chart_pattern", None):
            layers += 1

    if spec.show_smc_fvg:
        from ...chart_education_visual import draw_education_visuals_tv

        draw_education_visuals_tv(mapper, ax, ta)
        layers += 1

    _draw_sweep_tv(ax, mapper, ta)
    _draw_context_zone_tv(ax, mapper, ta)
    _draw_breakout_tv(ax, mapper, ta)
    if collect_breakout_retest_events(mapper.bars, ta, max_events=1):  # type: ignore[attr-defined]
        layers += 1

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
    """Matplotlib LabelBoard when spec-mode on; else None → legacy pro layers."""
    spec = chart_spec_for_ta(ta, symbol=symbol)
    if spec is None:
        return None

    from ...chart_label_layout import LabelBoard
    from .chart_mpl_spec import draw_mpl_spec_core

    board = LabelBoard()
    drawn = draw_mpl_spec_core(ax, bars, ta, spec)
    if drawn <= 0:
        drawn = 1

    if spec.show_primary_pattern:
        from ...chart_ed_minimal import draw_pro_teaching_layers

        draw_pro_teaching_layers(ax, bars, ta, mode="observation")

    if spec.show_smc_fvg:
        try:
            from ...chart_education_visual import draw_education_visuals_mpl

            draw_education_visuals_mpl(ax, bars, ta)
        except Exception:
            logger.debug("mpl smc overlays skipped", exc_info=True)

    from ...chart_display_policy import chart_teaching_tags_enabled, ed_chart_visual_only
    from ...chart_ed_minimal import add_minimal_context_labels
    from ...chart_teaching_tags import enrich_teaching_label_board

    mode = "ed_story" if spec.rbr_phase else "observation"
    if chart_teaching_tags_enabled() or not ed_chart_visual_only():
        enrich_teaching_label_board(board, bars, ta, mode=mode)
        if not ed_chart_visual_only():
            add_minimal_context_labels(board, bars, ta, mode=mode)
    _ = drawn
    return board
