from __future__ import annotations

from bot.chart_setup_interval import (
    plan_search_intervals,
    pick_setup_chart_interval,
    setup_chart_analysis_hours,
    ta_plan_readable,
)
from bot.ta_analysis import TAAnalysisResult


class _Cons:
    top = 182.0
    bottom = 176.0


class _Ta:
    post_pump = True
    consolidation = _Cons()
    phase = "consolidation"
    structure = "боковая структура"
    analysis_interval_minutes = 0


def test_no_forced_30_on_5m_scanner() -> None:
    assert pick_setup_chart_interval(_Ta(), 5) == 5  # type: ignore[arg-type]
    order = plan_search_intervals(_Ta(), 5)  # type: ignore[arg-type]
    assert order[0] == 5
    assert 30 in order
    assert order.index(15) < order.index(30)


def test_respects_analysis_interval_when_plan_exists() -> None:
    ta = TAAnalysisResult(
        analysis_interval_minutes=15,
        breakout_level=1.1,
        breakdown_level=1.0,
    )
    assert pick_setup_chart_interval(ta, 5) == 15
    assert plan_search_intervals(ta, 5) == [15]


def test_plan_readable_rbr() -> None:
    ta = TAAnalysisResult(
        market_metrics={"range_breakdown_retest": {"phase": "retest", "direction": "short"}},
    )
    assert ta_plan_readable(ta)


def test_setup_hours_48_for_30m() -> None:
    assert setup_chart_analysis_hours(30) == 48
