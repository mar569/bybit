"""Tests for oil news monitor."""
from __future__ import annotations

from bot.oil_monitor import (
    classify_news_impact,
    _is_relevant,
    apply_oil_bounce_to_ta,
    bounce_plan_near_level,
    build_oil_bounce_plan,
    detect_oil_market_mood,
    format_oil_bounce_alert,
    format_oil_market_digest,
    format_oil_news_message,
    format_single_oil_news,
    is_critical_oil_news,
    news_critical_score,
    summarize_oil_news_bias,
    OilBouncePlan,
    OilNewsBias,
    OilNewsItem,
    OilMarketSnapshot,
)
from bot.bybit_klines import KlineBar
from bot.ta_analysis import TAAnalysisResult


def test_is_relevant_iran_hormuz():
    assert _is_relevant("Iran threatens to close Strait of Hormuz shipping")
    assert _is_relevant("Brent crude rises on US sanctions Trump Iran")


def test_is_relevant_rejects_random():
    assert not _is_relevant("Local football match results")
    assert not _is_relevant("Brent crude weekly technical outlook chart")


def test_detect_themes_priority():
    from bot.oil_monitor import detect_oil_news_theme
    assert detect_oil_news_theme("Iran oil Trump sanctions") == "iran_geo"
    assert detect_oil_news_theme("EIA crude oil inventory build") == "inventory"
    assert detect_oil_news_theme("OPEC oil production cut quota") == "opec"
    assert detect_oil_news_theme("China buys more crude oil tanker") == "flow_deal"


def test_classify_news_impact():
    assert classify_news_impact("Oil prices surge on Hormuz block") == "bullish"
    assert classify_news_impact("Brent falls after US Iran deal") == "bearish"


def test_format_single_oil_news_has_link():
    items = [
        OilNewsItem(
            title="Oil prices fall after Hormuz deal",
            url="https://example.com/a",
            source="Reuters",
            published_ts=1_700_000_000.0,
        ),
    ]
    text = format_single_oil_news(items[0])
    assert "Hormuz" in text
    assert "example.com" in text
    assert "Открыть источник" in text


def test_format_oil_news_message_batch():
    items = [
        OilNewsItem(
            title="Oil prices fall after Hormuz deal",
            url="https://example.com/a",
            source="Reuters",
            published_ts=1_700_000_000.0,
        ),
    ]
    text = format_oil_news_message(items)
    assert "Hormuz" in text
    assert "example.com" in text


def test_format_oil_market_digest():
    snap = OilMarketSnapshot(
        label="Brent",
        symbol="BZ=F",
        price=90.5,
        high_7d=102.0,
        low_7d=82.5,
        verdict="WAIT",
        confidence=5,
        support=89.0,
        resistance=91.0,
        breakdown=86.5,
        breakout=93.0,
        phase="test",
        elliott="impulse",
        reason="range top",
    )
    text = format_oil_market_digest([snap])
    assert "Brent" in text
    assert "BZUSDT" in text or "Brent" in text or "TradFi" in text


def test_news_critical_score_hormuz():
    item = OilNewsItem(
        title="Iran threatens Strait of Hormuz blockade",
        url="",
        source="Reuters",
        published_ts=1_700_000_000.0,
    )
    assert news_critical_score(item.title) >= 3
    assert is_critical_oil_news(item)


def test_news_critical_rejects_weak():
    item = OilNewsItem(
        title="Oil market weekly recap Brent WTI",
        url="",
        source="Blog",
        published_ts=1_700_000_000.0,
    )
    assert not is_critical_oil_news(item, min_score=4)


def test_format_russian_news_lang_mark():
    item = OilNewsItem(
        title="нефть Иран Ормуз",
        url="https://example.com/ru",
        source="РИА",
        published_ts=1_700_000_000.0,
        lang="ru",
    )
    text = format_single_oil_news(item)
    assert "🇷🇺" in text


def test_detect_oil_market_mood_range():
    bars = [
        KlineBar(open_time=float(i), open=90.0, high=90.5, low=89.5, close=90.0, volume=1.0)
        for i in range(30)
    ]
    ta = TAAnalysisResult(
        nearest_support=89.5,
        nearest_resistance=90.5,
        verdict="WAIT",
    )
    mood = detect_oil_market_mood(bars, ta, 15)
    assert "база" in mood or "нейтраль" in mood or "range" in mood.lower()


def test_summarize_oil_news_bias_bullish_confirms_long():
    items = [
        OilNewsItem(
            title="Iran threatens Strait of Hormuz blockade",
            url="",
            source="Reuters",
            published_ts=1_700_000_000.0,
            impact="bullish",
        ),
        OilNewsItem(
            title="Oil prices surge on US sanctions",
            url="",
            source="Bloomberg",
            published_ts=1_700_000_100.0,
            impact="bullish",
        ),
    ]
    bias = summarize_oil_news_bias(items, ta_verdict="LONG")
    assert bias.bias == "bullish"
    assert bias.bullish == 2
    assert "вверх" in bias.summary_ru
    assert "приоритет LONG" in bias.how_to_use_ru


def test_summarize_oil_news_bias_conflict():
    items = [
        OilNewsItem(
            title="Brent falls after US Iran deal reopen Hormuz",
            url="",
            source="Reuters",
            published_ts=1_700_000_000.0,
            impact="bearish",
        ),
    ]
    bias = summarize_oil_news_bias(items, ta_verdict="LONG")
    assert bias.bias == "bearish"
    assert "Конфликт" in bias.how_to_use_ru


def test_digest_includes_news_bias():
    snap = OilMarketSnapshot(
        label="Brent",
        symbol="BZUSDT",
        price=90.5,
        high_7d=102.0,
        low_7d=82.5,
        verdict="WAIT",
        confidence=5,
        support=89.0,
        resistance=91.0,
        breakdown=86.5,
        breakout=93.0,
        phase="test",
        elliott="",
        reason="",
    )
    items = [
        OilNewsItem(
            title="OPEC oil production cut",
            url="",
            source="Reuters",
            published_ts=1_700_000_000.0,
            impact="bullish",
        ),
    ]
    bias = summarize_oil_news_bias(items, ta_verdict="WAIT")
    text = format_oil_market_digest([snap], news_bias=bias)
    assert "Новостной фон" in text
    assert "вверх" in text or "🟢" in text


def test_build_oil_bounce_plan_long_and_apply_ta():
    snap = OilMarketSnapshot(
        label="Brent",
        symbol="BZUSDT",
        price=90.5,
        high_7d=95.0,
        low_7d=85.0,
        verdict="WAIT",
        confidence=5,
        support=89.0,
        resistance=91.5,
        breakdown=88.0,
        breakout=92.0,
        phase="test",
        elliott="",
        reason="",
    )
    bias = OilNewsBias(
        bullish=2,
        bearish=0,
        neutral=0,
        weighted_score=4.5,
        bias="bullish",
        summary_ru="up",
        how_to_use_ru="long",
    )
    items = [
        OilNewsItem(
            title="Iran threatens Strait of Hormuz blockade",
            url="",
            source="Reuters",
            published_ts=1_700_000_000.0,
            impact="bullish",
        ),
    ]
    plan = build_oil_bounce_plan(snap, bias, news_items=items, min_score=3.0)
    assert plan is not None
    assert plan.side == "long"
    assert plan.bounce_level == 89.0
    assert plan.stop < plan.entry_lo
    assert plan.targets[0] > plan.entry_hi
    assert "Hormuz" in plan.catalyst or "Hormuz" in plan.reason_ru

    ta = TAAnalysisResult(verdict="WAIT", verdict_confidence=5)
    apply_oil_bounce_to_ta(ta, plan)
    assert ta.verdict == "LONG"
    assert ta.entry_zone is not None
    assert ta.elliott_stop_price == plan.stop
    assert ta.target_prices[:3] == list(plan.targets[:3])
    assert ta.bullish_scenario is not None

    alert = format_oil_bounce_alert(plan)
    assert "отскок LONG" in alert
    assert "89" in alert


def test_bounce_near_level_gate():
    plan = build_oil_bounce_plan(
        OilMarketSnapshot(
            label="Brent",
            symbol="BZUSDT",
            price=89.1,
            high_7d=95.0,
            low_7d=85.0,
            verdict="WAIT",
            confidence=5,
            support=89.0,
            resistance=91.5,
            breakdown=88.0,
            breakout=92.0,
            phase="",
            elliott="",
            reason="",
        ),
        OilNewsBias(
            bullish=2,
            bearish=0,
            weighted_score=4.0,
            bias="bullish",
            summary_ru="",
            how_to_use_ru="",
        ),
        min_score=3.0,
    )
    assert plan is not None
    assert bounce_plan_near_level(plan, near_pct=0.4)
    far = OilBouncePlan(
        side=plan.side,
        bounce_level=plan.bounce_level,
        entry_lo=plan.entry_lo,
        entry_hi=plan.entry_hi,
        stop=plan.stop,
        targets=plan.targets,
        catalyst=plan.catalyst,
        reason_ru=plan.reason_ru,
        strong=True,
        dist_pct=1.5,
    )
    assert not bounce_plan_near_level(far, near_pct=0.4)


def test_weak_news_no_bounce_plan():
    snap = OilMarketSnapshot(
        label="Brent",
        symbol="BZUSDT",
        price=90.0,
        high_7d=95.0,
        low_7d=85.0,
        verdict="WAIT",
        confidence=5,
        support=89.0,
        resistance=91.0,
        breakdown=88.0,
        breakout=92.0,
        phase="",
        elliott="",
        reason="",
    )
    bias = OilNewsBias(
        bullish=1,
        bearish=0,
        weighted_score=1.0,
        bias="bullish",
        summary_ru="",
        how_to_use_ru="",
    )
    assert build_oil_bounce_plan(snap, bias, min_score=3.0) is None
