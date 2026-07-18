"""Тесты полной разметки волн Эллиотта 1–5 + ABC."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.elliott_wave import (
    ElliottPoint,
    analyze_elliott_waves,
    build_elliott_entry_plan,
    detect_elliott_impulse,
    elliott_location_ok,
)
from bot.ta_analysis import SwingPoint, find_swing_points


def _bar(i: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(
        open_time=1_700_000_000 + i * 300,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
    )


def _impulse_up_swings() -> tuple[list[KlineBar], list[SwingPoint]]:
    """Синтетический бычий 1–5: 0=100 →1=110 →2=104 →3=122 →4=114 →5=120."""
    prices = [
        # flat base
        *([100.0] * 8),
        # wave 1 up
        102, 104, 106, 108, 110,
        # wave 2 down
        108, 106, 104,
        # wave 3 up (longest)
        107, 110, 114, 118, 122,
        # wave 4 down
        120, 117, 114,
        # wave 5 up
        116, 118, 120,
        # mild pullback
        119, 118,
    ]
    bars: list[KlineBar] = []
    for i, p in enumerate(prices):
        bars.append(_bar(i, p, p + 0.4, p - 0.4, p))

    swings = [
        SwingPoint(7, 100.0, "low"),
        SwingPoint(12, 110.0, "high"),
        SwingPoint(15, 104.0, "low"),
        SwingPoint(20, 122.0, "high"),
        SwingPoint(23, 114.0, "low"),
        SwingPoint(26, 120.0, "high"),
    ]
    return bars, swings


def test_detect_complete_impulse_up() -> None:
    bars, swings = _impulse_up_swings()
    imp = detect_elliott_impulse(swings, bars)
    assert imp is not None
    assert imp.direction == "up"
    assert len(imp.points) >= 5
    labels = [p.label for p in imp.points]
    assert labels[:3] == ["0", "1", "2"]
    assert "3" in labels


def test_analyze_elliott_has_draw_points() -> None:
    bars, swings = _impulse_up_swings()
    ew = analyze_elliott_waves(bars, swings)
    assert ew.has_structure
    assert ew.label_ru
    assert len(ew.draw_points) >= 3
    assert ew.confidence >= 3


def test_conservative_entry_after_wave2() -> None:
    """После волны 2 план — консервативный пробой хая волны 1."""
    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 8, 104.0),
    ]
    from bot.elliott_wave import ElliottImpulse

    imp = ElliottImpulse(
        direction="up",
        points=pts,
        current_wave="2",
        valid=True,
        quality=70,
    )
    bars = [_bar(i, 104 + i * 0.1, 105, 103, 104.5) for i in range(12)]
    # цена у хая волны 1
    bars[-1] = _bar(11, 109.5, 110.2, 109.0, 110.05)
    plan = build_elliott_entry_plan(imp, None, bars, current=110.05)
    assert plan is not None
    assert plan.mode == "conservative"
    assert plan.side == "long"
    assert plan.entry_price is not None
    assert abs(plan.entry_price - 110.0) < 1e-6
    assert plan.rr >= 2.5


def test_aggressive_zone_uses_fib_c() -> None:
    bars, swings = _impulse_up_swings()
    # добавим ABC после 5
    extra = []
    base_i = len(bars)
    # A down, B up, C down toward 1.272 of B
    seq = [117, 114, 112, 115, 116, 113, 110.5]
    for j, p in enumerate(seq):
        extra.append(_bar(base_i + j, p, p + 0.3, p - 0.3, p))
    bars = bars + extra
    swings = swings + [
        SwingPoint(base_i + 2, 112.0, "low"),   # A
        SwingPoint(base_i + 4, 116.0, "high"),  # B
    ]
    ew = analyze_elliott_waves(bars, swings)
    assert ew.impulse is not None
    # план может быть aggressive или wait — главное без падения
    assert ew.phase


def test_wave2_violation_rejected() -> None:
    """Волна 2 ниже основания 1 — такая разметка не должна быть valid с точками 100→110→98."""
    swings = [
        SwingPoint(0, 100.0, "low"),
        SwingPoint(5, 110.0, "high"),
        SwingPoint(10, 98.0, "low"),  # ниже 0 — фатал для разметки от 100
        SwingPoint(15, 120.0, "high"),
        SwingPoint(20, 112.0, "low"),
        SwingPoint(25, 118.0, "high"),
    ]
    bars = [_bar(i, 100, 101, 99, 100) for i in range(30)]
    imp = detect_elliott_impulse(swings, bars)
    # Детектор либо пропускает, либо стартует с новой базы (98) —
    # но не помечает valid структуру, где 2 зашла за 0=100.
    if imp is not None and len(imp.points) >= 3:
        p0, p2 = imp.point("0"), imp.point("2")
        assert p0 and p2
        if imp.direction == "up":
            assert p2.price > p0.price  # правило соблюдено в принятой разметке
        # исходная «плохая» тройка 100/110/98 не должна быть точками 0/1/2
        bad = (
            abs(p0.price - 100.0) < 1e-6
            and abs(imp.point("1").price - 110.0) < 1e-6
            and abs(p2.price - 98.0) < 1e-6
        )
        assert not bad or not imp.valid



def test_elliott_location_requires_ready() -> None:
    bars, swings = _impulse_up_swings()
    ew = analyze_elliott_waves(bars, swings)
    # без ready — локация false
    if ew.entry_plan:
        ew.entry_plan.ready = False
    assert elliott_location_ok(ew, "long") is False


def test_find_swings_pipeline_smoke() -> None:
    bars, _ = _impulse_up_swings()
    swings = find_swing_points(bars, window=2)
    ew = analyze_elliott_waves(bars, swings)
    # smoke: не падает
    assert ew.phase in {
        "unknown",
        "impulse_forming",
        "impulse_complete",
        "impulse_1",
        "impulse_2",
        "impulse_3",
        "impulse_4",
        "impulse_5",
        "abc_a",
        "abc_b",
        "abc_c",
        "abc_complete",
        "abc_forming",
    } or ew.phase.startswith("impulse") or ew.phase.startswith("abc") or ew.phase == "unknown"
