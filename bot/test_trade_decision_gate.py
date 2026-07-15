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


def test_chase_gets_skip_not_entry() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        action_priority="long",
        current_price=1.1,
        range_position=0.92,
        momentum_pct=2.0,
        wave_phase="late_impulse",
    )
    d = decide_trade_action(_sig(), ta, readiness=(True, "armed"))
    assert d.action == "skip"
    assert d.chase is True


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


def test_trend_seed_defaults_to_watch() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="long",
        current_price=1.06,
        range_position=0.72,
        momentum_pct=1.2,
    )
    d = decide_trade_action(
        _sig(signal_type="trend_seed", price_change_percent=2.0, details={"seed_cvd_missing": 0, "seed_extension_pct": 3.0}),
        ta,
        readiness=(False, "нет"),
        min_watch_score=30,
    )
    assert d.action == "watch"
    assert "потенциал" in d.reason.lower() or "тренд" in d.reason.lower()


def test_early_seed_survives_high_range_position() -> None:
    """TA range высокий, но seed ещё early (<8%) → WATCH, не skip."""
    ta = TAAnalysisResult(
        verdict="LONG",
        action_priority="long",
        current_price=1.05,
        range_position=0.88,
        momentum_pct=1.0,
    )
    d = decide_trade_action(
        _sig(
            signal_type="trend_seed",
            details={"seed_extension_pct": 4.0, "seed_cvd_missing": 0},
        ),
        ta,
        readiness=(False, "ждать"),
        watch_allowed=True,
        block_chase_watch=True,
    )
    assert d.action == "watch"
    assert d.chase is False


def test_chase_pulse_still_skipped() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        action_priority="long",
        current_price=1.1,
        range_position=0.92,
        momentum_pct=2.0,
    )
    d = decide_trade_action(
        _sig(signal_type="pulse_pump"),
        ta,
        readiness=(False, "x"),
        watch_allowed=False,
        block_chase_watch=True,
    )
    assert d.action == "skip"
def test_reversal_weak_cvd_not_entry() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        action_priority="long",
        current_price=0.317,
        range_position=0.55,
        momentum_pct=0.9,
        wave_has_confluence=True,
        wave_phase="wave_2_4_zone",
        wave_bias="long",
        nearest_support=0.316,
    )
    d = decide_trade_action(
        _sig(
            signal_type="reversal_pump",
            details={"cvd_ratio": 0.55},
            price_change_percent=0.9,
        ),
        ta,
        readiness=(True, "armed"),
        watch_allowed=True,
        min_entry_score=50,
    )
    assert d.action != "entry"
