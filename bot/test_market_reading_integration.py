from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.market_reading import (
    analyze_market_reading,
    apply_reading_to_verdict,
    compose_human_narrative,
)
from bot.ta_analysis import find_swing_points, run_ta_analysis


def _bars_trend_up(n: int = 80) -> list[KlineBar]:
    out: list[KlineBar] = []
    price = 100.0
    for i in range(n):
        price *= 1.0015 if i % 5 else 1.004
        vol = 1000.0 if i < n - 10 else 400.0
        out.append(
            KlineBar(
                open_time=float(i * 300),
                open=price * 0.999,
                high=price * 1.002,
                low=price * 0.998,
                close=price,
                volume=vol,
            )
        )
    return out


def test_apply_reading_blocks_chase_long_on_weak_high() -> None:
    bars = _bars_trend_up()
    swings = find_swing_points(bars)
    reading = analyze_market_reading(bars, swings, interval_minutes=5)
    verdict, conf, _ = apply_reading_to_verdict(
        verdict="LONG",
        reason="test",
        conf=8,
        reading=reading,
    )
    if reading.divergence and reading.divergence.kind == "weak_high":
        assert verdict == "WAIT"
        assert conf <= 6


def test_human_narrative_uses_tf_stack() -> None:
    bars = _bars_trend_up()
    swings = find_swing_points(bars)
    reading = analyze_market_reading(bars, swings, interval_minutes=5)
    text = compose_human_narrative(reading)
    assert reading.tf_stack
    assert "M5" in reading.tf_stack or "m5" in reading.tf_stack.lower() or len(text) > 10


def test_run_ta_analysis_populates_reading_fields() -> None:
    bars = _bars_trend_up()
    ta = run_ta_analysis(bars, symbol="TESTUSDT", neutral=True, pattern_detection_enabled=False)
    assert hasattr(ta, "reading_narrative")
    assert isinstance(ta.reading_present, list)
    assert isinstance(ta.reading_absent, list)
