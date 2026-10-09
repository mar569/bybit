"""PRO-график: один режим, один сценарий, без full manual на сигналах."""
from __future__ import annotations

import logging
import os
from typing import Literal

import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)

ProMode = Literal["ed_story", "range_wait", "observation", "legacy_manual"]


def signal_chart_legacy_enabled() -> bool:
    """Полный manual-chart только если явно включён (SIGNAL_CHART_LEGACY=1)."""
    return os.environ.get("SIGNAL_CHART_LEGACY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_pro_chart_mode(ta: TAAnalysisResult, *, allow_legacy: bool = False) -> ProMode:
    from .chart_ed_story import use_ed_story_chart
    from .chart_range_wait import use_range_wait_chart

    if use_ed_story_chart(ta):
        return "ed_story"
    if use_range_wait_chart(ta):
        return "range_wait"
    if allow_legacy:
        from .chart_story_router import resolve_chart_story_kind

        if resolve_chart_story_kind(ta) == "full_manual":
            return "legacy_manual"
    return "observation"


def pro_trailing_fraction(mode: ProMode) -> float:
    from .chart_ed_story import ED_STORY_TRAILING
    from .chart_observation import observation_trailing
    from .chart_range_wait import range_wait_trailing

    if mode == "range_wait":
        return range_wait_trailing()
    if mode == "observation":
        return observation_trailing()
    if mode == "legacy_manual":
        return 0.22
    return ED_STORY_TRAILING


def draw_pro_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    mode: ProMode,
    *,
    interval_minutes: int = 15,
):
    from .chart_label_layout import LabelBoard

    board = LabelBoard()
    single_canvas = False
    if mode != "legacy_manual":
        try:
            from .chart_display_policy import ed_chart_single_canvas_enabled, ed_playbook_v3_enabled

            if ed_playbook_v3_enabled() or ed_chart_single_canvas_enabled():
                from .core.playbook.chart_layers import draw_mpl_playbook_layers

                sym = str(getattr(ta, "symbol", "") or "")
                spec_board = draw_mpl_playbook_layers(
                    ax, bars, ta, symbol=sym, interval_minutes=interval_minutes,
                )
                if spec_board is not None:
                    board = spec_board
                    single_canvas = ed_chart_single_canvas_enabled()
        except Exception:
            logger.debug("mpl playbook layers fallback to legacy pro", exc_info=True)

    if single_canvas:
        return board

    if mode == "legacy_manual":
        from .chart_manual_layers import draw_education_overlays, draw_manual_ta_layers

        draw_manual_ta_layers(ax, bars, ta)
        draw_education_overlays(ax, bars, ta, manual_compact=True)
        return board
    if mode == "range_wait":
        from .chart_range_wait import draw_range_wait_layers_minimal

        draw_range_wait_layers_minimal(ax, bars, ta, interval_minutes=interval_minutes, board=board)
        from .chart_display_policy import ed_chart_visual_only
        from .chart_ed_minimal import add_minimal_context_labels

        if not ed_chart_visual_only():
            add_minimal_context_labels(board, bars, ta, mode="range_wait")
    elif mode == "observation":
        from .chart_observation import draw_observation_layers_minimal

        draw_observation_layers_minimal(ax, bars, ta, interval_minutes=interval_minutes, board=board)
        from .chart_display_policy import ed_chart_visual_only
        from .chart_ed_minimal import add_minimal_context_labels

        if not ed_chart_visual_only():
            add_minimal_context_labels(board, bars, ta, mode="observation")
    else:
        from .chart_ed_story import draw_ed_story_layers_pro

        draw_ed_story_layers_pro(ax, bars, ta, interval_minutes=interval_minutes, board=board)
        from .chart_display_policy import ed_chart_visual_only
        from .chart_ed_minimal import add_minimal_context_labels

        if not ed_chart_visual_only():
            add_minimal_context_labels(board, bars, ta, mode="ed_story")
    from .chart_display_policy import chart_teaching_tags_enabled, ed_chart_visual_only
    from .chart_ed_minimal import draw_pro_teaching_layers
    from .chart_teaching_tags import enrich_teaching_label_board

    draw_pro_teaching_layers(ax, bars, ta, mode=mode)
    if chart_teaching_tags_enabled() or not ed_chart_visual_only():
        enrich_teaching_label_board(board, bars, ta, mode=mode)
    return board


def finalize_pro_viewport(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    mode: ProMode,
) -> None:
    """Ylim по видимым свечам (xlim), без «обрезания» хая/лоя импульса."""
    if not bars:
        return
    from .chart_display_policy import ed_chart_single_canvas_enabled
    from .chart_viewport import apply_ylim_to_visible_bars

    extra: list[float] = []
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    for p in (brk, brdn):
        if p > 0:
            extra.append(p)
    from .range_breakdown_retest import get_rbr_from_ta

    rbr = get_rbr_from_ta(ta)
    if rbr:
        for k in ("range_top", "range_bottom", "stop"):
            v = float(rbr.get(k) or 0)
            if v > 0:
                extra.append(v)
    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        extra.append(float(smc.structure_break_level))

    apply_ylim_to_visible_bars(ax, bars, pad_ratio=0.11, extra_prices=extra)
    if not ed_chart_single_canvas_enabled() and mode == "legacy_manual":
        cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
        lo, hi = ax.get_ylim()
        pad = max((hi - lo) * 0.04, cur * 0.002)
        ax.set_ylim(lo - pad * 0.2, hi + pad * 0.2)
