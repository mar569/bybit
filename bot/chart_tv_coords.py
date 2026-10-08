"""Маппинг цена/бар → область свечей на скрине TradingView."""
from __future__ import annotations

from .bybit_klines import KlineBar
from .ta_analysis import TAAnalysisResult

# После crop+resize: снизу объём, сверху пара px отступа
TV_CHART_Y_BOTTOM = 0.10
TV_CHART_Y_TOP = 0.90


def bars_per_hour(interval_minutes: int) -> int:
    return max(1, 60 // max(1, int(interval_minutes)))


def tv_visible_bar_count(
    n: int,
    *,
    interval_minutes: int = 5,
    display_hours: int | None = None,
) -> int:
    """Сколько последних баров кладём на ширину скрина TV (как zoom_hours в matplotlib)."""
    if n <= 0:
        return 24
    if display_hours and display_hours > 0:
        want = max(36, int(display_hours) * bars_per_hour(interval_minutes))
        return max(24, min(n, want))
    return max(24, min(n, int(n * 0.58)))


def tv_vis_start(
    n: int,
    *,
    interval_minutes: int = 5,
    display_hours: int | None = None,
) -> int:
    vis = tv_visible_bar_count(n, interval_minutes=interval_minutes, display_hours=display_hours)
    return max(0, n - vis)


def bar_index_on_tv_screen(
    bar_idx: int,
    *,
    vis_start: int,
    n: int,
) -> bool:
    return vis_start - 2 <= int(bar_idx) < n


def price_to_tv_y(price: float, y_min: float, y_max: float) -> float:
    """Цена → доля кадра. y=0 низ PNG, y=1 верх: выше цена — выше на скрине."""
    if y_max <= y_min:
        return (TV_CHART_Y_BOTTOM + TV_CHART_Y_TOP) / 2
    ratio = (float(price) - y_min) / (y_max - y_min)
    ratio = max(0.0, min(1.0, ratio))
    return TV_CHART_Y_BOTTOM + ratio * (TV_CHART_Y_TOP - TV_CHART_Y_BOTTOM)


def price_on_tv_screen(price: float, y_min: float, y_max: float, *, margin_ratio: float = 0.06) -> bool:
    if y_max <= y_min or price <= 0:
        return False
    span = y_max - y_min
    margin = span * margin_ratio
    return (y_min - margin) <= float(price) <= (y_max + margin)


def bar_x_norm(idx: int, n: int, *, x_start: float = 0.06, x_end: float = 0.88) -> float:
    if n <= 1:
        return x_end
    return x_start + (idx / (n - 1)) * (x_end - x_start)


def interpolate_bar_price(
    bars: list[KlineBar],
    idx: float,
    *,
    price_a: float | None = None,
    price_b: float | None = None,
    idx_a: int | None = None,
    idx_b: int | None = None,
) -> float:
    """Цена на баре idx по линии между двумя опорными точками."""
    if idx_a is not None and idx_b is not None and price_a is not None and price_b is not None:
        if idx_b == idx_a:
            return float(price_a)
        t = (float(idx) - idx_a) / (idx_b - idx_a)
        return float(price_a) + t * (float(price_b) - float(price_a))
    i = max(0, min(int(round(idx)), len(bars) - 1))
    return float(bars[i].close)


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
    rbr = None
    metrics = getattr(ta, "market_metrics", None) or {}
    if isinstance(metrics, dict):
        raw = metrics.get("range_breakdown_retest")
        if isinstance(raw, dict):
            rbr = raw
    if rbr:
        for key in ("range_bottom", "range_top", "entry_lo", "entry_hi", "stop"):
            v = rbr.get(key)
            if v:
                levels.append(float(v))
        for t in (rbr.get("targets") or [])[:2]:
            if t:
                levels.append(float(t))
    return levels


def tv_visible_price_range(
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 5,
    display_hours: int | None = None,
) -> tuple[float, float]:
    n = len(bars)
    visible_count = tv_visible_bar_count(
        n, interval_minutes=interval_minutes, display_hours=display_hours,
    )
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
    max_span = core_span * 1.38
    mid = ta.current_price or (core_min + core_max) / 2.0
    for p in tv_nearby_levels(ta):
        if core_min - core_span * 0.32 <= p <= core_max + core_span * 0.32:
            prices.append(p)
    y_min, y_max = min(prices), max(prices)
    span = y_max - y_min
    if span > max_span:
        y_min = mid - max_span / 2.0
        y_max = mid + max_span / 2.0
    pad = max(span, core_span) * 0.04
    return y_min - pad, y_max + pad
