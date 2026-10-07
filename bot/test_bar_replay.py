from __future__ import annotations

from .bar_replay import parse_replay_from_text, resolve_as_of_index, trim_bars
from .bybit_klines import KlineBar


def _bars(n: int, *, start: float = 100.0) -> list[KlineBar]:
    out: list[KlineBar] = []
    for i in range(n):
        p = start + i * 0.5
        out.append(
            KlineBar(
                open_time=float(i * 300_000),
                open=p,
                high=p + 0.2,
                low=p - 0.2,
                close=p + 0.1,
                volume=1000 + i * 10,
            )
        )
    return out


def test_resolve_negative_bar_index() -> None:
    bars = _bars(10)
    sel = resolve_as_of_index(bars, bar_index=-2)
    assert sel is not None
    assert sel.end_index == 8
    assert len(trim_bars(bars, sel.end_index)) == 9


def test_parse_replay_price_and_ticker() -> None:
    cleaned, idx, price, _ = parse_replay_from_text("MONUSDT 15m @0.034")
    assert "@0.034" not in cleaned
    assert price == 0.034
    assert idx is None
