"""Tests for oil wire feeds + price crash alerts."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.oil_wire import (
    detect_oil_price_crash,
    format_oil_crash_alert,
    wire_headline_oil_relevant,
)


def _bars_waterfall(*, start: float = 87.0, end: float = 82.1, n: int = 20) -> list[KlineBar]:
    """Симулирует обвал ~5.6% за ~1ч на 5m свечах."""
    out: list[KlineBar] = []
    for i in range(n):
        t = 1_700_000_000 + i * 300
        if i < 6:
            px = start + (i % 3) * 0.05
        else:
            # линейный слив
            frac = (i - 6) / max(1, n - 7)
            px = start + (end - start) * frac
        out.append(
            KlineBar(
                open_time=t,
                open=px + 0.05,
                high=px + 0.15,
                low=px - 0.25,
                close=px,
                volume=1000.0,
            )
        )
    return out


def test_wire_relevance_oil_and_trump_iran():
    assert wire_headline_oil_relevant("Brent crude tumbles after Trump Iran comments")
    assert wire_headline_oil_relevant("Trump called off strike on Iran — Hormuz talks")
    assert wire_headline_oil_relevant("EIA crude oil inventory build surprises")
    assert wire_headline_oil_relevant(
        "US Treasury Secretary Bessent: We may have an Iran deal tomorrow to open Hormuz"
    )
    assert wire_headline_oil_relevant("Iran refuses to reopen Strait of Hormuz")
    assert not wire_headline_oil_relevant("Fed's Paulson: Economy is strong")
    assert not wire_headline_oil_relevant("EURUSD dips on soft PMI")
    assert not wire_headline_oil_relevant(
        "US June oil import price $86.06/bbl vs May $87.44/bbl"
    )


def test_market_moving_and_similarity_dedupe():
    from bot.oil_monitor import is_oil_market_moving_headline, titles_too_similar

    assert is_oil_market_moving_headline(
        "Bessent: Iran deal tomorrow to open Hormuz"
    )
    assert not is_oil_market_moving_headline("Oil prices mixed in Asia trade")
    assert titles_too_similar(
        "Trump called off Iran strike, Hormuz deal talks begin",
        "Trump calls off Iran strike as Hormuz deal talks start",
        threshold=0.5,
    )
    assert not titles_too_similar(
        "Trump called off Iran strike",
        "EIA crude inventories build more than expected",
        threshold=0.55,
    )


def test_primary_actors_trump_bessent_iran():
    from bot.oil_fastlane import detect_oil_primary_actors, format_fastlane_flash, FastLaneMeta
    from bot.oil_monitor import OilNewsItem, classify_news_impact

    assert detect_oil_primary_actors(
        "Trump Truth Social: Iran deal to reopen Hormuz"
    ) == ["Трамп", "Иран·Ормуз"]
    assert "Бессент" in detect_oil_primary_actors(
        "Bessent: Iran deal tomorrow to open Hormuz"
    )
    assert classify_news_impact(
        "Bessent: We may have an Iran deal tomorrow to open Hormuz"
    ) == "bearish"
    assert classify_news_impact(
        "Iran denies talks and will not reopen Hormuz"
    ) == "bullish"

    item = OilNewsItem(
        title="Bessent: Iran deal tomorrow to open Hormuz",
        url="https://www.forexlive.com/x",
        source="ForexLive",
        published_ts=1_700_000_000.0,
        impact="bearish",
        theme="trump_us",
    )
    meta = FastLaneMeta(
        outlet="ForexLive", tier=1, flash_score=14, is_flash=True, publisher="ForexLive"
    )
    text = format_fastlane_flash(item, meta=meta, compact=True)
    assert "Бессент" in text
    assert "Иран·Ормуз" in text


def test_compact_flash_and_ai_gate():
    from bot.oil_fastlane import (
        FastLaneMeta,
        format_fastlane_flash,
        should_ai_analyze_flash,
    )
    from bot.oil_monitor import OilNewsItem

    item = OilNewsItem(
        title="Bessent: Iran deal tomorrow to open Hormuz",
        url="https://www.forexlive.com/x",
        source="ForexLive",
        published_ts=1_700_000_000.0,
        impact="bearish",
        theme="trump_us",
    )
    meta = FastLaneMeta(
        outlet="ForexLive", tier=1, flash_score=14, is_flash=True, publisher="ForexLive"
    )
    text = format_fastlane_flash(item, meta=meta, compact=True)
    assert "Bessent" in text or "Бессент" in text
    assert "Не финансовый совет" not in text
    assert "Что ждать дальше" not in text
    assert should_ai_analyze_flash(item.title, meta, min_score=11)
    weak = FastLaneMeta(
        outlet="Investing.com", tier=1, flash_score=8, is_flash=True, publisher="x"
    )
    assert not should_ai_analyze_flash("Oil prices little changed", weak, min_score=11)


def test_detect_oil_crash_waterfall():
    bars = _bars_waterfall()
    alert = detect_oil_price_crash(
        bars, interval_minutes=5, pct_15m=1.5, pct_30m=3.0, pct_60m=4.0
    )
    assert alert is not None
    assert alert.direction == "down"
    assert alert.severity in {"crash", "mega", "warn"}
    assert alert.move_30m_pct < -1.0 or alert.range_60m_pct >= 3.0
    text = format_oil_crash_alert(alert, recent_headlines=["Trump delays Iran strike"])
    assert "UKOUSD" in text
    assert "ОБВАЛ" in text


def test_no_crash_on_flat():
    bars = [
        KlineBar(
            open_time=1_700_000_000 + i * 300,
            open=85.0,
            high=85.1,
            low=84.9,
            close=85.0 + (i % 2) * 0.02,
            volume=100.0,
        )
        for i in range(20)
    ]
    assert detect_oil_price_crash(bars, pct_15m=1.5, pct_30m=3.0, pct_60m=4.0) is None


def test_financialjuice_is_fastlane_outlet():
    from bot.oil_fastlane import detect_fastlane_outlet, is_fastlane_item
    from bot.oil_monitor import OilNewsItem

    meta = detect_fastlane_outlet(
        "Trump called off Iran strike, Hormuz deal talks",
        source="FinancialJuice",
        url="https://www.financialjuice.com/news/123",
    )
    assert meta is not None
    assert meta.outlet == "FinancialJuice"
    item = OilNewsItem(
        title="Trump called off Iran strike, Hormuz deal talks",
        url="https://www.financialjuice.com/news/123",
        source="FinancialJuice",
        published_ts=1_700_000_000.0,
        impact="bearish",
        theme="trump_us",
    )
    assert is_fastlane_item(item, min_flash_score=7)

def test_reject_cyber_water_and_weak_mirrors():
    from bot.oil_fastlane import fastlane_title_on_topic, is_syndicate_host
    from bot.oil_monitor import (
        _news_story_key,
        is_oil_market_moving_headline,
        is_weak_oil_news_source,
    )

    assert not is_oil_market_moving_headline(
        "Iran-Linked Cyberattacks on US Water Utilities Continue"
    )
    assert not fastlane_title_on_topic(
        "Iran-Linked Cyberattacks on US Water Utilities Continue"
    )
    assert is_oil_market_moving_headline(
        "Negotiators Close In on Deal With Iran to Open Hormuz"
    )
    assert is_weak_oil_news_source("LatestLY", "https://www.latestly.com/x")
    assert is_weak_oil_news_source("Telangana Today", "")
    assert is_syndicate_host("Telangana Today", "https://telanganatoday.com/x")
    k1 = _news_story_key("Iran Oman Agree on Proposed Shipping Route in Hormuz Strait")
    k2 = _news_story_key("Watch Iran Says Agreement Reached With Oman on Hormuz Shipping")
    assert "hormuz" in k1 and "iran" in k1
    assert ("deesc" in k1 and "deesc" in k2) or k1 == k2
