"""Отрисовка графических фигур на matplotlib-графике."""
from __future__ import annotations

from typing import TYPE_CHECKING

from datetime import datetime, timezone

import matplotlib.dates as mdates
from matplotlib.patches import Polygon, Rectangle

from .chart_pattern_models import ChartPattern, PatternLine, PatternPoint
from .chart_patterns import _quad_price
from .bybit_klines import KlineBar

if TYPE_CHECKING:
    import matplotlib.axes as maxes

PATTERN_STYLE: dict[str, dict[str, float | str]] = {
    "double_top": {"color": "#ff6b6b", "fill_alpha": 0.06},
    "double_bottom": {"color": "#3fb950", "fill_alpha": 0.06},
    "head_shoulders": {"color": "#ffa657", "fill_alpha": 0.08},
    "inverse_head_shoulders": {"color": "#3fb950", "fill_alpha": 0.08},
    "flag": {"color": "#58a6ff", "fill_alpha": 0.10},
    "pennant": {"color": "#58a6ff", "fill_alpha": 0.10},
    "triangle_symmetric": {"color": "#d2a8ff", "fill_alpha": 0.08},
    "triangle_ascending": {"color": "#3fb950", "fill_alpha": 0.08},
    "triangle_descending": {"color": "#f85149", "fill_alpha": 0.08},
    "wedge_rising": {"color": "#f0883e", "fill_alpha": 0.08},
    "wedge_falling": {"color": "#f0883e", "fill_alpha": 0.08},
    "false_breakout": {"color": "#ff7b72", "fill_alpha": 0.12},
    "one_two_three": {"color": "#d2a8ff", "fill_alpha": 0.06},
    "cup_handle": {"color": "#3fb950", "fill_alpha": 0.07},
    "inverse_cup_handle": {"color": "#f85149", "fill_alpha": 0.07},
    "baskerville_bullish": {"color": "#58a6ff", "fill_alpha": 0.10},
    "baskerville_bearish": {"color": "#ff6b6b", "fill_alpha": 0.10},
    "three_indians": {"color": "#d2a8ff", "fill_alpha": 0.08},
    "diamond": {"color": "#ffa657", "fill_alpha": 0.09},
}


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x_at(bars: list[KlineBar], idx: int) -> float:
    return mdates.date2num(_idx_to_date(bars, idx))


def _line_end_idx(bars: list[KlineBar], line: PatternLine) -> int:
    return min(len(bars) - 1, max(line.end_idx, line.start_idx) + 8)


def _draw_line(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    line: PatternLine,
    *,
    color: str,
    label: str = "",
    linestyle: str = "-",
    alpha: float = 0.9,
    extend: bool = True,
) -> None:
    end_idx = _line_end_idx(bars, line) if extend else line.end_idx
    x0 = _x_at(bars, line.start_idx)
    x1 = _x_at(bars, end_idx)
    if end_idx == line.start_idx:
        y1 = line.start_price
    else:
        slope = (line.end_price - line.start_price) / (line.end_idx - line.start_idx)
        y1 = line.start_price + slope * (end_idx - line.start_idx)
    ax.plot([x0, x1], [line.start_price, y1], color=color, linewidth=1.2, alpha=alpha, linestyle=linestyle)
    if label:
        ax.text(x1, y1, f" {label}", color=color, fontsize=6.3, va="center")


def _draw_points(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    points: tuple[PatternPoint, ...],
    *,
    color: str,
) -> None:
    for pt in points:
        x = _x_at(bars, pt.index)
        ax.plot(x, pt.price, marker="o", color=color, markersize=4.5, linestyle="None", alpha=0.95)
        if pt.role in {"head", "peak1", "peak2", "point1", "point2", "point3", "left_shoulder", "right_shoulder"}:
            ax.text(x, pt.price, f" {pt.role[:3]}", color=color, fontsize=5.8, va="bottom")


def _draw_target_stop(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    pattern: ChartPattern,
    *,
    color: str,
) -> None:
    if not bars:
        return
    x = _x_at(bars, len(bars) - 1)
    if pattern.target_price is not None:
        ax.axhline(pattern.target_price, color="#7ee787", linestyle=":", linewidth=0.85, alpha=0.75)
        ax.text(x, pattern.target_price, " TP", color="#7ee787", fontsize=6.2, va="bottom")
    if pattern.stop_price is not None:
        ax.axhline(pattern.stop_price, color="#ff7b72", linestyle=":", linewidth=0.75, alpha=0.65)
        ax.text(x, pattern.stop_price, " SL", color="#ff7b72", fontsize=6.0, va="top")


def _draw_head_shoulders(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    roles = {p.role: p for p in pattern.points}
    if not all(k in roles for k in ("left_shoulder", "head", "right_shoulder")):
        return
    ls, head, rs = roles["left_shoulder"], roles["head"], roles["right_shoulder"]
    neck_l = roles.get("neck_left")
    neck_r = roles.get("neck_right")
    if neck_l and neck_r and pattern.neckline:
        poly_x = [
            _x_at(bars, ls.index), _x_at(bars, neck_l.index), _x_at(bars, head.index),
            _x_at(bars, neck_r.index), _x_at(bars, rs.index),
        ]
        poly_y = [ls.price, neck_l.price, head.price, neck_r.price, rs.price]
        ax.add_patch(Polygon(
            list(zip(poly_x, poly_y)),
            closed=True,
            facecolor=color,
            edgecolor=color,
            alpha=0.07,
            linewidth=0.8,
        ))
    if pattern.neckline:
        ls_style = "-" if pattern.status == "confirmed" else "--"
        _draw_line(ax, bars, pattern.neckline, color=color, label="шея", linestyle=ls_style)
    _draw_points(ax, bars, pattern.points, color=color)


def _draw_double(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    for line in pattern.lines:
        role = "сопр." if line.role == "resistance" else "подд." if line.role == "support" else "шея"
        _draw_line(ax, bars, line, color=color, label=role, linestyle="--" if line.role in {"resistance", "support"} else "-")
    if pattern.zone_top is not None and pattern.zone_bottom is not None and pattern.points:
        i0 = pattern.points[0].index
        i1 = pattern.points[1].index
        rect = Rectangle(
            (_x_at(bars, i0), pattern.zone_bottom),
            _x_at(bars, i1) - _x_at(bars, i0),
            pattern.zone_top - pattern.zone_bottom,
            facecolor=color,
            edgecolor=color,
            alpha=0.05,
            linewidth=0.8,
        )
        ax.add_patch(rect)
    _draw_points(ax, bars, pattern.points, color=color)


def _draw_flag_pennant(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    if len(pattern.points) >= 2:
        pole_start = pattern.points[0]
        pole_end = pattern.points[1]
        x0 = _x_at(bars, pole_start.index)
        x1 = _x_at(bars, pole_end.index)
        ax.plot([x0, x1], [pole_start.price, pole_end.price], color=color, linewidth=1.5, alpha=0.85)
        ax.text((x0 + x1) / 2, (pole_start.price + pole_end.price) / 2, " шток", color=color, fontsize=6.0)
    for line in pattern.lines:
        _draw_line(ax, bars, line, color=color, linestyle="--", alpha=0.8)
    if pattern.zone_top is not None and pattern.zone_bottom is not None and len(pattern.points) >= 2:
        i0 = pattern.points[1].index
        i1 = pattern.points[-1].index
        rect = Rectangle(
            (_x_at(bars, i0), pattern.zone_bottom),
            max(0.0001, _x_at(bars, i1) - _x_at(bars, i0)),
            pattern.zone_top - pattern.zone_bottom,
            facecolor=color,
            edgecolor=color,
            alpha=0.08,
            linewidth=0.8,
        )
        ax.add_patch(rect)


def _draw_triangle_wedge(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    for line in pattern.lines:
        _draw_line(ax, bars, line, color=color, linestyle="-", alpha=0.88)
    _draw_points(ax, bars, pattern.points, color=color)


def _draw_false_breakout(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    if pattern.neckline:
        _draw_line(ax, bars, pattern.neckline, color=color, label="уровень", linestyle="-")
    if pattern.zone_top is not None and pattern.zone_bottom is not None:
        x0 = _x_at(bars, max(0, len(bars) - 12))
        x1 = _x_at(bars, len(bars) - 1)
        rect = Rectangle(
            (x0, min(pattern.zone_top, pattern.zone_bottom)),
            max(0.0001, x1 - x0),
            abs(pattern.zone_top - pattern.zone_bottom),
            facecolor=color,
            edgecolor=color,
            alpha=0.10,
            linewidth=0.8,
        )
        ax.add_patch(rect)
    _draw_points(ax, bars, pattern.points, color=color)


def _draw_one_two_three(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    if pattern.neckline:
        _draw_line(ax, bars, pattern.neckline, color=color, label="триггер", linestyle="-.")
    _draw_points(ax, bars, pattern.points, color=color)
    labels = {"point1": "1", "point2": "2", "point3": "3"}
    for pt in pattern.points:
        if pt.role in labels:
            ax.text(_x_at(bars, pt.index), pt.price, f" {labels[pt.role]}", color=color, fontsize=7, fontweight="bold")


def _draw_cup_handle(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    roles = {p.role: p for p in pattern.points}
    left = roles.get("cup_left_rim")
    bottom = roles.get("cup_bottom") or roles.get("cup_top")
    right = roles.get("cup_right_rim")
    handle = roles.get("handle_low") or roles.get("handle_high")
    if not left or not bottom or not right:
        return
    rim_price = max(left.price, right.price) if pattern.direction == "bullish" else min(left.price, right.price)
    arc_x: list[float] = []
    arc_y: list[float] = []
    for idx in range(left.index, right.index + 1):
        arc_x.append(_x_at(bars, idx))
        arc_y.append(_quad_price(left.index, left.price, bottom.index, bottom.price, right.index, right.price, idx))
    ax.plot(arc_x, arc_y, color=color, linewidth=1.3, alpha=0.85, linestyle="-")
    if pattern.neckline:
        _draw_line(ax, bars, pattern.neckline, color=color, label="обод", linestyle="--")
    if handle:
        ax.plot(
            [_x_at(bars, right.index), _x_at(bars, handle.index)],
            [right.price, handle.price],
            color=color, linewidth=1.0, alpha=0.75, linestyle=":",
        )
    _draw_points(ax, bars, pattern.points, color=color)


def _draw_baskerville(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    _draw_head_shoulders(ax, bars, pattern, color=color)
    if pattern.neckline:
        _draw_line(ax, bars, pattern.neckline, color="#58a6ff", label="шея", linestyle="--", alpha=0.7)
    reclaim = next((p for p in pattern.points if p.role == "reclaim"), None)
    if reclaim:
        x = _x_at(bars, reclaim.index)
        ax.annotate(
            "",
            xy=(x, reclaim.price),
            xytext=(x, pattern.zone_bottom or reclaim.price),
            arrowprops=dict(arrowstyle="->", color=color, lw=1.2, alpha=0.9),
        )


def _draw_three_indians(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    for line in pattern.lines:
        _draw_line(ax, bars, line, color=color, label="тренд", linestyle="-.")
    _draw_points(ax, bars, pattern.points, color=color)
    for i, pt in enumerate(pattern.points[:3], start=1):
        ax.text(_x_at(bars, pt.index), pt.price, f" {i}", color=color, fontsize=7, fontweight="bold")


def _draw_diamond(ax: "maxes.Axes", bars: list[KlineBar], pattern: ChartPattern, *, color: str) -> None:
    if len(pattern.lines) >= 2:
        upper, lower = pattern.lines[0], pattern.lines[1]
        end_idx = pattern.points[-1].index if pattern.points else len(bars) - 1
        mid_idx = (upper.start_idx + end_idx) // 2
        poly_x = [
            _x_at(bars, upper.start_idx), _x_at(bars, mid_idx), _x_at(bars, end_idx),
            _x_at(bars, mid_idx),
        ]
        poly_y = [
            upper.start_price, _line_value_draw(upper, mid_idx), _line_value_draw(upper, end_idx),
            _line_value_draw(lower, mid_idx),
        ]
        ax.add_patch(Polygon(list(zip(poly_x, poly_y)), closed=True, facecolor=color, edgecolor=color, alpha=0.07, linewidth=0.8))
        _draw_line(ax, bars, upper, color=color, linestyle="-", alpha=0.85)
        _draw_line(ax, bars, lower, color=color, linestyle="-", alpha=0.85)
    _draw_points(ax, bars, pattern.points, color=color)


def _line_value_draw(line: PatternLine, idx: int) -> float:
    if line.end_idx == line.start_idx:
        return line.start_price
    slope = (line.end_price - line.start_price) / (line.end_idx - line.start_idx)
    return line.start_price + slope * (idx - line.start_idx)


PATTERN_DRAWERS = {
    "double_top": _draw_double,
    "double_bottom": _draw_double,
    "head_shoulders": _draw_head_shoulders,
    "inverse_head_shoulders": _draw_head_shoulders,
    "flag": _draw_flag_pennant,
    "pennant": _draw_flag_pennant,
    "triangle_symmetric": _draw_triangle_wedge,
    "triangle_ascending": _draw_triangle_wedge,
    "triangle_descending": _draw_triangle_wedge,
    "wedge_rising": _draw_triangle_wedge,
    "wedge_falling": _draw_triangle_wedge,
    "false_breakout": _draw_false_breakout,
    "one_two_three": _draw_one_two_three,
    "cup_handle": _draw_cup_handle,
    "inverse_cup_handle": _draw_cup_handle,
    "baskerville_bullish": _draw_baskerville,
    "baskerville_bearish": _draw_baskerville,
    "three_indians": _draw_three_indians,
    "diamond": _draw_diamond,
}


def draw_chart_patterns(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    patterns: list[ChartPattern],
    *,
    max_patterns: int = 3,
    min_confidence: float = 0.55,
) -> None:
    if not bars or not patterns:
        return
    shown = 0
    for pattern in sorted(patterns, key=lambda p: (-p.confidence, p.status == "confirmed")):
        if pattern.confidence < min_confidence:
            continue
        style = PATTERN_STYLE.get(pattern.kind, {"color": "#ffa657", "fill_alpha": 0.08})
        color = str(style["color"])
        drawer = PATTERN_DRAWERS.get(pattern.kind)
        if not drawer:
            continue
        drawer(ax, bars, pattern, color=color)
        _draw_target_stop(ax, bars, pattern, color=color)
        status = "подтв." if pattern.status == "confirmed" else "форм."
        end_x = _x_at(bars, pattern.points[-1].index if pattern.points else len(bars) - 1)
        label_y = pattern.zone_top or (pattern.points[0].price if pattern.points else bars[-1].close)
        ax.text(
            end_x,
            label_y,
            f" {pattern.label_ru} ({status}, {pattern.confidence:.0%})",
            color=color,
            fontsize=6.8,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#0d1117", edgecolor=color, alpha=0.85),
        )
        shown += 1
        if shown >= max_patterns:
            break
