"""WAIT / нет story: только диапазон, триггеры, поток — без SMC и position-box."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_ed_story import _draw_flow_strip, _visible_x_span, ED_STORY_TRAILING
from .human_trade_brief import preferred_trade_side
from .ta_analysis import TAAnalysisResult, fmt_price

CHART_TEXT = "#e6edf3"


def use_observation_chart(ta: TAAnalysisResult) -> bool:
    from .chart_story_router import resolve_chart_story_kind

    return resolve_chart_story_kind(ta) == "observation"


def observation_trailing() -> float:
    return ED_STORY_TRAILING * 0.92


def _draw_range_band(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    cons = getattr(ta, "consolidation", None)
    if cons is None:
        return
    top, bot = float(cons.top), float(cons.bottom)
    if top <= bot:
        return
    x0, x1 = _visible_x_span(ax, bars)
    ax.add_patch(
        Rectangle(
            (x0, bot), x1 - x0, top - bot,
            facecolor="#8b949e", edgecolor="#8b949e", alpha=0.14, linewidth=0.8, zorder=1,
        )
    )
    ax.text(
        x0 + (x1 - x0) * 0.01, top,
        f"  диапазон {fmt_price(bot)}–{fmt_price(top)}  ",
        color="#8b949e", fontsize=7.2, fontweight="bold", va="bottom", ha="left", zorder=4,
    )


def _draw_triggers(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    x0, x1 = _visible_x_span(ax, bars)
    for price, kind, col in (
        (getattr(ta, "breakout_level", None), "лонг если ≥", "#3fb950"),
        (getattr(ta, "breakdown_level", None), "шорт если ≤", "#f85149"),
    ):
        if not price or float(price) <= 0:
            continue
        p = float(price)
        if abs(p - cur) / cur > 0.14:
            continue
        label = f"{kind} {fmt_price(p)}"
        ax.hlines(p, x0, x1, colors=col, linewidth=1.5, alpha=0.9, zorder=5)
        ax.text(x0, p, f"  {label}  ", color=col, fontsize=7, fontweight="bold", va="bottom", ha="left", zorder=6)


def _draw_caption(ax: plt.Axes, ta: TAAnalysisResult) -> None:
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    side = preferred_trade_side(ta)
    if seek:
        line = seek[:160]
    elif side == "long":
        line = "Ждём подтверждения вверх — без market у сопротивления"
    elif side == "short":
        line = "Ждём отказ / пробой — не шортить в импульс"
    else:
        line = "Наблюдение — триггер на графике"
    ax.text(
        0.5, 0.975, line,
        transform=ax.transAxes, va="top", ha="center", color=CHART_TEXT, fontsize=7.4, zorder=12,
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.96),
    )


def draw_observation_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
) -> None:
    _ = interval_minutes
    _draw_range_band(ax, bars, ta)
    _draw_flow_strip(ax, ta)
    _draw_caption(ax, ta)
