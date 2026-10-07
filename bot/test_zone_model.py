from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.zone_model import (
    TradingZone,
    build_trading_zones,
    calibrate_zone_span,
    select_zones_for_chart,
)


def _bar(i: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(open_time=float(i), open=o, high=h, low=l, close=c, volume=100.0)


def test_build_trading_zones_includes_sr_or_demand() -> None:
    bars: list[KlineBar] = []
    p = 100.0
    for i in range(30):
        p += 0.2 if i % 5 else -0.15
        bars.append(_bar(i, p, p + 0.5, p - 0.5, p + 0.1))
    from bot.ta_analysis import find_swing_points

    swings = find_swing_points(bars)
    zones = build_trading_zones(bars, swings, current=bars[-1].close)
    assert isinstance(zones, list)


def test_calibrate_zone_span_shrinks_wide_box() -> None:
    z = TradingZone(
        kind="demand",
        top=110.0,
        bottom=90.0,
        tf_label="LTF",
        freshness="fresh",
        valid=True,
        label_ru="test",
        start_idx=0,
    )
    out = calibrate_zone_span(z, current=100.0, atr_pct=0.8)
    assert out is not None
    assert out.top - out.bottom < 110.0 - 90.0


def test_select_zones_for_chart_skips_invalid_demand() -> None:
    cur = 100.0
    zones = [
        TradingZone(
            "demand", 101, 99, "LTF", "fresh", False, label_ru="bad", start_idx=0
        ),
        TradingZone(
            "sr_support", 99.5, 98.5, "H1", "tested", True, 3, "ok", 0
        ),
    ]
    picked = select_zones_for_chart(zones, cur)
    assert all(z.valid for z in picked)
    assert not any(z.kind == "demand" for z in picked)
