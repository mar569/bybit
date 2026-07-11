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


def test_clamp_cooldown_never_zero() -> None:
    from bot.settings import clamp_cooldown_seconds, MIN_SIGNAL_COOLDOWN_SECONDS

    assert clamp_cooldown_seconds(0) == 120
    assert clamp_cooldown_seconds(30) == MIN_SIGNAL_COOLDOWN_SECONDS
    assert clamp_cooldown_seconds(180) == 180


def test_resolve_short_playbook_after_pump() -> None:
    pb = resolve_trade_playbook(_signal_trend_dump(), _ta_short_verdict())
    assert pb is not None
    assert pb.side == "short"
    assert pb.stop_price == 0.01845
    assert len(pb.target_prices) >= 2


def test_hot_caption_has_playbook_no_footer() -> None:
    text = build_hot_caption(
        _signal_trend_dump(),
        _ta_short_verdict(),
        header="🚨 ТЕСТ",
        readiness=(True, "ok"),
    )
    assert "SHORT" in text
    assert "0.01783" in text or "0.0178" in text
    assert "Подробнее" not in text


def test_hot_caption_minimal_no_ta_wall() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        verdict_confidence=6,
        current_price=0.797,
        breakout_level=0.79700,
        invalidation_price=0.76516,
        target_prices=[0.81400],
        action_priority="long",
        narrative_plain=(
            "📐 Простыми словами: WAIT — консолидация после пампа — пробой границ локального range."
        ),
        flow_continuation=42,
        flow_correction=42,
        phase_label="боковик",
        structure_label="HH + HL",
        momentum_label="импульс вверх +1.9%",
    )
    quality = "✅ OI↑ цена↑ — aligned long · CVD 52% buy\n⚠️ HTF bearish"
    text = build_hot_caption(
        _signal_trend_dump(),
        ta,
        header="👀 <b>WATCH</b> · 🔥 тест",
        readiness=(False, "TA ждёт пробой уровня"),
        quality_html=quality,
        quality_tier="watch",
    )
    assert "Простыми словами" not in text
    assert "факторы смешаны" not in text
    assert "📐 пробой" not in text
    assert "0.797" in text
    assert "0.814" in text


def test_hot_caption_watch_no_duplicate_tier_line() -> None:
    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=7,
        current_price=0.067,
        breakdown_level=0.06695,
        invalidation_price=0.06835,
        target_prices=[0.06639],
        action_priority="short",
        narrative_plain="📉 TA SHORT — цель 0.06639",
        flow_continuation=42,
        flow_correction=42,
    )
    quality = "💧 OI↑ цена↓ — новые шорты\n📈 CVD live 57% buy"
    text = build_hot_caption(
        _signal_trend_dump(),
        ta,
        header="👀 <b>WATCH</b> · 🔥 тест",
        readiness=(False, "ждать пробой ≤0.06695"),
        quality_html=quality,
        quality_tier="watch",
    )
    assert text.count("👀") == 1
    assert text.count("WATCH") == 1
    assert "Простыми словами" not in text
    assert "0.06695" in text


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


def test_hot_narrative_only_in_pro_detail() -> None:
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
    hot = build_hot_caption(_signal_trend_dump(), ta, header="🚨 ТЕСТ")
    pro = build_pro_detail_html(_signal_trend_dump(), ta)
    assert "1778.03" in hot
    assert "коррекции" not in hot.lower()
    assert "1778.03" in pro or "коррекции" in pro.lower()


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
