"""Поиск sweep-шаблона и smoke ghost-баров."""
from __future__ import annotations

import matplotlib.pyplot as plt

from .chart_ed_story_history import find_liquidity_sweep_template, synthetic_sweep_template
from .test_range_breakdown_retest import _bar, _consolidation_bars


def test_find_or_synthetic_sweep():
    floor, ceil = 100.0, 104.0
    bars = _consolidation_bars(floor=floor, ceil=ceil, n=60)
    t = 1_700_100_000.0
    extra = []
    for j in range(8):
        c = ceil * (1.0 + 0.002 * j)
        extra.append(_bar(t + j * 900, c * 0.998, c * 1.012, c * 0.995, c * 0.999))
    for j in range(6):
        c = ceil * (1.0 - 0.008 * (j + 1))
        extra.append(_bar(t + (8 + j) * 900, c * 1.004, c * 1.006, c * 0.992, c * 0.996))
    hist = bars + extra
    found = find_liquidity_sweep_template(hist, resistance=ceil)
    assert found is not None or synthetic_sweep_template(resistance=ceil, floor=floor, tp=96.0).ohlc


def test_ghost_draw_smoke():
    from .chart_ed_story_history import draw_ghost_bars_forward

    floor, ceil = 100.0, 104.0
    bars = _consolidation_bars(floor=floor, ceil=ceil, n=40)
    tpl = synthetic_sweep_template(resistance=ceil, floor=floor, tp=96.0)
    fig, ax = plt.subplots()
    ax.set_xlim(0, 1)
    ax.set_ylim(95, 106)
    draw_ghost_bars_forward(ax, bars, tpl, interval_minutes=15, resistance=ceil, tp=96.0)
    plt.close(fig)
