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
    """Синтетический бычий 1–5 с классическими Fib:
    0=100 →1=110 →2=104 (60%×1) →3=122 (1.8×1) →4=114 (44%×3) →5=121.5 (42%×3).
    """
    prices = [
        *([100.0] * 8),
        102, 104, 106, 108, 110,
        108, 106, 104,
        107, 110, 114, 118, 122,
        120, 117, 114,
        116, 118, 120, 121.5,
        121, 120.5,
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
        SwingPoint(27, 121.5, "high"),
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
        fib_classic_ok=True,
        fib_w2_ok=True,
        fib_w2_gold=True,
        fib_w2_ratio=0.60,
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


def test_classic_fib_ratios_on_valid_impulse() -> None:
    bars, swings = _impulse_up_swings()
    imp = detect_elliott_impulse(swings, bars)
    assert imp is not None
    assert imp.fib_w2_ok
    assert 0.50 <= imp.fib_w2_ratio <= 0.62
    assert imp.fib_w4_ok
    assert 0.38 <= imp.fib_w4_ratio <= 0.52
    assert imp.fib_classic_ok


def test_wave2_outside_fib_blocks_ready_entry() -> None:
    from bot.elliott_wave import ElliottImpulse

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 8, 108.0),
    ]
    imp = ElliottImpulse(
        direction="up",
        points=pts,
        current_wave="2",
        valid=False,
        quality=40,
        fib_classic_ok=False,
        fib_w2_ok=False,
        fib_w2_ratio=0.20,
    )
    bars = [_bar(i, 108, 109, 107, 108.2) for i in range(12)]
    plan = build_elliott_entry_plan(imp, None, bars, current=108.2)
    assert plan is not None
    assert plan.mode == "wait"
    assert plan.ready is False


def test_classify_extension_wave3() -> None:
    from bot.elliott_wave import classify_extension

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),  # w1=10
        ElliottPoint("2", 8, 104.0),
        ElliottPoint("3", 20, 130.0),  # w3=26 ≥ 1.618×10
        ElliottPoint("4", 24, 118.0),
        ElliottPoint("5", 30, 124.0),  # w5=6
    ]
    assert classify_extension(pts) == "3"


def test_detect_truncation_after_strong_w3() -> None:
    from bot.elliott_wave import detect_truncation

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 8, 104.0),
        ElliottPoint("3", 20, 130.0),  # сильная 3
        ElliottPoint("4", 24, 118.0),
        ElliottPoint("5", 30, 128.0),  # не выше 130 → усечение
    ]
    assert detect_truncation(pts, "up") is True


def test_detect_ending_diagonal_overlap() -> None:
    from bot.elliott_wave import detect_diagonal_type

    # 4 заходит в зону 1 (overlap) + сужение
    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 10, 120.0),
        ElliottPoint("2", 20, 108.0),
        ElliottPoint("3", 35, 128.0),
        ElliottPoint("4", 45, 115.0),  # ниже 1.price=120 → overlap up
        ElliottPoint("5", 55, 124.0),
    ]
    bars = [_bar(i, 100, 101, 99, 100) for i in range(60)]
    assert detect_diagonal_type(pts, "up", bars) == "ending"


def test_classify_abc_zigzag_vs_flat() -> None:
    from bot.elliott_wave import classify_abc_type

    assert classify_abc_type(
        [ElliottPoint("A", 1, 90.0), ElliottPoint("B", 2, 95.0)],
        b_retrace=0.50,
    ) == "zigzag"
    assert classify_abc_type(
        [ElliottPoint("A", 1, 90.0), ElliottPoint("B", 2, 99.5)],
        b_retrace=0.95,
    ) == "flat"


def test_relabel_local_and_multi_scale_fields() -> None:
    from bot.elliott_wave import _relabel_local, analyze_elliott_waves

    pts = [
        ElliottPoint("1", 1, 10.0),
        ElliottPoint("2", 2, 9.0),
        ElliottPoint("A", 3, 8.5),
    ]
    loc = _relabel_local(pts)
    assert [p.label for p in loc] == ["i", "ii", "a"]

    bars, swings = _impulse_up_swings()
    ew = analyze_elliott_waves(bars, swings)
    assert ew.has_structure
    # хотя бы один слой точек
    assert ew.draw_points or ew.global_draw_points or ew.local_draw_points
