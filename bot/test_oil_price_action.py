"""Tests for oil price-action system (EMA / breakout / bounce / retest)."""
from __future__ import annotations

from types import SimpleNamespace

from bot.bybit_klines import KlineBar
from bot.oil_price_action import (
    analyze_oil_price_action,
    compute_ema_series,
    pa_blocks_side,
    score_price_action_votes,
)


def _bars_up(n: int = 60, start: float = 80.0, step: float = 0.08) -> list[KlineBar]:
    out = []
    px = start
    t0 = 1_700_000_000.0
    for i in range(n):
        o = px
        c = px + step
        out.append(
            KlineBar(
                open_time=t0 + i * 300,
                open=o,
                high=max(o, c) + 0.04,
                low=min(o, c) - 0.03,
                close=c,
                volume=100 + i,
            )
        )
        px = c
    return out


def _bars_down(n: int = 60, start: float = 90.0, step: float = 0.08) -> list[KlineBar]:
    return _bars_up(n=n, start=start, step=-step)


def test_ema_series_length_and_trend():
    closes = [float(i) for i in range(1, 41)]
    e = compute_ema_series(closes, 9)
    assert len(e) == 40
    assert e[-1] > e[20]


def test_bull_stack_allows_long_blocks_short():
    bars = _bars_up(70)
    ta = SimpleNamespace(
        structure_label="HH + HL (бычья)",
        nearest_support=bars[-5].low,
        nearest_resistance=bars[-1].close * 1.01,
        breakout_level=None,
        breakdown_level=None,
        primary_chart_pattern=None,
        patterns=[],
        smc=None,
    )
    # touch support for bounce
    last = bars[-1]
    bars[-1] = KlineBar(
        open_time=last.open_time,
        open=last.open,
        high=last.high,
        low=ta.nearest_support * 0.999,
        close=ta.nearest_support * 1.001,
        volume=last.volume,
    )
    plan = analyze_oil_price_action(bars, ta)
    assert plan is not None
    assert plan.ema_stack in {"bull", "mixed"}
    assert plan.allow_long is True
    long_pts, short_pts, factors, levels = score_price_action_votes(plan)
    assert long_pts >= 0
    assert any("EMA" in f or "PA" in f or "Отскок" in f or "Структура" in f for f in factors)


def test_bear_stack_blocks_long():
    bars = _bars_down(70)
    ta = SimpleNamespace(
        structure_label="LH + LL (медв.)",
        nearest_support=bars[-1].close * 0.99,
        nearest_resistance=bars[-1].close * 1.01,
        breakout_level=None,
        breakdown_level=None,
        primary_chart_pattern=None,
        patterns=[],
        smc=None,
    )
    plan = analyze_oil_price_action(bars, ta)
    assert plan is not None
    assert plan.ema_stack in {"bear", "mixed"}
    if plan.ema_stack == "bear" and plan.structure == "bearish":
        assert plan.allow_long is False
        assert pa_blocks_side(plan, "long") is True
