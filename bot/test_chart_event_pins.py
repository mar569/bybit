from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.chart_event_pins import resolve_sweep_geometry
from bot.smc_analysis import SmcMarker


def _bar(i: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(open_time=i, open=o, high=h, low=l, close=c, volume=1.0)


def test_resolve_sweep_finds_wick_bar() -> None:
    level = 100.0
    bars = [
        _bar(0, 99, 100.5, 98, 99),
        _bar(1, 99, 100.2, 98.5, 99.5),
        _bar(2, 99.5, 101.2, 99, 100.0),  # sweep wick
        _bar(3, 100, 100.4, 99.2, 99.8),
    ]
    marker = SmcMarker(
        index=3,
        price=level,
        kind="sweep",
        label="свип↑",
        direction="short",
        ref_price=level,
    )
    bar_i, wick, liq = resolve_sweep_geometry(bars, marker)
    assert bar_i == 2
    assert wick == 101.2
    assert liq == level
