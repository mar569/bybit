from __future__ import annotations

from .bybit_klines import KlineBar
from .swing_extreme_memory import SwingExtremeMemory
from .ta_analysis import SwingPoint


def test_memory_weak_high_on_second_run() -> None:
    mem = SwingExtremeMemory(ttl_seconds=3600)
    bars: list[KlineBar] = []
    for i in range(40):
        h = 1.0 + i * 0.01
        if i == 30:
            h = 1.5
        if i == 38:
            h = 1.55
        bars.append(
            KlineBar(
                open_time=float(i * 300_000),
                open=h,
                high=h,
                low=h - 0.02,
                close=h - 0.005,
                volume=5000 if i != 38 else 2000,
            )
        )
    swings = [
        SwingPoint(index=30, price=1.5, kind="high"),
        SwingPoint(index=38, price=1.55, kind="high"),
    ]
    div = mem.update("TESTUSDT", 5, bars, swings, cvd_ratio=0.55)
    assert div is None or div.kind == "weak_high"
    div2 = mem.update("TESTUSDT", 5, bars, swings, cvd_ratio=0.40)
    assert div2 is not None
    assert div2.kind == "weak_high"
