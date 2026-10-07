from __future__ import annotations

from .models import Signal
from .proactive_intel import build_proactive_intel_html
from .ta_analysis import TAAnalysisResult


def test_proactive_intel_contains_scenario_and_wait() -> None:
    sig = Signal(
        exchange="Binance",
        symbol="MONUSDT",
        signal_type="long",
        oi_period_minutes=10,
        oi_change_percent=4.2,
        oi_change_value=0.0,
        oi_change_usd=50_000.0,
        oi_direction="up",
        signals_today=1,
        price_change_percent=1.1,
        price_change_value=None,
        price_direction="up",
        volume_change_percent=None,
        trade_count=None,
        spread=None,
        funding_rate=None,
        liquidation_estimate=None,
        vwap=None,
        atr=None,
        rsi=None,
        ema_short=None,
        ema_long=None,
        volume_24h=None,
        volume_speed=None,
        signal_score=2,
        side="long",
        current_price=0.03,
        current_open_interest=1_000_000.0,
        link="",
    )
    ta = TAAnalysisResult(
        human_trade_brief="MON растёт, но вход только после отката.",
        scenario_engine_id="continuation",
        market_metrics={"scenario_engine": {"title": "Continuation short", "quality": "C"}},
        reading_seek_label="retest supply",
        invalidation_price=0.036,
    )
    html = build_proactive_intel_html(sig, ta)
    assert "Наблюдение" in html
    assert "не догоняем" in html.lower() or "Ждём" in html
