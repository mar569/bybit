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
    ta_scenario_followup_caption_html,
    ta_signal_caption_html,
    ta_signal_scenario_line_html,
    ta_telegram_breakdown_html,
    ta_telegram_caption_html,
    TAAnalysisResult,
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
    assert cons is not None
    assert breakout is not None and breakdown is not None
    if breakout and breakdown:
        assert breakout > breakdown


def test_primary_forecast_direction() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT", neutral=True)
    assert primary_forecast_direction(ta) in {"long", "short", "neutral"}


def test_manual_detailed_has_distances() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT", neutral=True)
    text = ta_manual_detailed_html(ta)
    assert "📍" in text
    assert "👉" in text or "Действие" in text


def test_ta_signal_caption_html() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=True, symbol="BTCUSDT")
    caption = ta_signal_caption_html(ta, signal_side="long")
    assert "📐 TA" in caption
    assert "▶️" in caption
    assert caption.count("\n") == 1


def test_signal_caption_aligns_with_short_signal() -> None:
    bars = _trend_up_bars(60)
    ta = run_ta_analysis(bars, is_long=False, symbol="ETHUSDT", neutral=True)
    caption = ta_signal_caption_html(ta, signal_side="short")
    assert "сигнал SHORT" in caption
    assert "Сигнал <b>SHORT</b>" in caption or "Открывать SHORT" in caption


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
    assert "Простыми словами" not in text
    assert "снят" in text.lower()
    assert "LONG" in text
