from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.volume_profile import volume_poc_price


def _bar(i: int, c: float, vol: float) -> KlineBar:
    return KlineBar(
        open_time=float(i),
        open=c,
        high=c * 1.001,
        low=c * 0.999,
        close=c,
        volume=vol,
    )


def test_volume_poc_prefers_high_volume_price() -> None:
    bars = [_bar(i, 100.0 + (i % 3) * 2.0, 10.0) for i in range(11)]
    bars.append(_bar(11, 108.0, 800.0))
    poc, label = volume_poc_price(bars)
    assert poc is not None
    assert poc >= 105.0
    assert "POC" in label
