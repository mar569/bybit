"""Quality guards: no blind SHORT 10/10 against price/flow."""
from __future__ import annotations

import time
from types import SimpleNamespace

from bot.bybit_klines import KlineBar
from bot.oil_flow import OilFlowProxy
from bot.oil_forecast import build_oil_forecast
from bot.oil_monitor import OilNewsBias, OilNewsItem
from bot.ta_analysis import TAAnalysisResult


def _snap(price: float = 80.0):
    return SimpleNamespace(
        price=price,
        support=79.4,
        resistance=80.3,
        breakdown=79.2,
        breakout=80.5,
        verdict="WAIT",
        confidence=5,
    )


def _bars_up(n: int = 20, start: float = 79.5) -> list[KlineBar]:
    """Имитация роста ~+0.9% за последние бары."""
    bars = []
    t0 = time.time() - n * 300
    px = start
    for i in range(n):
        nxt = px + 0.04
        bars.append(
            KlineBar(
                open_time=t0 + i * 300,
                open=px,
                high=nxt + 0.01,
                low=px - 0.01,
                close=nxt,
                volume=1000.0,
            )
        )
        px = nxt
    return bars


def test_stale_deal_tape_does_not_force_short_10():
    """Старая сделка Ормуз + рост цены + поток BUY → WAIT, не SHORT 10."""
    now = time.time()
    items = [
        OilNewsItem(
            title="Hormuz deal talks reopen Strait of Hormuz",
            url="https://x",
            source="Reuters",
            published_ts=now - 5 * 3600,  # cold
            impact="bearish",
            theme="iran_geo",
        )
    ]
    bias = OilNewsBias(
        bias="bearish",
        weighted_score=3.0,
        summary_ru="deal",
        how_to_use_ru="short",
        top_catalyst="Hormuz deal",
    )
    flow = OilFlowProxy(
        bias="buy",
        session_volume=1e5,
        recent_volume=5e4,
        prev_volume=3e4,
        volume_ratio=1.6,
        delta_recent=4400,
        delta_session=8000,
        buy_share_pct=62.0,
        bars_used=24,
        lookback=24,
        note_ru="покупки",
    )
    ta = TAAnalysisResult(verdict="WAIT", verdict_confidence=4)
    fc = build_oil_forecast(
        _snap(80.23),
        ta,
        news_bias=bias,
        news_items=items,
        bars=_bars_up(),
        flow=flow,
        interval_minutes=5,
    )
    assert fc.bias == "WAIT"
    assert fc.confidence <= 6
    assert "10" not in f"{fc.confidence}" or fc.confidence < 10


def test_rules_never_emit_confidence_10():
    now = time.time()
    items = [
        OilNewsItem(
            title="Hormuz deal ceasefire premium unwind",
            url="https://x",
            source="Reuters",
            published_ts=now - 600,  # hot
            impact="bearish",
            theme="iran_geo",
        )
    ]
    bias = OilNewsBias(
        bias="bearish",
        weighted_score=4.0,
        summary_ru="deal",
        how_to_use_ru="short",
        top_catalyst="deal",
    )
    flow = OilFlowProxy(
        bias="sell",
        session_volume=1e5,
        recent_volume=5e4,
        prev_volume=5e4,
        volume_ratio=1.0,
        delta_recent=-2000,
        delta_session=-3000,
        buy_share_pct=40.0,
        bars_used=24,
        lookback=24,
        note_ru="продажи",
    )
    ta = TAAnalysisResult(verdict="SHORT", verdict_confidence=8)
    # flat bars — no adverse chase
    bars = [
        KlineBar(
            open_time=now - i * 300,
            open=80.0,
            high=80.05,
            low=79.95,
            close=80.0,
            volume=100.0,
        )
        for i in range(20, 0, -1)
    ]
    fc = build_oil_forecast(
        _snap(80.0),
        ta,
        news_bias=bias,
        news_items=items,
        bars=bars,
        flow=flow,
        interval_minutes=5,
        ta_verdict_raw="SHORT",
        ta_confidence_raw=8,
    )
    assert fc.bias == "SHORT"
    assert fc.confidence <= 9
    assert fc.confidence != 10
