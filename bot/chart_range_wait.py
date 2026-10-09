"""WAIT у границ диапазона: два коридора + forward-tool, без day min / SMC / GiP."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_ed_story import ED_STORY_TRAILING, _draw_flow_strip, _visible_x_span
from .chart_position_boxes import draw_forward_long_projection, draw_forward_short_projection
from .ta_analysis import TAAnalysisResult, fmt_price

CHART_TEXT = "#e6edf3"
CORRIDOR_RES = "#f0c040"
CORRIDOR_SUP = "#3fb950"


def use_range_wait_chart(ta: TAAnalysisResult) -> bool:
    from .chart_ed_story import use_ed_story_chart

    if use_ed_story_chart(ta):
        return False
    if (getattr(ta, "verdict", "") or "").upper() != "WAIT":
        return False
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk <= 0 or brdn <= 0 or brk <= brdn:
        return False
    cur = float(getattr(ta, "current_price", 0) or 0)
    if cur <= 0:
        return False
    width = brk - brdn
    return brdn * 0.992 <= cur <= brk * 1.012 or abs(cur - brk) / width <= 0.35


def _draw_level_corridor(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    level: float,
    color: str,
    label: str,
    pad_frac: float = 0.0018,
) -> None:
    x0, x1 = _visible_x_span(ax, bars)
    pad = max(level * pad_frac, 0.0001)
    lo, hi = level - pad * 1.2, level + pad * 0.8
    ax.hlines(hi, x0, x1, colors=color, linewidth=1.85, alpha=0.95, zorder=5)
    ax.hlines(lo, x0, x1, colors=color, linewidth=1.85, alpha=0.95, zorder=5)
    ax.text(
        x0 + (x1 - x0) * 0.01,
        hi,
        f"  {label} {fmt_price(lo)}–{fmt_price(hi)}  ",
        color=color,
        fontsize=7.6,
        fontweight="bold",
        va="bottom",
        ha="left",
        zorder=6,
    )


def _draw_range_caption(ax: plt.Axes, ta: TAAnalysisResult) -> None:
    from .human_trade_brief import preferred_trade_side

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    side = preferred_trade_side(ta)
    if side == "long":
        line = f"Ждём закреп выше {fmt_price(brk)} · ниже {fmt_price(brdn)} — идея лонга off"
    elif side == "short":
        line = f"Между {fmt_price(brdn)}–{fmt_price(brk)} · шорт только после отказа у верха"
    else:
        line = f"Коридор {fmt_price(brdn)}–{fmt_price(brk)} — ждём выход из диапазона"
    ax.text(
        0.5,
        0.975,
        line,
        transform=ax.transAxes,
        va="top",
        ha="center",
        color=CHART_TEXT,
        fontsize=7.5,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.96),
    )


def draw_range_wait_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
) -> None:
    draw_range_wait_layers_minimal(ax, bars, ta, interval_minutes=interval_minutes, board=None)


def draw_range_wait_layers_minimal(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
    board=None,
) -> None:
    _ = interval_minutes
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk <= 0 or brdn <= 0 or brk <= brdn:
        return
    x0, x1 = _visible_x_span(ax, bars)
    ax.add_patch(
        Rectangle(
            (x0, brdn), max(x1 - x0, 0.001), brk - brdn,
            facecolor="#8b949e", edgecolor="#484f58", alpha=0.12, linewidth=0.8, zorder=1,
        )
    )
    ax.hlines(brk, x0, x1, colors=CORRIDOR_RES, linewidth=1.35, alpha=0.85, zorder=4)
    ax.hlines(brdn, x0, x1, colors=CORRIDOR_SUP, linewidth=1.35, alpha=0.85, zorder=4)
    if board is not None:
        board.reserve(brk)
        board.reserve(brdn)
    from .human_trade_brief import preferred_trade_side
    from .range_breakdown_retest import get_rbr_from_ta

    side = preferred_trade_side(ta)
    from .chart_display_policy import chart_trade_plan_on_chart_enabled

    if chart_trade_plan_on_chart_enabled():
        if get_rbr_from_ta(ta) or side == "short":
            draw_forward_short_projection(ax, bars, ta, use_xlim=True)
        elif side == "long":
            draw_forward_long_projection(ax, bars, ta, use_xlim=True)
    _draw_range_caption(ax, ta)


def range_wait_trailing() -> float:
    return ED_STORY_TRAILING


def range_wait_chart_zoom_hours(
    ta: object,
    bars: list,
    *,
    interval_minutes: int,
    analysis_hours: int,
    configured: int | None,
) -> int:
    from .manual_ta import manual_chart_zoom_hours

    base = manual_chart_zoom_hours(
        ta,
        bars,
        interval_minutes=interval_minutes,
        analysis_hours=analysis_hours,
        configured=configured,
    )
    min_visible = {5: 24, 10: 26, 15: 30, 30: 36, 60: 42}.get(interval_minutes, 24)
    need = max(min_visible, base)
    if configured is not None and int(configured) > 0:
        need = max(need, min(int(configured), analysis_hours))
    return max(8, min(int(need), analysis_hours))


def expand_range_wait_ylim(ax: plt.Axes, ta: TAAnalysisResult) -> None:
    from .chart_position_boxes import _entry_stop_tp

    plan = _entry_stop_tp(ta)
    prices: list[float] = []
    if plan:
        _, _e, stop, tp = plan
        prices.extend([stop, tp])
    for key in ("breakout_level", "breakdown_level"):
        v = getattr(ta, key, None)
        if v:
            prices.append(float(v))
    if ta.entry_zone and len(ta.entry_zone) == 2:
        prices.extend(float(x) for x in ta.entry_zone)
    if not prices:
        return
    lo, hi = ax.get_ylim()
    pmin, pmax = min(prices), max(prices)
    pad = max((hi - lo) * 0.06, pmax * 0.002)
    ax.set_ylim(min(lo, pmin - pad), max(hi, pmax + pad))
