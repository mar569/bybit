from __future__ import annotations

from bot.market_state import build_market_state
from bot.test_ta_analysis import _trend_up_bars
from bot.ta_analysis import find_swing_points, run_ta_analysis


def test_market_state_builds_scenario() -> None:
    bars = _trend_up_bars(60)
    swings = find_swing_points(bars)
    state = build_market_state(
        symbol="ETHUSDT",
        bars=bars,
        swings=swings,
        verdict="WAIT",
    )
    assert state.passport.symbol == "ETHUSDT"
    assert state.scenario is not None
    assert state.summary_ru


def test_run_ta_includes_market_state_fields() -> None:
    ta = run_ta_analysis(bars=_trend_up_bars(60), symbol="ETHUSDT")
    assert hasattr(ta, "market_state_summary")
    assert ta.scenario_engine_id or ta.market_state_summary or ta.entry_quality is not None
