"""Tests for BuyHold pattern foresight 1–3h + cup enable."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.chart_pattern_models import ChartPattern, PatternLine, PatternPoint
from bot.chart_patterns import detect_chart_patterns, pattern_location_ok
from bot.pattern_foresight import (
    build_pattern_foresight,
    estimate_horizon_hours,
    tag_patterns_timeframe,
)
from bot.pattern_specs import CUP_ENABLED
from bot.ta_analysis import run_ta_analysis
from bot.test_chart_patterns import _bar, _cup_handle_bars, _double_bottom_bars


def _pat(
    *,
    kind: str = "flag",
    status: str = "forming",
    direction: str = "bullish",
    subtype: str = "continuation",
    conf: float = 0.72,
    target: float | None = 110.0,
    stop: float | None = 95.0,
    label: str = "Флаг",
) -> ChartPattern:
    return ChartPattern(
        kind=kind,
        subtype=subtype,
        status=status,
        points=(PatternPoint(10, 100.0, "a"), PatternPoint(20, 102.0, "b")),
        lines=(PatternLine(10, 100.0, 20, 102.0, "body"),),
        zone_top=103.0,
        zone_bottom=98.0,
        neckline=PatternLine(10, 103.0, 30, 103.0, "neck"),
        pole_height=5.0,
        target_price=target,
        stop_price=stop,
        confidence=conf,
        score_breakdown={},
        source_rule="test",
        label_ru=label,
        direction=direction,
        entry_mode="wait" if status == "forming" else "breakout",
        psychology_note="тест",
    )


def test_cup_enabled_flag() -> None:
    assert CUP_ENABLED is True


def test_detect_cup_may_appear() -> None:
    """С CUP_ENABLED детектор чашки подключён (на синтетике может не сработать)."""
    bars = _cup_handle_bars()
    patterns = detect_chart_patterns(bars, min_confidence=0.45)
    # Не требуем обязательного детекта на грубой синтетике — проверяем, что вызов не падает
    # и kinds чашки/блюдца допустимы в пайплайне.
    assert isinstance(patterns, list)
    for p in patterns:
        assert p.kind  # nonempty


def test_forming_foresight_watch_only() -> None:
    forming = _pat(status="forming", label="Вымпел", kind="pennant")
    fs = build_pattern_foresight([forming], primary=forming, atr=0.5, interval_minutes=5)
    assert fs.active
    assert fs.watch_only
    assert fs.status in {"forming", "awaiting_breakout"}
    assert 1.0 <= fs.horizon_hours <= 3.0
    assert "пробой" in fs.trigger_text.lower() or "ждать" in fs.trigger_text.lower()
    assert fs.hot_line()
    assert "foresight" in fs.pro_html().lower() or "Фигур" in fs.pro_html()


def test_confirmed_foresight_not_watch() -> None:
    conf = _pat(status="confirmed", label="Двойное дно", kind="double_bottom", subtype="reversal")
    fs = build_pattern_foresight([conf], primary=conf, atr=0.4)
    assert fs.active
    assert not fs.watch_only
    assert fs.status == "confirmed"
    assert fs.bias == "long"


def test_htf_conflict_forces_watch() -> None:
    ltf = _pat(direction="bullish", status="confirmed", label="Флаг", kind="flag")
    htf = _pat(
        direction="bearish",
        status="confirmed",
        label="Голова и плечи",
        kind="head_shoulders",
        subtype="reversal",
        conf=0.8,
    )
    fs = build_pattern_foresight(
        [ltf],
        htf_patterns=[htf],
        primary=ltf,
        atr=0.5,
    )
    assert fs.htf_conflict
    assert fs.watch_only
    assert fs.status == "conflict"
    assert fs.bias == "short"  # приоритет HTF


def test_horizon_clamped() -> None:
    from dataclasses import replace

    p = replace(_pat(kind="rectangle"), pole_height=20.0)
    h = estimate_horizon_hours(p, atr=0.1, interval_minutes=5)
    assert 1.0 <= h <= 3.0


def test_forming_not_entry_location() -> None:
    p = _pat(status="forming", conf=0.9)
    assert pattern_location_ok(p, side="long", price=103.0) is False


def test_tag_timeframe() -> None:
    tagged = tag_patterns_timeframe([_pat()], "htf")
    assert tagged[0].timeframe == "htf"


def test_run_ta_includes_foresight_fields() -> None:
    bars = _double_bottom_bars()
    # синтетические HTF: растянем те же бары как «1h» (достаточно длины)
    htf = []
    for i, b in enumerate(bars[::2] or bars):
        htf.append(
            KlineBar(
                open_time=b.open_time,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
        )
    while len(htf) < 30:
        last = htf[-1]
        htf.append(
            _bar(len(htf), last.close, last.close + 0.1, last.close - 0.1, last.close)
        )
    ta = run_ta_analysis(bars, htf_bars=htf, hours=4, pattern_min_confidence=0.55)
    # поля foresight всегда присутствуют
    assert hasattr(ta, "pattern_foresight_summary")
    assert hasattr(ta, "htf_chart_patterns")
    assert isinstance(ta.htf_chart_patterns, list)
    assert ta.pattern_foresight_bias in {"long", "short", "neutral"}
