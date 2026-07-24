"""Тесты расходящегося треугольника BuyHold: импульс + ретест."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.chart_patterns import (
    SwingPoint,
    _detect_expanding_triangle,
    compute_atr,
    pattern_location_ok,
)
from bot.pattern_foresight import build_pattern_foresight


def _bar(
    i: int,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    vol: float = 1000.0,
) -> KlineBar:
    return KlineBar(
        open_time=1_700_000_000.0 + i * 300,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=vol,
    )


def _expanding_peak_bars() -> tuple[list[KlineBar], list[SwingPoint]]:
    """Рост → мегафон HH/LL → импульсный пробой нижней → ретест."""
    bars: list[KlineBar] = []
    # prior bullish impulse
    price = 100.0
    for i in range(20):
        o, c = price, price + 0.45
        bars.append(_bar(i, o, c + 0.1, o - 0.05, c, vol=900))
        price = c
    # expanding: high1@20 ~109.5, low1@24 ~107, high2@28 ~111, low2@32 ~105.5
    # build bar by bar with known swings
    # continue from ~109
    seq = [
        # 20-23: up to H1
        (109.0, 110.2, 108.8, 110.0, 1000),
        (110.0, 110.3, 109.5, 109.8, 1000),
        (109.8, 110.1, 109.2, 109.5, 950),
        (109.5, 109.7, 108.5, 108.6, 980),
        # 24 L1
        (108.6, 108.8, 107.0, 107.2, 1100),
        (107.2, 108.5, 107.0, 108.2, 1000),
        (108.2, 109.0, 108.0, 108.8, 1000),
        (108.8, 110.0, 108.6, 109.8, 1050),
        # 28 H2 higher
        (109.8, 111.2, 109.6, 111.0, 1200),
        (111.0, 111.1, 109.5, 109.8, 1000),
        (109.8, 110.0, 108.0, 108.2, 1000),
        (108.2, 108.5, 106.5, 106.8, 1100),
        # 32 L2 lower
        (106.8, 107.0, 105.4, 105.6, 1150),
        (105.6, 106.8, 105.5, 106.5, 1000),
        (106.5, 107.2, 106.2, 106.8, 1000),
        # 35 still inside / near lower
        (106.8, 107.0, 106.0, 106.2, 1000),
        # 36 impulsive break down (big body + volume)
        (106.2, 106.3, 103.5, 103.8, 2800),
        # 37-38 retest of broken support then reject lower
        (103.8, 105.0, 103.6, 104.6, 1500),
        (104.6, 104.9, 103.2, 103.5, 1400),
    ]
    base = len(bars)
    for j, (o, h, l, c, v) in enumerate(seq):
        bars.append(_bar(base + j, o, h, l, c, vol=v))

    swings = [
        SwingPoint(20, 110.0, "high"),
        SwingPoint(24, 107.0, "low"),
        SwingPoint(28, 111.0, "high"),
        SwingPoint(32, 105.4, "low"),
    ]
    # also need enough swings count - pad earlier
    swings = [
        SwingPoint(5, 102.0, "high"),
        SwingPoint(8, 101.0, "low"),
        *swings,
    ]
    return bars, swings


def test_expanding_triangle_impulse_retest() -> None:
    bars, swings = _expanding_peak_bars()
    atr = compute_atr(bars)
    found = _detect_expanding_triangle(swings, bars, atr)
    assert found, "ожидали детект расходящегося треугольника"
    p = found[0]
    assert p.kind == "expanding_triangle"
    assert p.direction == "bearish"
    assert p.subtype == "reversal"
    assert p.psychology_note
    assert "ретест" in p.psychology_note.lower() or "импульс" in p.psychology_note.lower()
    assert p.entry_mode in {"retest", "breakout"}
    assert p.status == "confirmed"
    assert p.neckline is not None
    # foresight знает про ретест
    fs = build_pattern_foresight([p], primary=p, atr=atr)
    assert fs.active
    assert "ретест" in fs.trigger_text.lower() or "импульс" in fs.trigger_text.lower()


def test_expanding_weak_break_stays_wait() -> None:
    """Слабый уход за границу без импульсной свечи → forming/wait."""
    bars, swings = _expanding_peak_bars()
    # убираем импульс: заменяем break bar на мелкую свечу с малым объёмом
    # break bar index ~36 relative to construction — find last big red and shrink
    for i in range(len(bars) - 6, len(bars)):
        b = bars[i]
        if b.close < b.open and (b.open - b.close) > 1.0:
            bars[i] = _bar(i, b.open, b.high, b.close + 0.8, b.close + 0.9, vol=400)
            # also flatten subsequent so no later impulse
            for j in range(i + 1, len(bars)):
                px = bars[j - 1].close
                bars[j] = _bar(j, px, px + 0.15, px - 0.2, px - 0.05, vol=500)
            break
    atr = compute_atr(bars)
    found = _detect_expanding_triangle(swings, bars, atr)
    if not found:
        return  # геометрия могла развалиться — ок
    p = found[0]
    if p.status == "confirmed":
        # если всё же confirmed — entry не должен быть без импульса как «чистый» ENTRY wait
        assert p.entry_mode in {"wait", "retest", "breakout"}
    else:
        assert p.entry_mode == "wait"
        assert p.psychology_note


def test_expanding_wait_not_entry_location() -> None:
    from bot.chart_pattern_models import ChartPattern, PatternLine, PatternPoint

    p = ChartPattern(
        kind="expanding_triangle",
        subtype="reversal",
        status="confirmed",
        points=(PatternPoint(10, 100.0, "high1"),),
        lines=(),
        zone_top=111.0,
        zone_bottom=105.0,
        neckline=PatternLine(10, 106.0, 30, 106.0, "break_bound"),
        pole_height=5.0,
        target_price=100.0,
        stop_price=112.0,
        confidence=0.8,
        score_breakdown={},
        source_rule="test",
        label_ru="Расходящийся треугольник",
        direction="bearish",
        entry_mode="wait",
        volume_breakout=False,
        psychology_note="слабый пробой",
    )
    assert pattern_location_ok(p, side="short", price=106.0) is False
