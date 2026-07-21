from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.ta_analysis import (
    classify_structure,
    detect_candle_patterns,
    detect_channel,
    detect_consolidation,
    detect_local_consolidation,
    detect_post_pump_phase,
    detect_price_zones,
    detect_recent_momentum,
    find_swing_points,
    primary_forecast_direction,
    resolve_trade_triggers,
    run_ta_analysis,
    ta_display_score,
    ta_manual_detailed_html,
    ta_user_intent_html,
    ta_plain_forecast_line,
    ta_scenario_followup_caption_html,
    ta_signal_forecast_summary_line,
    ta_signal_caption_html,
    ta_signal_scenario_line_html,
    should_skip_noise_signal,
    ta_signal_compact_block,
    ta_telegram_breakdown_html,
    ta_telegram_caption_html,
    TAAnalysisResult,
    ForecastPath,
    ConsolidationZone,
)


def _bar(
    i: int,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    base_ts: float = 1_700_000_000.0,
) -> KlineBar:
    return KlineBar(
        open_time=base_ts + i * 300,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
    )


def _trend_up_bars(n: int = 40) -> list[KlineBar]:
    bars: list[KlineBar] = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + 0.4
        bars.append(_bar(i, o, c + 0.2, o - 0.1, c))
        price = c
    return bars


def test_find_swing_points_detects_local_extrema() -> None:
    bars = [
        _bar(0, 10, 11, 9, 10),
        _bar(1, 10, 12, 9.5, 11),
        _bar(2, 11, 13, 10, 12),
        _bar(3, 12, 12.5, 11, 11.5),
        _bar(4, 11.5, 12, 10, 10.5),
        _bar(5, 10.5, 11, 9, 9.5),
    ]
    swings = find_swing_points(bars, window=1)
    kinds = {s.kind for s in swings}
    assert "high" in kinds
    assert "low" in kinds


def test_hammer_pattern() -> None:
    bars = _trend_up_bars(20)
    bars.append(_bar(20, 120.0, 120.5, 115.0, 120.2))
    patterns = detect_candle_patterns(bars)
    assert any(p.name == "hammer" for p in patterns)


def test_consolidation_narrow_range() -> None:
    bars = _trend_up_bars(25)
    base = 110.0
    for i in range(18):
        o = base + (i % 3) * 0.1
        bars.append(_bar(25 + i, o, o + 0.15, o - 0.15, o + 0.05))
    zone = detect_consolidation(bars, lookback=18)
    assert zone is not None
    assert zone.top - zone.bottom < zone.top * 0.04


def test_classify_structure_bullish() -> None:
    swings = find_swing_points(_trend_up_bars(50))
    label = classify_structure(swings)
    assert "бычья" in label or "боковая" in label


def test_run_ta_analysis_returns_levels_and_verdict() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="ETHUSDT")
    assert ta.verdict in {"LONG", "SHORT", "WAIT"}
    assert 1 <= ta.verdict_confidence <= 9
    assert ta.invalidation_price is not None
    assert len(ta.rulers) >= 1
    assert isinstance(ta.trader_plan, list)
    assert ta.bullish_scenario is not None or ta.bearish_scenario is not None


def _descending_channel_bars(n: int = 50) -> list[KlineBar]:
    bars: list[KlineBar] = []
    for i in range(n):
        mid = 100.0 - i * 0.35
        o = mid + 0.1
        c = mid - 0.1
        bars.append(_bar(i, o, mid + 0.5, mid - 0.5, c))
    return bars


def test_detect_channel_bear() -> None:
    bars = _descending_channel_bars(40)
    swings = find_swing_points(bars, window=2)
    channel = detect_channel(bars, swings)
    assert channel is not None
    assert channel.kind == "bear"


def test_detect_price_zones() -> None:
    bars = _trend_up_bars(50)
    swings = find_swing_points(bars, window=2)
    zones = detect_price_zones(bars, swings)
    assert isinstance(zones, list)


def test_bullish_scenario_on_uptrend() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="ETHUSDT")
    assert ta.bullish_scenario is not None
    assert ta.breakout_level is not None


def test_post_pump_local_range() -> None:
    bars: list[KlineBar] = []
    price = 0.00030
    for i in range(35):
        price *= 1.012
        o = price / 1.012
        bars.append(_bar(i, o, price * 1.004, o * 0.998, price))
    peak = price
    for i in range(18):
        wobble = 1.0 + (i % 4) * 0.0015 - 0.002
        price = peak * wobble
        bars.append(_bar(35 + i, price * 0.999, price * 1.002, price * 0.997, price))
    assert detect_post_pump_phase(bars)
    swings = find_swing_points(bars, window=2)
    levels = []
    zones = []
    key_levels = []
    breakout, breakdown, cons, post_pump = resolve_trade_triggers(
        bars, swings, levels, zones, key_levels,
    )
    assert post_pump
    assert breakout is not None and breakdown is not None
    if cons is not None:
        assert cons.top > cons.bottom
    if breakout and breakdown:
        assert breakout > breakdown


def test_post_pump_breakout_does_not_chase_spike() -> None:
    """После пампа LONG-триггер не должен уезжать на хай последней зелёной свечи."""
    bars: list[KlineBar] = []
    price = 1.0
    for i in range(28):
        price *= 1.015
        o = price / 1.015
        bars.append(_bar(i, o, price * 1.003, o * 0.998, price))
    # Консолидация вокруг ~1.5
    base = price
    for i in range(16):
        w = 1.0 + ((i % 5) - 2) * 0.002
        c = base * w
        bars.append(_bar(28 + i, c * 0.999, c * 1.004, c * 0.996, c))
    cons_high_before = max(b.high for b in bars[-16:])
    # Импульс выше локального хая (как EVAA 3.017 → 3.07+)
    spike = cons_high_before * 1.025
    bars.append(_bar(44, cons_high_before * 0.999, spike, cons_high_before * 0.997, spike * 0.998))
    bars.append(_bar(45, spike * 0.997, spike * 1.01, spike * 0.995, spike))

    assert detect_post_pump_phase(bars)
    swings = find_swing_points(bars, window=2)
    breakout, breakdown, cons, post_pump = resolve_trade_triggers(
        bars, swings, [], [], [],
    )
    assert post_pump
    current = bars[-1].close
    # Триггер не должен преследовать хай импульса.
    if breakout is not None:
        assert breakout < current * 1.002
        assert breakout <= cons_high_before * 1.012
    if cons is not None and breakout is not None:
        assert breakout <= cons.top * 1.002
    if breakdown is not None and breakout is not None:
        assert breakdown < breakout


def test_primary_forecast_direction() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT", neutral=True)
    assert primary_forecast_direction(ta) in {"long", "short", "neutral"}


def test_primary_forecast_wait_ignores_priority() -> None:
    from bot.ta_analysis import TAAnalysisResult

    ta = TAAnalysisResult(verdict="WAIT", action_priority="short", verdict_confidence=8)
    assert primary_forecast_direction(ta) == "neutral"
    assert primary_forecast_direction(
        TAAnalysisResult(verdict="SHORT", action_priority="long")
    ) == "short"


def test_manual_detailed_has_distances() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT", neutral=True)
    text = ta_manual_detailed_html(ta)
    assert "📍" in text
    assert "👉" in text or "Действие" in text


def test_ta_signal_caption_html() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT")
    caption = ta_signal_caption_html(ta, signal_side="long", compact=False)
    assert "📐 TA" in caption
    assert "▶️" in caption

    compact = ta_signal_caption_html(ta, signal_side="long", compact=True)
    assert "LONG" in compact.upper() or "Готов" in compact
    assert compact.count("\n") <= 3


def test_signal_caption_aligns_with_short_signal() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=False, symbol="ETHUSDT", neutral=True)
    caption = ta_signal_caption_html(ta, signal_side="short")
    assert "SHORT" in caption.upper()
    assert "ждём подтверждения" not in caption.lower()


def test_should_skip_noise_bad_rr() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_reason="вход невыгоден · плохой R:R",
        current_price=1.0,
        breakout_level=1.05,
    )
    skip, reason = should_skip_noise_signal(ta, "long", 3.0)
    assert skip is True
    assert "R:R" in reason


def test_should_skip_scanner_vs_ta_priority() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="short",
        current_price=0.35,
        breakdown_level=0.32,
        breakout_level=0.36,
    )
    skip, reason = should_skip_noise_signal(ta, "long", 2.0, signal_type="reversal_pump")
    assert skip is True
    assert "приоритет" in reason


def test_signal_compact_wait_long() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        current_price=0.19,
        breakout_level=0.1942,
        consolidation=ConsolidationZone(top=0.1942, bottom=0.18, start_idx=0, end_idx=5, label="range"),
        post_pump=True,
    )
    text = ta_signal_compact_block(
        ta,
        signal_side="long",
        readiness=(False, "TA ждёт пробой уровня"),
        signal_type="reversal_pump",
    )
    assert "Ждать LONG" in text
    assert "0.1942" in text
    assert "ждём подтверждения" not in text.lower()


def test_detect_liq_cascade_short() -> None:
    from bot.ta_range_trade import TaFactorContext, detect_liq_cascade_short

    factors = TaFactorContext(
        cvd_ratio=0.32,
        cvd_detail="CVD↓ продажи 68% объёма",
        liq_detail="",
        liq_long_boost=0,
        liq_short_boost=2,
        factor_lines=[],
    )
    liq = {
        "long_liq_usd": 520_000.0,
        "short_liq_usd": 12_000.0,
        "total_usd": 532_000.0,
        "window_minutes": 5,
    }
    sig = detect_liq_cascade_short(
        factors=factors,
        liq=liq,
        momentum="down",
        momentum_pct=-3.5,
        drawdown_pct=28.0,
        oi_narrative="aligned_short",
    )
    assert sig.active is True
    assert sig.side == "short"
    assert sig.strength >= 7


def test_manual_post_pump_below_trigger_not_active() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="long",
        verdict_confidence=8,
        current_price=0.05874,
        breakout_level=0.05995,
        breakdown_level=0.05727,
        invalidation_price=0.05698,
        target_prices=[0.06064],
        post_pump=True,
        flow_correction=42,
        flow_continuation=42,
        momentum_label="импульс вверх +3.1%",
        forecast_summary="Базовый сценарий: коррекция.",
        cvd_delta=-881_800.0,
        cvd_source="live",
    )
    text = ta_manual_detailed_html(ta)
    assert "WAIT" in text
    assert "активен" not in text.lower()
    assert "пробой" in text.lower()
    intent = ta_user_intent_html(ta, "long")
    assert "НЕ входить" in intent or "ждите" in intent.lower()


def test_ta_manual_detailed_html() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT", neutral=True)
    text = ta_manual_detailed_html(ta)
    assert "📍" in text
    assert "👉" in text


def test_ta_telegram_caption_html() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT")
    caption = ta_telegram_caption_html(ta)
    assert "TA" in caption or "WAIT" in caption or "LONG" in caption
    assert ta.verdict in caption


def test_detect_recent_momentum_down() -> None:
    bars = _trend_up_bars(25)
    base = bars[-1].close
    for i in range(8):
        c = base - (i + 1) * 0.8
        bars.append(_bar(25 + i, base - i * 0.8, base - i * 0.8 + 0.1, c - 0.2, c))
    momentum, pct = detect_recent_momentum(bars)
    assert momentum == "down"
    assert pct < 0


def test_neutral_ta_short_priority_after_dump() -> None:
    bars: list[KlineBar] = []
    price = 0.007
    for i in range(35):
        price *= 1.025
        o = price / 1.025
        bars.append(_bar(i, o, price * 1.01, o * 0.99, price))
    for i in range(15):
        price *= 0.985
        o = price / 0.985
        bars.append(_bar(35 + i, o, o * 1.005, price * 0.995, price))
    ta = run_ta_analysis(bars, symbol="VANRYUSDT", neutral=True)
    assert ta.market_bias in {"медвежий", "нейтральный"}
    assert ta.action_priority in {"short", "neutral"} or ta.verdict == "SHORT"


def test_neutral_ta_has_setup_clarity() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="ETHUSDT", neutral=True)
    assert ta.setup_clarity >= 3
    assert ta.professional_summary
    assert isinstance(ta.risk_notes, list)


def test_ta_breakdown_html_sections() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="VANRYUSDT", neutral=True)
    text = ta_telegram_breakdown_html(ta, symbol="VANRYUSDT", interval="15m")
    assert "WAIT" in text or "LONG" in text or "SHORT" in text
    assert ta_display_score(ta) >= 1


def test_post_pump_long_signal_hides_far_short_priority() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="short",
        current_price=1.6463,
        breakdown_level=1.4822,
        breakout_level=1.6520,
        post_pump=True,
        factor_lines=["CVD↑ покупки 73% объёма"],
    )
    line = ta_signal_scenario_line_html(ta, signal_side="long")
    assert "приоритет SHORT" not in line
    assert "LONG" in line


def test_continuation_followup_omits_correction_forecast() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=9,
        current_price=1.6463,
        breakout_level=1.6520,
        breakdown_level=1.4822,
        post_pump=True,
        factor_lines=["CVD↑ покупки 73% объёма"],
        forecast_summary="коррекция после пампа",
    )
    text = ta_scenario_followup_caption_html(ta, "continuation_confirmed", "long")
    assert "LONG" in text
    assert "1.6520" in text or "1.652" in text


def test_readiness_badge_ignores_scanner_timing_for_reversal() -> None:
    from bot.ta_analysis import evaluate_entry_readiness

    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=9,
        current_price=0.34740,
        breakdown_level=0.34760,
        dist_to_short_pct=0.06,
    )
    ready, reason = evaluate_entry_readiness(
        ta,
        "long",
        1,
        check_scanner_timing=False,
        signal_type="reversal_pump",
    )
    assert ready, reason


def test_reversal_pump_short_not_ready_when_price_above_trigger() -> None:
    from bot.ta_analysis import evaluate_entry_readiness

    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=9,
        current_price=0.3570,
        breakdown_level=0.34760,
        momentum_pct=1.5,
        momentum_label="импульс вверх",
    )
    ready, reason = evaluate_entry_readiness(
        ta,
        "long",
        2,
        check_scanner_timing=False,
        signal_type="reversal_pump",
    )
    assert not ready
    assert "отскок" in reason or "пробой" in reason or "выше" in reason
    assert "рано для входа" not in reason


def test_readiness_filter_still_checks_scanner_timing() -> None:
    from bot.ta_analysis import evaluate_entry_readiness

    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=9,
        current_price=0.34740,
        breakdown_level=0.34760,
        dist_to_short_pct=0.06,
    )
    ready, reason = evaluate_entry_readiness(
        ta,
        "short",
        1,
        check_scanner_timing=True,
        signal_type="reversal_dump",
    )
    assert not ready
    assert "рано для входа" in reason


def test_megadump_short_can_be_ready_on_armed_trigger() -> None:
    from bot.ta_analysis import evaluate_entry_readiness

    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=9,
        current_price=3.0640,
        breakdown_level=3.0490,
        momentum_pct=-1.2,
        momentum_label="импульс вниз",
    )
    ready, reason = evaluate_entry_readiness(
        ta,
        "short",
        3,
        check_scanner_timing=False,
        signal_type="mega_dump",
    )
    assert ready, reason


def test_megadump_short_scenario_hides_not_now_when_armed() -> None:
    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=9,
        current_price=3.0640,
        breakdown_level=3.0490,
        invalidation_price=3.4743,
        target_prices=[2.6890],
    )
    line = ta_signal_scenario_line_html(ta, signal_side="short", signal_type="mega_dump")
    assert "не сейчас" not in line
    assert "Открывать SHORT" in line


def test_regular_short_near_trigger_is_ready_even_before_exact_touch() -> None:
    from bot.ta_analysis import evaluate_entry_readiness

    ta = TAAnalysisResult(
        verdict="SHORT",
        verdict_confidence=8,
        current_price=3.0550,
        breakdown_level=3.0490,
        momentum_pct=-0.4,
    )
    ready, reason = evaluate_entry_readiness(
        ta,
        "short",
        4,
        check_scanner_timing=False,
        signal_type="dump",
    )
    assert ready, reason
    assert "SHORT-триггера" in reason or "пробой" in reason or "цена у триггера" in reason


def test_regular_long_near_trigger_is_ready_even_before_exact_touch() -> None:
    from bot.ta_analysis import evaluate_entry_readiness

    ta = TAAnalysisResult(
        verdict="LONG",
        verdict_confidence=8,
        current_price=3.0430,
        breakout_level=3.0490,
        momentum_pct=0.3,
    )
    ready, reason = evaluate_entry_readiness(
        ta,
        "long",
        4,
        check_scanner_timing=False,
        signal_type="pump",
    )
    assert ready, reason
    assert "LONG-триггера" in reason or "пробой" in reason or "цена у триггера" in reason


def test_plain_forecast_aligns_with_short_verdict() -> None:
    from bot.ta_range_trade import MarketFlowScores
    from bot.ta_analysis import build_ta_signal_narrative

    flow = MarketFlowScores(
        continuation=55,
        correction=48,
        convergence="weak",
        notes=["CVD: нейтрально", "OI: aligned_short"],
    )
    plain, plan, basis = build_ta_signal_narrative(
        verdict="SHORT",
        current=3.75,
        target_prices=[3.58],
        invalidation_price=3.9436,
        breakout_level=None,
        breakdown_level=3.706,
        bullish=None,
        bearish=None,
        flow=flow,
        correction_path=ForecastPath(
            kind="correction",
            label="коррекция ↓",
            waypoints=[3.75, 3.74, 3.60, 3.58],
            confidence=6,
            reason="откат к 3.60",
        ),
        continuation_path=ForecastPath(
            kind="continuation",
            label="продолжение ↑",
            waypoints=[3.75, 3.74, 3.76, 3.924],
            confidence=7,
            reason="рост к 3.924",
        ),
        factor_lines=["CVD нейтр. (48% buy)"],
        phase_label="коррекция",
        structure_label="LH + LL (медв.)",
        momentum_label="импульс вниз",
        oi_narrative_label="шорты набираются",
        market_bias="медвежий",
        range_trade_label="SHORT от сопротивления range",
        primary_scenario="снижение к 3.58",
        verdict_reason="медвежья структура + пробой",
    )
    assert "SHORT" in plain
    assert "3.58" in plain
    assert "продолжение вверх" not in plain.lower()
    assert "SHORT" in plan
    assert "CVD" in basis or "OI" in basis
    assert "поток" in basis


def test_long_verdict_not_mixed_with_short_range_label() -> None:
    from bot.ta_analysis import (
        evaluate_entry_readiness,
        ta_range_trade_opposes_verdict,
        ta_signal_compact_block,
        ta_signal_scenario_line_html,
    )

    ta = TAAnalysisResult(
        verdict="LONG",
        verdict_confidence=7,
        current_price=0.07245,
        breakout_level=0.07256,
        breakdown_level=0.07149,
        invalidation_price=0.07113,
        target_prices=[0.07332],
        entry_mode="breakout",
        range_trade_label="",
        range_trade_direction="",
        momentum_pct=1.0,
    )
    assert ta_range_trade_opposes_verdict(ta) is False
    ta_conflict = TAAnalysisResult(
        verdict="LONG",
        verdict_confidence=7,
        current_price=0.07245,
        breakout_level=0.07256,
        invalidation_price=0.07361,
        target_prices=[0.06900],
        entry_mode="range_edge",
        range_trade_label="SHORT от сопротивления range",
        range_trade_direction="short",
        momentum_pct=1.0,
    )
    assert ta_range_trade_opposes_verdict(ta_conflict)
    line = ta_signal_scenario_line_html(ta_conflict, signal_side="long", signal_type="reversal_pump")
    assert "SHORT от сопротивления" not in line
    compact = ta_signal_compact_block(
        ta_conflict,
        signal_side="long",
        readiness=(False, "range-сетап против вердикта TA"),
        signal_type="reversal_pump",
    )
    assert "Не по графику" in compact
    assert "SHORT от сопротивления range" in compact
    ready, reason = evaluate_entry_readiness(
        ta_conflict,
        "long",
        2,
        check_scanner_timing=False,
        signal_type="reversal_pump",
    )
    assert not ready
    assert "range" in reason.lower()


def test_pulse_wait_allows_armed_long_readiness() -> None:
    from bot.ta_analysis import evaluate_entry_readiness

    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=7,
        action_priority="long",
        current_price=0.795,
        breakout_level=0.797,
        invalidation_price=0.765,
        post_pump=True,
        momentum_pct=1.9,
        momentum_label="импульс вверх +1.9%",
    )
    ready, reason = evaluate_entry_readiness(
        ta,
        "long",
        2,
        check_scanner_timing=False,
        signal_type="pulse_pump",
        accept_armed=True,
    )
    assert ready, reason


def test_trade_quality_guard_rejects_inverted_long_levels() -> None:
    from bot.ta_analysis import _trade_quality_guard

    bad, reason = _trade_quality_guard(
        verdict="LONG",
        current=0.07245,
        stop=0.07361,
        targets=[0.06900],
    )
    assert bad
    assert "некорректен" in reason.lower() or "ниже" in reason.lower()
