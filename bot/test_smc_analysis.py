from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.smc_analysis import (
    analyze_smc,
    detect_fair_value_gaps,
    format_smc_compact_html,
    smc_strength_from_dict,
    smc_verdict_boost,
)
from bot.ta_analysis import find_swing_points


def _bar(
    i: int,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    base_ts: float = 1_700_000_000.0,
    step: int = 300,
) -> KlineBar:
    return KlineBar(
        open_time=base_ts + i * step,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
    )


def _bullish_fvg_bars() -> list[KlineBar]:
    return [
        _bar(0, 100, 101, 99.5, 100.5),
        _bar(1, 100.5, 103, 100, 102.5),
        _bar(2, 102.5, 105, 102, 104.5),
    ]


def test_detect_bullish_fvg() -> None:
    bars = _bullish_fvg_bars()
    gaps = detect_fair_value_gaps(bars, lookback=10)
    assert any(g.direction == "bullish" for g in gaps)


def test_analyze_smc_returns_checklist() -> None:
    bars: list[KlineBar] = []
    price = 100.0
    for i in range(30):
        o = price
        c = price - 0.3
        bars.append(_bar(i, o, o + 0.1, c - 0.2, c))
        price = c
    for i in range(30, 40):
        o = price
        c = price + 0.5
        bars.append(_bar(i, o, c + 0.2, o - 0.1, c))
        price = c
    smc = analyze_smc(bars)
    assert len(smc.checklist) == 6
    assert 0 <= smc.smc_score <= 10


def test_smc_verdict_boost_ready_pattern() -> None:
    from bot.smc_analysis import SmcContext

    smc = SmcContext(reversal_ready=True, reversal_direction="long")
    assert smc_verdict_boost(smc, is_long=True) >= 2


def test_format_smc_compact_html() -> None:
    from bot.smc_analysis import SmcContext

    smc = SmcContext(
        checklist=[("Слом структуры (BOS)", True), ("Расширение структуры", False)],
        smc_score=5,
        summary="тест",
    )
    html = format_smc_compact_html(smc)
    assert "да" in html
    assert "нет" in html


def test_smc_strength_from_dict() -> None:
    strength = smc_strength_from_dict(
        {"smc_score": 8, "reversal_ready": True, "reversal_direction": "long"},
        is_long=True,
    )
    assert strength > 0.7


def test_find_swing_points_used_by_smc() -> None:
    bars = _bullish_fvg_bars()
    swings = find_swing_points(bars, window=1)
    assert swings
