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


def test_prose_no_fake_trigger_on_seek_junk() -> None:
    ta = _minimal_ta(
        recent_price_action_ru=(
            "Последние свечи: зелёная → красная → сейчас: зелёная. Close 0.07252."
        ),
        reading_seek_label="слабость продаж на новом лое",
        market_participation_lines=["OI снижается на закрытии позиций"],
        action_priority="short",
        target_prices=[0.06697],
    )
    read = build_chart_read(ta, symbol="STRKUSDT")
    html = read.telegram_prose_html()
    assert "Триггер:" not in html
    assert "слабость продаж" not in html
    assert "Жду:" in html or "0.06697" in html or "0.095" in html
    assert "Close 0.07252" not in html
    assert "OI снижается" in html


def test_prose_rbr_short_trigger_not_wall() -> None:
    ta = _minimal_ta(
        recent_price_action_ru="Последние свечи: красная после отказа сверху. Close 101.05.",
        market_participation_lines=["CVD buy 8/10"],
        market_metrics={
            "range_breakdown_retest": {
                "phase": "await_break",
                "direction": "short",
                "range_top": 104.85,
                "range_bottom": 96.9615,
                "targets": [89.2046, 85.0],
            }
        },
        breakout_level=104.85,
        breakdown_level=96.9615,
        action_priority="short",
    )
    read = build_chart_read(ta, symbol="BZUSDT")
    html = read.telegram_prose_html()
    assert "Боковик" not in html or "Жду:" in html
    assert "96.9615" in html
    assert "Триггер:" not in html
    assert ";" not in html


def test_expect_target_above_price_for_long() -> None:
    ta = _minimal_ta(
        current_price=0.073,
        breakout_level=0.07303,
        breakdown_level=0.07251,
        action_priority="long",
        target_prices=[0.07131, 0.07811],
    )
    read = build_chart_read(ta, symbol="STRKUSDT")
    assert "0.07811" in read.expect_ru or "0.078" in read.expect_ru
    assert "0.07131" not in read.expect_ru


def test_scenario_not_short_through_breakout_up() -> None:
    from bot.bybit_klines import KlineBar

    ta = _minimal_ta(
        current_price=0.545,
        breakout_level=0.53316,
        breakdown_level=0.50469,
        action_priority="short",
        target_prices=[0.49],
    )
    bars = [
        KlineBar(open_time=1, open=0.52, high=0.525, low=0.518, close=0.522, volume=1),
        KlineBar(open_time=2, open=0.522, high=0.548, low=0.52, close=0.545, volume=1),
    ]
    from bot.core.chart_read import _scenario_from_ta

    wps, lbl = _scenario_from_ta(ta, bars)
    assert "↓" not in lbl or "retest" in lbl.lower()
    assert "↑" in lbl
    assert wps[1] <= ta.breakout_level * 1.001


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
