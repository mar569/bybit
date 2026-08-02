"""Tests for weekend open brief + Hormuz status + Gemini bias parse."""
from __future__ import annotations

from bot.oil_fastlane import parse_gemini_oil_bias, strip_gemini_oil_meta
from bot.oil_hormuz import format_hormuz_status, infer_hormuz_from_news
from bot.oil_monitor import OilNewsItem, classify_news_impact
from bot.oil_session import build_weekend_open_brief, oil_session_status
from bot.oil_why import _explain_headline


def test_taco_tumble_not_bullish():
    title = "Crude oil price tumbles on Hyperliquid as Trump TACOs on planned Iran attacks"
    assert classify_news_impact(title) == "bearish"
    _, _, side = _explain_headline(title)
    assert side == "down"


def test_parse_gemini_oil_bias():
    text = "OIL_RELEVANT: YES\nOIL_BIAS: DOWN\n1) Суть\nобвал"
    assert parse_gemini_oil_bias(text) == "bearish"
    assert "OIL_BIAS" not in strip_gemini_oil_meta(text)
    assert "Суть" in strip_gemini_oil_meta(text)


def test_hormuz_infer_reopen():
    items = [
        OilNewsItem(
            title="Tankers resume transit as Strait of Hormuz reopens after deal",
            url="https://example.com/1",
            source="Reuters",
            published_ts=1.0,
            impact="bearish",
            theme="iran_geo",
        )
    ]
    st = infer_hormuz_from_news(items)
    assert st.traffic == "open"
    text = format_hormuz_status(st)
    assert "Ормуз" in text
    assert "MarineTraffic" in text


def test_hormuz_status_from_straits_snapshot():
    from bot.oil_hormuz import _status_from_straits

    data = {
        "asOf": "2026-08-01T14:47:06.068Z",
        "verdict": {"status": "closed", "long": "Effectively closed to commercial shipping."},
        "aisConcurrentInZone": 154,
        "strandedOffshore": 320,
        "aisGaps": {"count": 33},
        "transits": {"count": 10, "baseline": 88, "asOfDate": "2026-07-23"},
        "hormuzIndex": {"crisisPressure": {"band": "extreme", "score": 92}},
    }
    pw = {"date": "2026-07-23", "n_tanker": 2, "n_cargo": 8, "n_total": 10}
    st = _status_from_straits(data, pw)
    assert st.traffic == "closed"
    assert st.vessels_in_zone == 154
    assert st.stranded == 320
    assert st.tankers_day == 2
    text = format_hormuz_status(st)
    assert "154" in text
    assert "320" in text
    assert "straits.live" in text


def test_hormuz_alert_on_reopen_and_stranded_spike():
    from bot.oil_hormuz import HormuzStatus, detect_hormuz_alert

    closed = HormuzStatus(
        traffic="closed",
        risk="critical",
        summary_ru="closed",
        evidence_ru=(),
        sources_ru=("t",),
        live_links_ru="",
        confidence=8,
        vessels_in_zone=150,
        stranded=300,
        tankers_dark=30,
        transits_day=10,
        transits_baseline=88,
    )
    # Первый снимок уже closed → алерт
    first = detect_hormuz_alert(None, closed)
    assert first is not None
    assert first.trade_critical is True
    assert "Ормуз" in first.message_html

    # Без изменений — тихо
    assert detect_hormuz_alert(closed, closed) is None

    opened = HormuzStatus(
        traffic="open",
        risk="elevated",
        summary_ru="open",
        evidence_ru=(),
        sources_ru=("t",),
        live_links_ru="",
        confidence=7,
        vessels_in_zone=160,
        stranded=120,
        tankers_dark=20,
        transits_day=70,
        transits_baseline=88,
    )
    reopen = detect_hormuz_alert(closed, opened)
    assert reopen is not None
    assert reopen.bias_hint == "bearish"
    assert reopen.trade_critical is True
    assert "важно для нефти" in reopen.message_html

    mild = HormuzStatus(
        traffic="restricted",
        risk="high",
        summary_ru="r",
        evidence_ru=(),
        sources_ru=("t",),
        live_links_ru="",
        confidence=6,
        vessels_in_zone=155,
        stranded=305,  # +5 — не важно
        tankers_dark=30,
    )
    assert detect_hormuz_alert(closed, mild) is not None  # traffic closed→restricted

    same_traffic = HormuzStatus(
        traffic="closed",
        risk="critical",
        summary_ru="c",
        evidence_ru=(),
        sources_ru=("t",),
        live_links_ru="",
        confidence=8,
        vessels_in_zone=150,
        stranded=305,
        tankers_dark=30,
        transits_day=10,
    )
    assert detect_hormuz_alert(closed, same_traffic) is None

    spike = HormuzStatus(
        traffic="closed",
        risk="critical",
        summary_ru="c",
        evidence_ru=(),
        sources_ru=("t",),
        live_links_ru="",
        confidence=8,
        vessels_in_zone=150,
        stranded=400,  # +100
        tankers_dark=30,
        transits_day=10,
    )
    spike_alert = detect_hormuz_alert(closed, spike)
    assert spike_alert is not None
    assert any("Скопление" in r for r in spike_alert.reasons_ru)


def test_hormuz_infer_blockade():
    items = [
        OilNewsItem(
            title="Iran blockade closes Strait of Hormuz shipping halted",
            url="https://example.com/2",
            source="WSJ",
            published_ts=1.0,
            impact="bullish",
            theme="iran_geo",
        )
    ]
    st = infer_hormuz_from_news(items)
    assert st.traffic == "closed"


def test_weekend_open_brief_down_on_taco():
    items = [
        OilNewsItem(
            title="Crude oil price tumbles as Trump TACOs on planned Iran attacks",
            url="https://ex.com",
            source="Invezz",
            published_ts=1.0,
            impact="bearish",
            theme="iran_geo",
        )
    ]
    from bot.oil_monitor import summarize_oil_news_bias

    bias = summarize_oil_news_bias(items)
    brief = build_weekend_open_brief(
        price=83.5,
        news_items=items,
        news_bias=bias,
        sat_high_hint=92.1,
        sun_low_hint=83.0,
    )
    assert brief.bias == "DOWN"
    assert "80" in brief.base_case_ru or "80" in brief.levels_ru


def test_oil_session_bybit_schedule():
    from datetime import datetime, timezone, timedelta
    from bot.oil_session import (
        is_ukousd_session_open,
        next_ukousd_open_msk,
        oil_session_status,
        should_send_preopen_alert,
    )

    msk = timezone(timedelta(hours=3))
    # Воскресенье 15:00 — закрыто, следующее открытие пн 01:00
    sun = datetime(2026, 8, 2, 15, 0, tzinfo=msk)
    assert is_ukousd_session_open(now=sun) is False
    nxt = next_ukousd_open_msk(now=sun)
    assert nxt.weekday() == 0 and nxt.hour == 1
    st = oil_session_status(now=sun)
    assert st.is_weekend_gap is True
    assert "01:00" in st.next_open_label_ru

    # Понедельник 02:00 — открыто
    mon = datetime(2026, 8, 3, 2, 0, tzinfo=msk)
    assert is_ukousd_session_open(now=mon) is True

    # Вторник 01:00 — ночной перерыв
    tue = datetime(2026, 8, 4, 1, 0, tzinfo=msk)
    assert is_ukousd_session_open(now=tue) is False

    # Пятница после открытия → следующее = понедельник 01:00 (не суббота!)
    fri = datetime(2026, 8, 7, 15, 0, tzinfo=msk)
    assert is_ukousd_session_open(now=fri) is True
    nxt_fri = next_ukousd_open_msk(now=fri)
    assert nxt_fri.weekday() == 0 and nxt_fri.hour == 1

    # За 45 мин до пн 01:00 = Mon 00:15
    pre = datetime(2026, 8, 3, 0, 15, tzinfo=msk)
    assert should_send_preopen_alert(now=pre) is True
    assert should_send_preopen_alert(now=mon) is False


def test_no_entry_signals_when_master_toggle_off(monkeypatch):
    """Мастер-тумблер Входы OFF — micro/levels молчат (сессия не блокирует)."""
    import asyncio
    from bot.oil_monitor import OilMonitorEngine

    class _SM:
        settings = type(
            "S",
            (),
            {
                "oil_entry_signals_enabled": False,
                "oil_micro_signals_enabled": True,
                "oil_micro_cooldown_seconds": 0,
                "oil_micro_max_per_hour": 10,
                "oil_level_alerts_enabled": True,
            },
        )()

    async def _noop(_msg: str) -> bool:
        return True

    eng = OilMonitorEngine(_SM(), on_news=_noop, on_level_alert=_noop)
    monkeypatch.setattr(
        "bot.oil_session.is_ukousd_session_open", lambda **_kw: True
    )
    assert eng._oil_entry_signals_allowed(_SM.settings) is False
    assert asyncio.run(eng._tick_micro_signals(_SM.settings)) == 0
    assert asyncio.run(eng._tick_level_alerts(_SM.settings)) == 0


def test_entry_signals_allowed_when_session_closed(monkeypatch):
    """Закрытая сессия сама по себе не глушит сигналы — только тумблер."""
    from bot.oil_monitor import OilMonitorEngine

    class _SM:
        settings = type("S", (), {"oil_entry_signals_enabled": True})()

    async def _noop(_msg: str) -> bool:
        return True

    eng = OilMonitorEngine(_SM(), on_news=_noop, on_level_alert=_noop)
    monkeypatch.setattr(
        "bot.oil_session.is_ukousd_session_open", lambda **_kw: False
    )
    assert eng._bybit_tradfi_open() is False
    assert eng._oil_entry_signals_allowed(_SM.settings) is True


def test_oil_session_status_runs():
    st = oil_session_status()
    assert st.market_open_hint_ru
    assert hasattr(st, "is_open")
