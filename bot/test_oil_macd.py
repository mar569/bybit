"""MACD + trend block for oil signals."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.oil_macd import compute_oil_macd, macd_blocks_side, trend_blocks_counter_trade


def _up_bars(n: int = 40, start: float = 80.0) -> list[KlineBar]:
    bars = []
    t0 = 1_700_000_000.0
    px = start
    for i in range(n):
        nxt = px + 0.05
        bars.append(
            KlineBar(
                open_time=t0 + i * 300,
                open=px,
                high=nxt + 0.02,
                low=px - 0.01,
                close=nxt,
                volume=1000.0,
            )
        )
        px = nxt
    return bars


def test_macd_bull_on_uptrend():
    bars = _up_bars()
    m = compute_oil_macd(bars)
    assert m is not None
    assert m.bias in {"bull", "neutral"}  # strong up → usually bull
    assert macd_blocks_side(m, "short") or m.bias == "neutral"


def test_trend_blocks_short_on_rally():
    bars = _up_bars()
    blocked, why = trend_blocks_counter_trade(bars, side="short", interval_minutes=5)
    assert blocked is True
    assert "SHORT" in why or "MACD" in why or "импульс" in why
