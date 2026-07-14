"""Тесты скоринга сетапа и арбитра."""
from __future__ import annotations

from bot.models import Signal
from bot.ta_analysis import TAAnalysisResult
from bot.trade_decision_gate import (
    decide_trade_action,
    score_trade_setup,
)


def _sig(**kwargs) -> Signal:
    base = dict(
        exchange="bybit",
        symbol="TESTUSDT",
        signal_type="impulse_pump",
        oi_period_minutes=5,
        oi_change_percent=2.0,
        oi_change_value=0.0,
        oi_change_usd=50_000.0,
        oi_direction="up",
        signals_today=1,
        price_change_percent=5.0,
        price_change_value=None,
        price_direction="up",
        volume_change_percent=None,
        trade_count=None,
        spread=None,
        funding_rate=None,
        liquidation_estimate=None,
        vwap=None,
        atr=None,
        rsi=None,
        ema_short=None,
        ema_long=None,
        volume_24h=None,
        volume_speed=None,
        signal_score=3,
        side="long",
        current_price=1.05,
        current_open_interest=1_000_000.0,
        link="",
        details={},
    )
    base.update(kwargs)
    return Signal(**base)


def test_chase_gets_watch_not_entry() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        action_priority="long",
        current_price=1.1,
        range_position=0.92,
        momentum_pct=2.0,
        wave_phase="late_impulse",
    )
    d = decide_trade_action(_sig(), ta, readiness=(True, "armed"))
    assert d.action == "watch"
    assert d.setup_score >= 0


def test_fib_setup_scores_entry() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        action_priority="long",
        current_price=1.05,
        range_position=0.45,
        momentum_pct=-0.2,
        wave_phase="wave_2_4_zone",
        wave_bias="long",
        wave_has_confluence=True,
        wave_confluence_count=2,
        wave_confluence_sr=True,
        nearest_support=1.048,
        invalidation_price=1.04,
        target_prices=[1.08],
    )
    setup = score_trade_setup(_sig(), ta)
    assert setup.total >= 50
    d = decide_trade_action(_sig(), ta, readiness=(True, "ok"), min_entry_score=55)
    assert d.action == "entry"


def test_weak_setup_watch() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="long",
        current_price=1.08,
        range_position=0.7,
        momentum_pct=0.5,
        breakout_level=1.05,
    )
    d = decide_trade_action(
        _sig(),
        ta,
        readiness=(False, "ждать 1.05"),
        min_watch_score=30,
    )
    assert d.action in {"watch", "entry"}
