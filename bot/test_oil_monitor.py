"""Tests for oil news monitor."""
from __future__ import annotations

from bot.oil_monitor import (
    classify_news_impact,
    _is_relevant,
    apply_oil_bounce_to_ta,
    bounce_plan_near_level,
    build_oil_bounce_plan,
    build_oil_scalp_call,
    detect_oil_market_mood,
    detect_oil_micro_signal,
    format_oil_bounce_alert,
    format_oil_market_digest,
    format_oil_micro_signal,
    format_oil_news_message,
    format_oil_scalp_block,
    format_single_oil_news,
    is_critical_oil_news,
    news_critical_score,
    sanitize_oil_session_bars,
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


def test_pro_analyst_blas_kemp_priority():
    from bot.oil_monitor import (
        detect_oil_news_theme,
        is_critical_oil_news,
        match_pro_oil_analyst,
        news_critical_score,
    )

    assert match_pro_oil_analyst("Javier Blas on Hormuz oil flood")[0] == "Javier Blas"
    assert match_pro_oil_analyst("Oil inventories", "John Kemp")[0] == "John Kemp"
    assert detect_oil_news_theme(
        "Brace for an oil flood when Hormuz reopens — Javier Blas"
    ) in {"iran_geo", "analyst"}
    assert detect_oil_news_theme(
        "John Kemp: crude inventories and fund positioning",
        source="Reuters",
    ) == "analyst"
    item = OilNewsItem(
        title="Javier Blas: energy markets after Hormuz talks",
        url="https://bloomberg.com/x",
        source="Bloomberg",
        published_ts=1_700_000_000.0,
        impact="bearish",
        theme="analyst",
    )
    assert news_critical_score(item.title, source=item.source) >= 5
    assert is_critical_oil_news(item, min_score=4)
    text = format_single_oil_news(item)
    assert "Javier Blas" in text
    assert "⭐" in text


def test_detect_themes_priority():
    from bot.oil_monitor import detect_oil_news_theme
    assert detect_oil_news_theme("Iran oil Trump sanctions") == "iran_geo"
    assert detect_oil_news_theme("EIA crude oil inventory build") == "inventory"
    assert detect_oil_news_theme("OPEC oil production cut quota") == "opec"
    assert detect_oil_news_theme("China buys more crude oil tanker") == "flow_deal"
    assert detect_oil_news_theme(
        "Barclays sees upside risks to 2026 Brent price view"
    ) == "analyst"
    assert detect_oil_news_theme(
        "EIA STEO cuts Brent crude oil forecast to $82"
    ) == "analyst"
    assert detect_oil_news_theme("Hormuz deal oil prices fall forecast") == "iran_geo"


def test_format_analyst_news_header():
    item = OilNewsItem(
        title="Barclays raises Brent crude oil forecast outlook",
        url="https://example.com/barclays",
        source="Reuters",
        published_ts=1_700_000_000.0,
        impact="bullish",
        theme="analyst",
    )
    text = format_single_oil_news(item)
    assert "аналитика" in text
    assert "Прогноз" in text
    assert "UKOUSD" in text


def test_pro_feed_theme_oilprice_headline():
    from bot.oil_monitor import _pro_feed_theme
    # Price + demand → flow_deal; чистый отраслевой oil-заголовок без flow → analyst
    assert _pro_feed_theme("Oil Prices Fall as Demand Concerns Mount") == "flow_deal"
    assert _pro_feed_theme("Brent Crude Slumps Below $87") == "analyst"
    assert _pro_feed_theme("Tech stocks rally on AI news") == ""


def test_classify_news_impact():
    assert classify_news_impact("Oil prices surge on Hormuz block") == "bullish"
    assert classify_news_impact("Brent falls after US Iran deal") == "bearish"
    assert classify_news_impact("EIA STEO cuts Brent forecast after Hormuz MOU") == "bearish"


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
    assert "UKOUSD" in text or "Brent" in text or "Yahoo" in text or "BZ=F" in text


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
        symbol="UKOUSD",
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
        symbol="UKOUSD",
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
            symbol="UKOUSD",
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
        symbol="UKOUSD",
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


def test_sanitize_oil_session_bars_drops_flats_and_gaps():
    dead = [KlineBar(float(i * 300), 90.0, 90.0, 90.0, 90.0, 0.0) for i in range(8)]
    live = [
        KlineBar(10_000.0 + i * 900, 90 + i * 0.05, 90.1 + i * 0.05, 89.9 + i * 0.05, 90.02 + i * 0.05, 1.0)
        for i in range(30)
    ]
    out = sanitize_oil_session_bars(dead + live, interval_minutes=5)
    assert len(out) == 30
    gaps = [out[i].open_time - out[i - 1].open_time for i in range(1, len(out))]
    assert all(abs(g - 300.0) < 1e-6 for g in gaps)


def test_oil_scalp_wait_in_mid_range():
    snap = OilMarketSnapshot(
        label="Brent · UKOUSD",
        symbol="UKOUSD",
        price=90.5,
        high_7d=93.0,
        low_7d=88.0,
        verdict="WAIT",
        confidence=4,
        support=89.8,
        resistance=91.2,
        breakdown=89.5,
        breakout=91.4,
        phase="",
        elliott="",
        reason="",
    )
    ta = TAAnalysisResult(verdict="WAIT", verdict_confidence=4, action_priority="long")
    bias = OilNewsBias(bias="neutral", weighted_score=0.0, summary_ru="", how_to_use_ru="")
    call = build_oil_scalp_call(
        snap, ta, news_bias=bias, market_mood="база / флэт", interval_minutes=5,
    )
    assert call.action == "wait"
    assert "НЕ ОТКРЫВАТЬ" in call.headline_ru
    text = format_oil_scalp_block(call)
    assert "10–100 мин" in text or "мин" in text


def test_oil_scalp_open_long_near_support_with_news():
    snap = OilMarketSnapshot(
        label="Brent · UKOUSD",
        symbol="UKOUSD",
        price=89.85,
        high_7d=93.0,
        low_7d=88.0,
        verdict="LONG",
        confidence=6,
        support=89.80,
        resistance=91.20,
        breakdown=89.40,
        breakout=91.40,
        phase="",
        elliott="",
        reason="",
    )
    ta = TAAnalysisResult(verdict="LONG", verdict_confidence=6, action_priority="long")
    bias = OilNewsBias(
        bias="bullish",
        weighted_score=4.0,
        bullish=2,
        summary_ru="новости↑",
        how_to_use_ru="",
    )
    bounce = OilBouncePlan(
        side="long",
        bounce_level=89.80,
        entry_lo=89.70,
        entry_hi=89.90,
        stop=89.30,
        targets=(90.50, 91.20),
        catalyst="Hormuz",
        reason_ru="отскок",
        strong=True,
        dist_pct=0.05,
    )
    call = build_oil_scalp_call(
        snap,
        ta,
        news_bias=bias,
        bounce_plan=bounce,
        market_mood="intraday бычий bias",
        interval_minutes=5,
    )
    assert call.action == "open_long"
    assert call.entry_lo is not None
    assert call.stop is not None
    assert "LONG" in call.headline_ru


def _micro_dump_bars(n: int = 30, *, start: float = 90.0) -> list[KlineBar]:
    bars: list[KlineBar] = []
    px = start
    t0 = 1_700_000_000.0
    for i in range(n - 5):
        bars.append(KlineBar(t0 + i * 300, px, px + 0.05, px - 0.05, px, 1.0))
    for j in range(5):
        o = px
        px = px * (1.0 - 0.0007)
        bars.append(
            KlineBar(t0 + (n - 5 + j) * 300, o, o + 0.02, px - 0.01, px, 2.0)
        )
    return bars


def test_oil_micro_signal_short_on_dump():
    bars = _micro_dump_bars()
    sig = detect_oil_micro_signal(bars, tp_pct=0.25, sl_pct=0.18, min_impulse_pct=0.12)
    assert sig is not None
    assert sig.side == "short"
    assert sig.target < sig.entry
    assert sig.stop > sig.entry
    assert 0.2 <= sig.tp_pct <= 0.3
    text = format_oil_micro_signal(sig)
    assert "SHORT" in text
    assert "TP" in text


def test_oil_micro_signal_skips_against_strong_news():
    bars = _micro_dump_bars()
    bias = OilNewsBias(
        bias="bullish",
        weighted_score=4.5,
        summary_ru="",
        how_to_use_ru="",
    )
    assert detect_oil_micro_signal(bars, news_bias=bias) is None


def test_resolve_oil_news_rejects_old_url_date():
    from bot.oil_monitor import resolve_oil_news_published_ts, oil_news_is_fresh
    import time as _t

    # RSS «сегодня», URL апрель 2026 → эффективная дата = апрель
    rss = _t.strftime("%a, %d %b %Y %H:%M:%S GMT", _t.gmtime())
    url = "https://energynow.com/2026/04/06/opec-agrees-to-boost-oil-output/"
    ts = resolve_oil_news_published_ts(rss_pub=rss, url=url)
    assert ts is not None
    assert not oil_news_is_fresh(ts, max_age_hours=18)


def test_resolve_oil_news_fresh_same_day_url():
    from bot.oil_monitor import resolve_oil_news_published_ts, oil_news_is_fresh
    from datetime import datetime, timezone

    today = datetime.now(tz=timezone.utc)
    url = f"https://example.com/{today.year:04d}/{today.month:02d}/{today.day:02d}/oil-update/"
    ts = resolve_oil_news_published_ts(rss_pub="", url=url)
    assert ts is not None
    assert oil_news_is_fresh(ts, max_age_hours=18)


def test_parse_rss_pub_empty_is_none():
    from bot.oil_monitor import _parse_rss_pub

    assert _parse_rss_pub("") is None
    assert _parse_rss_pub("   ") is None
