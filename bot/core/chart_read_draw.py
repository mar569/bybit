"""Отрисовка ChartRead на matplotlib (сценарий, entry/SL/TP)."""
from __future__ import annotations

from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ..bybit_klines import KlineBar
from ..chart_label_layout import LabelBoard, format_level_text
from ..ta_analysis import TAAnalysisResult, fmt_price
from .chart_read import ChartRead


def _x(bars: list[KlineBar], idx: int) -> float:
    idx = max(0, min(idx, len(bars) - 1))
    dt = datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)
    return mdates.date2num(dt)


def seed_board_from_read(board: LabelBoard, read: ChartRead, *, ref: float) -> None:
    board.reserve(ref)
    colors = {"R": "#d29922", "S": "#58a6ff", "BOS": "#f0c040", "MSS": "#f0c040"}
    for lv in read.levels:
        key = lv.label.split()[0] if lv.label else ""
        col = colors.get(key, "#8b949e")
        board.add(lv.price, format_level_text(lv.label, lv.price), col, priority=lv.priority, ref=ref)


def draw_scenario_waypoints_mpl(
    ax: plt.Axes,
    bars: list[KlineBar],
    waypoints: list[float],
    *,
    label: str = "",
) -> None:
    if not bars or len(waypoints) < 2:
        return
    xs = [_x(bars, len(bars) - 1 - max(0, (len(waypoints) - 1 - i) * 8)) for i in range(len(waypoints))]
    xs[-1] = _x(bars, len(bars) - 1)
    if len(waypoints) == 2:
        xs = [_x(bars, max(0, len(bars) - 24)), _x(bars, len(bars) - 1)]
    ys = [float(p) for p in waypoints if p > 0]
    if len(ys) < 2:
        return
    ax.plot(
        xs[: len(ys)],
        ys,
        color="#a371f7",
        linewidth=1.4,
        linestyle=(0, (4, 3)),
        alpha=0.92,
        zorder=5,
        marker="o",
        markersize=3,
    )
    if label:
        ax.text(
            xs[-1],
            ys[-1],
            f" {label[:28]}",
            color="#a371f7",
            fontsize=6.5,
            va="center",
            zorder=6,
        )


def draw_entry_bands_if_ready_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult, read: ChartRead) -> None:
    if not read.draw_entry_bands:
        return
    try:
        from ..chart_pro_visual import _draw_trade_plan_mpl

        _draw_trade_plan_mpl(ax, bars, ta)
    except Exception:
        pass


def apply_chart_read_to_canvas(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    read: ChartRead,
    board: LabelBoard,
) -> None:
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    from ..chart_display_policy import ed_chart_scenario_path_enabled

    if ed_chart_scenario_path_enabled():
        if read.scenario_waypoints and len(read.scenario_waypoints) >= 2:
            draw_scenario_waypoints_mpl(ax, bars, read.scenario_waypoints, label=read.scenario_label_ru)
        else:
            from ..chart_readable import draw_probable_path

            draw_probable_path(ax, bars, ta)
    draw_entry_bands_if_ready_mpl(ax, bars, ta, read)
    if read.setup_hint_ru:
        from ..chart_display_policy import chart_pdf_setup_hint_enabled

        if chart_pdf_setup_hint_enabled():
            ax.text(
                0.5,
                0.98,
                read.setup_hint_ru[:140],
                transform=ax.transAxes,
                ha="center",
                va="top",
                color="#e6edf3",
                fontsize=7.2,
                zorder=15,
                bbox=dict(boxstyle="round,pad=0.35", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.94),
            )
