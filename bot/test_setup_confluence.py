"""Tests for price-pattern, HTF, Fib, and SMC confluence."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.setup_confluence import (
    SetupConfluence,
    analyze_setup_confluence,
    confluence_boosts_gate,
)
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
