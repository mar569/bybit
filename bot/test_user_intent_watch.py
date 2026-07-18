"""Тесты ручной слежки LONG/SHORT (кнопки на WATCH)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from bot.scenario_watcher import ScenarioWatcher


def _settings(**kwargs):
    defaults = {
        "scenario_watch_enabled": True,
        "scenario_watch_minutes": 45,
        "scenario_watch_late_cancel_pct": 1.5,
        "scenario_watch_opposite_cancel_pct": 1.2,
        "scenario_watch_trigger_min_age_seconds": 0.0,
        "scenario_watch_enroll_cooldown_seconds": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _scanner(price: float):
    snap = MagicMock()
    snap.price = price
    sc = MagicMock()
    sc.get_snapshot_for.return_value = snap
    return sc


def test_user_intent_short_fires_on_breakdown_confirm() -> None:
    w = ScenarioWatcher()
    ok, _ = w.try_enroll_user_intent(
        exchange="bybit",
        symbol="XECUSDT",
        intent="short",
        price=0.0100,
        breakout_level=0.01021,
        breakdown_level=0.009789,
        settings=_settings(),
    )
    assert ok
    # ещё не пробили
    ups = w.tick(_scanner(0.00995), _settings())
    assert ups == []
    # пробой + буфер ~0.08%
    ups = w.tick(_scanner(0.00978), _settings())
    assert len(ups) == 1
    assert ups[0].kind == "entry_short"
    assert w.active_count == 0


def test_user_intent_long_late_cancel() -> None:
    w = ScenarioWatcher()
    ok, _ = w.try_enroll_user_intent(
        exchange="bybit",
        symbol="TESTUSDT",
        intent="long",
        price=1.0,
        breakout_level=1.01,
        breakdown_level=0.98,
        settings=_settings(scenario_watch_late_cancel_pct=1.5),
    )
    assert ok
    # уже улетели на +2% выше пробоя — опоздали
    ups = w.tick(_scanner(1.03), _settings(scenario_watch_late_cancel_pct=1.5))
    assert len(ups) == 1
    assert ups[0].kind == "cancelled_late"


def test_user_intent_opposite_cancels_short() -> None:
    w = ScenarioWatcher()
    w.try_enroll_user_intent(
        exchange="bybit",
        symbol="TESTUSDT",
        intent="short",
        price=1.0,
        breakout_level=1.01,
        breakdown_level=0.97,
        settings=_settings(scenario_watch_opposite_cancel_pct=1.0),
    )
    # сильный пробой вверх против short
    ups = w.tick(_scanner(1.025), _settings(scenario_watch_opposite_cancel_pct=1.0))
    assert len(ups) == 1
    assert ups[0].kind == "cancelled_opposite"


def test_cancel_watch() -> None:
    w = ScenarioWatcher()
    w.try_enroll_user_intent(
        exchange="bybit",
        symbol="AAAUSDT",
        intent="long",
        price=1.0,
        breakout_level=1.02,
        breakdown_level=0.98,
        settings=_settings(),
    )
    assert w.active_count == 1
    removed = w.cancel_watch("bybit", "AAAUSDT")
    assert removed is not None
    assert w.active_count == 0
