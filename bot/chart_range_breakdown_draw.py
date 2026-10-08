"""Сценарий RBR на PNG: retest вниз или «шорт от верха» после импульса."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _last_impulse_bar_index(bars: list[KlineBar], *, lookback: int = 14) -> int:
    if not bars:
        return 0
    lb = min(lookback, len(bars))
    start = len(bars) - lb
    best_i = len(bars) - 1
    best_range = 0.0
    for i in range(start, len(bars)):
        r = float(bars[i].high) - float(bars[i].low)
        if r > best_range:
            best_range = r
            best_i = i
    return best_i


def _draw_fade_top_resistance_story(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    floor: float,
    ceil: float,
    targets: list[float],
    ed_story: bool = False,
) -> None:
    """После импульса: одна ясная зона сопр. + путь «откат → тест верха» (как ручной TV)."""
    i0 = max(0, len(bars) - min(len(bars), 96))
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    pad = max(ceil * 0.0025, (ceil - floor) * 0.04)
    z_bot, z_top = ceil - pad * 2.2, ceil + pad * 0.8

    ax.add_patch(
        Rectangle(
            (x0, z_bot),
            x1 - x0,
            z_top - z_bot,
            facecolor="#d29922",
            edgecolor="#e3b341",
            alpha=0.22,
            linewidth=1.4,
            zorder=3,
        )
    )
    ax.hlines(ceil, x0, x1, colors="#e3b341", linewidth=2.0, alpha=0.95, zorder=4)
    ax.text(
        x0 + (x1 - x0) * 0.02,
        z_top,
        f"  зона сопр. {fmt_price(ceil)}  ",
        color="#e3b341",
        fontsize=8,
        fontweight="bold",
        va="bottom",
        ha="left",
        zorder=5,
        bbox=dict(boxstyle="round,pad=0.2", facecolor="#161b22", edgecolor="#e3b341", alpha=0.92),
    )

    imp_i = _last_impulse_bar_index(bars)
    x_imp = mdates.date2num(_idx_to_date(bars, imp_i))
    y_imp = float(bars[imp_i].high)
    cur = float(bars[-1].close)
    x_cur = mdates.date2num(_idx_to_date(bars, len(bars) - 1))

    dip = min(cur, y_imp * 0.992, ceil * 0.97)
    path_x = [x_imp, x_imp + (x_cur - x_imp) * 0.35, x_imp + (x_cur - x_imp) * 0.65, x1]
    path_y = [y_imp, dip, ceil * 0.996, ceil * 0.999]
    ax.plot(path_x, path_y, color="#e6edf3", linewidth=1.5, linestyle="-", alpha=0.88, zorder=6)
    ax.annotate(
        "",
        xy=(path_x[-1], path_y[-1]),
        xytext=(path_x[-2], path_y[-2]),
        arrowprops=dict(arrowstyle="->", color="#e6edf3", lw=1.8, alpha=0.95),
        zorder=7,
    )
    ax.scatter([path_x[-1]], [ceil], s=55, facecolors="none", edgecolors="#f0883e", linewidths=2.2, zorder=8)
    ax.text(
        path_x[-1],
        ceil * 1.004,
        "  реакция? → шорт  ",
        color="#f0883e",
        fontsize=7.5,
        fontweight="bold",
        ha="center",
        va="bottom",
        zorder=9,
        bbox=dict(boxstyle="round,pad=0.18", facecolor="#161b22", edgecolor="#f0883e", alpha=0.94),
    )

    if not ed_story:
        ax.text(
            x0,
            floor,
            f"  пол {fmt_price(floor)}  ",
            color="#8b949e",
            fontsize=6.8,
            va="top",
            ha="left",
            zorder=4,
        )
        if targets:
            tp_note = " → ".join(fmt_price(float(t)) for t in targets[:2])
            ax.text(
                0.02,
                0.04,
                f"Цели после закрепа под полом: {tp_note}",
                transform=ax.transAxes,
                color="#3fb950",
                fontsize=6.8,
                va="bottom",
                ha="left",
                zorder=10,
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#0d1117cc", edgecolor="#238636", alpha=0.9),
            )


def _draw_breakdown_retest_path(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    floor: float,
    ceil: float,
    tp: float,
) -> None:
    i0 = max(0, len(bars) - min(len(bars), 80))
    i_mid = max(i0 + 1, len(bars) - 24)
    i1 = len(bars) - 1
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x_mid = mdates.date2num(_idx_to_date(bars, i_mid))
    x1 = mdates.date2num(_idx_to_date(bars, i1))

    ax.plot([x0, x_mid], [ceil, ceil], color="#8b949e", linewidth=1.0, linestyle="--", alpha=0.75, zorder=4)
    ax.text(
        x0,
        ceil,
        f"  потолок {fmt_price(ceil)} / пол {fmt_price(floor)}  ",
        color="#8b949e",
        fontsize=6.8,
        va="bottom",
    )
    ax.plot([x_mid, x1], [floor, floor], color="#f85149", linewidth=1.2, alpha=0.85, zorder=4)

    ymin, ymax = ax.get_ylim()
    margin = max((ymax - ymin) * 0.04, floor * 0.002)
    path_x = [x_mid, x_mid + (x1 - x_mid) * 0.35, x_mid + (x1 - x_mid) * 0.55]
    path_y = [floor * 1.012, floor * 0.992, floor * 1.006]
    ax.plot(path_x, path_y, color="#58a6ff", linewidth=1.3, linestyle=":", alpha=0.85, zorder=5)
    ax.annotate(
        "",
        xy=(path_x[-1], path_y[-1]),
        xytext=(path_x[-2], path_y[-2]),
        arrowprops=dict(arrowstyle="->", color="#58a6ff", lw=1.4, alpha=0.9),
        zorder=6,
    )
    ax.text(
        path_x[1],
        path_y[1],
        "  retest  ",
        color="#e6edf3",
        fontsize=7,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.15", facecolor="#161b22", edgecolor="#58a6ff", alpha=0.9),
    )
    if tp >= ymin + margin:
        ax.annotate(
            "",
            xy=(x1, tp),
            xytext=(path_x[-1], path_y[-1]),
            arrowprops=dict(arrowstyle="->", color="#3fb950", lw=1.2, alpha=0.75),
            zorder=5,
        )
        ax.text(x1, tp, f"  цель ≈ {fmt_price(tp)}  ", color="#3fb950", fontsize=7, fontweight="bold", va="top")
    else:
        ax.text(
            0.02,
            0.05,
            f"Цель сценария (ниже экрана): {fmt_price(tp)}",
            transform=ax.transAxes,
            color="#3fb950",
            fontsize=6.8,
            va="bottom",
            ha="left",
            zorder=10,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#0d1117cc", edgecolor="#238636", alpha=0.9),
        )


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
    if floor <= 0 or ceil <= floor:
        return

    phase = str(rbr.get("phase") or "")
    targets = [float(t) for t in (rbr.get("targets") or []) if t]

    if phase in {"fade_top", "await_break"}:
        _draw_fade_top_resistance_story(ax, bars, floor=floor, ceil=ceil, targets=targets)
        return

    if tp <= 0:
        return
    _draw_breakdown_retest_path(ax, bars, floor=floor, ceil=ceil, tp=tp)
