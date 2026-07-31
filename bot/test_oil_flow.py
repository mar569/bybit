"""Tests for oil flow proxy (candle delta / volume)."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.oil_flow import compute_oil_flow_proxy, format_oil_flow_block
from bot.oil_monitor import OilMarketSnapshot, format_oil_market_digest


def _bar(ts: int, o: float, h: float, l: float, c: float, vol: float) -> KlineBar:
    return KlineBar(open_time=float(ts), open=o, high=h, low=l, close=c, volume=vol)


def test_flow_buy_bias_on_green_volume():
    bars = []
    for i in range(30):
        # Растущие свечи с объёмом — close у high
        bars.append(_bar(i, 80 + i * 0.1, 80.2 + i * 0.1, 79.9 + i * 0.1, 80.15 + i * 0.1, 1000))
    flow = compute_oil_flow_proxy(bars, lookback=8)
    assert flow is not None
    assert flow.bias == "buy"
    assert flow.buy_share_pct >= 55
    assert flow.delta_recent > 0
    text = format_oil_flow_block(flow)
    assert "Поток" in text
    assert "BUY" in text


def test_flow_sell_bias_on_red_volume():
    bars = []
    for i in range(30):
        px = 90 - i * 0.1
        bars.append(_bar(i, px + 0.1, px + 0.15, px - 0.05, px - 0.02, 800))
    flow = compute_oil_flow_proxy(bars, lookback=8)
    assert flow is not None
    assert flow.bias == "sell"
    assert flow.delta_recent < 0


def test_digest_includes_flow_block():
    bars = [_bar(i, 86, 86.3, 85.8, 86.2, 500) for i in range(40)]
    snap = OilMarketSnapshot(
        label="Brent · UKOUSD",
        symbol="UKOUSD",
        price=86.2,
        high_7d=90.0,
        low_7d=84.0,
        verdict="WAIT",
        confidence=5,
        support=85.5,
        resistance=87.0,
        breakdown=85.0,
        breakout=87.5,
        phase="база",
        elliott="",
        reason="",
    )
    text = format_oil_market_digest([snap], bars=bars, interval_minutes=15)
    assert "Поток (прокси)" in text
