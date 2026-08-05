"""Tests for oil confluence setups (manual TA)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.oil_confluence import (
    build_oil_confluence_setup,
    format_oil_confluence_setup,
    read_oil_chart_structure,
    setup_passes_gate,
)
from bot.oil_forecast import OilForecast
from bot.oil_monitor import OilBouncePlan, OilNewsBias, OilScalpCall
from bot.ta_analysis import TAAnalysisResult


@pytest.fixture(autouse=True)
def _disable_session_fragile(monkeypatch):
    monkeypatch.setattr(
        "bot.oil_entry_filters.is_session_open_fragile",
        lambda **_kw: False,
    )


def _snap(**kwargs):
    base = dict(
        label="Brent · UKOUSD",
        symbol="UKOUSD",
        price=87.5,
        high_7d=92.0,
        low_7d=84.0,
        verdict="SHORT",
        confidence=7,
        support=85.5,
        resistance=87.6,
        breakdown=85.0,
        breakout=88.5,
        phase="медвежий",
        elliott="",
        reason="test",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_short_setup_near_resistance_passes_gate():
    snap = _snap(price=87.55)  # near R 87.6
    ta = TAAnalysisResult(verdict="SHORT", verdict_confidence=7)
    forecast = OilForecast(
        bias="SHORT",
        scenario="deal_tape",
        confidence=7,
        horizon_ru="свинг",
        headline_ru="Bias SHORT",
        base_case_ru="вниз",
        alt_case_ru="вверх",
        invalidation_ru="выше 88.5",
        watch_list_ru=("Ормуз",),
        entry_hint_ru="short от R",
    )
    bias = OilNewsBias(
        bias="bearish",
        weighted_score=-4.0,
        summary_ru="новости↓",
        how_to_use_ru="short",
        top_catalyst="Hormuz deal",
    )
    scalp = OilScalpCall(
        action="open_short",
        hold_min=20,
        hold_max=60,
        entry_lo=87.4,
        entry_hi=87.7,
        stop=88.6,
        target=85.5,
        score=8,
        headline_ru="SHORT",
        factors_ru=("test",),
    )
    bounce = OilBouncePlan(
        side="short",
        bounce_level=87.6,
        entry_lo=87.4,
        entry_hi=87.7,
        stop=88.6,
        targets=(85.5, 85.0),
        catalyst="deal",
        reason_ru="отскок",
        strong=True,
        dist_pct=0.1,
    )
    setup = build_oil_confluence_setup(
        snap,
        ta,
        forecast=forecast,
        news_bias=bias,
        scalp_call=scalp,
        bounce_plan=bounce,
        ta_verdict_raw="SHORT",
        ta_confidence_raw=7,
        min_quality=7,
        near_pct=0.35,
    )
    assert setup is not None
    assert setup.side == "SHORT"
    assert setup_passes_gate(setup, min_quality=7)
    text = format_oil_confluence_setup(setup)
    assert "SHORT" in text
    assert "SL" in text or "стоп" in text.lower() or "вход" in text


def test_conflict_news_ta_does_not_pass_high_gate():
    snap = _snap(price=86.5, support=85.5, resistance=88.0, verdict="LONG", confidence=6)
    # mid-range, not near levels
    ta = TAAnalysisResult(verdict="LONG", verdict_confidence=6)
    forecast = OilForecast(
        bias="LONG",
        scenario="range",
        confidence=5,
        horizon_ru="intraday",
        headline_ru="LONG",
        base_case_ru="up",
        alt_case_ru="down",
        invalidation_ru="x",
        watch_list_ru=(),
        entry_hint_ru="x",
    )
    bias = OilNewsBias(
        bias="bearish",
        weighted_score=-5.0,
        summary_ru="news down",
        how_to_use_ru="short",
        top_catalyst="deal",
    )
    setup = build_oil_confluence_setup(
        snap,
        ta,
        forecast=forecast,
        news_bias=bias,
        ta_verdict_raw="LONG",
        ta_confidence_raw=6,
        min_quality=7,
        near_pct=0.35,
    )
    # Конфликт / нет касания → не шлём как готовый setup
    assert not setup_passes_gate(setup, min_quality=7)


def test_wait_format_when_conflict_close():
    snap = _snap(price=86.5, support=85.0, resistance=88.0)
    ta = TAAnalysisResult(verdict="WAIT", verdict_confidence=4)
    bias = OilNewsBias(
        bias="bullish",
        weighted_score=2.0,
        summary_ru="up",
        how_to_use_ru="long",
    )
    # Equal-ish votes without clear edge + another bearish push via scalp wait
    setup = build_oil_confluence_setup(
        snap,
        ta,
        news_bias=bias,
        forecast=OilForecast(
            bias="SHORT",
            scenario="deal_tape",
            confidence=6,
            horizon_ru="x",
            headline_ru="SHORT",
            base_case_ru="x",
            alt_case_ru="x",
            invalidation_ru="x",
            watch_list_ru=(),
            entry_hint_ru="x",
        ),
        min_quality=7,
        near_pct=0.35,
    )
    if setup is not None and setup.side == "WAIT":
        text = format_oil_confluence_setup(setup)
        assert "ждать" in text.lower() or "WAIT" in text
    else:
        # Либо None, либо WAIT-обёртка — главное не PASS gate
        assert not setup_passes_gate(setup, min_quality=7)


def test_long_near_support_with_aligned_factors():
    snap = _snap(
        price=85.6,
        support=85.5,
        resistance=88.0,
        breakdown=84.8,
        breakout=88.5,
        verdict="LONG",
        confidence=7,
    )
    ta = TAAnalysisResult(verdict="LONG", verdict_confidence=7)
    forecast = OilForecast(
        bias="LONG",
        scenario="disruption",
        confidence=7,
        horizon_ru="свинг",
        headline_ru="LONG",
        base_case_ru="up",
        alt_case_ru="down",
        invalidation_ru="bd",
        watch_list_ru=("Hormuz",),
        entry_hint_ru="long S",
    )
    bias = OilNewsBias(
        bias="bullish",
        weighted_score=4.0,
        summary_ru="news up",
        how_to_use_ru="long",
        top_catalyst="Hormuz block",
    )
    scalp = OilScalpCall(
        action="open_long",
        hold_min=15,
        hold_max=50,
        entry_lo=85.4,
        entry_hi=85.7,
        stop=84.7,
        target=88.0,
        score=8,
        headline_ru="LONG",
        factors_ru=("s",),
    )
    setup = build_oil_confluence_setup(
        snap,
        ta,
        forecast=forecast,
        news_bias=bias,
        scalp_call=scalp,
        ta_verdict_raw="LONG",
        ta_confidence_raw=7,
        min_quality=7,
        near_pct=0.35,
    )
    assert setup is not None
    assert setup.side == "LONG"
    assert setup_passes_gate(setup, min_quality=7)
    assert setup.entry_lo is not None and setup.stop is not None


def test_read_oil_chart_structure_notes():
    bars = []
    # rising then pullback-ish series
    px = 80.0
    for i in range(48):
        px = 80.0 + i * 0.05
        bars.append(
            SimpleNamespace(open=px - 0.02, high=px + 0.04, low=px - 0.05, close=px)
        )
    notes = read_oil_chart_structure(bars, price=px, support=81.0, resistance=84.0)
    assert notes
    joined = " ".join(notes)
    assert "Структура" in joined or "Позиция" in joined


def test_pro_format_card_title():
    setup = build_oil_confluence_setup(
        _snap(price=85.55, support=85.5, resistance=88.0, verdict="LONG", confidence=8),
        TAAnalysisResult(verdict="LONG", verdict_confidence=8),
        forecast=OilForecast(
            bias="LONG",
            scenario="disruption",
            confidence=8,
            horizon_ru="свинг",
            headline_ru="LONG",
            base_case_ru="up",
            alt_case_ru="down",
            invalidation_ru="bd",
            watch_list_ru=("Hormuz",),
            entry_hint_ru="long S",
        ),
        news_bias=OilNewsBias(
            bias="bullish",
            weighted_score=5.0,
            summary_ru="news up",
            how_to_use_ru="long",
            top_catalyst="Hormuz",
        ),
        ta_verdict_raw="LONG",
        ta_confidence_raw=8,
        min_quality=7,
        near_pct=0.35,
    )
    assert setup is not None
    text = format_oil_confluence_setup(setup)
    assert "ПРО" in text
    assert "LONG" in text or "WAIT" in text or "SHORT" in text


def test_stale_news_no_entry_points():
    import time

    stale = [
        SimpleNamespace(
            title="Iran Hormuz",
            published_ts=time.time() - 3 * 3600,
            impact="bullish",
            source="Reuters",
        )
    ]
    setup = build_oil_confluence_setup(
        _snap(price=85.55, support=85.5, resistance=88.0, verdict="WAIT", confidence=3),
        TAAnalysisResult(verdict="WAIT", verdict_confidence=3),
        news_bias=OilNewsBias(
            bias="bullish",
            weighted_score=5.0,
            summary_ru="up",
            how_to_use_ru="long",
            top_catalyst="Hormuz",
        ),
        news_items=stale,
        ta_verdict_raw="WAIT",
        ta_confidence_raw=3,
        min_quality=7,
        near_pct=0.35,
        news_entry_max_age_hours=1.0,
    )
    # Без TA/прогноза и без очков новостей — не должно пройти как A+ LONG
    assert not setup_passes_gate(setup, min_quality=7)
    if setup is not None:
        joined = " ".join(setup.factors_ru)
        assert (
            "без очков входа" in joined
            or "устарели" in joined
            or "старая" in joined
            or "ФОН" in joined
            or "фон" in joined
        )
