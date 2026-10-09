from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bot.chart_evidence_canvas import (
    _patterns_visible,
    draw_evidence_analysis_mpl,
    seed_evidence_level_board,
)
from bot.chart_label_layout import LabelBoard
from bot.ta_analysis import TAAnalysisResult


def test_evidence_skips_pattern_when_none() -> None:
    ta = TAAnalysisResult(current_price=100.0, verdict="WAIT", chart_patterns=[])
    assert not _patterns_visible(ta)


def test_evidence_board_no_nearest_sr() -> None:
    ta = TAAnalysisResult(
        current_price=102.0,
        breakout_level=104.0,
        breakdown_level=96.0,
        nearest_resistance=102.5,
        nearest_support=101.5,
    )
    board = LabelBoard()
    seed_evidence_level_board(board, ta, [_bar(102.0)])
    texts = " ".join(i.text for i in board.items)
    assert "ближ" not in texts.lower()
    assert "R" in texts and "S" in texts


def test_evidence_draw_smoke() -> None:
    ta = TAAnalysisResult(
        current_price=102.0,
        breakout_level=104.0,
        breakdown_level=96.0,
        verdict="WAIT",
    )
    fig, ax = plt.subplots()
    n = draw_evidence_analysis_mpl(ax, [_bar(102.0)] * 40, ta)
    plt.close(fig)
    assert n >= 1


def _bar(close: float):
    from bot.bybit_klines import KlineBar

    return KlineBar(open_time=0, open=close, high=close * 1.001, low=close * 0.999, close=close, volume=1.0)
