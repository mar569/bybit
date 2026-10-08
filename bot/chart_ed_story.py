"""Ed story v2: коридор сопр., импульс, ghost-бары из истории, forward short-tool."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_ed_story_history import draw_ghost_bars_forward, resolve_sweep_template
from .chart_position_boxes import draw_forward_short_projection
from .chart_range_breakdown_draw import _last_impulse_bar_index
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

CHART_BG = "#0d1117"
CHART_TEXT = "#e6edf3"
CORRIDOR = "#f0c040"

ED_STORY_TRAILING = 0.54


def use_ed_story_chart(ta: TAAnalysisResult) -> bool:
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return False
    return str(rbr.get("phase") or "") in {"fade_top", "await_break", "retest"}


def ed_story_min_analysis_hours(interval_minutes: int) -> int:
    """Сколько часов свечей грузим для ed_story (история под шаблон)."""
    return {5: 36, 10: 36, 15: 48, 30: 60, 60: 72}.get(interval_minutes, 36)


def ed_story_chart_zoom_hours(
    ta: object,
    bars: list,
    *,
    interval_minutes: int,
    analysis_hours: int,
    configured: int | None,
) -> int:
    """На экране минимум ~24–30ч + вся структура RBR."""
    from .manual_ta import compute_structure_bar_span, manual_chart_zoom_hours

    base = manual_chart_zoom_hours(
        ta,
        bars,
        interval_minutes=interval_minutes,
        analysis_hours=analysis_hours,
        configured=configured,
    )
    min_visible = {5: 24, 10: 26, 15: 30, 30: 36, 60: 42}.get(interval_minutes, 24)
    per_h = max(1, 60 // max(1, interval_minutes))
    span = compute_structure_bar_span(ta, bars)
    span_h = int(span / per_h) + 6
    need = max(min_visible, span_h, base)
    if configured is not None and int(configured) > 0:
        need = max(need, min(int(configured), analysis_hours))
    return max(8, min(int(need), analysis_hours))


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _visible_x_span(ax: plt.Axes, bars: list[KlineBar]) -> tuple[float, float]:
    x0, x1 = ax.get_xlim()
    if x1 > x0:
        return x0, x1
    i0 = max(0, len(bars) - min(len(bars), 160))
    return (
        mdates.date2num(_idx_to_date(bars, i0)),
        mdates.date2num(_idx_to_date(bars, len(bars) - 1)),
    )


def _draw_consolidation_context(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    floor: float,
    ceil: float,
) -> None:
    cons = getattr(ta, "consolidation", None)
    if cons is None or floor <= 0 or ceil <= floor:
        return
    start = int(getattr(cons, "start_idx", max(0, len(bars) - 80)))
    start = max(0, min(start, len(bars) - 2))
    x0 = mdates.date2num(_idx_to_date(bars, start))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    bot = float(getattr(cons, "bottom", floor) or floor)
    top = float(getattr(cons, "top", ceil) or ceil)
    ax.add_patch(
        Rectangle(
            (x0, bot),
            max(x1 - x0, 0.001),
            top - bot,
            facecolor="#8b949e",
            edgecolor="#484f58",
            alpha=0.07,
            linewidth=0.8,
            zorder=1,
        )
    )


def _draw_resistance_corridor(
    ax: plt.Axes,
    bars: list[KlineBar],
    rbr: dict,
    *,
    ceil: float,
) -> None:
    el = float(rbr.get("entry_lo") or ceil * 0.985)
    eh = float(rbr.get("entry_hi") or ceil * 1.006)
    z_lo = min(el, ceil * 0.998)
    z_hi = max(eh, ceil * 1.002)
    x0, x1 = _visible_x_span(ax, bars)
    ax.add_patch(
        Rectangle(
            (x0, z_lo),
            x1 - x0,
            z_hi - z_lo,
            facecolor="#f0c040",
            edgecolor="none",
            alpha=0.08,
            zorder=3,
        )
    )
    ax.hlines(z_hi, x0, x1, colors=CORRIDOR, linewidth=1.85, alpha=0.95, zorder=5)
    ax.hlines(z_lo, x0, x1, colors=CORRIDOR, linewidth=1.85, alpha=0.95, zorder=5)
    ax.text(
        x0 + (x1 - x0) * 0.01,
        z_hi,
        f"  сопр. {fmt_price(z_lo)} – {fmt_price(z_hi)}  ",
        color=CORRIDOR,
        fontsize=7.8,
        fontweight="bold",
        va="bottom",
        ha="left",
        zorder=6,
        bbox=dict(boxstyle="round,pad=0.22", facecolor=CHART_BG, edgecolor=CORRIDOR, alpha=0.93),
    )


def _draw_floor_corridor(ax: plt.Axes, bars: list[KlineBar], *, floor: float, rbr: dict) -> None:
    el = float(rbr.get("entry_lo") or floor * 0.996)
    eh = float(rbr.get("entry_hi") or floor * 1.01)
    x0, x1 = _visible_x_span(ax, bars)
    ax.hlines(eh, x0, x1, colors="#58a6ff", linewidth=1.6, alpha=0.9, zorder=5)
    ax.hlines(el, x0, x1, colors="#58a6ff", linewidth=1.6, alpha=0.9, zorder=5)
    ax.text(
        x0 + (x1 - x0) * 0.01,
        eh,
        f"  retest {fmt_price(el)} – {fmt_price(eh)}  ",
        color="#58a6ff",
        fontsize=7.5,
        fontweight="bold",
        va="bottom",
        ha="left",
        zorder=6,
    )


def _draw_range_floor_line(ax: plt.Axes, bars: list[KlineBar], floor: float) -> None:
    x0, x1 = _visible_x_span(ax, bars)
    ax.hlines(floor, x0, x1, colors="#8b949e", linewidth=1.0, linestyle="--", alpha=0.65, zorder=4)
    ax.text(x0, floor, f"  пол {fmt_price(floor)}  ", color="#8b949e", fontsize=6.8, va="top", ha="left", zorder=5)


def _draw_impulse_demand_zone(ax: plt.Axes, bars: list[KlineBar], *, floor: float) -> None:
    if len(bars) < 10:
        return
    imp_i = _last_impulse_bar_index(bars, lookback=22)
    seg_start = max(0, imp_i - 10)
    seg = bars[seg_start : imp_i + 1]
    if not seg:
        return
    lo = min(float(b.low) for b in seg)
    hi = max(float(b.high) for b in seg[: max(1, len(seg) - 2)])
    if hi <= lo:
        return
    x0 = mdates.date2num(_idx_to_date(bars, seg_start))
    x1 = mdates.date2num(_idx_to_date(bars, min(imp_i + 2, len(bars) - 1)))
    ax.add_patch(
        Rectangle(
            (x0, lo),
            max(x1 - x0, 0.001),
            hi - lo,
            facecolor="#3fb950",
            edgecolor="#238636",
            alpha=0.14,
            linewidth=1.0,
            zorder=2,
        )
    )
    ax.text(
        x0,
        lo,
        "  база импульса  ",
        color="#3fb950",
        fontsize=6.6,
        va="bottom",
        ha="left",
        zorder=5,
    )


def _draw_impulse_trendline(ax: plt.Axes, bars: list[KlineBar]) -> None:
    if len(bars) < 12:
        return
    imp_i = _last_impulse_bar_index(bars, lookback=22)
    seg_start = max(0, imp_i - 2)
    seg = bars[seg_start:]
    swing: list[tuple[int, float]] = []
    for j in range(1, len(seg) - 1):
        i = seg_start + j
        if seg[j].low <= seg[j - 1].low and seg[j].low <= seg[j + 1].low:
            swing.append((i, float(seg[j].low)))
    if len(swing) < 2:
        swing = [(imp_i, float(bars[imp_i].low)), (len(bars) - 1, float(bars[-1].low))]
    else:
        swing = swing[-5:]
    i0, p0 = swing[0]
    i1, p1 = swing[-1]
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, i1))
    ax.plot([x0, x1], [p0, p1], color="#e6edf3", linewidth=1.75, alpha=0.92, zorder=4)


def _draw_story_caption(ax: plt.Axes, ta: TAAnalysisResult, rbr: dict) -> None:
    phase = str(rbr.get("phase") or "")
    hist = " · аналог на истории" if phase in {"fade_top", "await_break"} else ""
    if phase in {"fade_top", "await_break"}:
        line = f"Вынос ликвидности у сопр. → отказ → шорт{hist}"
    elif phase == "retest":
        line = "Retest пола — шорт после подтверждения, не в импульс вверх"
    else:
        line = str(rbr.get("label_ru") or "Сценарий на графике.")
    ax.text(
        0.5,
        0.975,
        line,
        transform=ax.transAxes,
        va="top",
        ha="center",
        color=CHART_TEXT,
        fontsize=7.6,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.96),
    )


def expand_ed_story_ylim(ax: plt.Axes, ta: TAAnalysisResult) -> None:
    """TP/SL и ghost-бары не обрезаются по Y."""
    from .chart_position_boxes import _entry_stop_tp

    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return
    plan = _entry_stop_tp(ta)
    prices: list[float] = []
    if plan:
        _, _e, stop, tp = plan
        prices.extend([stop, tp])
    for key in ("range_top", "range_bottom", "entry_lo", "entry_hi"):
        v = rbr.get(key)
        if v:
            prices.append(float(v))
    if not prices:
        return
    lo, hi = ax.get_ylim()
    pmin, pmax = min(prices), max(prices)
    pad = max((hi - lo) * 0.06, pmax * 0.002)
    ax.set_ylim(min(lo, pmin - pad), max(hi, pmax + pad))


def draw_ed_story_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
) -> None:
    if not bars:
        return
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return

    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    targets = [float(t) for t in (rbr.get("targets") or []) if t]
    tp = float(targets[0]) if targets else float(rbr.get("swing_low_target") or 0)
    phase = str(rbr.get("phase") or "")

    _draw_consolidation_context(ax, bars, ta, floor=floor, ceil=ceil)

    if floor > 0:
        _draw_range_floor_line(ax, bars, floor)
    _draw_impulse_demand_zone(ax, bars, floor=floor)
    _draw_impulse_trendline(ax, bars)

    resistance = ceil if ceil > 0 else float(rbr.get("entry_hi") or 0)
    if phase in {"fade_top", "await_break"} and resistance > 0:
        _draw_resistance_corridor(ax, bars, rbr, ceil=resistance)
        template = resolve_sweep_template(
            bars, resistance=resistance, floor=floor or resistance * 0.92, tp=tp or resistance * 0.9,
        )
        draw_ghost_bars_forward(
            ax,
            bars,
            template,
            interval_minutes=interval_minutes,
            resistance=float(rbr.get("entry_hi") or resistance),
            tp=tp if tp > 0 else resistance * 0.88,
        )
    elif phase == "retest" and floor > 0:
        _draw_floor_corridor(ax, bars, floor=floor, rbr=rbr)
        template = resolve_sweep_template(
            bars, resistance=floor * 1.008, floor=floor, tp=tp or floor * 0.92,
        )
        draw_ghost_bars_forward(
            ax,
            bars,
            template,
            interval_minutes=interval_minutes,
            resistance=floor * 1.006,
            tp=tp if tp > 0 else floor * 0.9,
        )

    draw_forward_short_projection(ax, bars, ta, use_xlim=True)
    _draw_story_caption(ax, ta, rbr)
