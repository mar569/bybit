"""Tests for post-pump candle compression dual-breakout logic."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.ta_analysis import (
    detect_candle_compression,
    detect_post_pump_phase,
    recent_green_candle_bias,
)
from bot.ta_range_trade import evaluate_market_flow


def _bar(i: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> KlineBar:
    return KlineBar(
        open_time=1_700_000_000_000 + i * 300_000,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
    )


def _pump_then_compress() -> list[KlineBar]:
    bars: list[KlineBar] = []
    # Impulse up ~0.04 → 0.056
    price = 0.040
    for i in range(24):
        nxt = price * 1.015
        bars.append(_bar(i, price, nxt * 1.002, price * 0.998, nxt, v=500))
        price = nxt
    # Compression / tight range near highs (greenish)
    top = price
    for j in range(12):
        i = 24 + j
        o = top * (0.995 + (j % 3) * 0.001)
        c = o * 1.0015  # mostly green
        bars.append(_bar(i, o, max(o, c) * 1.001, min(o, c) * 0.999, c, v=80))
    return bars


def test_detect_compression_after_pump() -> None:
    bars = _pump_then_compress()
    assert detect_post_pump_phase(bars)
    assert detect_candle_compression(bars)
    assert recent_green_candle_bias(bars)


def test_flow_compression_not_forced_short() -> None:
    flow = evaluate_market_flow(
        momentum="flat",
        momentum_pct=-0.3,
        phase="consolidation",
        oi_narrative="accumulation",
        oi_context_strength=0.5,
        cvd_ratio=0.40,  # soft distribution
        liq_long_boost=0,
        liq_short_boost=0,
        range_position=0.90,
        post_pump=True,
        drawdown_from_high_pct=1.0,
        compression=True,
        green_bias=True,
    )
    # Without compression this used to be heavily corr; now dual
    assert flow.continuation >= flow.correction - 8
    assert any("сжатие" in n for n in flow.notes)


def test_flow_post_pump_without_compression_still_corr_bias() -> None:
    flow = evaluate_market_flow(
        momentum="up",
        momentum_pct=0.5,
        phase="impulse_up",
        oi_narrative="",
        oi_context_strength=0.4,
        cvd_ratio=0.40,
        liq_long_boost=0,
        liq_short_boost=0,
        range_position=0.90,
        post_pump=True,
        drawdown_from_high_pct=1.0,
        compression=False,
        green_bias=False,
    )
    assert flow.correction > flow.continuation + 10
