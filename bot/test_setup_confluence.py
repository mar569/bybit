"""Тесты Pro confluence: HTF EW + ABCDE + score/grade."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.setup_confluence import (
    SetupConfluence,
    analyze_setup_confluence,
    confluence_boosts_gate,
    detect_abcde_correction,
    detect_ending_diagonal,
)
from bot.elliott_wave import ElliottImpulse, ElliottPoint
from bot.ta_analysis import SwingPoint


def _bar(i: int, price: float, *, step: int = 300) -> KlineBar:
    t = 1_700_000_000 + i * step
    return KlineBar(
        open_time=t,
        open=price,
        high=price * 1.001,
        low=price * 0.999,
        close=price,
        volume=100.0,
    )


def _swing(idx: int, price: float, kind: str) -> SwingPoint:
    return SwingPoint(index=idx, price=price, kind=kind)


def test_abcde_converging_down() -> None:
    # 0 high + A low B high C low D high E low (схождение)
    prices = [100, 90, 96, 91, 94, 91.5]
    kinds = ["high", "low", "high", "low", "high", "low"]
    swings = [_swing(i * 3, p, k) for i, (p, k) in enumerate(zip(prices, kinds))]
    bars = [_bar(i, 95.0) for i in range(20)]
    abcde = detect_abcde_correction(swings, bars, direction="down")
    assert abcde is not None
    assert abcde.valid
    assert len(abcde.points) == 5
    assert [p.label for p in abcde.points] == ["A", "B", "C", "D", "E"]


def test_ending_diagonal_with_wedge_kind() -> None:
    pts = [
        ElliottPoint("0", 0, 100),
        ElliottPoint("1", 1, 110),
        ElliottPoint("2", 2, 104),
        ElliottPoint("3", 3, 118),
        ElliottPoint("4", 4, 112),
        ElliottPoint("5", 5, 116),
    ]
    impulse = ElliottImpulse(
        direction="up",
        points=pts,
        current_wave="5",
        valid=True,
    )

    class _Pat:
        kind = "wedge_rising"

    assert detect_ending_diagonal(impulse, _Pat()) is True


def test_confluence_boosts_gate_a() -> None:
    setup = SetupConfluence(score=80, grade="A", side="long", ideal_ready=True, htf_bias="long")
    pts, notes = confluence_boosts_gate(setup, "long")
    assert pts >= 18
    assert any("confluence A" in n for n in notes)


def test_confluence_penalty_against_side() -> None:
    setup = SetupConfluence(score=75, grade="A", side="short", ideal_ready=False)
    pts, notes = confluence_boosts_gate(setup, "long")
    assert pts < 0
    assert any("против" in n for n in notes)


def test_analyze_setup_confluence_runs() -> None:
    prices = [100 + i * 0.5 for i in range(40)]
    bars = [_bar(i, p) for i, p in enumerate(prices)]
    swings = [
        _swing(2, 101, "low"),
        _swing(8, 106, "high"),
        _swing(14, 103, "low"),
        _swing(22, 112, "high"),
        _swing(28, 108, "low"),
        _swing(35, 118, "high"),
    ]
    htf = [_bar(i, 100 + i * 1.2, step=3600) for i in range(30)]
    result = analyze_setup_confluence(bars, swings, htf_bars=htf, current=prices[-1])
    assert 0 <= result.score <= 100
    assert result.grade in {"A", "B", "C", "D"}
    assert result.side in {"long", "short", "neutral"}
