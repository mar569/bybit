from __future__ import annotations

from bot.models import Signal
from bot.settings import ScannerSettings
from bot.signal_quality_gate import (
    assess_signal_quality,
    classify_oi_price_flow,
    format_manual_ta_flow_html,
)
from bot.ta_analysis import TAAnalysisResult
from bot.bybit_cvd import summarize_taker_cvd


def _settings() -> ScannerSettings:
    return ScannerSettings()


def _short_dump_signal() -> Signal:
    return Signal(
        exchange="Bybit",
        symbol="SKLUSDT",
        signal_type="reversal_dump",
        oi_period_minutes=5,
        oi_change_percent=-2.5,
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
        signal_score=3,
        side="short",
        current_price=0.00539,
        current_open_interest=1_000_000,
        link="https://example.com",
        details={
            "cvd_ratio": 0.58,
            "smc": {
                "liquidity_sweep": True,
                "sweep_direction": "long",
                "reversal_ready": True,
                "reversal_direction": "long",
            },
            "market_structure": {
                "dead_cat_bounce": True,
                "post_crash": True,
                "phase": "correction_up",
            },
        },
    )


def test_classify_squeeze_risk() -> None:
    key, label = classify_oi_price_flow(-1.2, 1.5)
    assert key == "squeeze_risk"
    assert "squeeze" in label.lower()


def test_cvd_blocks_short() -> None:
    signal = _short_dump_signal()
    result = assess_signal_quality(
        signal,
        settings=_settings(),
        readiness=(False, "ждать пробой"),
    )
    assert result.tier == "skip"
    assert "CVD" in result.block_reason or "sweep" in result.block_reason.lower()


def test_capitulation_blocks_fade_short() -> None:
    signal = _short_dump_signal()
    signal.details["cvd_ratio"] = 0.35
    signal.details["smc"] = {}
    signal.oi_change_percent = -2.0
    signal.price_change_percent = -2.5
    result = assess_signal_quality(signal, settings=_settings())
    assert result.tier == "skip"


def test_htf_blocks_short_against_bullish() -> None:
    signal = _short_dump_signal()
    signal.oi_change_percent = -1.0
    signal.price_change_percent = -1.2
    signal.details["cvd_ratio"] = 0.35
    signal.details["smc"] = {"htf_structure": "bullish", "liquidity_sweep": False}
    signal.details["market_structure"] = {}
    result = assess_signal_quality(signal, settings=_settings())
    assert result.tier == "skip"
    assert "HTF" in result.block_reason or any("HTF" in w for w in result.warnings)


def test_outcome_feedback_blocks_weak_type() -> None:
    signal = _short_dump_signal()
    signal.oi_change_percent = -1.0
    signal.price_change_percent = -1.2
    signal.details["cvd_ratio"] = 0.35
    signal.details["smc"] = {}
    signal.details["market_structure"] = {}
    result = assess_signal_quality(
        signal,
        settings=_settings(),
        outcome_stats=(20, 28.0),
    )
    assert result.tier == "skip"
    joined = result.block_reason + " " + " ".join(result.warnings)
    assert "winrate" in joined or "edge" in joined


def test_scanner_skip_disabled_by_default() -> None:
    assert _settings().signal_quality_scanner_skip_enabled is False


def test_manual_ta_flow_warns_on_sweep() -> None:
    ta = TAAnalysisResult(
        verdict="SHORT",
        action_priority="short",
        momentum_pct=1.2,
        smc=type("S", (), {
            "liquidity_sweep": True,
            "sweep_direction": "long",
            "reversal_ready": True,
            "reversal_direction": "long",
            "reversal_stage": "discount",
        })(),
    )
    cvd = summarize_taker_cvd(600, 400, trade_count=200, window_minutes=10)
    html = format_manual_ta_flow_html(ta, cvd_snap=cvd)
    assert "Sweep" in html
    assert "отскок" in html or "SHORT" in html
