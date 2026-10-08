"""RBR-сетапы не должны глохнуть в skip без алерта."""
from __future__ import annotations

from bot.models import Signal
from bot.range_breakdown_retest import evaluate_range_breakdown_retest, rbr_alert_eligible
from bot.signal_quality_gate import assess_signal_quality
from bot.ta_analysis import TAAnalysisResult, run_ta_analysis
from bot.test_range_breakdown_retest import _Box, _consolidation_bars
from bot.telegram_bot import _watch_allowed_for_signal


class _Settings:
    signal_quality_gate_enabled = True
    signal_watch_mode_enabled = False
    signal_cvd_gate_enabled = False
    signal_sweep_guard_enabled = False
    signal_flow_matrix_enabled = False
    signal_funding_squeeze_enabled = False
    signal_htf_gate_enabled = False
    signal_btc_regime_filter_enabled = False
    signal_outcome_feedback_enabled = False
    signal_rbr_watch_to_alert_channel = True


def _ta_rbr_fade_top() -> TAAnalysisResult:
    floor, ceil = 0.13, 0.15
    bars = _consolidation_bars(floor=floor, ceil=ceil, n=44)
    current = float(bars[-1].close)
    setup = evaluate_range_breakdown_retest(
        bars,
        consolidation=_Box(top=ceil, bottom=floor),
        breakdown=floor,
        breakout=ceil,
        post_pump=True,
        current=current,
    )
    assert setup is not None
    ta = run_ta_analysis(bars, symbol="ALGOUSDT", interval_minutes=15)
    ta.market_metrics = {"range_breakdown_retest": setup.to_dict()}
    return ta


def test_rbr_alert_eligible():
    ta = _ta_rbr_fade_top()
    assert rbr_alert_eligible(ta)


def test_quality_tier_watch_when_watch_mode_off():
    ta = _ta_rbr_fade_top()
    sig = Signal(
        exchange="bybit",
        symbol="ALGOUSDT",
        signal_type="vertical_dump",
        oi_period_minutes=5,
        oi_change_percent=0.0,
        oi_change_value=0.0,
        oi_change_usd=None,
        oi_direction="flat",
        signals_today=1,
        price_change_percent=-2.1,
        price_change_value=None,
        price_direction="down",
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
        signal_score=6,
        side="short",
        current_price=None,
        current_open_interest=None,
        link="",
        details={},
    )
    q = assess_signal_quality(
        sig,
        ta=ta,
        settings=_Settings(),
        readiness=(False, "график WAIT — ждём реакцию у сопр."),
    )
    assert q.tier == "watch"


def test_rbr_compact_caption_no_plan_wall():
    from bot.human_trade_brief import format_rbr_alert_caption_html

    ta = _ta_rbr_fade_top()
    html = format_rbr_alert_caption_html(ta, symbol="ALGOUSDT")
    assert "План" not in html
    assert "📊" not in html
    assert "на графике" in html


def test_watch_allowed_for_rbr_without_global_watch():
    ta = _ta_rbr_fade_top()
    sig = Signal(
        exchange="bybit",
        symbol="ALGOUSDT",
        signal_type="vertical_dump",
        oi_period_minutes=5,
        oi_change_percent=0.0,
        oi_change_value=0.0,
        oi_change_usd=None,
        oi_direction="flat",
        signals_today=1,
        price_change_percent=-2.1,
        price_change_value=None,
        price_direction="down",
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
        signal_score=6,
        side="short",
        current_price=None,
        current_open_interest=None,
        link="",
        details={},
    )
    assert _watch_allowed_for_signal(sig, _Settings(), ta)
