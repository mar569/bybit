"""Тесты профи-аналитика и ABC."""
from __future__ import annotations

from bot.models import Signal
from bot.ta_analysis import TAAnalysisResult
from bot.trade_analyst import (
    build_fib_action_text,
    build_trade_thesis,
    fib_action_line_html,
    format_thesis_hot_html,
    format_thesis_pro_html,
)
from bot.trade_decision_gate import TradeDecision
from bot.wave_structure import FibLevel, ImpulseLeg, detect_abc_pattern
from bot.bybit_klines import KlineBar
from bot.ta_analysis import SwingPoint


def _sig() -> Signal:
    return Signal(
        exchange="bybit",
        symbol="TESTUSDT",
        signal_type="impulse_pump",
        oi_period_minutes=5,
        oi_change_percent=2.0,
        oi_change_value=0.0,
        oi_change_usd=50_000.0,
        oi_direction="up",
        signals_today=1,
        price_change_percent=5.0,
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
        signal_score=3,
        side="long",
        current_price=1.05,
        current_open_interest=1_000_000.0,
        link="",
        details={},
    )


def test_thesis_entry_with_abc_and_fib() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        verdict_confidence=8,
        structure_label="HH + HL (бычья)",
        wave_phase="wave_2_4_zone",
        wave_bias="long",
        wave_has_confluence=True,
        wave_confluence_count=2,
        wave_confluence_sr=True,
        fib_status="ready",
        fib_levels=[
            FibLevel(0.5, 1.05, "retracement", "Fib 0.5"),
            FibLevel(0.618, 1.038, "retracement", "Fib 0.618"),
        ],
        elliott_label="бычий импульс · золотая зона Fib 0.5–0.618 (волна 2/4)",
        abc_phase="C",
        abc_label_ru="волна C коррекции · B @ 50% A",
        entry_zone=(1.048, 1.052),
        invalidation_price=1.04,
        target_prices=[1.08, 1.10],
        current_price=1.05,
    )
    thesis = build_trade_thesis(
        _sig(),
        ta,
        decision=TradeDecision("entry", "локация fib", location="fib"),
        readiness=(True, "триггер"),
    )
    assert thesis.action == "entry"
    assert "Fib" in thesis.thesis or "ABC" in thesis.thesis
    assert "Вход от Fib" in thesis.fib_action
    hot = format_thesis_hot_html(thesis)
    assert "1.048" in hot
    assert "1.040" in hot
    assert "Fib" in hot
    pro = format_thesis_pro_html(thesis)
    assert "Fib" in pro


def test_thesis_watch_on_late_impulse() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="long",
        wave_phase="late_impulse",
        fib_status="late_impulse",
        fib_reject_reason="финал импульса — не входить вдогонку, ждать откат к Fib",
        elliott_label="бычий финал импульса (волна 5)",
        range_position=0.9,
        momentum_pct=2.0,
        current_price=1.1,
        fib_levels=[
            FibLevel(0.5, 1.05, "retracement", "Fib 0.5"),
            FibLevel(0.618, 1.038, "retracement", "Fib 0.618"),
        ],
    )
    thesis = build_trade_thesis(
        _sig(),
        ta,
        decision=TradeDecision("watch", "погоня", chase=True),
    )
    assert thesis.action == "watch"
    assert "market-вход" in thesis.thesis.lower() or "не входить" in thesis.thesis.lower()
    assert "вход ≈ 1.1" not in (thesis.wait_for or "")
    hot = format_thesis_hot_html(thesis)
    assert "ждать" in hot.lower() or "Fib" in hot
    assert "Не входить" in thesis.fib_action or "ждать" in thesis.fib_action.lower()


def test_fib_action_no_impulse() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        fib_status="no_impulse",
        fib_reject_reason="пила, не импульс (низкая efficiency)",
        current_price=1.0,
    )
    text = build_fib_action_text(ta, action="skip")
    assert "Fib не" in text
    assert "пила" in text or "П/С" in text
    line = fib_action_line_html(ta)
    assert line.startswith("📐")
    assert "Fib" in line


def test_abc_detection_up_impulse() -> None:
    leg = ImpulseLeg(0, 10, 1.0, 1.10, "up", size_pct=10.0)
    swings = [
        SwingPoint(0, 1.0, "low"),
        SwingPoint(10, 1.10, "high"),
        SwingPoint(15, 1.05, "low"),
        SwingPoint(20, 1.08, "high"),
        SwingPoint(25, 1.04, "low"),
    ]
    bars = [KlineBar(0, 1.0, 1.0, 1.0, 1.0, 100.0)] * 30
    abc = detect_abc_pattern(swings, bars, leg)
    assert abc is not None
    assert abc.phase in {"B", "C", "complete", "A"}
