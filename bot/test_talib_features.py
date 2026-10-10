from __future__ import annotations

from bot.analytics.talib_features import compute_rsi_series, compute_talib_snapshot, talib_available
from bot.bybit_klines import KlineBar


def _bars(n: int = 80, *, trend: float = 0.001) -> list[KlineBar]:
    bars: list[KlineBar] = []
    p = 100.0
    for i in range(n):
        o = p
        p *= 1.0 + trend + (0.002 if i % 7 == 0 else -0.001)
        h = max(o, p) * 1.002
        l = min(o, p) * 0.998
        bars.append(
            KlineBar(
                open_time=float(i * 300_000),
                open=o,
                high=h,
                low=l,
                close=p,
                volume=1000.0 + i,
            )
        )
    return bars


def test_rsi_series_fallback_without_talib() -> None:
    closes = [100.0 + i * 0.1 for i in range(40)]
    series = compute_rsi_series(closes, 14)
    assert len(series) == len(closes)
    assert 0 <= series[-1] <= 100


def test_talib_snapshot_graceful() -> None:
    snap = compute_talib_snapshot(_bars())
    if talib_available():
        assert snap.available
        assert snap.rsi_14 is not None
    else:
        assert not snap.available
