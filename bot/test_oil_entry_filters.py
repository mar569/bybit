"""Tests for oil entry quality filters (session / chase / close)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.bybit_klines import KlineBar
from bot.oil_confluence import build_oil_confluence_setup
from bot.oil_entry_filters import (
    is_chase_for_side,
    is_session_open_fragile,
    last_bar_closes_beyond,
    measure_recent_move,
    minutes_since_ukousd_open,
    oil_entry_block_reason,
)
from bot.oil_forecast import OilForecast
from bot.oil_monitor import OilNewsBias, detect_oil_micro_signal
from bot.ta_analysis import TAAnalysisResult

_MSK = timezone(timedelta(hours=3), name="MSK")


def _bars_ramp(start: float, end: float, n: int = 40) -> list[KlineBar]:
    bars: list[KlineBar] = []
    for i in range(n):
        t = float(i)
        px = start + (end - start) * (i / max(n - 1, 1))
        bars.append(KlineBar(t, px, px + 0.05, px - 0.05, px, 1.0))
    return bars


def test_session_open_fragile_monday():
    # Пн 01:05 МСК — хрупкое открытие
    now = datetime(2026, 8, 3, 1, 5, tzinfo=_MSK)  # Monday
    assert is_session_open_fragile(now=now, block_minutes=20) is True
    assert (minutes_since_ukousd_open(now=now) or 0) < 20
    # Пн 02:00 — уже ок
    later = datetime(2026, 8, 3, 2, 0, tzinfo=_MSK)
    assert is_session_open_fragile(now=later, block_minutes=20) is False


def test_measure_priced_in_move():
    # резкий импульс на последних ~30–60м (6–12 баров 5m)
    bars = _bars_ramp(80.0, 80.1, n=30)
    for i in range(12):
        px = 80.1 + (i + 1) * 0.12  # +1.44% за час
        bars.append(KlineBar(float(100 + i), px - 0.05, px + 0.05, px - 0.08, px, 1.0))
    move = measure_recent_move(bars, interval_minutes=5)
    assert move is not None
    assert move.priced_in is True
    assert is_chase_for_side("long", move, near_level=False) is True
    assert is_chase_for_side("long", move, near_level=True) is False


def test_close_beyond_level():
    bars = _bars_ramp(84.0, 84.5, n=12)
    assert last_bar_closes_beyond(bars, side="long", level=84.4) is True
    assert last_bar_closes_beyond(bars, side="short", level=84.4) is False


def test_micro_blocked_on_session_open(monkeypatch):
    bars = _bars_ramp(84.0, 84.15, n=20)
    # Make last bars a clean short dump with bodies
    for i in range(5):
        px = 84.15 - i * 0.03
        bars[-(5 - i)] = KlineBar(float(100 + i), px + 0.02, px + 0.03, px - 0.01, px, 2.0)

    monkeypatch.setattr(
        "bot.oil_entry_filters.is_session_open_fragile",
        lambda **kw: True,
    )
    assert detect_oil_micro_signal(bars, apply_session_filter=True) is None


def test_micro_blocked_on_chase(monkeypatch):
    # Последние 12×5m = час: −1.2%, при этом micro-lookback ~4 бара тоже вниз
    bars: list[KlineBar] = []
    px = 84.0
    for i in range(20):
        bars.append(KlineBar(float(i), px, px + 0.02, px - 0.02, px, 1.0))
    for i in range(12):
        px = px - 0.085  # ~−1.02% за час
        bars.append(KlineBar(float(50 + i), px + 0.04, px + 0.05, px - 0.02, px, 2.0))

    monkeypatch.setattr(
        "bot.oil_entry_filters.is_session_open_fragile",
        lambda **kw: False,
    )
    move = measure_recent_move(bars, interval_minutes=5)
    assert move is not None and move.priced_in
    assert detect_oil_micro_signal(bars, apply_chase_filter=True) is None


def test_confluence_chase_becomes_wait():
    snap = SimpleNamespace(
        label="Brent",
        symbol="UKOUSD",
        price=88.5,  # далеко от S/R
        high_7d=92.0,
        low_7d=84.0,
        verdict="SHORT",
        confidence=8,
        support=85.5,
        resistance=87.6,
        breakdown=85.0,
        breakout=89.0,
        phase="",
        elliott="",
        reason="test",
    )
    ta = TAAnalysisResult(verdict="SHORT", verdict_confidence=8)
    forecast = OilForecast(
        bias="SHORT",
        scenario="deal_tape",
        confidence=8,
        horizon_ru="свинг",
        headline_ru="Bias SHORT",
        base_case_ru="вниз",
        alt_case_ru="вверх",
        invalidation_ru="выше 89",
        watch_list_ru=("Ормуз",),
        entry_hint_ru="short от R",
    )
    news = OilNewsBias(
        bias="bearish",
        weighted_score=4.0,
        summary_ru="down",
        how_to_use_ru="",
    )
    bars: list[KlineBar] = []
    px = 90.0
    for i in range(20):
        bars.append(KlineBar(float(i), px, px + 0.02, px - 0.02, px, 1.0))
    for i in range(12):
        px = px - 0.12
        bars.append(KlineBar(float(30 + i), px + 0.05, px + 0.06, px - 0.02, px, 1.0))
    setup = build_oil_confluence_setup(
        snap,
        ta,
        forecast=forecast,
        news_bias=news,
        min_quality=7,
        near_pct=0.35,
        bars=bars,
        interval_minutes=5,
        apply_session_filter=False,
        apply_chase_filter=True,
        require_close_break=False,
    )
    assert setup is not None
    assert setup.side == "WAIT"
    low = setup.trigger_ru.lower()
    assert ("chase" in low) or ("уже" in low) or ("отыгра" in low) or ("ход" in low)


def test_oil_entry_block_reason_session():
    now = datetime(2026, 8, 3, 1, 5, tzinfo=_MSK)
    reason = oil_entry_block_reason(
        side="short",
        now=now,
        session_block_minutes=20,
        apply_chase_filter=False,
    )
    assert reason is not None
    assert "сессии" in reason
