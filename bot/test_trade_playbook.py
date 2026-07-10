from __future__ import annotations

from bot.models import Signal
from bot.ta_analysis import TAAnalysisResult
from bot.trade_playbook import (
    build_hot_caption,
    build_pro_detail_html,
    format_playbook_html,
    resolve_trade_playbook,
    TradePlaybook,
)


def _ta_short_verdict() -> TAAnalysisResult:
    return TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=7,
        current_price=0.01783,
        breakdown_level=0.01783,
        invalidation_price=0.01845,
        target_prices=[0.01690, 0.01600, 0.01430],
        post_pump=True,
        range_position=0.92,
        action_priority="short",
        primary_scenario="снижение к 0.0169",
    )


def _signal_trend_dump() -> Signal:
    return Signal(
        exchange="Bybit",
        symbol="SENTUSDT",
        signal_type="trend_dump",
        oi_period_minutes=5,
        oi_change_percent=2.5,
        oi_change_value=0,
        oi_change_usd=50_000.0,
        oi_direction="down",
        signals_today=1,
        price_change_percent=-3.2,
        price_change_value=None,
        price_direction="down",
        volume_change_percent=0,
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
        signal_score=1,
        side="short",
        current_price=0.01783,
        current_open_interest=1_000_000,
        link="https://example.com",
        details={"trend_prior_pct": 18.0},
    )


def test_resolve_short_playbook_after_pump() -> None:
    pb = resolve_trade_playbook(_signal_trend_dump(), _ta_short_verdict())
    assert pb is not None
    assert pb.side == "short"
    assert pb.stop_price == 0.01845
    assert len(pb.target_prices) >= 2


def test_hot_caption_has_playbook_and_hint() -> None:
    text = build_hot_caption(
        _signal_trend_dump(),
        _ta_short_verdict(),
        header="🚨 ТЕСТ",
        readiness=(True, "ok"),
    )
    assert "SHORT" in text
    assert "0.01783" in text or "0.0178" in text
    assert "Подробнее" in text


def test_pro_detail_includes_verbose_sections() -> None:
    text = build_pro_detail_html(_signal_trend_dump(), _ta_short_verdict())
    assert "Подробный разбор" in text
    assert "SENTUSDT" in text
    assert "График" in text


def test_format_playbook_targets() -> None:
    pb = TradePlaybook(
        side="short",
        entry_price=0.00565,
        entry_op="≥",
        stop_price=0.00590,
        target_prices=[0.00537, 0.00510],
        logic="тест",
        aligned=True,
    )
    html = format_playbook_html(pb)
    assert "TP" not in html or "Цели" in html
    assert "0.00565" in html
