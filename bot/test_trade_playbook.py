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


def test_pro_detail_no_major_duplicates() -> None:
    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=9,
        current_price=542.80,
        breakdown_level=536.30,
        invalidation_price=557.33,
        target_prices=[530.94, 525.57, 517.53],
        action_priority="short",
        primary_scenario="снижение к 530.94",
        narrative_plain=(
            "📉 Простыми словами: TA SHORT — цель снижения 530.94. "
            "Сейчас отскок — затем short при ≤536.30."
        ),
        narrative_basis=(
            "📊 На чём основано: структура боковая · CVD продажи · слом структуры"
        ),
    )
    signal = Signal(
        exchange="Bybit",
        symbol="KORUUSDT",
        signal_type="reversal_pump",
        oi_period_minutes=5,
        oi_change_percent=0.0,
        oi_change_value=0,
        oi_change_usd=0,
        oi_direction="up",
        signals_today=1,
        price_change_percent=1.42,
        price_change_value=None,
        price_direction="up",
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
        side="long",
        current_price=542.80,
        current_open_interest=1_000_000,
        link="https://example.com",
        details={"probability_percent": 79},
    )
    text = build_pro_detail_html(signal, ta, readiness=(False, "ждать пробой ≤536.30"))
    assert text.count("Сканер поймал краткий отскок") <= 1
    assert text.count("На чём основано") <= 1
    assert text.count("Простыми словами") <= 1
    assert text.count("536.30") >= 1


def test_hot_caption_includes_narrative_forecast() -> None:
    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=8,
        current_price=1800.0,
        breakdown_level=1795.99,
        target_prices=[1778.03],
        narrative_plain="📉 TA SHORT — цель 1778.03",
        flow_continuation=38,
        flow_correction=58,
        phase_label="боковик",
        oi_narrative_label="закрытие лонгов",
    )
    text = build_hot_caption(
        _signal_trend_dump(),
        ta,
        header="🚨 ТЕСТ",
    )
    assert "1778.03" in text
    assert "коррекции" in text.lower() or "corr" in text.lower()


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
