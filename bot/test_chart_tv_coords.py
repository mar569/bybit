from __future__ import annotations

from .bybit_klines import KlineBar
from .chart_tv_coords import bar_x_norm, price_to_tv_y, tv_visible_bar_count, tv_visible_price_range
from .chart_tv_pro_overlay import TvCoordMapper
from .ta_analysis import TAAnalysisResult


def _bar(t: int, c: float) -> KlineBar:
    return KlineBar(open_time=t, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)


def test_price_to_tv_y_monotonic():
    y_lo = price_to_tv_y(100.0, 90.0, 110.0)
    y_hi = price_to_tv_y(110.0, 90.0, 110.0)
    assert y_hi > y_lo  # y=0 низ кадра: выше цена — выше на графике


def test_mapper_x_increases_with_bar_index():
    bars = [_bar(i * 60, 100 + i * 0.1) for i in range(40)]
    ta = TAAnalysisResult(current_price=bars[-1].close)
    y_min, y_max = tv_visible_price_range(bars, ta, interval_minutes=5, display_hours=14)
    m = TvCoordMapper(bars, y_min, y_max, interval_minutes=5, display_hours=14)
    assert m.x(10) <= m.x(20) <= m.x(39)


def test_display_hours_controls_visible_bars():
    n = 200
    assert tv_visible_bar_count(n, interval_minutes=5, display_hours=14) == 168
    assert tv_visible_bar_count(n, interval_minutes=5, display_hours=None) == max(24, int(n * 0.58))


def test_bar_x_norm_endpoints():
    assert bar_x_norm(0, 10) == 0.06
    assert abs(bar_x_norm(9, 10) - 0.88) < 1e-6
