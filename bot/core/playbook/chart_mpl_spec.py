"""Matplotlib layers from ChartSpec (manual/signal annotated PNG)."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .chart_spec import ChartSpec
from ...bybit_klines import KlineBar
from ...chart_display_policy import chart_entry_zone_tags_enabled, chart_teaching_tags_enabled
from ...chart_ed_story import _visible_x_span
from ...range_breakdown_retest import get_rbr_from_ta
from ...ta_analysis import TAAnalysisResult

_KIND_COLOR = {
    "range_top": "#f0c040",
    "range_bottom": "#3fb950",
    "break_up": "#58a6ff",
    "break_down": "#ff7b72",
}


def draw_mpl_spec_core(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    spec: ChartSpec,
    *,
    levels_only: bool = False,
) -> int:
    if not bars:
        return 0
    x0, x1 = _visible_x_span(ax, bars)
    drawn = 0
    tops: list[float] = []
    bots: list[float] = []
    for lv in spec.levels:
        if lv.price <= 0:
            continue
        color = _KIND_COLOR.get(lv.kind, "#8b949e")
        ax.hlines(lv.price, x0, x1, colors=color, linewidth=1.35, alpha=0.88, zorder=5)
        drawn += 1
        if lv.kind == "range_top":
            tops.append(lv.price)
        if lv.kind == "range_bottom":
            bots.append(lv.price)
        if chart_teaching_tags_enabled() and lv.label:
            tag = lv.label[:12]
            ax.text(x0, lv.price, f"  {tag}", color=color, fontsize=7, va="center", zorder=6)

    if not levels_only and spec.show_range and tops and bots:
        ax.add_patch(
            Rectangle(
                (x0, min(bots)),
                max(x1 - x0, 0.001),
                max(tops) - min(bots),
                facecolor="#8b949e",
                alpha=0.08,
                zorder=2,
            )
        )
        drawn += 1

    rbr = get_rbr_from_ta(ta)
    if levels_only:
        return drawn

    if rbr and spec.rbr_phase == "fade_top":
        resistance = max(tops) if tops else float(rbr.get("range_top") or 0)
        if resistance > 0:
            el = float(rbr.get("entry_lo") or resistance * 0.985)
            eh = float(rbr.get("entry_hi") or resistance * 1.006)
            z_lo, z_hi = min(el, resistance * 0.998), max(eh, resistance * 1.002)
            ax.add_patch(
                Rectangle(
                    (x0 + (x1 - x0) * 0.55, z_lo),
                    (x1 - x0) * 0.42,
                    z_hi - z_lo,
                    facecolor="#f0c040",
                    alpha=0.12,
                    zorder=3,
                )
            )
            drawn += 1
            if chart_teaching_tags_enabled() and chart_entry_zone_tags_enabled():
                ax.text(x0, z_hi, "  ЗОНА", color="#f0c040", fontsize=7, va="bottom", zorder=6)
    elif rbr and spec.rbr_phase == "retest":
        floor = min(bots) if bots else float(rbr.get("range_bottom") or 0)
        if floor > 0:
            el = float(rbr.get("entry_lo") or floor * 0.996)
            eh = float(rbr.get("entry_hi") or floor * 1.01)
            ax.hlines(eh, x0, x1, colors="#58a6ff", linewidth=1.4, alpha=0.9, zorder=5)
            ax.hlines(el, x0, x1, colors="#58a6ff", linewidth=1.4, alpha=0.9, zorder=5)
            if chart_teaching_tags_enabled():
                ax.text(x0, eh, "  RETEST", color="#58a6ff", fontsize=7, va="bottom", zorder=6)
            drawn += 1
    return drawn
