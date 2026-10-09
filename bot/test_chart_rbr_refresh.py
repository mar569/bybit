from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.chart_rbr_refresh import rbr_invalidated_by_price, refresh_rbr_for_chart
from bot.ta_analysis import TAAnalysisResult


def _bar(t: float, c: float, *, h: float | None = None, l: float | None = None) -> KlineBar:
    h = h if h is not None else c * 1.002
    l = l if l is not None else c * 0.998
    return KlineBar(open_time=t, open=c, high=h, low=l, close=c, volume=1.0)


def test_rbr_dropped_after_breakout_above_ceil() -> None:
    ta = TAAnalysisResult(current_price=1.48, verdict="WAIT")
    ta.market_metrics = {
        "range_breakdown_retest": {
            "phase": "fade_top",
            "direction": "short",
            "range_top": 1.43,
            "range_bottom": 1.41,
            "entry_lo": 1.42,
            "entry_hi": 1.44,
            "targets": [1.40],
        }
    }
    bars = [_bar(1.0 + i, 1.47 + (0.01 if i == 9 else 0)) for i in range(10)]
    bars[-1] = _bar(10.0, 1.489, h=1.495, l=1.47)
    assert rbr_invalidated_by_price(ta, bars)
    ta2 = refresh_rbr_for_chart(ta, bars)
    assert ta2.market_metrics.get("range_breakdown_retest") is None
    assert "playbook_v3" not in (ta2.market_metrics or {})
