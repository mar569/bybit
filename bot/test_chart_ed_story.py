"""Режим ed_story_chart: RBR + отступ вправо под forward-блок."""
from __future__ import annotations

import matplotlib.pyplot as plt

from .chart_ed_story import ED_STORY_TRAILING, draw_ed_story_layers, use_ed_story_chart
from .chart_renderer import _apply_display_zoom, _render_chart_figure
from .test_range_breakdown_retest import _Box, _bar, _consolidation_bars
from .range_breakdown_retest import evaluate_range_breakdown_retest
from .ta_analysis import TAAnalysisResult, run_ta_analysis


def _ta_with_rbr(phase: str) -> TAAnalysisResult | None:
    floor, ceil = 0.0032, 0.0042
    if phase == "fade_top":
        bars = _consolidation_bars(floor=floor, ceil=ceil, n=48)
        last = bars[-1]
        bars = bars[:-1] + [
            _bar(last.open_time, ceil * 0.98, ceil * 1.02, ceil * 0.96, ceil * 0.99),
        ]
    else:
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
        return None
    if phase == "fade_top" and setup.phase not in {"fade_top", "await_break"}:
        setup.phase = "fade_top"
    ta = run_ta_analysis(bars, symbol="FIGHTUSDT", interval_minutes=15)
    mm = dict(getattr(ta, "market_metrics", None) or {})
    mm["range_breakdown_retest"] = setup.to_dict()
    ta.market_metrics = mm
    return ta


def test_use_ed_story_for_rbr_phases():
    for ph in ("fade_top", "retest"):
        ta = _ta_with_rbr(ph)
        assert ta is not None
        assert use_ed_story_chart(ta)


def test_ed_story_trailing_fits_forward_box():
    ta = _ta_with_rbr("fade_top")
    assert ta is not None
    floor, ceil = 0.0032, 0.0042
    bars = _consolidation_bars(floor=floor, ceil=ceil, n=48)
    fig, ax = plt.subplots(figsize=(10, 5))
    _apply_display_zoom(ax, bars, display_hours=12, interval_minutes=15, trailing=ED_STORY_TRAILING)
    draw_ed_story_layers(ax, bars, ta)
    x0, x1 = ax.get_xlim()
    assert x1 > x0
    assert (x1 - x0) / max(x1 - x0, 1e-9) >= 0.99
    plt.close(fig)


def test_render_ed_story_png_smoke():
    ta = _ta_with_rbr("fade_top")
    assert ta is not None
    floor, ceil = 0.0032, 0.0042
    bars = _consolidation_bars(floor=floor, ceil=ceil, n=48)
    png = _render_chart_figure(
        bars,
        ta,
        symbol="FIGHT",
        title_suffix="test",
        accent_color="#f85149",
        interval_minutes=15,
        signal_chart=True,
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
