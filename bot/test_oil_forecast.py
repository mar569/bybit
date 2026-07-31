"""Tests for oil forecast engine."""
from __future__ import annotations

from types import SimpleNamespace

from bot.oil_forecast import (
    OilForecast,
    build_oil_forecast,
    detect_oil_scenario,
    format_oil_forecast_block,
)
from bot.oil_monitor import OilNewsBias, OilNewsItem, format_oil_market_digest
from bot.ta_analysis import TAAnalysisResult


def _snap(**kwargs):
    base = dict(
        label="Brent · UKOUSD",
        symbol="UKOUSD",
        price=86.5,
        high_7d=92.0,
        low_7d=84.0,
        verdict="WAIT",
        confidence=5,
        support=85.5,
        resistance=87.8,
        breakdown=85.0,
        breakout=88.2,
        phase="база",
        elliott="",
        reason="test",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_detect_deal_tape_scenario():
    items = [
        OilNewsItem(
            title="Oil falls as Hormuz deal talks progress MOU",
            url="https://x",
            source="Reuters",
            published_ts=1.0,
            impact="bearish",
            theme="iran_geo",
        ),
    ]
    assert detect_oil_scenario(items) == "deal_tape"


def test_detect_disruption_scenario():
    items = [
        OilNewsItem(
            title="Iran attack closes Strait of Hormuz oil tanker route",
            url="https://x",
            source="Reuters",
            published_ts=1.0,
            impact="bullish",
            theme="iran_geo",
        ),
    ]
    assert detect_oil_scenario(items) == "disruption"


def test_build_forecast_short_on_deal_tape():
    snap = _snap(verdict="SHORT", confidence=6)
    ta = TAAnalysisResult(verdict="SHORT", verdict_confidence=6)
    bias = OilNewsBias(
        bias="bearish",
        weighted_score=-4.0,
        summary_ru="Давление вниз",
        how_to_use_ru="Искать short",
        top_catalyst="Hormuz deal",
    )
    items = [
        OilNewsItem(
            title="Brent drops on Hormuz reopen deal",
            url="https://x",
            source="Reuters",
            published_ts=1.0,
            impact="bearish",
            theme="iran_geo",
        ),
    ]
    fc = build_oil_forecast(
        snap,
        ta,
        news_bias=bias,
        news_items=items,
        ta_verdict_raw="SHORT",
        ta_confidence_raw=6,
    )
    assert fc.bias == "SHORT"
    assert fc.scenario == "deal_tape"
    assert fc.confidence >= 5
    text = format_oil_forecast_block(fc)
    assert "SHORT" in text
    assert "Прогноз UKOUSD" in text
    assert "Отмена" in text


def test_build_forecast_wait_on_mixed():
    snap = _snap()
    ta = TAAnalysisResult(verdict="WAIT", verdict_confidence=4)
    items = [
        OilNewsItem(
            title="Hormuz deal talks continue",
            url="https://x",
            source="A",
            published_ts=1.0,
            impact="bearish",
            theme="iran_geo",
        ),
        OilNewsItem(
            title="Iran strike threat blocks Hormuz tankers",
            url="https://x",
            source="B",
            published_ts=1.0,
            impact="bullish",
            theme="iran_geo",
        ),
    ]
    fc = build_oil_forecast(snap, ta, news_items=items)
    assert fc.scenario in {"mixed_geo", "deal_tape", "disruption"}
    text = format_oil_forecast_block(fc)
    assert "Прогноз" in text


def test_build_forecast_long_with_missing_breakout():
    """Regression: fmt_price(None) when R есть, BO=None."""
    snap = _snap(verdict="LONG", confidence=6, resistance=88.0, breakout=None)
    ta = TAAnalysisResult(verdict="LONG", verdict_confidence=6)
    bias = OilNewsBias(
        bias="bullish",
        weighted_score=3.0,
        summary_ru="up",
        how_to_use_ru="long",
    )
    items = [
        OilNewsItem(
            title="Iran attack closes Strait of Hormuz oil tanker",
            url="https://x",
            source="Reuters",
            published_ts=1.0,
            impact="bullish",
            theme="iran_geo",
        ),
    ]
    fc = build_oil_forecast(
        snap,
        ta,
        news_bias=bias,
        news_items=items,
        ta_verdict_raw="LONG",
        ta_confidence_raw=6,
    )
    assert fc.bias == "LONG"
    assert "цели" in fc.entry_hint_ru


def test_digest_includes_forecast_block():
    snap = _snap(verdict="SHORT", confidence=7)
    fc = OilForecast(
        bias="SHORT",
        scenario="deal_tape",
        confidence=7,
        horizon_ru="свинг 1–3д",
        headline_ru="Bias SHORT · deal",
        base_case_ru="Давление вниз",
        alt_case_ru="Альт вверх",
        invalidation_ru="Отмена выше 88",
        watch_list_ru=("Ормуз",),
        entry_hint_ru="Short от R",
    )
    text = format_oil_market_digest([snap], forecast=fc)
    assert "Прогноз UKOUSD" in text
    assert "SHORT" in text
