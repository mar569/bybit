"""Tests for oil why (honest move drivers)."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.oil_flow import OilFlowProxy
from bot.oil_monitor import OilNewsBias, OilNewsItem
from bot.oil_why import build_oil_why_report, format_oil_why_report


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


def test_why_up_with_bullish_geo_news():
    bars = _bars_up()
    items = [
        OilNewsItem(
            title="Iran attack closes Strait of Hormuz oil tankers",
            url="https://x",
            source="Reuters",
            published_ts=1.0,
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
    )
    assert rep is not None
    assert rep.direction == "up"
    assert rep.confidence >= 5
    text = format_oil_why_report(rep)
    assert "Почему цена" in text
    assert "Hormuz" in text or "Ормуз" in text or "Иран" in text


def test_why_flags_conflict_when_news_bearish_but_price_up():
    bars = _bars_up()
    bias = OilNewsBias(
        bias="bearish",
        weighted_score=-4.0,
        summary_ru="down",
        how_to_use_ru="short",
        top_catalyst="Hormuz deal flows",
    )
    items = [
        OilNewsItem(
            title="Oil falls as Hormuz tanker flows recover deal",
            url="https://x",
            source="CNBC",
            published_ts=1.0,
            impact="bearish",
            theme="iran_geo",
        ),
    ]
    rep = build_oil_why_report(
        bars, news_items=items, news_bias=bias, interval_minutes=15
    )
    assert rep is not None
    assert rep.direction == "up"
    assert any("медвежь" in a.lower() or "отскок" in a.lower() for a in rep.against_ru)
    assert rep.confidence <= 5
