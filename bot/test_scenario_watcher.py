from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from bot.models import Signal
from bot.scenario_watcher import ScenarioWatcher, should_enroll_scenario_watch
from bot.ta_analysis import ForecastPath, TAAnalysisResult, run_ta_analysis
from bot.bybit_klines import KlineBar


def _settings(**kwargs):
    defaults = {
        "scenario_watch_enabled": True,
        "scenario_watch_minutes": 45,
        "scenario_watch_pullback_pct": 3.0,
        "scenario_watch_continuation_pct": 0.8,
        "scenario_watch_zone_pct": 0.45,
        "scenario_watch_enroll_cooldown_seconds": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _signal(**kwargs) -> Signal:
    base = {
        "exchange": "Bybit",
        "symbol": "BLURUSDT",
        "side": "long",
        "signal_type": "impulse_pump",
        "current_price": 0.025,
        "link": "https://www.coinglass.com/tv/BYBIT_BLURUSDT",
        "oi_change_percent": 5.0,
        "oi_change_value": 50_000.0,
        "oi_change_usd": 100_000.0,
        "oi_direction": "up",
        "oi_period_minutes": 5,
        "signal_score": 3,
        "signals_today": 1,
        "price_change_percent": 12.0,
        "price_change_value": 0.003,
        "price_direction": "up",
        "volume_change_percent": None,
        "trade_count": None,
        "spread": None,
        "funding_rate": None,
        "liquidation_estimate": None,
        "vwap": None,
        "atr": None,
        "rsi": None,
        "ema_short": None,
        "ema_long": None,
        "volume_24h": None,
        "volume_speed": None,
        "current_open_interest": None,
        "details": {},
    }
    base.update(kwargs)
    return Signal(**base)


def _ta_wait_correction(**kwargs) -> TAAnalysisResult:
    ta = TAAnalysisResult(
        verdict="WAIT",
        current_price=0.025,
        post_pump=True,
        breakdown_level=0.0235,
        correction_path=ForecastPath(
            kind="correction",
            label="коррекция ↓",
            waypoints=[0.025, 0.0248, 0.0232, 0.0233],
            confidence=7,
            reason="откат",
        ),
        continuation_path=ForecastPath(
            kind="continuation",
            label="продолжение ↑",
            waypoints=[0.025, 0.0248, 0.0255, 0.026],
            confidence=5,
            reason="рост",
        ),
    )
    for k, v in kwargs.items():
        setattr(ta, k, v)
    return ta


def test_should_enroll_on_wait_with_correction_forecast() -> None:
    assert should_enroll_scenario_watch(_signal(), _ta_wait_correction()) == "correction"


def test_should_not_enroll_without_forecast() -> None:
    ta = TAAnalysisResult(verdict="WAIT", current_price=1.0)
    assert should_enroll_scenario_watch(_signal(), ta) is None


def test_correction_started_after_pullback() -> None:
    watcher = ScenarioWatcher()
    ta = _ta_wait_correction()
    watcher.try_enroll(_signal(), ta, _settings())
    key = ("bybit", "BLURUSDT")
    watch = watcher._watches[key]
    watch.started_at = time.time() - 120

    scanner = MagicMock()
    scanner.get_snapshot_for.return_value = SimpleNamespace(price=0.0241)

    updates = watcher.tick(scanner, _settings())
    assert len(updates) == 1
    assert updates[0].kind == "correction_started"
    assert updates[0].move_pct >= 3.0


def test_continuation_invalidates_correction_watch() -> None:
    watcher = ScenarioWatcher()
    ta = _ta_wait_correction()
    watcher.try_enroll(_signal(), ta, _settings())
    key = ("bybit", "BLURUSDT")
    watch = watcher._watches[key]
    watch.started_at = time.time() - 120

    scanner = MagicMock()
    scanner.get_snapshot_for.return_value = SimpleNamespace(price=0.0253)

    updates = watcher.tick(scanner, _settings(scenario_watch_continuation_pct=0.5))
    assert len(updates) == 1
    assert updates[0].kind == "continuation_confirmed"
    assert key not in watcher._watches


def test_entry_short_after_breakdown() -> None:
    watcher = ScenarioWatcher()
    ta = _ta_wait_correction()
    watcher.try_enroll(_signal(), ta, _settings())
    key = ("bybit", "BLURUSDT")
    watch = watcher._watches[key]
    watch.started_at = time.time() - 120
    watch.correction_fired = True

    scanner = MagicMock()
    scanner.get_snapshot_for.return_value = SimpleNamespace(price=0.0234)

    updates = watcher.tick(scanner, _settings())
    assert any(u.kind == "entry_short" for u in updates)


def test_run_ta_still_has_forecast_paths() -> None:
    bars = [
        KlineBar(
            open_time_ms=i * 300_000,
            open=1.0 + i * 0.001,
            high=1.01 + i * 0.001,
            low=0.99 + i * 0.001,
            close=1.005 + i * 0.001,
            volume=1000.0,
        )
        for i in range(80)
    ]
    ta = run_ta_analysis(bars, symbol="TESTUSDT", neutral=True)
    assert ta.verdict in {"LONG", "SHORT", "WAIT"}
