"""Единый гейт: симметрия LONG/SHORT — против импульса закрыто, по импульсу открыто."""
from __future__ import annotations

from types import SimpleNamespace

from bot.bybit_klines import KlineBar
from bot.oil_confluence import build_oil_confluence_setup
from bot.oil_forecast import build_oil_forecast
from bot.oil_monitor import (
    OilMarketSnapshot,
    OilNewsBias,
    OilNewsItem,
    build_oil_bounce_plan,
    build_oil_scalp_call,
)
from bot.oil_signal_gate import evaluate_oil_signal_gate, gate_apply_to_side
from bot.ta_analysis import TAAnalysisResult


def _up_bars(n: int = 40, start: float = 80.0, step: float = 0.08) -> list[KlineBar]:
    bars: list[KlineBar] = []
    t0 = 1_700_000_000.0
    px = start
    for i in range(n):
        nxt = px + step
        bars.append(
            KlineBar(
                open_time=t0 + i * 300,
                open=px,
                high=nxt + 0.02,
                low=px - 0.01,
                close=nxt,
                volume=1000.0,
            )
        )
        px = nxt
    return bars


def _down_bars(n: int = 40, start: float = 85.0, step: float = 0.08) -> list[KlineBar]:
    bars: list[KlineBar] = []
    t0 = 1_700_000_000.0
    px = start
    for i in range(n):
        nxt = px - step
        bars.append(
            KlineBar(
                open_time=t0 + i * 300,
                open=px,
                high=px + 0.01,
                low=nxt - 0.02,
                close=nxt,
                volume=1000.0,
            )
        )
        px = nxt
    return bars


def _snap(px: float) -> OilMarketSnapshot:
    return OilMarketSnapshot(
        label="Brent",
        symbol="UKOUSD.s",
        price=px,
        high_7d=px * 1.02,
        low_7d=px * 0.97,
        verdict="SHORT",
        confidence=8,
        support=px * 0.99,
        resistance=px * 1.01,
        breakdown=px * 0.985,
        breakout=px * 1.015,
        phase="test",
        elliott="",
        reason="test",
    )


def test_gate_blocks_short_on_uptrend():
    bars = _up_bars()
    gate = evaluate_oil_signal_gate(bars, interval_minutes=5)
    assert gate.allow_short is False
    assert gate.trend == "up" or gate.move_30m_pct > 0
    assert gate_apply_to_side(gate, "SHORT") == "WAIT"


def test_gate_blocks_long_on_downtrend():
    bars = _down_bars()
    gate = evaluate_oil_signal_gate(bars, interval_minutes=5)
    assert gate.allow_long is False
    assert gate.allow_short is True
    assert gate_apply_to_side(gate, "LONG") == "WAIT"
    assert gate_apply_to_side(gate, "SHORT") == "SHORT"


def test_forecast_short_on_downtrend_inventory():
    """Падение + EIA build → SHORT (не заточен только на LONG)."""
    import time

    bars = _down_bars()
    px = float(bars[-1].close)
    snap = _snap(px)
    now = time.time()
    items = [
        OilNewsItem(
            title="EIA crude inventories build larger than expected stocks",
            url="https://example.com/eia",
            source="Reuters",
            published_ts=now - 600,
            impact="bearish",
            theme="inventory",
        )
    ]
    fc = build_oil_forecast(
        snap,
        TAAnalysisResult(verdict="SHORT", verdict_confidence=8),
        news_bias=OilNewsBias(
            bias="bearish",
            weighted_score=-5.0,
            summary_ru="запасы",
            top_catalyst="EIA build",
        ),
        news_items=items,
        bars=bars,
        interval_minutes=5,
        ta_verdict_raw="SHORT",
        ta_confidence_raw=8,
    )
    assert fc.bias == "SHORT"


def test_forecast_short_on_downtrend_with_deal_tape():
    """Deal в ленте + цена уже падает → SHORT ок."""
    import time

    bars = _down_bars()
    px = float(bars[-1].close)
    snap = _snap(px)
    now = time.time()
    items = [
        OilNewsItem(
            title="Hormuz deal progress oil premium unwind tumble",
            url="https://example.com/deal",
            source="Reuters",
            published_ts=now - 600,
            impact="bearish",
            theme="iran_geo",
        )
    ]
    fc = build_oil_forecast(
        snap,
        TAAnalysisResult(verdict="SHORT", verdict_confidence=8),
        news_bias=OilNewsBias(
            bias="bearish",
            weighted_score=-5.0,
            summary_ru="deal",
            top_catalyst="Hormuz deal",
        ),
        news_items=items,
        bars=bars,
        interval_minutes=5,
        ta_verdict_raw="SHORT",
        ta_confidence_raw=8,
    )
    assert fc.bias == "SHORT"
    assert fc.scenario == "deal_tape"


def test_forecast_wait_not_short_on_rally_deal_tape():
    bars = _up_bars()
    px = float(bars[-1].close)
    snap = _snap(px)
    news = OilNewsBias(
        bias="bearish",
        weighted_score=-5.0,
        summary_ru="deal",
        top_catalyst="Hormuz deal talks Oman 3%",
    )
    now = bars[-1].open_time + 60
    items = [
        OilNewsItem(
            title="Hormuz deal progress Oman 3% fee Iran still talks not final",
            url="https://example.com/1",
            source="Reuters",
            published_ts=now - 600,
            impact="bearish",
            theme="iran_geo",
        )
    ]
    fc = build_oil_forecast(
        snap,
        TAAnalysisResult(verdict="SHORT", verdict_confidence=8),
        news_bias=news,
        news_items=items,
        bars=bars,
        interval_minutes=5,
        ta_verdict_raw="SHORT",
        ta_confidence_raw=8,
    )
    assert fc.bias == "WAIT"


def test_bounce_none_on_rally_bearish_news():
    bars = _up_bars()
    px = float(bars[-1].close)
    snap = _snap(px)
    news = OilNewsBias(
        bias="bearish",
        weighted_score=-5.0,
        summary_ru="deal",
        top_catalyst="Hormuz",
    )
    plan = build_oil_bounce_plan(
        snap,
        news,
        news_items=[],
        min_score=3.0,
        bars=bars,
        interval_minutes=5,
    )
    assert plan is None


def test_scalp_no_open_short_on_rally():
    bars = _up_bars()
    px = float(bars[-1].close)
    snap = _snap(px)
    snap.resistance = px * 1.001
    call = build_oil_scalp_call(
        snap,
        TAAnalysisResult(verdict="SHORT", verdict_confidence=8),
        news_bias=OilNewsBias(
            bias="bearish",
            weighted_score=-4.0,
            summary_ru="x",
            top_catalyst="x",
        ),
        interval_minutes=5,
        bars=bars,
        ta_verdict_raw="SHORT",
        ta_confidence_raw=8,
    )
    assert call.action != "open_short"


def test_confluence_wait_not_short_on_rally():
    bars = _up_bars()
    px = float(bars[-1].close)
    snap = _snap(px)
    forecast = SimpleNamespace(bias="SHORT", confidence=8, scenario="deal_tape")
    news = OilNewsBias(
        bias="bearish",
        weighted_score=-5.0,
        summary_ru="deal",
        top_catalyst="Hormuz deal",
    )
    setup = build_oil_confluence_setup(
        snap,
        TAAnalysisResult(verdict="SHORT", verdict_confidence=8),
        forecast=forecast,
        news_bias=news,
        bars=bars,
        interval_minutes=5,
        min_quality=5,
        apply_session_filter=False,
        apply_chase_filter=False,
    )
    assert setup is None or setup.side == "WAIT"
