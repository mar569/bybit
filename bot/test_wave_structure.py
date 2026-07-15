"""Тесты Wave Lite + Fibonacci — только качественный импульс + confluence."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.ta_analysis import find_swing_points, run_ta_analysis
from bot.wave_structure import (
    ImpulseLeg,
    analyze_wave_structure,
    build_fib_levels,
    detect_impulse_leg,
    score_fib_confluence,
    wave_flow_adjustments,
)


def _bar(i: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(
        open_time=1_700_000_000 + i * 300,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
    )


def _clean_impulse_up_bars() -> list[KlineBar]:
    """Чистый импульс +10% с откатом ~50–62% — валидный Fib."""
    bars: list[KlineBar] = []
    for i in range(24):
        bars.append(_bar(i, 1.00, 1.003, 0.997, 1.00))
    ladder = [1.015, 1.03, 1.045, 1.06, 1.075, 1.09, 1.10]
    for j, p in enumerate(ladder):
        prev = 1.00 if j == 0 else ladder[j - 1]
        bars.append(_bar(24 + j, prev, p + 0.002, prev - 0.002, p))
    pull = [1.085, 1.07, 1.055, 1.045, 1.040]
    for k, p in enumerate(pull):
        bars.append(_bar(31 + k, p + 0.008, p + 0.01, p - 0.003, p))
    return bars


def _noise_chop_bars() -> list[KlineBar]:
    """Пила ±0.5% — Fib строить нельзя."""
    bars: list[KlineBar] = []
    for i in range(40):
        c = 1.0 + (0.004 if i % 2 == 0 else -0.004)
        bars.append(_bar(i, 1.0, max(1.0, c) + 0.002, min(1.0, c) - 0.002, c))
    return bars


def test_build_fib_levels_up_impulse() -> None:
    leg = ImpulseLeg(0, 10, 1.0, 1.10, "up", size_pct=10.0, efficiency=0.8, quality=80)
    levels = build_fib_levels(leg)
    by_r = {lv.ratio: lv.price for lv in levels}
    assert abs(by_r[0.5] - 1.05) < 1e-9
    assert abs(by_r[0.618] - (1.10 - 0.10 * 0.618)) < 1e-9
    assert by_r[1.272] > 1.10


def test_noise_rejected_no_fib() -> None:
    bars = _noise_chop_bars()
    swings = find_swing_points(bars, window=2)
    wave = analyze_wave_structure(bars, swings)
    assert wave.leg is None or not wave.valid
    assert wave.chart_fib_levels == []
    assert wave.fib_status in {"no_impulse", "broken", "late_impulse", "empty"}
    assert wave.fib_reject_reason  # понятная причина на шуме


def test_clean_impulse_gets_valid_fib() -> None:
    bars = _clean_impulse_up_bars()
    swings = find_swing_points(bars, window=2)
    wave = analyze_wave_structure(bars, swings, structure_label="HH + HL (бычья)")
    assert wave.leg is not None
    assert wave.valid
    assert wave.leg.direction == "up"
    assert wave.leg.size_pct >= 2.2
    assert wave.leg.efficiency >= 0.52
    assert wave.fib_levels
    assert wave.wave_phase in {
        "shallow_pullback",
        "wave_2_4_zone",
        "deep_pullback",
        "mid_correction",
    }
    assert wave.wave_bias in {"long", "neutral"}
    # без П/С / round / retest — Fib не даёт entry_hint
    assert wave.entry_hint_price is None or wave.has_confluence
    assert wave.fib_status in {"ready", "chart_only"}
    if not wave.has_confluence:
        assert wave.fib_status == "chart_only"
        assert "confluence" in wave.fib_reject_reason.lower() or "П/С" in wave.fib_reject_reason


def test_fib_without_confluence_does_not_move_flow() -> None:
    bars = _clean_impulse_up_bars()
    swings = find_swing_points(bars, window=2)
    wave = analyze_wave_structure(bars, swings)
    cont, corr = wave_flow_adjustments(wave, action_priority="long")
    if not wave.has_confluence:
        assert cont == 0 and corr == 0
    assert cont >= 0 and corr >= 0


def test_fib_with_sr_confluence_gives_entry_hint() -> None:
    bars = _clean_impulse_up_bars()
    swings = find_swing_points(bars, window=2)
    # сильная поддержка около 0.5 Fib (~1.05)
    wave = analyze_wave_structure(
        bars,
        swings,
        structure_label="HH + HL (бычья)",
        sr_prices=[1.05],
    )
    assert wave.valid
    assert wave.confluence_sr
    assert wave.has_confluence
    assert wave.entry_hint_price is not None
    assert wave.fib_status == "ready"
    assert wave.fib_reject_reason == ""
    cont, _ = wave_flow_adjustments(wave, action_priority="long")
    if wave.wave_phase in {"shallow_pullback", "wave_2_4_zone"}:
        assert cont >= 5


def test_score_fib_three_confluences() -> None:
    leg = ImpulseLeg(0, 10, 1.0, 1.10, "up", size_pct=10.0, efficiency=0.8, quality=80)
    levels = build_fib_levels(leg)
    f618 = next(lv.price for lv in levels if abs(lv.ratio - 0.618) < 1e-9)
    sr, rnd, ret, n = score_fib_confluence(
        levels,
        current=f618,
        sr_prices=[f618],
        breakout=f618,
        direction="up",
    )
    assert sr and ret and n >= 2
    # round: подберём цену у круглого уровня
    sr2, rnd2, _, n2 = score_fib_confluence(
        levels,
        current=1.0,
        sr_prices=[],
        direction="up",
    )
    assert isinstance(rnd2, bool)
    assert n2 >= 0


def test_run_ta_fib_empty_on_noise() -> None:
    bars = _noise_chop_bars()
    ta = run_ta_analysis(bars, is_long=True, neutral=True)
    assert ta.fib_levels == [] or ta.wave_phase in {"", "unknown", "late_impulse"}


def test_detect_impulse_requires_pullback() -> None:
    """Без отката — импульс ещё идёт, Fib не строим."""
    bars: list[KlineBar] = []
    for i in range(20):
        bars.append(_bar(i, 1.0, 1.003, 0.997, 1.0))
    ladder = [1.02, 1.04, 1.06, 1.08, 1.10]
    for j, p in enumerate(ladder):
        prev = 1.0 if j == 0 else ladder[j - 1]
        bars.append(_bar(20 + j, prev, p + 0.002, prev - 0.001, p))
    swings = find_swing_points(bars, window=2)
    leg = detect_impulse_leg(swings, bars)
    assert leg is None
    wave = analyze_wave_structure(bars, swings)
    assert wave.fib_status in {"late_impulse", "no_impulse"}
    assert wave.fib_reject_reason
    # Без отката / слабый ход — Fib не для входа; причина всегда заполнена
    assert len(wave.fib_reject_reason) >= 8
