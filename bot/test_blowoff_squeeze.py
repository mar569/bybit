from __future__ import annotations

from bot.blowoff_squeeze import (
    apply_blowoff_squeeze_context,
    higher_high_rising_volume,
    volume_climax_near_high,
)
from bot.bybit_klines import KlineBar
from bot.market_reading import MarketReading, TimeframeRead
from bot.ta_analysis import find_swing_points


def _bar(i: int, price: float, vol: float) -> KlineBar:
    return KlineBar(
        open_time=float(i * 300),
        open=price * 0.999,
        high=price * 1.001,
        low=price * 0.998,
        close=price,
        volume=vol,
    )


def test_volume_climax_near_high() -> None:
    bars = [_bar(i, 100 + i * 0.1, 1000.0) for i in range(20)]
    bars[-1] = _bar(19, 102.0, 5000.0)
    ok, note = volume_climax_near_high(bars)
    assert ok
    assert "пиковый объём" in note


def test_early_trap_blocks_short() -> None:
    bars = [_bar(i, 100 + i * 0.5, 1000.0 + i * 50) for i in range(30)]
    swings = find_swing_points(bars)
    reading = MarketReading(
        working=TimeframeRead("M5", "bullish", "M5: HH+HL вверх"),
        live_scenario="continuation",
        seek_label="у хая",
    )
    out, verdict, _, reason, pri = apply_blowoff_squeeze_context(
        reading,
        bars=bars,
        swings=swings,
        verdict="SHORT",
        conf=8,
        reason="",
        action_priority="short",
        momentum_pct=3.5,
        range_position=0.92,
        drawdown_from_high_pct=0.5,
        rsi_div=None,
        liq_context=None,
        market_metrics={"funding_rate": 0.0012},
    )
    assert verdict == "WAIT"
    assert pri == "short"
    assert "squeeze" in reason.lower() or "ранний" in reason.lower()
    assert any("кульминация" in x.lower() for x in out.absent)
