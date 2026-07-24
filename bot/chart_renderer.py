from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Ellipse, Rectangle

from .bybit_klines import BybitKlineCache, KlineBar
from .bybit_cvd import get_taker_cvd_cache
from .chart_pattern_draw import (
    draw_chart_patterns,
    draw_htf_pattern_levels,
    draw_pattern_foresight_path,
)
from .chart_elliott_draw import draw_elliott_waves, draw_setup_forecast_path
from .pattern_specs import MAX_CHART_PATTERNS, MIN_DRAW_CONFIDENCE
from .chart_pro_layers import (
    draw_buy_flat_sell_zones,
    draw_pro_chart_layers,
    draw_rsi_panel,
    draw_volume_panel,
)
from .manual_ta import pattern_chart_hours, chart_display_hours, structure_aware_display_hours
from .chart_screenshot import chart_capture_service
from .market_structure import FiveMinOiBar
from .ta_analysis import (
    TAAnalysisResult,
    TradeScenario,
    fmt_price,
    run_ta_analysis,
    ta_chart_key_levels_text,
    ta_chart_plan_text,
    ta_chart_panel_text,
    ta_chart_scenario_text,
    ta_chart_summary_text,
    ta_chart_tv_overlay_text,
    ta_display_score,
    primary_forecast_direction,
    _short_trigger_state,
)

logger = logging.getLogger(__name__)

_kline_cache = BybitKlineCache(ttl_seconds=60.0)

CHART_STYLE = {
    "bg": "#0d1117",
    "panel": "#161b22",
    "panel_border": "#30363d",
    "grid": "#21262d",
    "text": "#c9d1d9",
    "up": "#26a69a",
    "down": "#ef5350",
    "accent_long": "#3fb950",
    "accent_short": "#f85149",
    "warning": "#d29922",
    "level_support": "#58a6ff",
    "level_resistance": "#f0883e",
    "trend_bull": "#3fb950",
    "trend_bear": "#f85149",
    "channel": "#a371f7",
    "zone_support": "#3fb950",
    "zone_resistance": "#f85149",
    "ruler": "#d2a8ff",
    "pattern": "#ffa657",
    "inv": "#ff7b72",
    "target": "#7ee787",
    "entry": "#ffa657",
    "scenario_bull": "#3fb950",
    "scenario_bear": "#f85149",
    "fib": "#8b949e",
    "fib_key": "#d2a8ff",
}


def _bar_times(bars: list[KlineBar]) -> list[datetime]:
    return [datetime.fromtimestamp(b.open_time, tz=timezone.utc) for b in bars]


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _bar_width_days(bars: list[KlineBar]) -> float:
    times = _bar_times(bars)
    if len(times) < 2:
        return 5.0 / (24 * 60)
    widths = [
        mdates.date2num(times[i]) - mdates.date2num(times[i - 1])
        for i in range(1, len(times))
    ]
    return max(sum(widths) / len(widths), 1e-6)


def _x_after_last_bar(bars: list[KlineBar], bars_ahead: float = 12.0) -> float:
    times = _bar_times(bars)
    return mdates.date2num(times[-1]) + _bar_width_days(bars) * bars_ahead


def _forecast_path_xs(bars: list[KlineBar], n_points: int) -> list[float]:
    """X-координаты пунктирного прогноза — шире шаг, чтобы не слипалось у последней свечи."""
    if not bars or n_points < 1:
        return []
    times = _bar_times(bars)
    start_x = mdates.date2num(times[-1])
    bar_w = _bar_width_days(bars)
    step = bar_w * 5.5
    return [start_x + step * i for i in range(n_points)]


def _apply_chart_breathing_room(ax: plt.Axes, bars: list[KlineBar], *, trailing: float = 0.34, leading: float = 0.03) -> None:
    """Пустое поле справа под подписи и пунктиры прогноза."""
    times = _bar_times(bars)
    t0 = mdates.date2num(times[0])
    t1 = mdates.date2num(times[-1])
    span = max(t1 - t0, 1e-6)
    ax.set_xlim(t0 - span * leading, t1 + span * trailing)


def _visible_bars(
    bars: list[KlineBar],
    display_hours: int,
    interval_minutes: int,
) -> list[KlineBar]:
    if not bars or display_hours <= 0:
        return bars
    per_hour = max(1, 60 // max(1, interval_minutes))
    n = max(36, display_hours * per_hour)
    if len(bars) <= n:
        return bars
    return bars[-n:]


def _apply_display_zoom(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    display_hours: int,
    interval_minutes: int,
    trailing: float = 0.22,
    leading: float = 0.02,
    set_ylim: bool = True,
) -> None:
    """Зум на последние N часов: шире свечи + Y по видимому диапазону (не весь дамп)."""
    if not bars:
        return
    vis = _visible_bars(bars, display_hours, interval_minutes)
    if len(vis) < 2:
        _apply_chart_breathing_room(ax, bars, trailing=trailing, leading=leading)
        return
    times = _bar_times(vis)
    t0 = mdates.date2num(times[0])
    t1 = mdates.date2num(times[-1])
    span = max(t1 - t0, 1e-6)
    ax.set_xlim(t0 - span * leading, t1 + span * trailing)
    if not set_ylim:
        return
    peak = max(b.high for b in vis)
    trough = min(b.low for b in vis)
    if not (peak > trough > 0) or not (peak < 1e12):
        return
    pad = max((peak - trough) * 0.16, peak * 0.002)
    lo = max(0.0, trough - pad)
    hi = peak + pad
    # защита от вырожденного/огромного диапазона (ломает bbox/renderer)
    if hi / max(lo, 1e-12) > 1e6:
        mid = bars[-1].close if bars[-1].close > 0 else peak
        lo, hi = mid * 0.92, mid * 1.08
    ax.set_ylim(lo, hi)


@dataclass
class _RightLabel:
    price: float
    text: str
    color: str
    va: str = "center"


def _price_near(a: float, b: float, ref: float) -> bool:
    if ref <= 0:
        ref = max(a, b, 1e-9)
    return abs(a - b) <= ref * 0.0024


def _collect_right_labels(ta: TAAnalysisResult) -> list[_RightLabel]:
    """Подписи справа — без дублей на одной цене. На WAIT — только ключевые уровни."""
    ref = ta.current_price or 1.0
    seen: list[float] = []
    out: list[_RightLabel] = []
    is_wait = (getattr(ta, "verdict", "") or "").upper() == "WAIT"

    def add(price: float | None, text: str, color: str, va: str = "center") -> None:
        if price is None:
            return
        if any(_price_near(price, s, ref) for s in seen):
            return
        seen.append(price)
        out.append(_RightLabel(price, text, color, va))

    if ta.breakout_level:
        add(ta.breakout_level, f"LONG≥{fmt_price(ta.breakout_level)}", CHART_STYLE["entry"], "bottom")
    if ta.breakdown_level and (
        ta.breakout_level is None
        or not _price_near(ta.breakdown_level, ta.breakout_level, ref)
    ):
        add(ta.breakdown_level, f"SHORT≤{fmt_price(ta.breakdown_level)}", CHART_STYLE["accent_short"], "top")
    # STOP/TP справа — только при направленном вердикте (на WAIT дублируют foresight/path)
    if not is_wait:
        if ta.invalidation_price:
            add(ta.invalidation_price, f"STOP {fmt_price(ta.invalidation_price)}", CHART_STYLE["inv"])
        for j, tp in enumerate(ta.target_prices[:2]):
            add(tp, f"TP{j + 1} {fmt_price(tp)}", CHART_STYLE["target"])
    for fl in getattr(ta, "fib_levels", None) or []:
        if fl.ratio in {0.5, 0.618}:
            add(fl.price, fl.label, CHART_STYLE["fib_key"])
    for lv in ta.levels[:2]:
        color = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
        add(lv.price, fmt_price(lv.price), color)
    if ta.entry_zone and not is_wait:
        lo, hi = ta.entry_zone
        add(hi, "зона входа", CHART_STYLE["accent_long"], "bottom")
    return out


def _layout_right_label_xs(bars: list[KlineBar], labels: list[_RightLabel]) -> list[float]:
    """Разносит подписи по колонкам, если уровни близко по цене."""
    if not labels:
        return []
    times = _bar_times(bars)
    base = mdates.date2num(times[-1])
    bar_w = _bar_width_days(bars)
    ref = labels[0].price
    # Шире зона «слипания» + больше колонок вправо
    min_gap = abs(ref) * 0.012
    xs = [0.0] * len(labels)
    col = 0
    prev_price: float | None = None
    for idx, lbl in sorted(enumerate(labels), key=lambda t: t[1].price):
        if prev_price is not None and lbl.price - prev_price < min_gap:
            col += 1
        else:
            col = 0
        xs[idx] = base + bar_w * (14.0 + col * 7.0)
        prev_price = lbl.price
    return xs


def _deconflict_right_label_ys(
    labels: list[_RightLabel],
    *,
    y_min: float | None = None,
    y_max: float | None = None,
) -> list[float]:
    """Разносит подписи по Y, если цены слишком близко (линии остаются на цене)."""
    if not labels:
        return []
    prices = [lbl.price for lbl in labels]
    lo = min(prices) if y_min is None else min(min(prices), y_min)
    hi = max(prices) if y_max is None else max(max(prices), y_max)
    span = max(hi - lo, abs(prices[0]) * 0.02, 1e-9)
    # Минимальный зазор ~1.8% от видимого диапазона (было 1.1%)
    min_gap = span * 0.018
    order = sorted(range(len(labels)), key=lambda i: labels[i].price)
    ys = [lbl.price for lbl in labels]
    for k in range(1, len(order)):
        i_prev, i_cur = order[k - 1], order[k]
        if ys[i_cur] - ys[i_prev] < min_gap:
            ys[i_cur] = ys[i_prev] + min_gap
    if y_max is not None and ys[order[-1]] > y_max:
        overflow = ys[order[-1]] - y_max
        for i in order:
            ys[i] -= overflow * 0.55
    for k in range(1, len(order)):
        i_prev, i_cur = order[k - 1], order[k]
        if ys[i_cur] - ys[i_prev] < min_gap:
            ys[i_cur] = ys[i_prev] + min_gap
    return ys


def _draw_right_price_labels(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    labels = _collect_right_labels(ta)
    if not labels:
        return
    xs = _layout_right_label_xs(bars, labels)
    y_lim = ax.get_ylim()
    ys = _deconflict_right_label_ys(labels, y_min=y_lim[0], y_max=y_lim[1])
    for lbl, x, y in zip(labels, xs, ys):
        ax.text(
            x,
            y,
            lbl.text,
            color=lbl.color,
            fontsize=6.9,
            va="center",
            ha="left",
            bbox=dict(
                boxstyle="round,pad=0.18",
                facecolor=CHART_STYLE["bg"],
                edgecolor=lbl.color,
                alpha=0.78,
                linewidth=0.55,
            ),
            zorder=8,
        )


def _extend_channel_line(
    bars: list[KlineBar],
    start_idx: int,
    start_price: float,
    end_idx: int,
    end_price: float,
) -> tuple[datetime, float, datetime, float]:
    if end_idx == start_idx:
        end_idx = start_idx + 1
    slope = (end_price - start_price) / (end_idx - start_idx)
    last_idx = len(bars) - 1
    ext_price = start_price + slope * (last_idx - start_idx)
    return (
        _idx_to_date(bars, start_idx),
        start_price,
        _idx_to_date(bars, last_idx),
        ext_price,
    )


def _draw_candles(ax: plt.Axes, bars: list[KlineBar], *, interval_minutes: int = 5) -> None:
    if not bars:
        return
    width_minutes = max(interval_minutes * 0.8, 2.0)
    width_days = width_minutes / (24 * 60)
    for bar in bars:
        ts = datetime.fromtimestamp(bar.open_time, tz=timezone.utc)
        color = CHART_STYLE["up"] if bar.close >= bar.open else CHART_STYLE["down"]
        ax.plot([ts, ts], [bar.low, bar.high], color=color, linewidth=1.0, solid_capstyle="round")
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        height = max(body_high - body_low, (bar.high - bar.low) * 0.05 if bar.high > bar.low else bar.close * 0.0002)
        rect = Rectangle(
            (mdates.date2num(ts) - width_days / 2, body_low),
            width_days,
            height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.5,
        )
        ax.add_patch(rect)


def _draw_zones(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return
    x0 = mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 50)))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    width = max(x1 - x0, 0.001)
    for zone in ta.zones:
        color = CHART_STYLE["zone_resistance"] if zone.kind == "resistance" else CHART_STYLE["zone_support"]
        alpha = 0.22 if zone.touches >= 2 else 0.14
        rect = Rectangle(
            (x0, zone.bottom),
            width,
            zone.top - zone.bottom,
            facecolor=color,
            edgecolor=color,
            alpha=alpha,
            linewidth=1.0,
            linestyle="--",
        )
        ax.add_patch(rect)
        label = zone.label
        if zone.kind == "resistance" and zone.touches >= 2:
            label = f"зона сопр. {label}"
        elif zone.kind == "support" and zone.touches >= 2:
            label = f"зона подд. {label}"
        ax.text(
            x0, zone.top, f"  {label}",
            color=color, fontsize=6.5, va="bottom", ha="left", alpha=0.95,
        )


def _draw_channel(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    ch = ta.channel
    if ch is None or not bars:
        return
    color = CHART_STYLE["channel"]
    upper = _extend_channel_line(
        bars, ch.upper_start_idx, ch.upper_start_price, ch.upper_end_idx, ch.upper_end_price,
    )
    lower = _extend_channel_line(
        bars, ch.lower_start_idx, ch.lower_start_price, ch.lower_end_idx, ch.lower_end_price,
    )
    ax.plot([upper[0], upper[2]], [upper[1], upper[3]], color=color, linewidth=1.4, alpha=0.9)
    ax.plot([lower[0], lower[2]], [lower[1], lower[3]], color=color, linewidth=1.4, alpha=0.9)
    ax.text(
        mdates.date2num(upper[2]), upper[3], f" {ch.label}",
        color=color, fontsize=7, va="bottom",
    )


def _draw_scenario_path(
    ax: plt.Axes,
    bars: list[KlineBar],
    scenario: TradeScenario | None,
    *,
    color: str,
) -> None:
    if scenario is None or not bars or not scenario.target_prices:
        return
    times = _bar_times(bars)
    start_x = mdates.date2num(times[-1])
    start_y = bars[-1].close
    step = _bar_width_days(bars) * 5.5
    x, y = start_x, start_y
    for tp in scenario.target_prices[:4]:
        next_x = x + step * 0.85
        ax.annotate(
            "",
            xy=(next_x, tp),
            xytext=(x, y),
            arrowprops=dict(
                arrowstyle="->",
                color=color,
                lw=1.1,
                linestyle="dashed",
                alpha=0.75,
                shrinkA=0,
                shrinkB=0,
            ),
        )
        x, y = next_x, tp


def _draw_zigzag_forecast_path(
    ax: plt.Axes,
    bars: list[KlineBar],
    waypoints: list[float],
    *,
    color: str,
    label: str,
    alpha: float = 0.88,
    lw: float = 1.35,
) -> None:
    """Пунктирный путь коррекции/продолжения вправо от последней свечи."""
    if not bars or len(waypoints) < 2:
        return
    xs = _forecast_path_xs(bars, len(waypoints))
    ax.plot(xs, waypoints, color=color, linestyle="--", linewidth=lw, alpha=alpha, zorder=4)
    ax.annotate(
        "",
        xy=(xs[-1], waypoints[-1]),
        xytext=(xs[-2], waypoints[-2]),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, linestyle="dashed", alpha=alpha),
    )
    va = "top" if waypoints[-1] < waypoints[0] else "bottom"
    label_x = xs[-1] + _bar_width_days(bars) * (1.5 + len(xs) * 0.15)
    ax.text(label_x, waypoints[-1], label, color=color, fontsize=7, fontweight="bold", va=va, ha="left")


def _draw_market_forecast_paths(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    """Рисует коррекцию или продолжение строго в сторону вердикта."""
    direction = primary_forecast_direction(ta)
    if direction == "neutral":
        return False

    corr = ta.correction_path if direction == "short" else None
    cont = ta.continuation_path if direction == "long" else None
    if corr is None and cont is None:
        return False

    corr_color = "#ffa657"
    cont_color = CHART_STYLE["accent_long"]
    if corr:
        _draw_zigzag_forecast_path(ax, bars, corr.waypoints, color=corr_color, label=corr.label)
        if len(corr.waypoints) >= 3:
            pb = corr.waypoints[2]
            ax.axhline(pb, color=corr_color, linestyle=":", linewidth=0.85, alpha=0.55, zorder=3)
            ax.text(
                _x_after_last_bar(bars, 4), pb, f"откат {fmt_price(pb)}",
                color=corr_color, fontsize=6.8, va="top", ha="left",
            )
        return True
    if cont:
        _draw_zigzag_forecast_path(ax, bars, cont.waypoints, color=cont_color, label=cont.label)
        return True
    return False


def _draw_extended_trend_lines(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return
    last_idx = len(bars) - 1
    for tl in ta.trend_lines:
        color = CHART_STYLE["trend_bull"] if tl.kind == "bull" else CHART_STYLE["trend_bear"]
        start_idx = tl.start_idx
        end_idx = tl.end_idx
        if end_idx == start_idx:
            end_idx = start_idx + 1
        slope = (tl.end_price - tl.start_price) / (end_idx - start_idx)
        ext_price = tl.start_price + slope * (last_idx - start_idx)
        x0 = _idx_to_date(bars, start_idx)
        x1 = _idx_to_date(bars, last_idx)
        label = "тренд ↑" if tl.kind == "bull" else "тренд ↓"
        ax.plot([x0, x1], [tl.start_price, ext_price], color=color, linewidth=1.35, alpha=0.88, linestyle="-")
        ax.text(
            mdates.date2num(x1), ext_price, f" {label}",
            color=color, fontsize=6.5, va="bottom" if tl.kind == "bull" else "top",
        )


def _draw_consolidation_box(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not ta.consolidation or not bars:
        return
    z = ta.consolidation
    x0 = _idx_to_date(bars, z.start_idx)
    x1 = _idx_to_date(bars, z.end_idx)
    x0n = mdates.date2num(x0)
    x1n = mdates.date2num(x1)
    mid_x = x0n + (x1n - x0n) * 0.5
    mid_y = (z.top + z.bottom) / 2.0
    rect = Rectangle(
        (x0n, z.bottom),
        x1n - x0n,
        z.top - z.bottom,
        facecolor=CHART_STYLE["warning"],
        edgecolor=CHART_STYLE["warning"],
        alpha=0.12,
        linewidth=1.1,
        linestyle="--",
    )
    ax.add_patch(rect)
    ax.text(
        mid_x, mid_y, "БОКОВИК",
        color=CHART_STYLE["warning"], fontsize=7, fontweight="bold",
        ha="center", va="center",
        bbox=dict(boxstyle="round,pad=0.25", facecolor=CHART_STYLE["bg"], edgecolor=CHART_STYLE["warning"], alpha=0.85),
    )
    # Стрелки выхода только в сторону вердикта; WAIT — без направленных стрелок
    direction = primary_forecast_direction(ta)
    arrow_dx = max((x1n - x0n) * 0.08, 0.0008)
    if direction in {"long", "neutral"} and ta.verdict != "SHORT":
        if direction == "long" or ta.verdict == "WAIT":
            alpha_up = 0.9 if direction == "long" else 0.35
            ax.annotate(
                "",
                xy=(x1n + arrow_dx, z.top),
                xytext=(x1n, mid_y),
                arrowprops=dict(arrowstyle="->", color=CHART_STYLE["accent_long"], lw=1.2, alpha=alpha_up),
            )
            if direction == "long":
                ax.text(
                    x1n + arrow_dx * 0.3, z.top * 1.0003, "↑",
                    color=CHART_STYLE["accent_long"], fontsize=6.5, va="bottom", ha="left",
                )
    if direction in {"short", "neutral"} and ta.verdict != "LONG":
        if direction == "short" or ta.verdict == "WAIT":
            alpha_dn = 0.9 if direction == "short" else 0.35
            ax.annotate(
                "",
                xy=(x1n + arrow_dx, z.bottom),
                xytext=(x1n, mid_y),
                arrowprops=dict(arrowstyle="->", color=CHART_STYLE["accent_short"], lw=1.2, alpha=alpha_dn),
            )
            if direction == "short":
                ax.text(
                    x1n + arrow_dx * 0.3, z.bottom * 0.9997, "↓",
                    color=CHART_STYLE["accent_short"], fontsize=6.5, va="top", ha="left",
                )


def _draw_breakout_arrows(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return
    direction = primary_forecast_direction(ta)
    last_ts = _idx_to_date(bars, len(bars) - 1)
    x = mdates.date2num(last_ts)
    y = bars[-1].close
    span = max(mdates.date2num(last_ts) - mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 12))), 0.001)
    dx = span * 0.35

    if direction != "short" and ta.breakout_level and y <= ta.breakout_level * 1.002:
        ax.annotate(
            "",
            xy=(x + dx, ta.breakout_level),
            xytext=(x, y),
            arrowprops=dict(
                arrowstyle="-|>",
                color=CHART_STYLE["accent_long"],
                lw=1.6,
                alpha=0.95,
                connectionstyle="arc3,rad=0.12",
            ),
        )
        ax.text(
            x + dx * 0.55, (y + ta.breakout_level) / 2,
            "↑",
            color=CHART_STYLE["accent_long"], fontsize=8, fontweight="bold", ha="center", va="center",
        )

    if direction != "long" and ta.breakdown_level and y >= ta.breakdown_level * 0.998:
        ax.annotate(
            "",
            xy=(x + dx, ta.breakdown_level),
            xytext=(x, y),
            arrowprops=dict(
                arrowstyle="-|>",
                color=CHART_STYLE["accent_short"],
                lw=2.0 if ta.momentum_label.startswith("импульс вниз") else 1.6,
                alpha=0.95,
                connectionstyle="arc3,rad=-0.12",
            ),
        )
        ax.text(
            x + dx * 0.55, (y + ta.breakdown_level) / 2,
            "↓",
            color=CHART_STYLE["accent_short"], fontsize=8, fontweight="bold", ha="center", va="center",
        )
    elif direction != "long" and ta.momentum_label.startswith("импульс вниз") and ta.breakdown_level:
        ax.text(
            x, y * 1.002, " давление ↓",
            color=CHART_STYLE["accent_short"], fontsize=7, fontweight="bold", ha="left",
        )


def _draw_level_hints(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars or not ta.levels:
        return
    last_ts = _idx_to_date(bars, len(bars) - 1)
    x = mdates.date2num(last_ts)
    y = bars[-1].close
    for lv in ta.levels[:2]:
        color = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
        if lv.kind == "support" and y > lv.price:
            ax.annotate(
                "",
                xy=(x, lv.price * 1.0005),
                xytext=(x, y),
                arrowprops=dict(arrowstyle="->", color=color, lw=1.0, alpha=0.65, linestyle="dotted"),
            )
        elif lv.kind == "resistance" and y < lv.price:
            ax.annotate(
                "",
                xy=(x, lv.price * 0.9995),
                xytext=(x, y),
                arrowprops=dict(arrowstyle="->", color=color, lw=1.0, alpha=0.65, linestyle="dotted"),
            )


def _draw_signal_markers(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    for marker in ta.signal_markers:
        ts = _idx_to_date(bars, marker.index)
        bar = bars[marker.index]
        is_buy = marker.side == "buy"
        y = bar.low * 0.9992 if is_buy else bar.high * 1.0008
        color = CHART_STYLE["accent_long"] if is_buy else CHART_STYLE["accent_short"]
        ax.text(
            mdates.date2num(ts), y, marker.label,
            fontsize=7, fontweight="bold", color="white", ha="center", va="center",
            bbox=dict(boxstyle="square,pad=0.25", facecolor=color, edgecolor="none", alpha=0.95),
        )


def _draw_smc_annotations(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    smc = ta.smc
    if smc is None or not bars:
        return
    x0 = mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 50)))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    width = max(x1 - x0, 0.001)
    smc_color = "#f0c040"

    for gap in smc.fvgs[-3:]:
        color = "#3ddc84" if gap.direction == "bullish" else "#ff6b6b"
        rect = Rectangle(
            (mdates.date2num(_idx_to_date(bars, gap.start_idx)), gap.bottom),
            mdates.date2num(_idx_to_date(bars, gap.end_idx)) - mdates.date2num(_idx_to_date(bars, gap.start_idx)),
            gap.top - gap.bottom,
            facecolor=color, edgecolor=color, alpha=0.18, linewidth=0.8,
        )
        ax.add_patch(rect)

    if smc.discount_zone:
        lo, hi = smc.discount_zone
        rect = Rectangle(
            (x0, lo), width, hi - lo,
            facecolor=smc_color, edgecolor=smc_color, alpha=0.12, linewidth=0.8, linestyle="--",
        )
        ax.add_patch(rect)
        ax.text(x0, hi, "  зона дисконта", color=smc_color, fontsize=6.5, va="bottom")

    if smc.premium_zone:
        lo, hi = smc.premium_zone
        rect = Rectangle(
            (x0, lo), width, hi - lo,
            facecolor="#ff8c42", edgecolor="#ff8c42", alpha=0.12, linewidth=0.8, linestyle="--",
        )
        ax.add_patch(rect)
        ax.text(x0, hi, "  зона премии", color="#ff8c42", fontsize=6.5, va="bottom")

    if smc.equilibrium_50:
        ax.axhline(smc.equilibrium_50, color=smc_color, linestyle=":", linewidth=0.9, alpha=0.75)
        ax.text(x1, smc.equilibrium_50, " 50%", color=smc_color, fontsize=6.5, va="center")

    if smc.structure_break_level:
        ax.axhline(
            smc.structure_break_level, color=smc_color, linestyle="-.", linewidth=1.0, alpha=0.85,
        )
        ax.text(
            x1, smc.structure_break_level, " BOS",
            color=smc_color, fontsize=7, va="bottom",
        )

    for lv in smc.liquidity_levels[:4]:
        ls = ":" if "daily" in lv.kind or "weekly" in lv.kind else "-"
        color = "#8899aa"
        ax.axhline(lv.price, color=color, linestyle=ls, linewidth=0.6, alpha=0.5)

    for marker in smc.markers[:3]:
        if marker.index >= len(bars):
            continue
        ts = _idx_to_date(bars, marker.index)
        color = CHART_STYLE["accent_long"] if marker.direction == "long" else CHART_STYLE["accent_short"]
        ax.plot(ts, marker.price, marker="*", color=color, markersize=8, linestyle="None")


def _draw_fib_levels(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Fib 0.382 / 0.5 / 0.618 (+ extensions) — ключевые 0.5/0.618 заметнее."""
    levels = getattr(ta, "fib_levels", None) or []
    if not levels or not bars:
        return
    current = ta.current_price or bars[-1].close
    # Не рисуем уровни далеко от цены (>12%)
    for fl in levels:
        if current > 0 and abs(fl.price - current) / current > 0.12:
            continue
        is_key = fl.ratio in {0.5, 0.618}
        color = CHART_STYLE["fib_key"] if is_key else CHART_STYLE["fib"]
        lw = 1.05 if is_key else 0.55
        alpha = 0.85 if is_key else 0.4
        ls = "-." if is_key else ":"
        ax.axhline(fl.price, color=color, linestyle=ls, linewidth=lw, alpha=alpha)
        # Подпись: 0.5 / 0.618 крупнее и справа у цены
        if is_key:
            x1 = mdates.date2num(_bar_times(bars)[-1])
            ratio_lbl = "0.5" if abs(fl.ratio - 0.5) < 1e-9 else "0.618"
            # Чуть выше линии + фон, чтобы не слипалось с TP/триггерами
            y_off = abs(fl.price) * 0.0025
            ax.text(
                x1, fl.price + y_off, f" Fib {ratio_lbl} ",
                color=color, fontsize=7.0, va="bottom", ha="left", alpha=0.95,
                fontweight="bold",
                bbox=dict(
                    boxstyle="round,pad=0.12",
                    facecolor=CHART_STYLE["bg"],
                    edgecolor=color,
                    alpha=0.7,
                    linewidth=0.4,
                ),
                zorder=6,
            )
        elif fl.ratio in {0.382, 1.272, 1.618}:
            x0 = mdates.date2num(_bar_times(bars)[0])
            ax.text(
                x0, fl.price, f" {fl.label}",
                color=color, fontsize=5.5, va="bottom", ha="left", alpha=0.75,
            )

    # Пунктир импульсной ноги (старт → конец)
    start = getattr(ta, "wave_leg_start", None)
    end = getattr(ta, "wave_leg_end", None)
    if start and end and ta.swings:
        # Найти индексы по цене среди последних swings — приближённо через rulers
        for ruler in ta.rulers[:1]:
            if abs(ruler.from_price - start) / max(start, 1e-9) < 0.01 or abs(ruler.to_price - end) / max(end, 1e-9) < 0.01:
                x0 = _idx_to_date(bars, ruler.start_idx)
                x1 = _idx_to_date(bars, ruler.end_idx)
                ax.plot(
                    [x0, x1], [ruler.from_price, ruler.to_price],
                    color=CHART_STYLE["fib_key"], linestyle="--", linewidth=0.7, alpha=0.45,
                )
                break


def _draw_ta_annotations(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return

    is_wait = (getattr(ta, "verdict", "") or "").upper() == "WAIT"
    has_chart_pattern = bool(getattr(ta, "primary_chart_pattern", None))

    draw_buy_flat_sell_zones(ax, bars, ta)
    _draw_zones(ax, bars, ta)
    _draw_smc_annotations(ax, bars, ta)
    _draw_channel(ax, bars, ta)
    _draw_extended_trend_lines(ax, bars, ta)
    _draw_consolidation_box(ax, bars, ta)
    draw_chart_patterns(
        ax,
        bars,
        ta.chart_patterns,
        max_patterns=MAX_CHART_PATTERNS,
        min_confidence=MIN_DRAW_CONFIDENCE,
        force_primary=getattr(ta, "primary_chart_pattern", None),
        draw_target_labels=False,  # цель/SL — только линии; текст справа / path
    )
    # HTF фигура (уровни) — без лишнего текста на WAIT оставляем линии
    draw_htf_pattern_levels(
        ax,
        bars,
        getattr(ta, "primary_htf_chart_pattern", None),
        conflict=bool(getattr(ta, "pattern_foresight_htf_conflict", False)),
        quiet=is_wait,
    )
    # Foresight-стрелка только при LONG/SHORT и если нет setup-path
    setup_path_preview = getattr(ta, "forecast_path_prices", None) or []
    setup_grade = getattr(ta, "setup_grade", "") or ""
    has_setup_path = len(setup_path_preview) >= 2 and setup_grade in {"A", "B", "C"}
    if (
        not is_wait
        and not has_setup_path
        and getattr(ta, "pattern_foresight_summary", "")
    ):
        draw_pattern_foresight_path(
            ax,
            bars,
            current_price=float(getattr(ta, "current_price", 0) or (bars[-1].close if bars else 0)),
            pattern=getattr(ta, "primary_chart_pattern", None),
            horizon_hours=float(getattr(ta, "pattern_foresight_horizon", 0) or 0),
            bias=str(getattr(ta, "pattern_foresight_bias", "neutral") or "neutral"),
            watch_only=bool(getattr(ta, "pattern_foresight_watch_only", False)),
            status=str(getattr(ta, "pattern_foresight_status", "") or ""),
            quiet_labels=True,
        )
    # Волны Эллиотта (1–5 + ABC) — поверх / рядом с фигурами
    from .elliott_wave import ElliottWaveResult

    ew_pts = getattr(ta, "elliott_draw_points", None) or []
    if ew_pts:
        ew_stub = ElliottWaveResult(
            label_ru=getattr(ta, "elliott_label", "") or "",
            phase=getattr(ta, "elliott_phase", "") or "",
            draw_points=list(ew_pts),
            confidence=int(getattr(ta, "elliott_confidence", 0) or 0),
            extension=str(getattr(ta, "elliott_extension", "") or ""),
            truncated=bool(getattr(ta, "elliott_truncated", False)),
            diagonal=str(getattr(ta, "elliott_diagonal", "") or ""),
            corr_type=str(getattr(ta, "elliott_corr_type", "") or ""),
            structure_note_ru=str(getattr(ta, "elliott_structure_note", "") or ""),
            triangle_kind=str(getattr(ta, "elliott_triangle_kind", "") or ""),
            triangle_bias=str(getattr(ta, "elliott_triangle_bias", "") or ""),
            complex_kind=str(getattr(ta, "elliott_complex_kind", "") or ""),
            fib_target_prices=list(getattr(ta, "elliott_fib_targets", None) or []),
            fib_target_labels=list(getattr(ta, "elliott_fib_target_labels", None) or []),
            path_bias=str(getattr(ta, "elliott_path_bias", "") or ""),
            path_prices=list(getattr(ta, "elliott_path_prices", None) or []),
            path_labels=list(getattr(ta, "elliott_path_labels", None) or []),
            path_reason_ru=str(getattr(ta, "elliott_path_reason", "") or ""),
            triangle_obj=getattr(ta, "elliott_triangle_obj", None),
            global_draw_points=list(getattr(ta, "elliott_global_draw_points", None) or []),
            local_draw_points=list(getattr(ta, "elliott_local_draw_points", None) or []),
            global_label_ru=str(getattr(ta, "elliott_global_label", "") or ""),
            local_label_ru=str(getattr(ta, "elliott_local_label", "") or ""),
            has_global=bool(getattr(ta, "elliott_global_draw_points", None)),
            has_local=bool(getattr(ta, "elliott_local_draw_points", None)),
        )
        # восстановить entry plan для линии входа — не на WAIT (дубли TP/STOP)
        if not is_wait and getattr(ta, "elliott_entry_price", None):
            from .elliott_wave import ElliottEntryPlan

            ew_stub.entry_plan = ElliottEntryPlan(
                mode=getattr(ta, "elliott_entry_mode", "") or "wait",
                side="long" if (ta.wave_bias or "") == "long" else "short",
                entry_price=ta.elliott_entry_price,
                stop_price=getattr(ta, "elliott_stop_price", None),
                tp1=(ta.elliott_tp_prices[0] if ta.elliott_tp_prices else None),
                tp2=(ta.elliott_tp_prices[1] if len(ta.elliott_tp_prices) > 1 else None),
                ready=bool(getattr(ta, "elliott_entry_ready", False)),
            )
        # На WAIT не рисуем EW path-стрелку (структура волн остаётся)
        if is_wait:
            ew_stub.path_prices = []
            ew_stub.path_labels = []
            ew_stub.path_bias = ""
            ew_stub.fib_target_prices = []
            ew_stub.fib_target_labels = []
        draw_elliott_waves(ax, bars, ew_stub)

    # HTF Elliott (пунктир) + прогнозный путь Pro-confluence
    htf_pts = getattr(ta, "htf_elliott_draw_points", None) or []
    if htf_pts:
        htf_stub = ElliottWaveResult(
            label_ru=getattr(ta, "htf_elliott_label", "") or "",
            phase=getattr(ta, "htf_elliott_phase", "") or "",
            draw_points=list(htf_pts),
            confidence=0,
            diagonal="ending" if getattr(ta, "is_ending_diagonal", False) else "",
            corr_type="triangle" if getattr(ta, "is_abcde", False) else "",
            structure_note_ru=(
                "конечная диагональ"
                if getattr(ta, "is_ending_diagonal", False)
                else ("треугольник ABCDE" if getattr(ta, "is_abcde", False) else "")
            ),
        )
        draw_elliott_waves(ax, bars, htf_stub, style="htf", max_points=14)

    setup_path = getattr(ta, "forecast_path_prices", None) or []
    setup_side = (getattr(ta, "setup_side", "") or "").lower()
    verdict = (getattr(ta, "verdict", "") or "").upper()
    path_ok = (
        not is_wait
        and len(setup_path) >= 2
        and getattr(ta, "setup_grade", "") in {"A", "B", "C"}
        and (
            (verdict == "LONG" and setup_side == "long")
            or (verdict == "SHORT" and setup_side == "short")
        )
    )
    if path_ok:
        draw_setup_forecast_path(
            ax,
            bars,
            list(setup_path),
            list(getattr(ta, "forecast_path_labels", None) or []),
        )

    if ta.breakout_level:
        ax.axhline(ta.breakout_level, color=CHART_STYLE["entry"], linestyle="-", linewidth=1.0, alpha=0.85)
    if ta.breakdown_level and (
        ta.breakout_level is None
        or abs(ta.breakdown_level - ta.breakout_level) > max(ta.current_price, 1e-9) * 0.0005
    ):
        ax.axhline(ta.breakdown_level, color=CHART_STYLE["accent_short"], linestyle="-", linewidth=0.9, alpha=0.8)
    for lv in ta.levels[:2]:
        color = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
        ax.axhline(lv.price, color=color, linestyle="-", linewidth=0.75, alpha=0.55)

    _draw_fib_levels(ax, bars, ta)

    if len(ta.levels) < 2:
        _draw_level_hints(ax, bars, ta)
    if not is_wait:
        _draw_breakout_arrows(ax, bars, ta)

    for ruler in ta.rulers[:1]:
        x0 = _idx_to_date(bars, ruler.start_idx)
        x1 = _idx_to_date(bars, ruler.end_idx)
        mid_x = mdates.date2num(x0) + (mdates.date2num(x1) - mdates.date2num(x0)) * 0.5
        mid_y = (ruler.from_price + ruler.to_price) / 2.0
        ax.annotate(
            "",
            xy=(x1, ruler.to_price),
            xytext=(x0, ruler.from_price),
            arrowprops=dict(arrowstyle="<->", color=CHART_STYLE["ruler"], lw=0.9, alpha=0.7),
        )
        ax.text(
            mid_x, mid_y, ruler.label,
            color=CHART_STYLE["ruler"], fontsize=6.5, ha="center",
            bbox=dict(boxstyle="round,pad=0.2", facecolor=CHART_STYLE["bg"], edgecolor="none", alpha=0.8),
        )

    # Свечные маркеры не рисуем, если уже есть графическая фигура (шум справа)
    if not has_chart_pattern:
        for pat in ta.patterns[-2:]:
            bar = bars[pat.index]
            ts = _idx_to_date(bars, pat.index)
            y = bar.high * 1.001 if pat.bullish is not False else bar.low * 0.999
            marker = "^" if pat.bullish else "v" if pat.bullish is False else "o"
            ax.plot(ts, y, marker=marker, color=CHART_STYLE["pattern"], markersize=6, linestyle="None")

    direction = primary_forecast_direction(ta)
    path_kind = "skip" if is_wait else draw_pro_chart_layers(ax, bars, ta)
    if path_kind == "default":
        if not _draw_market_forecast_paths(ax, bars, ta):
            if direction == "long":
                _draw_scenario_path(ax, bars, ta.bullish_scenario, color=CHART_STYLE["scenario_bull"])
            elif direction == "short":
                _draw_scenario_path(ax, bars, ta.bearish_scenario, color=CHART_STYLE["scenario_bear"])
            # WAIT / neutral — без направленных scenario-стрелок
    # bounce_short: не рисуем бычий continuation поверх SHORT

    _draw_signal_markers(ax, bars, ta)

    if ta.invalidation_price and not is_wait:
        ax.axhline(
            ta.invalidation_price, color=CHART_STYLE["inv"],
            linestyle="--", linewidth=1.0, alpha=0.9,
        )
    for j, tp in enumerate(ta.target_prices[:2]):
        if ta.verdict == "SHORT" and tp >= ta.current_price:
            continue
        if ta.verdict == "LONG" and tp <= ta.current_price:
            continue
        if ta.verdict == "WAIT":
            continue
        ax.axhline(tp, color=CHART_STYLE["target"], linestyle=":", linewidth=0.75, alpha=0.65)
    if ta.entry_zone:
        lo, hi = ta.entry_zone
        ax.axhspan(lo, hi, color=CHART_STYLE["accent_long"], alpha=0.1)

    _draw_right_price_labels(ax, bars, ta)


def _draw_info_panels(fig: plt.Figure, ta: TAAnalysisResult, *, with_subpanels: bool = False) -> None:
    """Текстовые блоки в боковых полях — уровни слева, план/итог справа."""
    base_bbox = dict(
        boxstyle="round,pad=0.55",
        facecolor=CHART_STYLE["panel"],
        edgecolor=CHART_STYLE["panel_border"],
        alpha=0.94,
    )
    panel_style = dict(
        transform=fig.transFigure,
        color=CHART_STYLE["text"],
        fontsize=7.6,
        linespacing=1.38,
        bbox=base_bbox,
    )
    bull_style = {**panel_style, "color": CHART_STYLE["accent_long"]}
    bear_style = {**panel_style, "color": CHART_STYLE["accent_short"]}

    # Отступ от края, чтобы боксы не липли к рамке
    lx, rx = 0.018, 0.982

    key_levels = ta_chart_key_levels_text(ta)
    if key_levels:
        fig.text(lx, 0.975, key_levels, va="top", ha="left", **panel_style)

    # Справа сверху вниз: ИТОГ → ПЛАН → сценарий (с зазорами)
    fig.text(rx, 0.975, ta_chart_panel_text(ta), va="top", ha="right", **panel_style)

    plan = ta_chart_plan_text(ta)
    if plan:
        fig.text(rx, 0.68, plan, va="top", ha="right", **panel_style)

    scenario_y = 0.36 if plan else 0.68
    bull_text = ta_chart_scenario_text(ta.bullish_scenario, title="БЫЧИЙ СЦЕНАРИЙ")
    if bull_text and ta.verdict in {"LONG", "WAIT"}:
        fig.text(rx, scenario_y, bull_text, va="top", ha="right", **bull_style)

    bear_text = ta_chart_scenario_text(ta.bearish_scenario, title="МЕДВЕЖИЙ СЦЕНАРИЙ")
    if bear_text and ta.verdict == "SHORT":
        fig.text(rx, scenario_y, bear_text, va="top", ha="right", **bear_style)

    summary = ta_chart_summary_text(ta)
    if summary:
        fig.text(
            0.50, 0.018 if not with_subpanels else 0.125,
            summary,
            va="bottom", ha="center", fontsize=7.5, color=CHART_STYLE["text"],
            transform=fig.transFigure,
            bbox=dict(
                boxstyle="round,pad=0.45",
                facecolor=CHART_STYLE["panel"],
                edgecolor=CHART_STYLE["warning"],
                alpha=0.90,
            ),
        )


def _draw_info_panels_pro(fig: plt.Figure, ta: TAAnalysisResult, *, with_subpanels: bool = False) -> None:
    """PRO-версия: уровни слева, план/итог/сценарий справа."""
    def _drop_dup_title(text: str, title: str) -> str:
        if not text:
            return ""
        lines = text.splitlines()
        if lines and lines[0].strip().upper() == title.strip().upper():
            return "\n".join(lines[1:]).strip()
        return text

    left_x, right_x = 0.018, 0.982
    text_color = CHART_STYLE["text"]
    panel_fc = "#101828"
    edge = CHART_STYLE["panel_border"]

    base = dict(
        transform=fig.transFigure,
        fontsize=8.2,
        color=text_color,
        linespacing=1.40,
        bbox=dict(boxstyle="round,pad=0.55", facecolor=panel_fc, edgecolor=edge, alpha=0.97),
    )

    fig.text(
        left_x,
        0.975,
        "КЛЮЧЕВЫЕ УРОВНИ\n" + (
            _drop_dup_title(ta_chart_key_levels_text(ta), "КЛЮЧЕВЫЕ УРОВНИ")
            or "уровни не определены"
        ),
        ha="left",
        va="top",
        **base,
    )

    fig.text(
        right_x,
        0.975,
        "ИТОГ\n" + _drop_dup_title(ta_chart_panel_text(ta), "ИТОГ"),
        ha="right",
        va="top",
        **base,
    )
    fig.text(
        right_x,
        0.68,
        "ПЛАН ДЕЙСТВИЙ\n" + (
            _drop_dup_title(ta_chart_plan_text(ta), "ПЛАН ДЕЙСТВИЙ")
            or "ожидать подтверждения"
        ),
        ha="right",
        va="top",
        **base,
    )

    show_bull = (
        ta.bullish_scenario is not None
        and (ta.verdict == "LONG" or ta.action_priority == "long" or ta.bearish_scenario is None)
    )
    show_bear = (
        ta.bearish_scenario is not None
        and (ta.verdict == "SHORT" or ta.action_priority == "short" or ta.bullish_scenario is None)
    )
    scenario_y = 0.36
    if show_bull and ta.bullish_scenario:
        bull = ta.bullish_scenario
        bull_lines = [
            "БЫЧИЙ СЦЕНАРИЙ",
            f"Триггер: ≥ {fmt_price(bull.trigger_price)}",
            f"TP1/TP2: {' / '.join(fmt_price(t) for t in bull.target_prices[:2])}",
            f"SL: {fmt_price(bull.stop_price)}",
        ]
        fig.text(
            right_x, scenario_y, "\n".join(bull_lines),
            ha="right", va="top", fontsize=8.0, color=CHART_STYLE["accent_long"],
            transform=fig.transFigure, linespacing=1.36,
            bbox=dict(boxstyle="round,pad=0.52", facecolor="#0f1f17", edgecolor=CHART_STYLE["accent_long"], alpha=0.96),
        )
    if show_bear and ta.bearish_scenario and not show_bull:
        bear = ta.bearish_scenario
        bear_lines = [
            "МЕДВЕЖИЙ СЦЕНАРИЙ",
            f"Триггер: ≤ {fmt_price(bear.trigger_price)}",
            f"TP1/TP2: {' / '.join(fmt_price(t) for t in bear.target_prices[:2])}",
            f"SL: {fmt_price(bear.stop_price)}",
        ]
        fig.text(
            right_x, scenario_y, "\n".join(bear_lines),
            ha="right", va="top", fontsize=8.0, color=CHART_STYLE["accent_short"],
            transform=fig.transFigure, linespacing=1.36,
            bbox=dict(boxstyle="round,pad=0.52", facecolor="#231417", edgecolor=CHART_STYLE["accent_short"], alpha=0.96),
        )


def _draw_pro_market_zones(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """PRO-зоны на самом графике: сопротивление/поддержка и ключевые уровни."""
    if not bars:
        return
    x0 = _idx_to_date(bars, max(0, len(bars) - 70))
    x1 = _idx_to_date(bars, len(bars) - 1)

    # Сопротивления
    resistances = [lv.price for lv in ta.levels if lv.kind == "resistance"][:2]
    for i, price in enumerate(resistances, 1):
        span = max(price * 0.0035, 1e-9)
        ax.axhspan(price - span, price + span, xmin=0.07, xmax=0.98, color="#f85149", alpha=0.14, zorder=1)
        ax.text(
            mdates.date2num(x1), price + span, f" R{i} {fmt_price(price)}",
            color="#ffb3ad", fontsize=7, va="bottom", ha="left",
        )
        if i == 1:
            # Широкая плашка "зона сильного сопротивления"
            top_span = max(price * 0.006, 1e-9)
            ax.axhspan(price - top_span, price + top_span, xmin=0.26, xmax=0.96, color="#7d1f25", alpha=0.23, zorder=1)
            ax.text(
                mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 25))),
                price + top_span * 0.15,
                "Зона сильного сопротивления",
                color="#ffd0cc",
                fontsize=8,
                ha="left",
                va="center",
            )

    # Поддержки
    supports = [lv.price for lv in ta.levels if lv.kind == "support"][:2]
    for i, price in enumerate(supports, 1):
        span = max(price * 0.0035, 1e-9)
        ax.axhspan(price - span, price + span, xmin=0.07, xmax=0.98, color="#3fb950", alpha=0.14, zorder=1)
        ax.text(
            mdates.date2num(x1), price - span, f" S{i} {fmt_price(price)}",
            color="#b2f2bb", fontsize=7, va="top", ha="left",
        )

    # Явные триггеры
    if ta.breakout_level:
        ax.axhline(ta.breakout_level, color="#7ee787", linewidth=1.35, linestyle="-", alpha=0.9)
    if ta.breakdown_level:
        ax.axhline(ta.breakdown_level, color="#ff7b72", linewidth=1.35, linestyle="-", alpha=0.9)

    # Подпись локального тренда
    if ta.trend_lines:
        tl = ta.trend_lines[0]
        ax.text(
            mdates.date2num(x0), tl.start_price,
            " восходящий тренд" if tl.kind == "bull" else " нисходящий тренд",
            color=CHART_STYLE["trend_bull"] if tl.kind == "bull" else CHART_STYLE["trend_bear"],
            fontsize=7,
            va="bottom" if tl.kind == "bull" else "top",
        )
    # Подписи локальных зон
    if supports:
        s = supports[0]
        ax.annotate(
            "Локальная поддержка",
            xy=(_idx_to_date(bars, max(0, len(bars) - 20)), s),
            xytext=(_idx_to_date(bars, max(0, len(bars) - 38)), s * 1.01),
            color="#8ee7a7",
            fontsize=7,
            arrowprops=dict(arrowstyle="->", color="#8ee7a7", lw=1.0, alpha=0.8),
        )
    if resistances:
        r = resistances[0]
        ax.annotate(
            "Локальное сопротивление",
            xy=(_idx_to_date(bars, max(0, len(bars) - 16)), r),
            xytext=(_idx_to_date(bars, max(0, len(bars) - 34)), r * 1.01),
            color="#ffb3ad",
            fontsize=7,
            arrowprops=dict(arrowstyle="->", color="#ffb3ad", lw=1.0, alpha=0.8),
        )


def _draw_pro_paths(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Пунктирные пути бычьего/медвежьего сценария вправо."""
    if not bars:
        return
    last_t = _idx_to_date(bars, len(bars) - 1)
    t1 = mdates.num2date(mdates.date2num(last_t) + 0.018, tz=timezone.utc)
    t2 = mdates.num2date(mdates.date2num(last_t) + 0.036, tz=timezone.utc)
    p0 = bars[-1].close
    if ta.bullish_scenario and ta.bullish_scenario.target_prices:
        p1 = ta.bullish_scenario.target_prices[0]
        p2 = ta.bullish_scenario.target_prices[min(1, len(ta.bullish_scenario.target_prices) - 1)]
        ax.plot([last_t, t1, t2], [p0, p1, p2], color="#3fb950", linestyle="--", linewidth=1.4, alpha=0.75)
    if ta.bearish_scenario and ta.bearish_scenario.target_prices:
        p1 = ta.bearish_scenario.target_prices[0]
        p2 = ta.bearish_scenario.target_prices[min(1, len(ta.bearish_scenario.target_prices) - 1)]
        ax.plot([last_t, t1, t2], [p0, p1, p2], color="#f85149", linestyle="--", linewidth=1.4, alpha=0.75)


def _style_axes(ax: plt.Axes, bars: list[KlineBar]) -> None:
    ax.grid(True, color=CHART_STYLE["grid"], linewidth=0.4, alpha=0.7)
    ax.tick_params(colors=CHART_STYLE["text"], labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(CHART_STYLE["grid"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_ylabel("USDT", color=CHART_STYLE["text"], fontsize=9)
    if bars:
        peak = max(b.high for b in bars)
        trough = min(b.low for b in bars)
        if peak > trough:
            pad = (peak - trough) * 0.12
            ax.set_ylim(trough - pad, peak + pad)


def _render_chart_figure(
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    symbol: str,
    title_suffix: str,
    accent_color: str,
    interval_minutes: int = 5,
    pro_mode: bool = False,
    enhanced: bool = True,
    display_hours: int | None = None,
) -> bytes:
    use_enhanced = enhanced
    if use_enhanced:
        # Шире область свечей (~+13%): боковые панели остаются, но уже
        fig = plt.figure(figsize=(19.2, 10.8), dpi=120)
        gs = fig.add_gridspec(3, 1, height_ratios=[4.2, 1.0, 0.9], hspace=0.04)
        ax = fig.add_subplot(gs[0])
        ax_vol = fig.add_subplot(gs[1], sharex=ax)
        ax_rsi = fig.add_subplot(gs[2], sharex=ax)
        fig.patch.set_facecolor(CHART_STYLE["bg"])
        fig.subplots_adjust(left=0.130, right=0.805, top=0.92, bottom=0.08)
    else:
        fig_size = (20.4, 10.2) if pro_mode else (19.0, 8.5)
        fig, ax = plt.subplots(figsize=fig_size, dpi=120)
        ax_vol = None
        ax_rsi = None
        fig.patch.set_facecolor(CHART_STYLE["bg"])
        ax.set_facecolor(CHART_STYLE["bg"])
        if pro_mode:
            fig.subplots_adjust(left=0.11, right=0.89, top=0.93, bottom=0.09)
        else:
            fig.subplots_adjust(left=0.12, right=0.88, top=0.92, bottom=0.10)

    ax.set_facecolor(CHART_STYLE["bg"])
    _draw_candles(ax, bars, interval_minutes=interval_minutes)
    _draw_ta_annotations(ax, bars, ta)
    if use_enhanced and ax_vol is not None and ax_rsi is not None:
        draw_volume_panel(ax_vol, bars)
        draw_rsi_panel(ax_rsi, bars)
        plt.setp(ax.get_xticklabels(), visible=False)
        plt.setp(ax_vol.get_xticklabels(), visible=False)

    current = bars[-1].close
    ax.axhline(current, color=accent_color, linestyle="--", linewidth=0.9, alpha=0.85)
    ax.text(
        _x_after_last_bar(bars, 14), current, f"сейчас {fmt_price(current)}",
        color=accent_color, fontsize=7, va="center", ha="left",
    )
    mode_suffix = " · PRO" if pro_mode else ""
    ax.set_title(
        f"{symbol}  ·  {ta.verdict} {ta_display_score(ta)}/10  ·  {title_suffix}{mode_suffix}",
        color=CHART_STYLE["text"], fontsize=12 if pro_mode else 11, pad=14,
    )
    if pro_mode:
        _draw_info_panels_pro(fig, ta, with_subpanels=use_enhanced)
    else:
        _draw_info_panels(fig, ta, with_subpanels=use_enhanced)
    _style_axes(ax, bars)
    # Зум: анализ может быть на 18ч, экран — последние N часов (читаемые свечи)
    from .manual_ta import chart_display_hours

    zoom_h = display_hours if display_hours and display_hours > 0 else chart_display_hours(interval_minutes)
    _apply_display_zoom(ax, bars, display_hours=zoom_h, interval_minutes=interval_minutes, set_ylim=True)
    if use_enhanced and ax_vol is not None and ax_rsi is not None:
        # vol/rsi только по X; Y у них свой
        _apply_display_zoom(
            ax_vol, bars, display_hours=zoom_h, interval_minutes=interval_minutes, set_ylim=False,
        )
        _apply_display_zoom(
            ax_rsi, bars, display_hours=zoom_h, interval_minutes=interval_minutes, set_ylim=False,
        )
    fig.autofmt_xdate(rotation=0)

    # НЕ bbox_inches="tight": при зуме артисты вне осей раздувают PNG до миллионов px
    buffer = io.BytesIO()
    try:
        fig.savefig(
            buffer,
            format="png",
            facecolor=fig.get_facecolor(),
            dpi=120,
            pad_inches=0.08,
        )
    except ValueError as exc:
        logger.warning("Chart save failed (%s), retry without zoom ylim", exc)
        # fallback: полный диапазон X/Y без tight
        _apply_chart_breathing_room(ax, bars, trailing=0.20, leading=0.02)
        if bars:
            peak = max(b.high for b in bars)
            trough = min(b.low for b in bars)
            if peak > trough > 0:
                pad = (peak - trough) * 0.10
                ax.set_ylim(trough - pad, peak + pad)
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", facecolor=fig.get_facecolor(), dpi=100)
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


async def _fetch_bars(
    symbol: str,
    hours: int,
    *,
    interval_minutes: int = 5,
) -> list[KlineBar]:
    per_hour = max(1, 60 // interval_minutes)
    # 18ч×5m = 216 баров; запас до ~20ч
    limit = max(24, min(hours * per_hour + 8, 280))
    bars = await _kline_cache.get_klines(
        symbol,
        limit=limit,
        interval_minutes=interval_minutes,
    )
    if len(bars) < 12:
        return []
    return bars[-hours * per_hour:]


# Область свечей на скриншоте TradingView (норм. координаты, 0=низ)
TV_CHART_Y_BOTTOM = 0.24
TV_CHART_Y_TOP = 0.84


def _price_to_axis_y(price: float, y_min: float, y_max: float) -> float:
    if y_max <= y_min:
        return (TV_CHART_Y_BOTTOM + TV_CHART_Y_TOP) / 2
    ratio = (price - y_min) / (y_max - y_min)
    ratio = max(0.0, min(1.0, ratio))
    return TV_CHART_Y_BOTTOM + (1.0 - ratio) * (TV_CHART_Y_TOP - TV_CHART_Y_BOTTOM)


def _tv_nearby_levels(ta: TAAnalysisResult) -> list[float]:
    levels: list[float] = []
    for p in (ta.breakout_level, ta.breakdown_level, ta.invalidation_price):
        if p:
            levels.append(float(p))
    for p in (ta.target_prices or [])[:3]:
        levels.append(float(p))
    for p in (ta.nearest_support, ta.nearest_resistance):
        if p:
            levels.append(float(p))
    for sc in (ta.bullish_scenario, ta.bearish_scenario):
        if sc is None:
            continue
        if sc.trigger_price:
            levels.append(float(sc.trigger_price))
        for p in (sc.target_prices or [])[:2]:
            levels.append(float(p))
    return levels


def _tv_visible_price_range(bars: list[KlineBar], ta: TAAnalysisResult) -> tuple[float, float]:
    """Диапазон цен под видимое окно TV — линии попадают на свечи."""
    n = len(bars)
    visible_count = max(24, min(n, int(n * 0.58)))
    visible = bars[-visible_count:]
    prices: list[float] = []
    for b in visible:
        prices.extend([b.low, b.high])
    if ta.current_price:
        prices.append(ta.current_price)
    for lv in ta.levels[:4]:
        prices.append(lv.price)
    if ta.consolidation:
        prices.extend([ta.consolidation.top, ta.consolidation.bottom])
    if not prices:
        return 0.0, 1.0
    core_min, core_max = min(prices), max(prices)
    core_span = max(core_max - core_min, core_min * 0.0005)
    max_span = core_span * 1.42
    mid = ta.current_price or (core_min + core_max) / 2.0

    for p in _tv_nearby_levels(ta):
        if core_min - core_span * 0.38 <= p <= core_max + core_span * 0.38:
            prices.append(p)

    y_min, y_max = min(prices), max(prices)
    span = y_max - y_min
    if span > max_span:
        y_min = mid - max_span / 2.0
        y_max = mid + max_span / 2.0
    pad = max(span, core_span) * 0.035
    return y_min - pad, y_max + pad


def _tv_level_in_range(price: float | None, y_min: float, y_max: float) -> bool:
    if price is None:
        return False
    margin = max((y_max - y_min) * 0.06, price * 0.0004)
    return (y_min - margin) <= price <= (y_max + margin)


def _bar_x_norm(idx: int, n: int, *, x_start: float = 0.06, x_end: float = 0.88) -> float:
    if n <= 1:
        return x_end
    return x_start + (idx / (n - 1)) * (x_end - x_start)


def _ema_series(bars: list[KlineBar], period: int) -> list[float]:
    if not bars:
        return []
    k = 2.0 / (period + 1)
    out: list[float] = []
    ema = bars[0].close
    for bar in bars:
        ema = bar.close * k + ema * (1 - k)
        out.append(ema)
    return out


def _draw_tv_range_box(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    y_at: Any,
) -> None:
    if ta.consolidation:
        z = ta.consolidation
        x0 = _bar_x_norm(z.start_idx, len(bars))
        x1 = _bar_x_norm(z.end_idx, len(bars))
        y_bot, y_top = y_at(z.bottom), y_at(z.top)
    elif ta.breakout_level and ta.breakdown_level:
        x0, x1 = 0.18, 0.88
        y_bot, y_top = y_at(ta.breakdown_level), y_at(ta.breakout_level)
    else:
        return

    rect = Rectangle(
        (x0, y_bot), x1 - x0, y_top - y_bot,
        facecolor=CHART_STYLE["warning"], edgecolor=CHART_STYLE["warning"],
        alpha=0.10, linewidth=1.0, linestyle="--", zorder=1,
    )
    ax.add_patch(rect)
    ax.text(
        (x0 + x1) / 2, y_top, " RANGE ",
        color=CHART_STYLE["warning"], fontsize=7.5, ha="center", va="bottom", fontweight="bold",
        bbox=dict(facecolor="#161b22aa", edgecolor="none", pad=1),
    )


def _draw_tv_trendlines(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    y_at: Any,
) -> None:
    n = len(bars)
    for tl in ta.trend_lines[:2]:
        color = CHART_STYLE["trend_bull"] if tl.kind == "bull" else CHART_STYLE["trend_bear"]
        x0 = _bar_x_norm(tl.start_idx, n)
        end_idx = min(n - 1, tl.end_idx + max(3, n // 8))
        x1 = _bar_x_norm(end_idx, n)
        if tl.end_idx == tl.start_idx:
            slope = 0.0
        else:
            slope = (tl.end_price - tl.start_price) / (tl.end_idx - tl.start_idx)
        ext_price = tl.start_price + slope * (end_idx - tl.start_idx)
        ax.plot(
            [x0, x1], [y_at(tl.start_price), y_at(ext_price)],
            color=color, linewidth=1.4, alpha=0.85, zorder=2,
        )
        label = "тренд↑" if tl.kind == "bull" else "тренд↓"
        ax.text(x1, y_at(ext_price), f" {label}", color=color, fontsize=7.0, va="bottom" if tl.kind == "bull" else "top")

    ch = ta.channel
    if ch is None:
        return
    color = CHART_STYLE["channel"]
    for start_idx, start_p, end_idx, end_p in (
        (ch.upper_start_idx, ch.upper_start_price, ch.upper_end_idx, ch.upper_end_price),
        (ch.lower_start_idx, ch.lower_start_price, ch.lower_end_idx, ch.lower_end_price),
    ):
        if end_idx == start_idx:
            continue
        slope = (end_p - start_p) / (end_idx - start_idx)
        ext_idx = min(n - 1, end_idx + max(2, n // 10))
        ext_p = start_p + slope * (ext_idx - start_idx)
        ax.plot(
            [_bar_x_norm(start_idx, n), _bar_x_norm(ext_idx, n)],
            [y_at(start_p), y_at(ext_p)],
            color=color, linewidth=1.2, alpha=0.75, linestyle="-", zorder=2,
        )


def _draw_tv_emas(ax: plt.Axes, bars: list[KlineBar], y_at: Any) -> None:
    if len(bars) < 25:
        return
    n = len(bars)
    ema20 = _ema_series(bars, 20)
    ema50 = _ema_series(bars, 50)
    xs = [_bar_x_norm(i, n) for i in range(n)]
    ax.plot(xs, [y_at(p) for p in ema20], color="#58a6ff", linewidth=0.9, alpha=0.55, zorder=2)
    ax.plot(xs, [y_at(p) for p in ema50], color="#f0883e", linewidth=0.9, alpha=0.55, zorder=2)
    ax.text(0.07, y_at(ema20[-1]), " 20", color="#58a6ff", fontsize=6.5, va="center")
    ax.text(0.07, y_at(ema50[-1]), " 50", color="#f0883e", fontsize=6.5, va="center")


def _tv_zone_levels(ta: TAAnalysisResult, bars: list[KlineBar]) -> tuple[float, float, float, float, float, float] | None:
    sell_lo = sell_hi = flat_lo = flat_hi = buy_lo = buy_hi = 0.0
    smc = ta.smc
    if smc and smc.premium_zone and smc.discount_zone:
        sell_lo, sell_hi = smc.premium_zone
        buy_lo, buy_hi = smc.discount_zone
        flat_lo, flat_hi = sell_lo, buy_hi
    elif ta.consolidation:
        z = ta.consolidation
        span = z.top - z.bottom
        sell_lo, sell_hi = z.top - span * 0.12, z.top
        buy_lo, buy_hi = z.bottom, z.bottom + span * 0.12
        flat_lo, flat_hi = buy_hi, sell_lo
    else:
        seg = bars[-min(60, len(bars)) :]
        hi = max(b.high for b in seg)
        lo = min(b.low for b in seg)
        span = hi - lo
        if span <= 0:
            return None
        sell_lo, sell_hi = hi - span * 0.28, hi
        buy_lo, buy_hi = lo, lo + span * 0.28
        flat_lo, flat_hi = buy_hi, sell_lo
    if sell_hi <= sell_lo and buy_hi <= buy_lo:
        return None
    return buy_lo, buy_hi, flat_lo, flat_hi, sell_lo, sell_hi


def _draw_tv_buy_flat_sell_zones(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    y_at: Any,
) -> None:
    levels = _tv_zone_levels(ta, bars)
    if not levels:
        return
    buy_lo, buy_hi, flat_lo, flat_hi, sell_lo, sell_hi = levels
    x0, width = 0.03, 0.84

    def _band(lo: float, hi: float, color: str, label: str) -> None:
        if hi <= lo:
            return
        y_bot = y_at(lo)
        y_top = y_at(hi)
        rect = Rectangle(
            (x0, min(y_bot, y_top)),
            width,
            max(abs(y_top - y_bot), 0.0015),
            facecolor=color,
            edgecolor="none",
            alpha=0.10,
            zorder=0,
        )
        ax.add_patch(rect)
        ax.text(
            x0 + 0.01, (y_bot + y_top) / 2, f" {label}",
            color=color, fontsize=7.2, fontweight="bold", va="center", alpha=0.92,
        )

    _band(buy_lo, buy_hi, "#3fb950", "BUY")
    _band(flat_lo, flat_hi, "#8b949e", "flat")
    _band(sell_lo, sell_hi, "#f85149", "SELL")


def _draw_tv_sweep_circles(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    y_at: Any,
) -> None:
    smc = ta.smc
    if smc is None or not bars:
        return
    n = len(bars)
    w_x, h_y = 0.022, 0.014
    for marker in smc.markers:
        if marker.kind != "sweep" or marker.index >= n:
            continue
        x = _bar_x_norm(marker.index, n)
        y = y_at(marker.price)
        color = "#ffd33d" if marker.direction == "long" else "#ff7b72"
        ax.add_patch(
            Ellipse(
                (x, y), w_x, h_y,
                fill=False, edgecolor=color, linewidth=2.0, linestyle="-", zorder=6,
            )
        )
        ax.text(x, y + h_y * 0.5, " sweep", color=color, fontsize=6.8, ha="center", va="bottom", fontweight="bold")


def _draw_tv_zigzag(
    ax: plt.Axes,
    x0: float,
    span: float,
    waypoints: list[float],
    y_at: Any,
    *,
    color: str,
    label: str,
    alpha: float,
    lw: float = 1.4,
) -> None:
    if len(waypoints) < 2:
        return
    xs = [x0 + span * i for i in range(len(waypoints))]
    ys = [y_at(p) for p in waypoints]
    ax.plot(xs, ys, color=color, linewidth=lw, linestyle="--", alpha=alpha, zorder=3)
    ax.annotate(
        "", xy=(xs[-1], ys[-1]), xytext=(xs[-2], ys[-2]),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, linestyle="dashed", alpha=alpha),
    )
    va = "top" if waypoints[-1] < waypoints[0] else "bottom"
    ax.text(xs[-1], ys[-1], f" {label}", color=color, fontsize=7.5, va=va, fontweight="bold")


def _draw_tv_bounce_short_path(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    current: float,
    y_at: Any,
    *,
    x0: float,
    span: float,
    y_min: float | None = None,
    y_max: float | None = None,
) -> bool:
    if ta.verdict != "SHORT" or not ta.breakdown_level or not bars:
        return False
    state, _ = _short_trigger_state(ta)
    if state == "ready":
        return False
    px = current
    bd = ta.breakdown_level
    if px <= bd * 1.001:
        return False
    resist = ta.breakout_level or ta.nearest_resistance
    if not resist or resist <= px * 1.0005:
        resist = px * 1.006
    tp = ta.target_prices[0] if ta.target_prices else bd * 0.992
    if y_min is not None and y_max is not None and not _tv_level_in_range(tp, y_min, y_max):
        tp = bd
    mid_pull = (px + resist) / 2.0
    waypoints = [px, mid_pull, resist, bd, tp]
    _draw_tv_zigzag(
        ax, x0, span, waypoints, y_at,
        color="#c9d1d9", label="отскок→short", alpha=0.88, lw=1.5,
    )
    return True


def _draw_tv_pro_layers(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    current: float,
    y_at: Any,
) -> None:
    _draw_tv_buy_flat_sell_zones(ax, ta, bars, y_at)
    _draw_tv_sweep_circles(ax, ta, bars, y_at)


def _draw_tv_forecast_paths(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    current: float,
    y_at: Any,
    *,
    y_min: float | None = None,
    y_max: float | None = None,
) -> None:
    """Коррекция / продолжение пунктиром вправо от последней свечи."""
    n = len(bars)
    x0 = _bar_x_norm(n - 1, n, x_start=0.62, x_end=0.90)
    span = 0.08
    y0 = y_at(current)
    ax.plot(x0, y0, "o", color="white", markersize=4, zorder=5)

    if _draw_tv_bounce_short_path(
        ax, ta, bars, current, y_at, x0=x0, span=span, y_min=y_min, y_max=y_max,
    ):
        return

    direction = primary_forecast_direction(ta)
    if direction == "neutral":
        return

    def _draw_zigzag_tv(waypoints: list[float], *, color: str, label: str, alpha: float) -> None:
        _draw_tv_zigzag(ax, x0, span, waypoints, y_at, color=color, label=label, alpha=alpha)

    corr = ta.correction_path if direction == "short" else None
    cont = ta.continuation_path if direction == "long" else None
    if corr:
        _draw_zigzag_tv(corr.waypoints, color="#ffa657", label=corr.label, alpha=0.9)
        return
    if cont:
        _draw_zigzag_tv(cont.waypoints, color=CHART_STYLE["accent_long"], label=cont.label, alpha=0.9)
        return

    def _draw_path(scenario: TradeScenario, *, color: str, label: str, va: str) -> None:
        x1, x2 = x0 + span, min(0.97, x0 + span * 2)
        y_trig = y_at(scenario.trigger_price)
        y_tp = y_at(scenario.target_prices[0])
        ax.plot(
            [x0, x1, x2], [y0, y_trig, y_tp],
            color=color, linewidth=1.4, linestyle="--", alpha=0.9, zorder=3,
        )
        ax.annotate(
            "", xy=(x2, y_tp), xytext=(x1, y_trig),
            arrowprops=dict(arrowstyle="-|>", color=color, lw=1.5, linestyle="dashed", alpha=0.95),
        )
        ax.text(x2, y_tp, f" {label}", color=color, fontsize=7.5, va=va, fontweight="bold")

    if direction == "long" and ta.bullish_scenario and ta.bullish_scenario.target_prices:
        _draw_path(ta.bullish_scenario, color=CHART_STYLE["accent_long"], label="прогноз↑", va="bottom")
    elif direction == "short" and ta.bearish_scenario and ta.bearish_scenario.target_prices:
        _draw_path(ta.bearish_scenario, color=CHART_STYLE["accent_short"], label="прогноз↓", va="top")


def _tv_forecast_legend(ta: TAAnalysisResult) -> str:
    if ta.forecast_summary:
        lines = [ta.forecast_summary[:120]]
        if ta.correction_path:
            lines.append(f"↘ {ta.correction_path.reason}")
        if ta.continuation_path:
            lines.append(f"↗ {ta.continuation_path.reason}")
        return "\n".join(lines[:3])
    direction = primary_forecast_direction(ta)
    if direction == "long" and ta.bullish_scenario and ta.bullish_scenario.target_prices:
        bs = ta.bullish_scenario
        tps = "→".join(fmt_price(t) for t in bs.target_prices[:2])
        return f"Сценарий ↑ {fmt_price(bs.trigger_price)} {tps}"
    if direction == "short" and ta.bearish_scenario and ta.bearish_scenario.target_prices:
        bs = ta.bearish_scenario
        tps = "→".join(fmt_price(t) for t in bs.target_prices[:2])
        return f"Сценарий ↓ {fmt_price(bs.trigger_price)} {tps}"
    lines = ["Уровни →"]
    if ta.breakout_level:
        lines.append(f"↑ {fmt_price(ta.breakout_level)}")
    if ta.breakdown_level:
        lines.append(f"↓ {fmt_price(ta.breakdown_level)}")
    return "\n".join(lines[:3])


def _overlay_ta_on_tradingview(
    tv_png: bytes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    symbol: str,
    interval_minutes: int,
    hours: int,
) -> bytes:
    import matplotlib.image as mpimg

    img = mpimg.imread(io.BytesIO(tv_png))
    img_h, img_w = img.shape[:2]
    dpi = 100
    fig, ax = plt.subplots(figsize=(img_w / dpi, img_h / dpi), dpi=dpi)
    fig.subplots_adjust(0, 0, 1, 1)
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.imshow(img, extent=[0, 1, 0, 1], aspect="equal", zorder=0, interpolation="bilinear")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    y_min, y_max = _tv_visible_price_range(bars, ta)

    def y_at(price: float) -> float:
        return _price_to_axis_y(price, y_min, y_max)

    x_line_lo, x_line_hi, x_lbl = 0.04, 0.82, 0.84

    if ta.breakout_level:
        y = y_at(ta.breakout_level)
        ax.axhline(y, xmin=x_line_lo, xmax=x_line_hi, color=CHART_STYLE["entry"], linewidth=1.4, alpha=0.9, zorder=2)
        ax.text(x_lbl, y, f" R {fmt_price(ta.breakout_level)}", color=CHART_STYLE["entry"], fontsize=7.2, va="center", fontweight="bold")
    if ta.breakdown_level:
        y = y_at(ta.breakdown_level)
        ax.axhline(y, xmin=x_line_lo, xmax=x_line_hi, color=CHART_STYLE["accent_short"], linewidth=1.4, alpha=0.9, zorder=2)
        ax.text(x_lbl, y, f" S {fmt_price(ta.breakdown_level)}", color=CHART_STYLE["accent_short"], fontsize=7.2, va="center", fontweight="bold")

    for fl in getattr(ta, "fib_levels", None) or []:
        if fl.ratio not in {0.5, 0.618, 1.272}:
            continue
        if not _tv_level_in_range(fl.price, y_min, y_max):
            continue
        y = y_at(fl.price)
        is_key = fl.ratio in {0.5, 0.618}
        ratio_lbl = "0.5" if abs(fl.ratio - 0.5) < 1e-9 else (
            "0.618" if abs(fl.ratio - 0.618) < 1e-9 else fl.label
        )
        ax.axhline(
            y, xmin=x_line_lo, xmax=x_line_hi,
            color=CHART_STYLE["fib_key"],
            linewidth=1.0 if is_key else 0.7,
            alpha=0.75 if is_key else 0.55,
            linestyle="-." if is_key else ":",
            zorder=2,
        )
        ax.text(
            x_lbl, y, f" Fib {ratio_lbl}",
            color=CHART_STYLE["fib_key"],
            fontsize=7.0 if is_key else 6.2,
            va="center",
            alpha=0.95,
            fontweight="bold" if is_key else "normal",
        )

    if ta.invalidation_price:
        y = y_at(ta.invalidation_price)
        ax.axhline(y, xmin=x_line_lo, xmax=x_line_hi, color=CHART_STYLE["inv"], linewidth=0.9, linestyle="--", alpha=0.75, zorder=2)
        ax.text(x_lbl, y, f" SL {fmt_price(ta.invalidation_price)}", color=CHART_STYLE["inv"], fontsize=6.8, va="center")

    for j, tp in enumerate(ta.target_prices[:2]):
        if not _tv_level_in_range(tp, y_min, y_max):
            continue
        y = y_at(tp)
        ax.axhline(y, xmin=x_line_lo, xmax=x_line_hi, color=CHART_STYLE["target"], linewidth=0.85, alpha=0.65, linestyle="--", zorder=2)
        ax.text(x_lbl, y, f" TP{j + 1} {fmt_price(tp)}", color=CHART_STYLE["target"], fontsize=6.8, va="center", clip_on=True)

    current = bars[-1].close
    _draw_tv_forecast_paths(ax, ta, bars, current, y_at, y_min=y_min, y_max=y_max)

    header = f"{symbol} · {ta.verdict} {ta_display_score(ta)}/10 · {interval_minutes}m"
    if ta.dist_to_long_pct is not None or ta.dist_to_short_pct is not None:
        bits: list[str] = []
        if ta.dist_to_long_pct is not None:
            bits.append(f"L {ta.dist_to_long_pct:.1f}%")
        if ta.dist_to_short_pct is not None:
            bits.append(f"S {ta.dist_to_short_pct:.1f}%")
        header += f" · {' / '.join(bits)}"
    ax.text(
        0.02, 0.98, header,
        transform=ax.transAxes, va="top", ha="left", color=CHART_STYLE["text"], fontsize=8.2, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.28", facecolor="#161b22cc", edgecolor=CHART_STYLE["panel_border"]),
    )
    if ta.verdict == "WAIT" and "вход невыгоден" in (ta.verdict_reason or ""):
        ax.text(
            0.02, 0.91, "⛔ NO TRADE",
            transform=ax.transAxes, va="top", ha="left", color="#ff7b72", fontsize=8, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.22", facecolor="#161b22cc", edgecolor="#ff7b72"),
        )

    panel = ta_chart_tv_overlay_text(ta, hours=hours, interval_minutes=interval_minutes)
    ax.text(
        0.98, 0.98, panel,
        transform=ax.transAxes, va="top", ha="right", color=CHART_STYLE["text"], fontsize=7.6,
        bbox=dict(boxstyle="round,pad=0.30", facecolor="#161b22cc", edgecolor=CHART_STYLE["panel_border"]),
        linespacing=1.24,
    )

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    buffer = io.BytesIO()
    fig.savefig(
        buffer,
        format="png",
        dpi=dpi,
        facecolor=fig.get_facecolor(),
        pad_inches=0,
        bbox_inches=None,
    )
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


async def render_annotated_chart(
    symbol: str,
    *,
    side: str = "long",
    hours: int = 5,
    interval_minutes: int = 5,
    structure_warning: str = "",
    oi_bars: list[FiveMinOiBar] | None = None,
    invalidation_price: float | None = None,
    verdict_override: str | None = None,
    neutral: bool = False,
    chart_source: str = "annotated",
    exchange: str = "bybit",
    liq_context: dict | None = None,
    pattern_detection_enabled: bool = True,
    pattern_min_confidence: float = 0.55,
    display_hours: int | None = None,
) -> tuple[bytes | None, TAAnalysisResult | None]:
    # Полный lookback для паттернов/EW; на экране — зум display_hours
    analysis_hours = max(hours, pattern_chart_hours(interval_minutes))
    zoom_hours = chart_display_hours(interval_minutes, configured=display_hours)
    zoom_hours = min(zoom_hours, analysis_hours)
    bars = await _fetch_bars(symbol, analysis_hours, interval_minutes=interval_minutes)
    if not bars:
        return None, None

    btc_bars: list[KlineBar] | None = None
    htf_bars: list[KlineBar] | None = None
    history_bars: list[KlineBar] | None = bars
    if symbol.upper() not in {"BTCUSDT", "BTCUSD", "BTCUSDC"}:
        btc_bars = await _fetch_bars("BTCUSDT", analysis_hours, interval_minutes=interval_minutes)
    if interval_minutes <= 15:
        htf_bars = await _fetch_bars(symbol, max(24, analysis_hours * 2), interval_minutes=60)

    taker_cvd = None
    if symbol:
        lookback_min = min(180.0, max(20.0, len(bars) * interval_minutes))
        try:
            taker_cvd = await get_taker_cvd_cache().get_cvd(
                symbol, lookback_minutes=lookback_min,
            )
        except Exception:
            logger.debug("Taker CVD fetch failed for %s", symbol, exc_info=True)

    is_long = side == "long"
    ta = run_ta_analysis(
        bars,
        is_long=is_long,
        oi_bars=oi_bars,
        btc_bars=btc_bars,
        htf_bars=htf_bars,
        symbol=symbol,
        hours=analysis_hours,
        invalidation_price=invalidation_price,
        neutral=neutral,
        liq_context=liq_context,
        interval_minutes=interval_minutes,
        history_bars=history_bars,
        taker_cvd=taker_cvd,
        pattern_detection_enabled=pattern_detection_enabled,
        pattern_min_confidence=pattern_min_confidence,
    )
    if verdict_override:
        ta.verdict = verdict_override

    # Зум экрана: ≥12ч на 5m + расширить, если EW/дамп шире окна
    ew_idxs = [
        int(getattr(p, "index", -1))
        for p in (getattr(ta, "elliott_draw_points", None) or [])
        if getattr(p, "index", -1) >= 0
    ]
    elliott_span = (max(ew_idxs) - min(ew_idxs)) if len(ew_idxs) >= 2 else 0
    zoom_hours = structure_aware_display_hours(
        interval_minutes=interval_minutes,
        analysis_hours=analysis_hours,
        configured=display_hours,
        drawdown_pct=float(getattr(ta, "drawdown_from_high_pct", 0) or 0),
        elliott_span_bars=elliott_span,
        fib_span_bars=0,
    )

    source = (chart_source or "annotated").lower()
    if source in {"tv_annotated", "tradingview"}:
        try:
            tv_png = await asyncio.wait_for(
                chart_capture_service.capture_tradingview(
                    exchange, symbol, interval_minutes=interval_minutes,
                ),
                timeout=18.0,
            )
        except asyncio.TimeoutError:
            tv_png = None
        if tv_png:
            if source == "tv_annotated":
                return (
                    _overlay_ta_on_tradingview(
                        tv_png, bars, ta,
                        symbol=symbol,
                        interval_minutes=interval_minutes,
                        hours=hours,
                    ),
                    ta,
                )
            return tv_png, ta
        logger.info("TradingView unavailable for %s, fallback to matplotlib", symbol)

    accent = CHART_STYLE["accent_long"] if is_long else CHART_STYLE["accent_short"]
    pro_mode = source == "annotated_pro"
    title = f"Bybit {interval_minutes}m · вид {zoom_hours}ч"
    if analysis_hours > zoom_hours:
        title = f"{title} (анализ {analysis_hours}ч)"
    png = _render_chart_figure(
        bars, ta,
        symbol=symbol,
        title_suffix=title,
        accent_color=accent,
        interval_minutes=interval_minutes,
        pro_mode=pro_mode,
        display_hours=zoom_hours,
        enhanced=source in {"annotated", "annotated_pro"},
    )
    return png, ta


async def render_signal_chart(
    symbol: str,
    *,
    side: str = "long",
    hours: int = 5,
    interval_minutes: int = 5,
    structure_warning: str = "",
    probability_percent: float | None = None,
    oi_bars: list[FiveMinOiBar] | None = None,
    liq_context: dict | None = None,
    chart_source: str = "annotated",
    exchange: str = "bybit",
    display_hours: int | None = None,
) -> tuple[bytes | None, TAAnalysisResult | None]:
    png, ta = await render_annotated_chart(
        symbol,
        side=side,
        hours=hours,
        interval_minutes=interval_minutes,
        structure_warning=structure_warning,
        oi_bars=oi_bars,
        liq_context=liq_context,
        neutral=True,
        chart_source=chart_source,
        exchange=exchange,
        display_hours=display_hours,
    )
    if png is None:
        return None, None
    if probability_percent is not None and ta is not None:
        logger.debug(
            "Annotated chart %s: TA %s %s/10, prob %.0f%%",
            symbol, ta.verdict, ta.verdict_confidence, probability_percent,
        )
    return png, ta


async def render_analysis_chart(
    symbol: str,
    *,
    direction: str,
    hours: int = 5,
    interval_minutes: int = 5,
    invalidation_price: float | None = None,
    oi_bars: list[FiveMinOiBar] | None = None,
    liq_context: dict | None = None,
    exchange: str = "bybit",
) -> tuple[bytes | None, TAAnalysisResult | None]:
    is_long = direction != "short"
    verdict_override = "WAIT" if direction == "wait" else None
    return await render_annotated_chart(
        symbol,
        side="long" if is_long else "short",
        hours=hours,
        interval_minutes=interval_minutes,
        invalidation_price=invalidation_price,
        oi_bars=oi_bars,
        liq_context=liq_context,
        neutral=True,
        exchange=exchange,
        verdict_override=verdict_override,
    )


async def get_signal_chart_png(
    signal_exchange: str,
    signal_symbol: str,
    *,
    chart_source: str = "annotated",
    chart_hours: int = 5,
    chart_interval_minutes: int = 5,
    side: str = "long",
    structure_warning: str = "",
    probability_percent: float | None = None,
    coinglass_url: str = "",
    oi_bars: list[FiveMinOiBar] | None = None,
    liq_context: dict | None = None,
    display_hours: int | None = None,
) -> tuple[bytes | None, str, TAAnalysisResult | None, str]:
    source = (chart_source or "annotated").lower()
    fail_reason = ""

    if source in {"annotated", "annotated_pro", "tv_annotated"}:
        png, ta = await render_signal_chart(
            signal_symbol,
            side=side,
            hours=chart_hours,
            interval_minutes=chart_interval_minutes,
            structure_warning=structure_warning,
            probability_percent=probability_percent,
            oi_bars=oi_bars,
            liq_context=liq_context,
            chart_source=source,
            exchange=signal_exchange,
            display_hours=display_hours,
        )
        if png:
            return png, source, ta, ""
        return None, "none", ta, "нет свечей Bybit или ошибка matplotlib"

    if source == "tradingview":
        try:
            png = await asyncio.wait_for(
                chart_capture_service.capture_tradingview(
                    signal_exchange,
                    signal_symbol,
                    interval_minutes=chart_interval_minutes,
                ),
                timeout=12.0,
            )
            if png:
                return png, "tradingview", None, ""
            fail_reason = "TradingView screenshot пустой"
        except asyncio.TimeoutError:
            fail_reason = "TradingView timeout 12с"
        except Exception as exc:
            fail_reason = f"TradingView: {exc}"
            logger.warning("TradingView chart failed for %s: %s", signal_symbol, exc)
    elif source == "coinglass" and coinglass_url:
        try:
            png = await asyncio.wait_for(
                chart_capture_service.capture_coinglass(coinglass_url),
                timeout=12.0,
            )
            if png:
                return png, "coinglass", None, ""
            fail_reason = "CoinGlass screenshot пустой"
        except asyncio.TimeoutError:
            fail_reason = "CoinGlass timeout 12с"
        except Exception as exc:
            fail_reason = f"CoinGlass: {exc}"
            logger.warning("CoinGlass chart failed for %s: %s", signal_symbol, exc)
    elif source == "coinglass":
        fail_reason = "нет URL CoinGlass"

    if source not in {"generated", "annotated", "annotated_pro", "tv_annotated"}:
        logger.info("Chart %s unavailable for %s (%s), fallback to annotated", source, signal_symbol, fail_reason)

    png, ta = await render_signal_chart(
        signal_symbol,
        side=side,
        hours=chart_hours,
        interval_minutes=chart_interval_minutes,
        structure_warning=structure_warning,
        probability_percent=probability_percent,
        oi_bars=oi_bars,
        liq_context=liq_context,
        chart_source="annotated",
        exchange=signal_exchange,
    )
    if png:
        return png, "annotated", ta, fail_reason
    extra = fail_reason or "annotated fallback не удался"
    return None, "none", ta, extra
