from __future__ import annotations

from bot.models import Signal
from bot.scenario_watcher import ScenarioWatcher
from bot.settings import ScannerSettings
from bot.ta_analysis import TAAnalysisResult


def _watch_signal() -> Signal:
    return Signal(
        exchange="Bybit",
        symbol="SKLUSDT",
        signal_type="trend_dump",
        oi_period_minutes=5,
        oi_change_percent=-2.0,
        oi_change_value=0,
        oi_change_usd=50_000.0,
        oi_direction="down",
        signals_today=1,
        price_change_percent=-2.0,
        price_change_value=None,
        price_direction="down",
        volume_change_percent=0,
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
        side="short",
        current_price=0.00540,
        current_open_interest=1_000_000,
        link="https://example.com",
        details={},
    )


def test_try_enroll_quality_watch_short() -> None:
    watcher = ScenarioWatcher()
    ta = TAAnalysisResult(
        verdict="SHORT",
        current_price=0.00540,
        breakdown_level=0.00534,
        target_prices=[0.00527],
    )
    ok = watcher.try_enroll_quality_watch(
        _watch_signal(), ta, ScannerSettings(), quality_tier="watch",
    )
    assert ok is True
    assert watcher.active_count == 1
    watch = next(iter(watcher._watches.values()))
    assert watch.trigger_only is True
    assert watch.breakdown_level == 0.00534
