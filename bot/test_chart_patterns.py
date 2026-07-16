from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.chart_patterns import (
    compute_atr,
    detect_chart_patterns,
    find_pattern_swings,
    pattern_location_ok,
    pick_primary_pattern,
)
from bot.chart_pattern_models import ChartPattern, PatternLine, PatternPoint
from bot.trade_decision_gate import detect_location
from bot.ta_analysis import run_ta_analysis


def _bar(
    i: int,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    base_ts: float = 1_700_000_000.0,
) -> KlineBar:
    return KlineBar(
        open_time=base_ts + i * 300,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
    )


def _double_bottom_bars() -> list[KlineBar]:
    bars: list[KlineBar] = []
    price = 108.0
    i = 0
    for _ in range(12):
        o = price
        c = price - 0.55
        bars.append(_bar(i, o, o + 0.2, c - 0.15, c))
        price = c
        i += 1
    for _ in range(8):
        o = price
        c = price + 0.65
        bars.append(_bar(i, o, c + 0.15, o - 0.1, c))
        price = c
        i += 1
    for _ in range(10):
        o = price
        c = price - 0.5
        bars.append(_bar(i, o, o + 0.15, c - 0.15, c))
        price = c
        i += 1
    for _ in range(6):
        o = price
        c = price + 0.2
        bars.append(_bar(i, o, c + 0.1, o - 0.05, c))
        price = c
        i += 1
    return bars


def _head_shoulders_bars() -> list[KlineBar]:
    bars: list[KlineBar] = []
    i = 0
    price = 100.0
    for _ in range(6):
        o = price
        c = price + 0.8
        bars.append(_bar(i, o, c + 0.3, o - 0.1, c))
        price = c
        i += 1
    for _ in range(4):
        o = price
        c = price - 0.7
        bars.append(_bar(i, o, o + 0.1, c - 0.2, c))
        price = c
        i += 1
    for _ in range(6):
        o = price
        c = price + 1.1
        bars.append(_bar(i, o, c + 0.3, o - 0.1, c))
        price = c
        i += 1
    for _ in range(4):
        o = price
        c = price - 0.8
        bars.append(_bar(i, o, o + 0.1, c - 0.2, c))
        price = c
        i += 1
    for _ in range(5):
        o = price
        c = price + 0.5
        bars.append(_bar(i, o, c + 0.2, o - 0.1, c))
        price = c
        i += 1
    for _ in range(4):
        o = price
        c = price - 0.9
        bars.append(_bar(i, o, o + 0.1, c - 0.2, c))
        price = c
        i += 1
    for _ in range(10):
        o = price
        c = price - 0.4
        bars.append(_bar(i, o, o + 0.1, c - 0.15, c))
        price = c
        i += 1
    return bars


def _cup_handle_bars() -> list[KlineBar]:
    bars: list[KlineBar] = []
    i = 0
    price = 120.0
    for _ in range(8):
        o = price
        c = price - 0.4
        bars.append(_bar(i, o, o + 0.2, c - 0.15, c))
        price = c
        i += 1
    for step in range(14):
        o = price
        c = price - 0.55 + step * 0.08
        bars.append(_bar(i, o, o + 0.15, c - 0.25, c))
        price = c
        i += 1
    for step in range(14):
        o = price
        c = price + 0.55 - step * 0.05
        bars.append(_bar(i, o, c + 0.2, o - 0.1, c))
        price = c
        i += 1
    for _ in range(5):
        o = price
        c = price - 0.35
        bars.append(_bar(i, o, o + 0.1, c - 0.15, c))
        price = c
        i += 1
    for _ in range(4):
        o = price
        c = price + 0.15
        bars.append(_bar(i, o, c + 0.1, o - 0.05, c))
        price = c
        i += 1
    return bars


def _three_indians_bars() -> list[KlineBar]:
    bars: list[KlineBar] = []
    i = 0
    price = 100.0
    for _ in range(10):
        o = price
        c = price + 0.5
        bars.append(_bar(i, o, c + 0.2, o - 0.1, c))
        price = c
        i += 1
    peaks = [0.8, 1.0, 1.2]
    for bump in peaks:
        for _ in range(6):
            o = price
            c = price + bump
            bars.append(_bar(i, o, c + 0.3, o - 0.2, c))
            price = c
            i += 1
        for _ in range(4):
            o = price
            c = price - bump * 0.6
            bars.append(_bar(i, o, o + 0.1, c - 0.2, c))
            price = c
            i += 1
    for _ in range(8):
        o = price
        c = price - 0.5
        bars.append(_bar(i, o, o + 0.1, c - 0.15, c))
        price = c
        i += 1
    return bars


def test_find_pattern_swings_filters_noise() -> None:
    bars = _double_bottom_bars()
    raw = find_pattern_swings(bars, window=3)
    assert len(raw) >= 3
    kinds = [s.kind for s in raw]
    assert "high" in kinds and "low" in kinds


def test_detect_double_bottom() -> None:
    bars = _double_bottom_bars()
    patterns = detect_chart_patterns(bars, min_confidence=0.45)
    kinds = {p.kind for p in patterns}
    assert "double_bottom" in kinds
    primary = pick_primary_pattern(patterns)
    assert primary is not None
    assert primary.target_price is not None
    assert primary.neckline is not None


def test_detect_head_shoulders() -> None:
    bars = _head_shoulders_bars()
    patterns = detect_chart_patterns(bars, min_confidence=0.45)
    assert any(p.kind == "head_shoulders" for p in patterns)


def test_detect_cup_handle() -> None:
    bars = _cup_handle_bars()
    patterns = detect_chart_patterns(bars, min_confidence=0.45)
    assert any(p.kind == "cup_handle" for p in patterns)


def test_detect_three_indians() -> None:
    bars = _three_indians_bars()
    patterns = detect_chart_patterns(bars, min_confidence=0.42)
    assert any(p.kind == "three_indians" for p in patterns)


def test_pattern_location_ok_near_rim() -> None:
    pat = ChartPattern(
        kind="cup_handle",
        subtype="continuation",
        status="forming",
        points=(),
        lines=(),
        zone_top=110.0,
        zone_bottom=100.0,
        neckline=PatternLine(0, 110.0, 50, 110.0, "rim"),
        pole_height=10.0,
        target_price=120.0,
        stop_price=99.0,
        confidence=0.75,
        score_breakdown={},
        source_rule="test",
        label_ru="test",
        direction="bullish",
    )
    assert pattern_location_ok(pat, side="long", price=109.5, tol_pct=1.0)


def test_detect_location_pattern() -> None:
    from bot.ta_analysis import TAAnalysisResult

    pat = ChartPattern(
        kind="double_bottom",
        subtype="reversal",
        status="confirmed",
        points=(),
        lines=(),
        zone_top=105.0,
        zone_bottom=100.0,
        neckline=PatternLine(0, 105.0, 50, 105.0, "neckline"),
        pole_height=5.0,
        target_price=110.0,
        stop_price=99.0,
        confidence=0.8,
        score_breakdown={},
        source_rule="test",
        label_ru="test",
        direction="bullish",
    )
    ta = TAAnalysisResult(current_price=104.8, primary_chart_pattern=pat)
    assert detect_location(ta, "long") == "pattern"


def test_run_ta_analysis_includes_chart_patterns() -> None:
    bars = _double_bottom_bars()
    ta = run_ta_analysis(bars, neutral=True, pattern_detection_enabled=True)
    assert isinstance(ta.chart_patterns, list)
    assert ta.primary_chart_pattern is None or ta.primary_chart_pattern.kind


def test_compute_atr_positive() -> None:
    bars = _double_bottom_bars()
    assert compute_atr(bars) > 0
