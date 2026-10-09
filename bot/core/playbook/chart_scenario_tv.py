"""RBR scenario on TV — только когда фаза уже подтверждена (не «ghost retest»)."""
from __future__ import annotations

import matplotlib.pyplot as plt

from ...chart_display_policy import chart_teaching_tags_enabled
from ...chart_range_breakdown_draw import _last_impulse_bar_index
from ...range_breakdown_retest import get_rbr_from_ta
from ...ta_analysis import TAAnalysisResult, fmt_price


def draw_rbr_scenario_narrative_tv(ax: plt.Axes, mapper: object, ta: TAAnalysisResult) -> int:
    rbr = get_rbr_from_ta(ta)
    if not rbr or not getattr(mapper, "bars", None):
        return 0
    bars = mapper.bars  # type: ignore[attr-defined]
    phase = str(rbr.get("phase") or "")
    if phase in {"await_break"}:
        return 0

    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    targets = [float(t) for t in (rbr.get("targets") or []) if t]
    tp = float(rbr.get("swing_low_target") or 0) or (targets[0] if targets else 0.0)
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    layers = 0

    if phase == "fade_top" and ceil > 0 and mapper.price_visible(ceil):  # type: ignore[attr-defined]
        imp_i = _last_impulse_bar_index(bars)
        x_imp = mapper.x(imp_i)  # type: ignore[attr-defined]
        y_imp = mapper.y(float(bars[imp_i].high))  # type: ignore[attr-defined]
        x_end = mapper.x(len(bars) - 1)  # type: ignore[attr-defined]
        y_ceil = mapper.y(ceil)  # type: ignore[attr-defined]
        ax.plot([x_imp, x_end], [y_imp, y_ceil], color="#e6edf3", linewidth=1.25, alpha=0.78, zorder=6)
        ax.scatter([x_end], [y_ceil], s=42, facecolors="none", edgecolors="#f0883e", linewidths=1.8, zorder=7)
        if chart_teaching_tags_enabled():
            ax.text(
                x_end,
                y_ceil,
                "  отказ? → вниз  ",
                color="#f0883e",
                fontsize=6.2,
                fontweight="bold",
                va="bottom",
                ha="center",
                zorder=8,
                bbox=dict(boxstyle="round,pad=0.15", facecolor="#161b22", edgecolor="#f0883e", alpha=0.92),
            )
        layers += 2
    elif phase == "retest" and floor > 0 and ceil > floor:
        i0 = max(mapper.vis_start, len(bars) - min(len(bars), 36))  # type: ignore[attr-defined]
        x0 = mapper.x(i0)  # type: ignore[attr-defined]
        x1 = mapper.x(len(bars) - 1)  # type: ignore[attr-defined]
        y_floor = mapper.y(floor)  # type: ignore[attr-defined]
        el = float(rbr.get("entry_lo") or floor * 0.996)
        eh = float(rbr.get("entry_hi") or floor * 1.01)
        mapper.hline(ax, eh, color="#58a6ff", lw=1.35, alpha=0.88)  # type: ignore[attr-defined]
        mapper.hline(ax, el, color="#58a6ff", lw=1.35, alpha=0.88)  # type: ignore[attr-defined]
        x_br = x0 + (x1 - x0) * 0.55
        path_x = [x_br, x_br + (x1 - x_br) * 0.3, x1]
        path_y = [mapper.y(floor * mult) for mult in (1.008, 0.994, 1.002)]  # type: ignore[attr-defined]
        ax.plot(path_x, path_y, color="#58a6ff", linewidth=1.2, linestyle=":", alpha=0.85, zorder=5)
        if chart_teaching_tags_enabled():
            ax.text(
                path_x[1],
                path_y[1],
                f"  retest {fmt_price(floor)}  ",
                color="#58a6ff",
                fontsize=6.2,
                fontweight="bold",
                zorder=6,
                bbox=dict(boxstyle="round,pad=0.12", facecolor="#161b22", edgecolor="#58a6ff", alpha=0.9),
            )
        layers += 3
        if tp > 0 and tp < cur and mapper.price_visible(tp):  # type: ignore[attr-defined]
            y_tp = mapper.y(tp)  # type: ignore[attr-defined]
            ax.annotate(
                "",
                xy=(x1, y_tp),
                xytext=(path_x[-1], path_y[-1]),
                arrowprops=dict(arrowstyle="->", color="#3fb950", lw=1.15, alpha=0.82),
                zorder=5,
            )
            ax.text(
                x1,
                y_tp,
                f"  T1 {fmt_price(tp)}  ",
                color="#3fb950",
                fontsize=6.2,
                fontweight="bold",
                va="top",
                zorder=6,
            )
            layers += 1
    return layers
