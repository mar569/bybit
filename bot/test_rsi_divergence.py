"""Tests for TradingView-style RSI calculative divergence."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.rsi_divergence import (
    compute_rsi_wilder,
    detect_rsi_divergences,
    rsi_divergence_flow_adjust,
)


def _bar(i: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(
        open_time=1_700_000_000_000 + i * 3_600_000,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def _regular_bear_series() -> list[KlineBar]:
    """Price higher highs with weakening momentum (RSI LH) — synthetic."""
    bars: list[KlineBar] = []
    # base chop
    p = 100.0
    for i in range(30):
        bars.append(_bar(i, p, p + 0.4, p - 0.4, p + 0.1))
        p += 0.05
    # swing high A around 112
    for i in range(30, 42):
        p = 105 + (i - 30) * 0.6
        bars.append(_bar(i, p - 0.3, p + 0.5, p - 0.5, p))
    # pullback
    for i in range(42, 55):
        p = 112 - (i - 42) * 0.35
        bars.append(_bar(i, p + 0.2, p + 0.4, p - 0.4, p))
    # swing high B higher price but slower (smaller green bodies → RSI weaker)
    for i in range(55, 72):
        p = 108 + (i - 55) * 0.45
        bars.append(_bar(i, p - 0.15, p + 0.25, p - 0.25, p))
    # settle
    for i in range(72, 90):
        bars.append(_bar(i, p, p + 0.2, p - 0.3, p - 0.1))
        p -= 0.05
    return bars


def _regular_bull_series() -> list[KlineBar]:
    bars: list[KlineBar] = []
    p = 120.0
    for i in range(25):
        bars.append(_bar(i, p, p + 0.3, p - 0.3, p - 0.05))
        p -= 0.08
    # low A
    for i in range(25, 38):
        p = 110 - (i - 25) * 0.5
        bars.append(_bar(i, p + 0.2, p + 0.4, p - 0.5, p))
    # bounce
    for i in range(38, 50):
        p = 104 + (i - 38) * 0.4
        bars.append(_bar(i, p - 0.2, p + 0.4, p - 0.3, p))
    # low B lower price, stronger bounce candles → RSI HL
    for i in range(50, 65):
        p = 108 - (i - 50) * 0.55
        # stronger closes relative to range
        bars.append(_bar(i, p + 0.1, p + 0.6, p - 0.3, p + 0.35))
    for i in range(65, 85):
        bars.append(_bar(i, p, p + 0.5, p - 0.2, p + 0.3))
        p += 0.2
    return bars


def test_rsi_wilder_bounds() -> None:
    closes = [100.0 + i * 0.5 for i in range(40)]
    rsi = compute_rsi_wilder(closes, 14)
    assert len(rsi) == 40
    assert 50 <= rsi[-1] <= 100


def test_detect_finds_some_divergence() -> None:
    # At least one of the synthetic series should produce a divergence
    found = False
    for series in (_regular_bear_series(), _regular_bull_series()):
        res = detect_rsi_divergences(series, pivot_left=3, pivot_right=3, min_bars_between=3)
        assert len(res.rsi) == len(series)
        if res.divergences:
            found = True
            assert res.last is not None
            assert res.last.label in {"Bull", "Bear"}
            assert 0.0 <= res.last.strength <= 1.0
    assert found, "expected at least one synthetic divergence"


def test_flow_adjust_regular_bear() -> None:
    from bot.rsi_divergence import RsiDivergence, RsiDivergenceResult

    last = RsiDivergence(
        kind="regular_bear",
        label="Bear",
        label_ru="test",
        idx_a=10,
        idx_b=30,
        price_a=1.0,
        price_b=1.1,
        rsi_a=75.0,
        rsi_b=60.0,
        strength=0.7,
        bars_between=20,
        rsi_delta=-15.0,
    )
    pack = RsiDivergenceResult(last=last, bias="short", divergences=[last], rsi_last=55.0)
    cont, corr, notes = rsi_divergence_flow_adjust(pack)
    assert corr > cont
    assert any("Bear" in n for n in notes)
