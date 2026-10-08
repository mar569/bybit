"""Стрелка сценария: верх range → пробой пола → retest → swing low."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def draw_range_breakdown_retest_path(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
) -> None:
    rbr = get_rbr_from_ta(ta)
    if not rbr or not bars:
        return
    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    tp = float(rbr.get("swing_low_target") or 0)
    if floor <= 0 or tp <= 0 or ceil <= floor:
        return

    i0 = max(0, len(bars) - min(len(bars), 80))
    i_mid = max(i0 + 1, len(bars) - 24)
    i1 = len(bars) - 1
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x_mid = mdates.date2num(_idx_to_date(bars, i_mid))
    x1 = mdates.date2num(_idx_to_date(bars, i1))

    y_top = ceil
    y_floor = floor
    y_tp = tp

    ax.plot([x0, x_mid], [y_top, y_top], color="#8b949e", linewidth=1.0, linestyle="--", alpha=0.75, zorder=4)
    ax.text(x0, y_top, f"  пол {fmt_price(y_floor)} / потолок {fmt_price(y_top)}  ", color="#8b949e", fontsize=6.8, va="bottom")

    ax.plot([x_mid, x1], [y_floor, y_floor], color="#f85149", linewidth=1.2, alpha=0.85, zorder=4)

    path_x = [x_mid, x_mid + (x1 - x_mid) * 0.35, x_mid + (x1 - x_mid) * 0.55, x1]
    path_y = [y_floor * 1.012, y_floor * 0.992, y_floor * 1.006, y_tp]
    ax.annotate(
        "",
        xy=(path_x[-1], path_y[-1]),
        xytext=(path_x[-2], path_y[-2]),
        arrowprops=dict(arrowstyle="->", color="#3fb950", lw=1.6, alpha=0.9),
        zorder=6,
    )
    ax.plot(path_x, path_y, color="#58a6ff", linewidth=1.3, linestyle=":", alpha=0.85, zorder=5)
    ax.text(
        path_x[1], path_y[1],
        "  retest  ",
        color="#e6edf3", fontsize=7, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.15", facecolor="#161b22", edgecolor="#58a6ff", alpha=0.9),
    )
    ax.text(
        path_x[-1], y_tp,
        f"  цель ≈ {fmt_price(y_tp)}  ",
        color="#3fb950", fontsize=7, fontweight="bold", va="top",
    )
