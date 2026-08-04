"""Tests: calendar lock, post-news reaction, trading bias age, compact cards."""
from __future__ import annotations

import time
from types import SimpleNamespace

from bot.oil_calendar import (
    calendar_entry_lock,
    detect_scheduled_speech_freeze,
    format_morning_desk_brief,
    upcoming_oil_events,
)
from bot.oil_monitor import OilNewsItem, summarize_oil_news_bias
from bot.oil_reaction import (
    confirm_reaction_with_bars,
    reaction_blocks_entry,
    start_reaction,
)
from bot.oil_x import _oil_relevant


def test_trading_bias_ignores_morning_news_in_evening():
    now = time.time()
    items = [
        OilNewsItem(
            title="Iran threatens Hormuz blockade",
            url="",
            source="Reuters",
            published_ts=now - 8 * 3600,
            impact="bullish",
        )
    ]
    bias = summarize_oil_news_bias(items, max_age_hours=2.0)
    assert bias.bias == "neutral"
    assert bias.bullish == 0


def test_trading_bias_keeps_fresh_news():
    now = time.time()
    items = [
        OilNewsItem(
            title="Iran threatens Hormuz blockade",
            url="",
            source="Reuters",
            published_ts=now - 20 * 60,
            impact="bullish",
        )
    ]
    bias = summarize_oil_news_bias(items, max_age_hours=2.0)
    assert bias.bias == "bullish"


def test_reaction_blocks_until_wait_and_confirm():
    rx = start_reaction(
        impact="bearish",
        title="Trump deal Hormuz",
        wait_minutes=10,
        expire_minutes=45,
        now_ts=1_000_000.0,
    )
    assert rx is not None
    assert reaction_blocks_entry(rx, now_ts=1_000_000.0 + 60)
    assert reaction_blocks_entry(rx, now_ts=1_000_000.0 + 11 * 60)  # weak/None

    bars = [
        SimpleNamespace(open=80.2, high=80.3, low=80.0, close=80.1),
        SimpleNamespace(open=80.0, high=80.2, low=79.5, close=79.6),
        SimpleNamespace(open=79.6, high=79.7, low=79.0, close=79.1),
    ]
    rx2 = confirm_reaction_with_bars(rx, bars, now_ts=1_000_000.0 + 11 * 60)
    assert rx2.confirmed is True
    assert reaction_blocks_entry(rx2, now_ts=1_000_000.0 + 11 * 60) is None


def test_calendar_events_and_brief():
    from bot.oil_calendar import format_morning_desk_brief

    evs = upcoming_oil_events(horizon_hours=80)
    assert any(e.kind in {"eia", "api", "inventory", "nfp", "cpi", "speech", "macro", "fed", "opec"} for e in evs) or evs == []
    # fallback всегда даёт api/eia если FF пуст
    text = format_morning_desk_brief(upcoming_oil_events(horizon_hours=80) or None)
    assert "DESK" in text
    assert "Нефть" in text or "нефть" in text.lower()


def test_calendar_lock_around_event():
    from bot.oil_calendar import OilCalendarEvent

    now = time.time()
    ev = OilCalendarEvent(
        key="t",
        title_ru="EIA тест",
        when_ts=now + 600,
        kind="eia",
        impact="High",
        lock_before_min=25,
        lock_after_min=20,
    )
    lock = calendar_entry_lock(now_ts=now + 60, events=[ev])
    assert lock.active
    lock2 = calendar_entry_lock(now_ts=now - 3600, events=[ev])
    assert not lock2.active


def test_speech_freeze_detect():
    lock = detect_scheduled_speech_freeze(
        "Trump will speak at a press conference on Iran at 3pm ET"
    )
    assert lock.active
    assert lock.until_ts > time.time()


def test_newsroom_flash_only_when_important():
    from bot.oil_calendar import (
        OilCalendarEvent,
        format_newsroom_desk_flash,
        important_events_today,
    )

    now = time.time()
    quiet = [
        OilCalendarEvent(
            key="x",
            title_ru="Minor PMI",
            when_ts=now + 3600,
            kind="macro",
            impact="Low",
        )
    ]
    assert important_events_today(quiet) == []
    assert format_newsroom_desk_flash(quiet) == ""

    hot = [
        OilCalendarEvent(
            key="e",
            title_ru="EIA запасы",
            when_ts=now + 7200,
            kind="eia",
            impact="High",
        )
    ]
    assert important_events_today(hot)
    text = format_newsroom_desk_flash(hot)
    assert "важно" in text.lower()
    assert "EIA" in text


def test_x_oil_relevant_filter():
    assert _oil_relevant("OIL: Brent jumps after Hormuz headlines")
    assert not _oil_relevant("Celebrity wears new dress at gala")
