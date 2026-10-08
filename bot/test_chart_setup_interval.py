from __future__ import annotations

from bot.chart_setup_interval import pick_setup_chart_interval, setup_chart_analysis_hours


class _Cons:
    top = 182.0
    bottom = 176.0


class _Ta:
    post_pump = True
    consolidation = _Cons()
    phase = "consolidation"
    structure = "боковая структура"


def test_pick_30m_after_pump_range() -> None:
    assert pick_setup_chart_interval(_Ta(), 5) == 30  # type: ignore[arg-type]


def test_setup_hours_48_for_30m() -> None:
    assert setup_chart_analysis_hours(30) == 48
