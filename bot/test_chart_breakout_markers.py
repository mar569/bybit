from __future__ import annotations

from .bybit_klines import KlineBar
from .chart_breakout_markers import collect_breakout_retest_events
from .ta_analysis import TAAnalysisResult


def _bar(t: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(open_time=t, open=o, high=h, low=l, close=c, volume=1000.0)


def test_break_down_and_retest_detected():
    level = 100.0
    bars = [
        _bar(i * 60, 101, 101.5, 100.5, 101) for i in range(10)
    ]
    bars.append(_bar(600, 100.2, 100.5, 99.2, 99.5))  # break
    bars.extend(_bar(660 + i * 60, 99, 99.5, 98.5, 98.8) for i in range(3))
    bars.append(_bar(840, 99.0, 100.3, 98.8, 99.2))  # retest touch

    ta = TAAnalysisResult(
        verdict="WAIT",
        breakdown_level=level,
        current_price=bars[-1].close,
    )
    ev = collect_breakout_retest_events(bars, ta, max_events=2)
    kinds = {e.kind for e in ev}
    assert "breakout" in kinds
    assert any(e.price == level for e in ev)
