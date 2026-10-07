from __future__ import annotations

from bot.chart_pattern_models import ChartPattern, PatternLine, PatternPoint
from bot.chart_patterns import pattern_relevant_now, pick_primary_pattern
from bot.test_chart_patterns import _bar


def _double_bottom_pattern(*, last_trough_i: int, neck: float, trough: float) -> ChartPattern:
    peak_i = last_trough_i - 8
    return ChartPattern(
        kind="double_bottom",
        subtype="reversal",
        status="confirmed",
        points=(
            PatternPoint(last_trough_i - 16, trough, "trough1"),
            PatternPoint(last_trough_i, trough * 1.002, "trough2"),
            PatternPoint(peak_i, neck, "neck"),
        ),
        lines=(),
        zone_top=neck,
        zone_bottom=trough,
        neckline=PatternLine(peak_i, neck, last_trough_i, neck, "neckline"),
        pole_height=neck - trough,
        target_price=neck + (neck - trough),
        stop_price=trough * 0.99,
        confidence=0.78,
        score_breakdown={},
        source_rule="test",
        label_ru="Двойное дно",
        direction="bullish",
    )


def _simple_bar(i: int, c: float) -> KlineBar:
    return _bar(i, c, c * 1.002, c * 0.998, c)


def test_stale_double_bottom_after_parabolic_pump() -> None:
    trough, neck = 0.302, 0.335
    n = 120
    bars = [_simple_bar(i, 0.31 + i * 0.0002) for i in range(n)]
    for i in range(80, n):
        c = 0.32 + (i - 80) * 0.0025
        bars[i] = _simple_bar(i, c)
    bars[-1] = _simple_bar(n - 1, 0.44)
    pat = _double_bottom_pattern(last_trough_i=40, neck=neck, trough=trough)
    assert pattern_relevant_now(pat, bars, current=0.44) is False
    assert pick_primary_pattern([pat], bars=bars, current=0.44) is None


def test_fresh_double_bottom_still_relevant() -> None:
    trough, neck = 0.30, 0.318
    bars = [_simple_bar(i, 0.305 + i * 0.0003) for i in range(80)]
    pat = _double_bottom_pattern(last_trough_i=70, neck=neck, trough=trough)
    assert pattern_relevant_now(pat, bars, current=0.317) is True
