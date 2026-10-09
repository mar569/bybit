"""ChartRead: единый объект для prose + canvas."""
from __future__ import annotations

from bot.core.chart_read import ChartRead, build_chart_read, get_or_build_chart_read
from bot.ta_analysis import TAAnalysisResult


def _minimal_ta(**kw: object) -> TAAnalysisResult:
    ta = TAAnalysisResult(
        current_price=1.0,
        breakout_level=1.05,
        breakdown_level=0.95,
        verdict="WAIT",
        analysis_interval_minutes=15,
        market_metrics={},
    )
    for key, value in kw.items():
        setattr(ta, key, value)
    return ta


def test_build_chart_read_levels_and_trigger() -> None:
    ta = _minimal_ta()
    read = build_chart_read(ta, symbol="MONUSDT")
    assert read.symbol == "MONUSDT"
    assert read.bias in {"long", "short", "wait", ""}
    assert read.trigger_ru
    assert len(read.levels) >= 2
    assert read.situation_ru


def test_chart_read_cache_on_ta() -> None:
    ta = _minimal_ta(current_price=2.0, breakout_level=2.1, breakdown_level=1.9)
    a = get_or_build_chart_read(ta, symbol="MONUSDT")
    b = get_or_build_chart_read(ta, symbol="MONUSDT")
    assert a.phase == b.phase
    assert isinstance(ta.market_metrics.get("chart_read_v1"), dict)


def test_telegram_prose_html_escapes() -> None:
    read = ChartRead(
        symbol="X",
        phase="observe",
        phase_ru="Фаза.",
        structure_ru="",
        bias="wait",
        situation_ru="Цена <test> у уровня.",
        expect_ru="Ждём retest.",
        trigger_ru="Close < 1.0",
        flow_hint_ru="",
        pattern_label="",
        pattern_confidence=0.0,
    )
    html = read.telegram_prose_html()
    assert "&lt;" in html
    assert "<test>" not in html
