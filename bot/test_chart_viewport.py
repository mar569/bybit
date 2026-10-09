from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.manual_ta import compute_structure_bar_span


def _bars(n: int, *, hi_at: int = 0, lo_at: int = 0) -> list[KlineBar]:
    out: list[KlineBar] = []
    for i in range(n):
        h, l, c = 100.0, 99.0, 99.5
        if i == hi_at:
            h, l, c = 130.0, 128.0, 129.0
        if i == lo_at:
            h, l, c = 90.0, 88.0, 89.0
        out.append(KlineBar(open_time=i * 900, open=c, high=h, low=l, close=c, volume=1.0))
    return out


def test_structure_span_includes_global_extremes() -> None:
    bars = _bars(80, hi_at=5, lo_at=70)
    span = compute_structure_bar_span(object(), bars)
    assert span >= 74
