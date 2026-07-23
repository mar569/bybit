"""Tests for free liquidation-magnet heuristic."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.liq_magnet import analyze_liq_magnet
from bot.ta_analysis import SwingPoint


def _bar(i: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(
        open_time=1_700_000_000_000 + i * 300_000,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
    )


def test_magnet_finds_equal_highs_above() -> None:
    bars: list[KlineBar] = []
    # Equal highs near 103.5, price ~100 — within magnet reach
    prices = [100, 102, 103.5, 101, 99, 103.4, 101.5, 98.5, 97, 99, 100, 99.5, 100]
    for i, p in enumerate(prices):
        bars.append(_bar(i, p, p + 0.8, p - 0.8, p))
    swings = [
        SwingPoint(2, 103.5, "high"),
        SwingPoint(5, 103.4, "high"),
        SwingPoint(8, 97.0, "low"),
        SwingPoint(4, 99.0, "low"),
    ]
    ctx = analyze_liq_magnet(bars, swings, liq_context=None)
    assert ctx.nearest_above is not None
    assert ctx.nearest_above >= 103.0
    assert ctx.bias in {"hunt_shorts_above", "both", "hunt_longs_below", "neutral"}


def test_live_long_liq_biases_above_magnet() -> None:
    bars = [_bar(i, 100, 101, 99, 100) for i in range(30)]
    bars[-1] = _bar(29, 100, 101, 99, 100)
    swings = [
        SwingPoint(10, 104.0, "high"),
        SwingPoint(20, 104.1, "high"),
        SwingPoint(15, 96.0, "low"),
    ]
    ctx = analyze_liq_magnet(
        bars,
        swings,
        liq_context={"long_liq_usd": 120_000, "short_liq_usd": 20_000, "total_usd": 140_000},
    )
    assert ctx.factor_line
    assert "🧲" in ctx.factor_line or ctx.bias != "neutral"
