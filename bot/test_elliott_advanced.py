"""Тесты PPT-расширений: треугольники, WXY, Fib-проекции волны 5."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.elliott_advanced import (
    detect_double_triple_three,
    detect_expanding_triangle,
    detect_horizontal_triangle,
    project_wave5_fib_targets,
)
from bot.elliott_wave import ElliottImpulse, ElliottPoint
from bot.ta_analysis import SwingPoint


def _bar(i: int, p: float) -> KlineBar:
    return KlineBar(
        open_time=1_700_000_000 + i * 300,
        open=p,
        high=p + 0.3,
        low=p - 0.3,
        close=p,
        volume=1000.0,
    )


def test_contracting_triangle_detect() -> None:
    # A=100, B=110, C=103, D=108, E=104 — схождение, breakout LONG
    swings = [
        SwingPoint(10, 100.0, "low"),
        SwingPoint(20, 110.0, "high"),
        SwingPoint(30, 103.0, "low"),
        SwingPoint(40, 108.0, "high"),
        SwingPoint(50, 104.5, "low"),
    ]
    bars = [_bar(i, 105.0) for i in range(60)]
    tri = detect_horizontal_triangle(swings, bars, direction="down")
    assert tri is not None
    assert tri.kind == "contracting"
    assert tri.breakout_bias == "long"
    assert [p.label for p in tri.points] == ["A", "B", "C", "D", "E"]


def test_expanding_triangle_detect() -> None:
    swings = [
        SwingPoint(10, 100.0, "low"),
        SwingPoint(20, 108.0, "high"),
        SwingPoint(30, 96.0, "low"),
        SwingPoint(40, 114.0, "high"),
        SwingPoint(50, 90.0, "low"),
    ]
    bars = [_bar(i, 100.0) for i in range(60)]
    tri = detect_expanding_triangle(swings, bars, direction="down")
    assert tri is not None
    assert tri.kind == "expanding"
    assert tri.breakout_bias == "long"


def test_double_three_detect() -> None:
    # боковой W-X-Y
    swings = [
        SwingPoint(5, 100.0, "low"),
        SwingPoint(10, 106.0, "high"),
        SwingPoint(15, 101.0, "low"),
        SwingPoint(20, 105.5, "high"),
        SwingPoint(25, 100.5, "low"),
    ]
    bars = [_bar(i, 103.0) for i in range(40)]
    c = detect_double_triple_three(swings, bars, prior_trend="long")
    assert c is not None
    assert c.kind == "double_three"
    assert c.resume_bias == "long"


def test_wave5_fib_targets_extension3() -> None:
    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 8, 104.0),
        ElliottPoint("3", 20, 128.0),
        ElliottPoint("4", 24, 116.0),
    ]
    imp = ElliottImpulse(
        direction="up",
        points=pts,
        current_wave="4",
        extension="3",
        valid=True,
        quality=70,
    )
    targets = project_wave5_fib_targets(imp)
    assert targets
    # w5=1.00×w1 от 4: 116+10=126
    assert any(abs(t.price - 126.0) < 0.01 and t.source == "extension_3" for t in targets)
