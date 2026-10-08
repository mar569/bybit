from __future__ import annotations

from .chart_range_wait import use_range_wait_chart
from .ta_analysis import TAAnalysisResult


def test_use_range_wait_chart_jup_like() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        current_price=0.3802,
        breakout_level=0.381,
        breakdown_level=0.37187,
        action_priority="long",
    )
    assert use_range_wait_chart(ta)


def test_use_range_wait_skips_without_levels() -> None:
    ta = TAAnalysisResult(verdict="WAIT", current_price=1.0)
    assert not use_range_wait_chart(ta)
