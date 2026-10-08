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
    if mode == "legacy_manual":
        from .chart_manual_layers import draw_education_overlays, draw_manual_ta_layers

        draw_manual_ta_layers(ax, bars, ta)
        draw_education_overlays(ax, bars, ta, manual_compact=True)
        return board
    if mode == "range_wait":
        from .chart_range_wait import draw_range_wait_layers_minimal

        draw_range_wait_layers_minimal(ax, bars, ta, interval_minutes=interval_minutes, board=board)
        from .chart_ed_minimal import add_minimal_context_labels

        add_minimal_context_labels(board, bars, ta, mode="range_wait")
    elif mode == "observation":
        from .chart_observation import draw_observation_layers_minimal

        draw_observation_layers_minimal(ax, bars, ta, interval_minutes=interval_minutes, board=board)
        from .chart_ed_minimal import add_minimal_context_labels

        add_minimal_context_labels(board, bars, ta, mode="observation")
    else:
        from .chart_ed_story import draw_ed_story_layers_pro

        draw_ed_story_layers_pro(ax, bars, ta, interval_minutes=interval_minutes, board=board)
        from .chart_ed_minimal import add_minimal_context_labels

        add_minimal_context_labels(board, bars, ta, mode="ed_story")
    from .chart_ed_minimal import draw_pro_teaching_layers

    draw_pro_teaching_layers(ax, bars, ta, mode=mode)
    return board


def finalize_pro_viewport(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    mode: ProMode,
) -> None:
    """Ylim: свечи + план, без простирания до далёкого TP."""
    if not bars:
        return
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    vis_lo = min(float(b.low) for b in bars[-min(120, len(bars)) :])
    vis_hi = max(float(b.high) for b in bars[-min(120, len(bars)) :])
    lo, hi = vis_lo, vis_hi
    pad = max((hi - lo) * 0.08, cur * 0.003)

    from .chart_plan_display import build_display_plan
    from .chart_position_boxes import plan_for_display

    plan = plan_for_display(ta)
    if plan:
        disp = build_display_plan(
            side=plan[0],
            entry=plan[1],
            entry_lo=plan[2],
            entry_hi=plan[3],
            stop=plan[4],
            tp=plan[5],
        )
        prices = [disp.entry_lo, disp.entry_hi, disp.stop, disp.tp, cur]
        from .range_breakdown_retest import get_rbr_from_ta

        rbr = get_rbr_from_ta(ta)
        if rbr:
            prices.extend(
                float(rbr[k])
                for k in ("range_top", "range_bottom")
                if rbr.get(k)
            )
        pmin, pmax = min(prices), max(prices)
        # Не тянуть Y на далёкий swing — только экранный план
        cur_band = max(cur * 0.09, (hi - lo) * 0.5)
        pmin = max(pmin, cur - cur_band)
        pmax = min(pmax, cur + cur_band * 1.05)
        lo = min(lo, pmin - pad)
        hi = max(hi, pmax + pad)
    else:
        brk = float(getattr(ta, "breakout_level", 0) or 0)
        brdn = float(getattr(ta, "breakdown_level", 0) or 0)
        for p in (brk, brdn):
            if p > 0:
                lo = min(lo, p - pad)
                hi = max(hi, p + pad)

    ax.set_ylim(lo, hi)
