"""Tests for oil why (plain-language drivers)."""
from __future__ import annotations

import time

from bot.bybit_klines import KlineBar
from bot.oil_flow import OilFlowProxy
from bot.oil_monitor import OilNewsBias, OilNewsItem
from bot.oil_why import _pick_news, build_oil_why_report, format_oil_why_report


def _bars_up(n: int = 40) -> list[KlineBar]:
    out = []
    for i in range(n):
        px = 85.0 + i * 0.05
        out.append(
            KlineBar(
                open_time=float(i),
                open=px - 0.02,
                high=px + 0.05,
                low=px - 0.05,
                close=px,
                volume=1000.0,
            )
        )
    return out


def test_why_plain_explains_hormuz_attack():
    bars = _bars_up()
    items = [
        OilNewsItem(
            title="Iran attack closes Strait of Hormuz oil tankers",
            url="https://x",
            source="Reuters",
            published_ts=time.time() - 900,
            impact="bullish",
            theme="iran_geo",
        ),
    ]
    bias = OilNewsBias(
        bias="bullish",
        weighted_score=4.0,
        summary_ru="up",
        how_to_use_ru="long",
        top_catalyst="Hormuz attack",
    )
    flow = OilFlowProxy(
        bias="buy",
        session_volume=10000,
        recent_volume=3000,
        prev_volume=2000,
        volume_ratio=1.5,
        delta_recent=500,
        delta_session=800,
        buy_share_pct=62.0,
        bars_used=24,
        lookback=12,
        note_ru="ok",
    )
    rep = build_oil_why_report(
        bars,
        news_items=items,
        news_bias=bias,
        flow=flow,
        interval_minutes=15,
        ai_now_ru="Главный драйвер: страх блока Ормуза.",
    )
    assert rep is not None
    assert rep.direction == "up"
    assert "Ормуз" in rep.plain_ru or "ормуз" in rep.plain_ru.lower()
    text = format_oil_why_report(rep)
    assert "Почему нефть" in text
    assert "Сейчас" in text
    assert "Драйверы" in text
    assert "Осторожно" not in text
    assert "war-premium" not in text.lower()
    assert "News-bias" not in text
    # Заголовок на EN не должен торчать сырым
    assert "closes Strait" not in text


def test_why_flags_open_hormuz_deal_vs_rising_price():
    bars = _bars_up()
    items = [
        OilNewsItem(
            title="US considering Iran’s offer to open Strait of Hormuz in exchange for lifting blockade",
            url="https://x",
            source="News",
            published_ts=time.time() - 600,
            impact="bearish",
            theme="iran_geo",
        ),
    ]
    bias = OilNewsBias(
        bias="bearish",
        weighted_score=-3.0,
        summary_ru="down",
        how_to_use_ru="short",
        top_catalyst="open Hormuz",
    )
    rep = build_oil_why_report(
        bars, news_items=items, news_bias=bias, interval_minutes=15
    )
    assert rep is not None
    assert rep.direction == "up"
    text = format_oil_why_report(rep)
    assert "открыт" in text.lower() or "сделк" in text.lower() or "пролив" in text.lower() or "ормуз" in text.lower()
    assert "Осторожно" not in text
    assert "отскок" in rep.do_now_ru.lower() or "мешает" in rep.do_now_ru.lower() or "давит" in rep.do_now_ru.lower()


def test_pick_news_prefers_fresh_matching_direction():
    now = time.time()
    old = OilNewsItem(
        title="Iran attack closes Strait of Hormuz",
        url="https://old",
        source="Reuters",
        published_ts=now - 20 * 3600,
        impact="bullish",
        theme="iran_geo",
    )
    fresh = OilNewsItem(
        title="Trump cancels Iran strike, deal to open Hormuz",
        url="https://fresh",
        source="AP",
        published_ts=now - 1800,
        impact="bearish",
        theme="iran_geo",
    )
    # При падении цены — свежий cancel выше старого attack
    picked = _pick_news([old, fresh], limit=2, prefer_direction="down", max_age_hours=24)
    assert picked
    assert "cancel" in picked[0].title.lower() or "cancels" in picked[0].title.lower()


def test_strong_move_ignores_stale_background():
    """Сильный ход сейчас — сюжет 3ч назад не главный драйвер."""
    now = time.time()
    # Сильное падение за час
    bars = []
    for i in range(40):
        px = 80.0 - (0.0 if i < 36 else (i - 35) * 0.15)
        bars.append(
            KlineBar(
                open_time=float(i),
                open=px + 0.02,
                high=px + 0.05,
                low=px - 0.05,
                close=px,
                volume=1000.0,
            )
        )
    stale = OilNewsItem(
        title="US Iran talks reopen Strait of Hormuz make progress",
        url="https://old",
        source="Reuters",
        published_ts=now - 3.5 * 3600,
        impact="bearish",
        theme="iran_geo",
    )
    fresh = OilNewsItem(
        title="Bessent: Iran deal to open Hormuz signed",
        url="https://new",
        source="X @DeItaone",
        published_ts=now - 900,
        impact="bearish",
        theme="iran_geo",
    )
    picked = _pick_news(
        [stale, fresh],
        limit=2,
        prefer_direction="down",
        max_age_hours=6.0,
        prefer_fresh_hours=2.0,
        strong_move=True,
        now_ts=now,
    )
    assert len(picked) == 1
    assert "Bessent" in picked[0].title or "deal" in picked[0].title.lower()

    rep = build_oil_why_report(
        bars, news_items=[stale], news_bias=None, interval_minutes=15
    )
    assert rep is not None
    # Только старое при сильном ходе → gap
    assert rep.gap_note_ru or "свеж" in rep.plain_ru.lower() or "ищем" in rep.plain_ru.lower()


def test_pick_news_dedupes_mirrors_and_skips_weak():
    now = time.time()
    items = [
        OilNewsItem(
            title="US Iran talks reopen Strait of Hormuz make progress",
            url="https://r",
            source="Reuters",
            published_ts=now - 600,
            impact="bearish",
            theme="iran_geo",
        ),
        OilNewsItem(
            title="US Iran talks reopen Strait of Hormuz make progress",
            url="https://i",
            source="India Today",
            published_ts=now - 700,
            impact="bearish",
            theme="iran_geo",
        ),
        OilNewsItem(
            title="Bessent says Hormuz agreement within days",
            url="https://k",
            source="Kurdistan24",
            published_ts=now - 400,
            impact="bearish",
            theme="iran_geo",
        ),
    ]
    picked = _pick_news(items, limit=3, prefer_direction="down", max_age_hours=6)
    assert len(picked) == 1
    assert picked[0].source == "Reuters"
