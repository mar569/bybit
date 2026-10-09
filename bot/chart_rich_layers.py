"""Legacy alias: rich = тот же ChartSpec PRO (см. chart_pro_visual)."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .chart_label_layout import LabelBoard
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)


def draw_rich_analysis_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    mode: str,
    interval_minutes: int = 15,
) -> LabelBoard:
    from .chart_display_policy import ed_playbook_v3_enabled
    from .core.playbook.chart_layers import draw_mpl_playbook_layers

    if ed_playbook_v3_enabled():
        sym = str(getattr(ta, "symbol", "") or "")
        board = draw_mpl_playbook_layers(
            ax, bars, ta, symbol=sym, interval_minutes=interval_minutes,
        )
        if board is not None:
            return board
    logger.debug("rich layers: no playbook spec, empty board")
    return LabelBoard()
