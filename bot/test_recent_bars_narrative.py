from __future__ import annotations

from .bybit_klines import KlineBar
from .recent_bars_narrative import describe_recent_bars


def _bar(o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(open_time=0, open=o, high=h, low=l, close=c, volume=1.0)


def test_describe_rejection_wick(monkeypatch) -> None:
    monkeypatch.setenv("ED_TELEGRAM_CANDLE_NARRATIVE", "1")
    bars = [
        _bar(100, 101, 99.5, 100.8),
        _bar(100.8, 102, 100.5, 101.5),
        _bar(101.5, 103, 101, 101.2),  # long upper wick
        _bar(101.2, 101.5, 100, 100.3),
    ]
    text = describe_recent_bars(bars, count=4)
    assert "Последние свечи" in text
    assert "отказ" in text.lower() or "красная" in text.lower()
