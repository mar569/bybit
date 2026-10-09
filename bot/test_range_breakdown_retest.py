"""Синтетика для playbook range → break → retest."""
from __future__ import annotations

from dataclasses import dataclass

from .bybit_klines import KlineBar
from .range_breakdown_retest import evaluate_range_breakdown_retest


@dataclass
class _Box:
    top: float
    bottom: float


def _bar(t: float, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(open_time=t, open=o, high=h, low=l, close=c, volume=1.0)


def _consolidation_bars(
    floor: float = 176.0,
    ceil: float = 182.0,
    n: int = 40,
    *,
    break_down: bool = False,
    retest: bool = False,
) -> list[KlineBar]:
    bars: list[KlineBar] = []
    t = 1_700_000_000.0
    for i in range(n):
        mid = (floor + ceil) / 2
        h, l = ceil * 0.999, floor * 1.001
        c = mid + (i % 5 - 2) * (ceil - floor) * 0.02
        bars.append(_bar(t + i * 1800, mid, h, l, c))
    if break_down:
        for j in range(8):
            c = floor * (0.992 - j * 0.001)
            bars.append(_bar(t + (n + j) * 1800, floor, floor * 1.002, c * 0.998, c))
    if retest:
        c = floor * 1.004
        bars.append(_bar(t + (n + 8) * 1800, c * 0.998, c * 1.006, floor * 0.995, c))
    return bars


def test_rbr_retest_phase():
    floor, ceil = 176.0, 182.0
    bars = _consolidation_bars(floor=floor, ceil=ceil, break_down=True, retest=True)
    current = float(bars[-1].close)
    setup = evaluate_range_breakdown_retest(
        bars,
        consolidation=_Box(top=ceil, bottom=floor),
        breakdown=floor,
        breakout=ceil,
        post_pump=True,
        current=current,
    )
    assert setup is not None
    assert setup.phase == "retest"
    assert setup.direction == "short"
    assert setup.swing_low_target < floor


def test_rbr_retest_stop_not_at_wide_ceil():
    floor, ceil = 0.10737, 0.12361
    bars = _consolidation_bars(floor=floor, ceil=ceil, break_down=True, retest=True)
    current = float(bars[-1].close)
    setup = evaluate_range_breakdown_retest(
        bars,
        consolidation=_Box(top=ceil, bottom=floor),
        breakdown=floor,
        breakout=ceil,
        post_pump=True,
        current=current,
    )
    if setup is None:
        return
    assert setup.stop < ceil * 1.002
    assert setup.stop <= floor * 1.03


def test_rbr_suppressed_on_repeat_spike_at_support():
    floor, ceil = 0.105, 0.118
    bars = _consolidation_bars(floor=floor, ceil=ceil, break_down=True, retest=True)
    current = floor * 1.005
    bars[-1] = _bar(bars[-1].open_time, current, current * 1.002, floor * 0.999, current)
    setup = evaluate_range_breakdown_retest(
        bars,
        consolidation=_Box(top=ceil, bottom=floor),
        breakdown=floor,
        breakout=ceil,
        post_pump=True,
        current=current,
        repeat_spike_dump_risk=True,
    )
    assert setup is None


def test_rbr_fade_top_phase():
    floor, ceil = 176.0, 182.0
    bars = _consolidation_bars(floor=floor, ceil=ceil, n=50)
    current = ceil * 0.995
    bars[-1] = _bar(bars[-1].open_time, current, ceil * 1.002, floor, current)
    setup = evaluate_range_breakdown_retest(
        bars,
        consolidation=_Box(top=ceil, bottom=floor),
        breakdown=floor,
        breakout=ceil,
        post_pump=False,
        current=current,
    )
    assert setup is not None
    assert setup.phase == "fade_top"
    assert floor in setup.targets or setup.targets[0] <= ceil


def test_rbr_fade_top_not_await_break_after_floor_wicks():
    """Цена у потолка не должна получать «ждём пробой», если в хвосте был wick под пол."""
    floor, ceil = 1.410, 1.430
    bars = _consolidation_bars(floor=floor, ceil=ceil, n=36, break_down=True)
    current = ceil * 0.997
    bars[-1] = _bar(bars[-1].open_time + 9000, current * 0.996, ceil * 1.004, floor * 1.002, current)
    setup = evaluate_range_breakdown_retest(
        bars,
        consolidation=_Box(top=ceil, bottom=floor),
        breakdown=floor,
        breakout=ceil,
        post_pump=True,
        current=current,
    )
    assert setup is not None
    assert setup.phase == "fade_top"
