"""TV/mpl — PDF-style layers (docs/.cursor_pdf_pages)."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from .chart_spec import ChartSpec
from ...chart_display_policy import ed_chart_spec_layers_enabled, ed_pdf_chart_style_enabled
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
    if not ed_chart_spec_layers_enabled():
        return None

    from ...chart_rbr_refresh import refresh_rbr_for_chart
    from ...chart_pdf_style import draw_pdf_style_tv, sanitize_rbr_for_pdf

    ta = refresh_rbr_for_chart(ta, bars)
    ta = sanitize_rbr_for_pdf(ta, bars)

    if ed_pdf_chart_style_enabled():
        layers = draw_pdf_style_tv(ax, mapper, bars, ta)
        return max(layers, 2)

    return None


def draw_mpl_playbook_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    interval_minutes: int = 15,
) -> object | None:
    from ...chart_display_policy import ed_chart_single_canvas_enabled

    if ed_chart_single_canvas_enabled():
        from ...chart_story_router import enrich_ta_for_chart_story
        from ...chart_ed_canvas import draw_ed_analysis_canvas_mpl

        ta = enrich_ta_for_chart_story(ta, bars)
        return draw_ed_analysis_canvas_mpl(
            ax, bars, ta, symbol=symbol, interval_minutes=interval_minutes,
        )

    spec = chart_spec_for_ta(ta, symbol=symbol)
    if spec is None:
        return None

    from ...chart_label_layout import LabelBoard
    from ...chart_rbr_refresh import refresh_rbr_for_chart
    from ...chart_pro_visual import draw_pro_visual_mpl
    from ...chart_pdf_style import sanitize_rbr_for_pdf

    ta = refresh_rbr_for_chart(ta, bars)
    ta = sanitize_rbr_for_pdf(ta, bars)
    spec = chart_spec_for_ta(ta, symbol=symbol) or spec

    board = LabelBoard()
    draw_pro_visual_mpl(ax, bars, ta, spec, interval_minutes=interval_minutes)
    return board
