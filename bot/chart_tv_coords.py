"""Маппинг цена/бар → область свечей на скрине TradingView."""
from __future__ import annotations

from .bybit_klines import KlineBar
from .ta_analysis import TAAnalysisResult

TV_CHART_Y_BOTTOM = 0.24
TV_CHART_Y_TOP = 0.84


def price_to_tv_y(price: float, y_min: float, y_max: float) -> float:
    if y_max <= y_min:
        return (TV_CHART_Y_BOTTOM + TV_CHART_Y_TOP) / 2
    ratio = (price - y_min) / (y_max - y_min)
    ratio = max(0.0, min(1.0, ratio))
    return TV_CHART_Y_BOTTOM + (1.0 - ratio) * (TV_CHART_Y_TOP - TV_CHART_Y_BOTTOM)


def bar_x_norm(idx: int, n: int, *, x_start: float = 0.06, x_end: float = 0.88) -> float:
    if n <= 1:
        return x_end
    return x_start + (idx / (n - 1)) * (x_end - x_start)


def tv_nearby_levels(ta: TAAnalysisResult) -> list[float]:
    levels: list[float] = []
    for p in (ta.breakout_level, ta.breakdown_level, ta.invalidation_price):
        if p:
            levels.append(float(p))
    for p in (ta.target_prices or [])[:3]:
        levels.append(float(p))
    for p in (ta.nearest_support, ta.nearest_resistance):
        if p:
            levels.append(float(p))
    return levels


def tv_visible_price_range(bars: list[KlineBar], ta: TAAnalysisResult) -> tuple[float, float]:
    n = len(bars)
    visible_count = max(24, min(n, int(n * 0.58)))
    visible = bars[-visible_count:]
    prices: list[float] = []
    for b in visible:
        prices.extend([b.low, b.high])
    if ta.current_price:
        prices.append(ta.current_price)
    for lv in ta.levels[:4]:
        prices.append(lv.price)
    if ta.consolidation:
        prices.extend([ta.consolidation.top, ta.consolidation.bottom])
    if not prices:
        return 0.0, 1.0
    core_min, core_max = min(prices), max(prices)
    core_span = max(core_max - core_min, core_min * 0.0005)
    max_span = core_span * 1.42
    mid = ta.current_price or (core_min + core_max) / 2.0
    for p in tv_nearby_levels(ta):
        if core_min - core_span * 0.38 <= p <= core_max + core_span * 0.38:
            prices.append(p)
    y_min, y_max = min(prices), max(prices)
    span = y_max - y_min
    if span > max_span:
        y_min = mid - max_span / 2.0
        y_max = mid + max_span / 2.0
    pad = max(span, core_span) * 0.035
    return y_min - pad, y_max + pad
