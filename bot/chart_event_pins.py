"""Точная привязка SMC-событий к свечам: свип на фитиле, BOS на баре закрытия."""
from __future__ import annotations

from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .chart_display_policy import chart_teaching_tags_enabled
from .ta_analysis import fmt_price

CHART_BG = "#0d1117"


def _idx_to_x(bars: list[KlineBar], idx: int) -> float:
    idx = max(0, min(idx, len(bars) - 1))
    return mdates.date2num(datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc))


def _bar_width_x(bars: list[KlineBar]) -> float:
    if len(bars) < 2:
        return 0.001
    return max(_idx_to_x(bars, 1) - _idx_to_x(bars, 0), 0.0004)


def resolve_sweep_geometry(
    bars: list[KlineBar],
    marker: object,
) -> tuple[int, float, float]:
    """(bar_idx, wick_price, liquidity_level)."""
    idx = int(getattr(marker, "index", len(bars) - 1))
    direction = str(getattr(marker, "direction", "") or "")
    ref = getattr(marker, "ref_price", None)
    level = float(ref if ref is not None else getattr(marker, "price", 0) or 0)
    wick = float(getattr(marker, "price", level) or level)
    take_highs = direction == "short"
    start = max(0, idx - 28)
    end = min(len(bars), idx + 3)
    best_i = idx
    best_wick = wick
    for i in range(start, end):
        b = bars[i]
        if take_highs and level > 0 and float(b.high) >= level * 0.9995:
            if float(b.high) >= best_wick * 0.9999:
                best_i, best_wick = i, float(b.high)
        if not take_highs and level > 0 and float(b.low) <= level * 1.0005:
            if float(b.low) <= best_wick * 1.0001 or best_i == idx:
                best_i, best_wick = i, float(b.low)
    if level <= 0:
        level = best_wick
    return best_i, best_wick, level


def resolve_bos_geometry(
    bars: list[KlineBar],
    marker: object,
    *,
    break_level: float | None,
) -> tuple[int, float, float]:
    """(break_bar_idx, close_at_break, level)."""
    level = float(
        break_level
        if break_level is not None
        else getattr(marker, "ref_price", None) or getattr(marker, "price", 0) or 0
    )
    direction = str(getattr(marker, "direction", "") or "long")
    from_i = max(0, int(getattr(marker, "index", 0)))
    for i in range(from_i, len(bars)):
        c = float(bars[i].close)
        if direction == "long" and level > 0 and c > level * 1.00015:
            return i, c, level
        if direction == "short" and level > 0 and c < level * 0.99985:
            return i, c, level
    i = min(len(bars) - 1, max(from_i, 0))
    return i, float(bars[i].close), level


def draw_sweep_pin_mpl(
    ax: plt.Axes,
    bars: list[KlineBar],
    marker: object,
    *,
    draw_level_line: bool = True,
) -> None:
    if not bars:
        return
    bar_i, wick, level = resolve_sweep_geometry(bars, marker)
    x = _idx_to_x(bars, bar_i)
    bw = _bar_width_x(bars)
    color = "#ffd33d"
    is_up = str(getattr(marker, "direction", "") or "") == "short"
    x0, x1 = x - bw * 2.5, x + bw * 3.5
    if draw_level_line:
        ax.hlines(level, x0, x1, colors=color, linewidth=1.0, alpha=0.85, linestyles="--", zorder=7)
    ax.scatter(
        [x],
        [wick],
        s=160,
        facecolors="none",
        edgecolors=color,
        linewidths=2.2,
        zorder=9,
    )
    if not chart_teaching_tags_enabled():
        return
    label = "SWEEP ↑" if is_up else "SWEEP ↓"
    y_off = abs(wick) * 0.0018 if is_up else -abs(wick) * 0.0018
    ax.annotate(
        f"  {label}  ",
        xy=(x, wick),
        xytext=(x + bw * 2.2, wick + y_off),
        color=color,
        fontsize=7,
        fontweight="bold",
        ha="left",
        va="bottom" if is_up else "top",
        zorder=10,
        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.1, shrinkA=0, shrinkB=0),
        bbox=dict(boxstyle="round,pad=0.2", facecolor=CHART_BG, edgecolor=color, alpha=0.92),
    )
    if draw_level_line:
        ax.text(
            x0,
            level,
            f"  liq {fmt_price(level)}  ",
            color=color,
            fontsize=6.2,
            va="bottom" if is_up else "top",
            zorder=8,
        )


def draw_bos_pin_mpl(
    ax: plt.Axes,
    bars: list[KlineBar],
    marker: object,
    *,
    break_level: float | None,
    kind_label: str,
    draw_level_line: bool = True,
) -> None:
    if not bars:
        return
    bar_i, close_p, level = resolve_bos_geometry(bars, marker, break_level=break_level)
    if level <= 0:
        return
    x = _idx_to_x(bars, bar_i)
    bw = _bar_width_x(bars)
    direction = str(getattr(marker, "direction", "") or "")
    col = "#ff7b72" if direction == "short" else "#58a6ff"
    if draw_level_line:
        ax.hlines(
            level,
            _idx_to_x(bars, max(0, bar_i - 8)),
            _idx_to_x(bars, min(len(bars) - 1, bar_i + 2)),
            colors="#f0c040",
            linewidth=1.15,
            alpha=0.9,
            zorder=6,
        )
    ax.scatter([x], [close_p], s=130, marker="*", c=col, edgecolors="white", linewidths=0.4, zorder=9)
    if not chart_teaching_tags_enabled():
        return
    ax.annotate(
        f"  {kind_label} {fmt_price(level)}  ",
        xy=(x, close_p),
        xytext=(x + bw * 2.0, close_p + abs(close_p) * 0.002),
        color=col,
        fontsize=6.8,
        fontweight="bold",
        ha="left",
        va="bottom",
        zorder=10,
        arrowprops=dict(arrowstyle="-|>", color=col, lw=1.0),
        bbox=dict(boxstyle="round,pad=0.18", facecolor=CHART_BG, edgecolor=col, alpha=0.9),
    )


def draw_sweep_pin_tv(
    ax: plt.Axes,
    mapper: object,
    bars: list[KlineBar],
    marker: object,
) -> None:
    if not bars:
        return
    bar_i, wick, level = resolve_sweep_geometry(bars, marker)
    if not mapper.bar_visible(bar_i):  # type: ignore[attr-defined]
        return
    x = mapper.x(bar_i)  # type: ignore[attr-defined]
    y_w, y_l = mapper.y(wick), mapper.y(level)  # type: ignore[attr-defined]
    color = "#ffd33d"
    ax.plot([x, x], [y_l, y_w], color=color, linewidth=1.2, alpha=0.9, zorder=8)
    ax.plot(x, y_w, marker="o", mfc="none", mec=color, ms=10, mew=2.2, zorder=9)
    if chart_teaching_tags_enabled():
        is_up = str(getattr(marker, "direction", "") or "") == "short"
        ax.text(
            x + 0.015,
            y_w,
            "SWEEP" + ("↑" if is_up else "↓"),
            color=color,
            fontsize=6.5,
            va="center",
            zorder=10,
        )
