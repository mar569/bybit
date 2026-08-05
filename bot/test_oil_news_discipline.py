"""News discipline: late news ≠ entry signal."""
from __future__ import annotations

import time
from types import SimpleNamespace

from bot.bybit_klines import KlineBar
from bot.oil_news_discipline import (
    assess_news_for_trade,
    news_is_hot_for_reaction,
)


def _item(title: str, *, age_h: float, impact: str = "bearish"):
    return SimpleNamespace(
        title=title,
        published_ts=time.time() - age_h * 3600,
        impact=impact,
        source="Reuters",
    )


def test_hour_old_news_is_warm_not_entry():
    bias = SimpleNamespace(bias="bearish", weighted_score=-3.0, top_catalyst="Hormuz deal")
    items = [_item("Iran deal to reopen Hormuz", age_h=1.0)]
    a = assess_news_for_trade(items, news_bias=bias, hot_hours=0.5, warm_hours=2.0)
    assert a.mode == "warm"
    assert a.for_entry is False
    assert a.block_long is True
    assert "ФОН" in a.rule_ru or "фон" in a.rule_ru.lower()


def test_fresh_news_is_hot_for_entry():
    bias = SimpleNamespace(bias="bearish", weighted_score=-4.0, top_catalyst="deal")
    items = [_item("Hormuz reopen deal", age_h=0.2)]
    a = assess_news_for_trade(items, news_bias=bias, hot_hours=0.5)
    assert a.mode == "hot"
    assert a.for_entry is True
    assert a.block_long is True
    assert a.block_short is False


def test_priced_in_blocks_chase():
    now = time.time()
    bias = SimpleNamespace(bias="bearish", weighted_score=-3.0, top_catalyst="deal")
    news_ts = now - 0.25 * 3600
    items = [
        SimpleNamespace(
            title="Hormuz deal",
            published_ts=news_ts,
            impact="bearish",
            source="WSJ",
        )
    ]
    # Цена уже упала ~0.6% с момента новости
    bars = []
    for i in range(20):
        # open_time around news then drop
        ot = news_ts - 600 + i * 60
        px = 80.0 - (0.0 if i < 5 else (i - 5) * 0.04)
        bars.append(
            KlineBar(
                open_time=ot,
                open=px,
                high=px + 0.02,
                low=px - 0.02,
                close=px,
                volume=1000.0,
            )
        )
    a = assess_news_for_trade(
        items, news_bias=bias, bars=bars, now=now, hot_hours=0.5, priced_in_pct=0.35
    )
    assert a.priced_in is True
    assert a.for_entry is False
    assert a.block_short is True
    assert "не догонять" in a.rule_ru.lower() or "ушла" in a.rule_ru.lower()


def test_stale_flash_not_hot_for_reaction():
    assert news_is_hot_for_reaction(time.time() - 200, hot_hours=0.5)
    assert not news_is_hot_for_reaction(time.time() - 3600, hot_hours=0.5)
