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

from .bybit_klines import BybitKlineCache, KlineBar, fetch_bybit_klines_sync
from .bybit_cvd import get_taker_cvd_cache
from .chart_pattern_draw import (
    draw_chart_patterns,
    draw_htf_pattern_levels,
    draw_pattern_foresight_path,
)
from .chart_elliott_draw import draw_elliott_waves
from .pattern_specs import MAX_CHART_PATTERNS, MIN_DRAW_CONFIDENCE
from .chart_reference_levels import draw_reference_horizontals
from .chart_pro_layers import (
    draw_buy_flat_sell_zones,
    draw_pro_chart_layers,
)
from .manual_ta import (
    chart_display_hours,
    intraday_chart_zoom_hours,
    manual_chart_zoom_hours,
    pattern_chart_hours,
    structure_aware_display_hours,
)
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

# Signal/manual PNG: один DPI на create+save; ~2560px ширина — норм для Telegram без «мыла»
SIGNAL_CHART_DPI = 160
SIGNAL_CHART_FIG_SIZE = (16.0, 8.8)

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
    "entry": "#3fb950",
    "stop": "#f85149",
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
    plan_side = ""
    if is_wait:
        from .human_trade_brief import preferred_trade_side

        plan_side = preferred_trade_side(ta)
    show_plan = not is_wait or plan_side in {"long", "short"}
    if show_plan:
        if ta.invalidation_price:
            tag = f"SL {plan_side[:1].upper()}" if plan_side else "SL"
            add(
                ta.invalidation_price,
                f"{tag} {fmt_price(ta.invalidation_price)}",
                CHART_STYLE["inv"],
            )
        for j, tp in enumerate(ta.target_prices[:3]):
            add(tp, f"TP{j + 1} {fmt_price(tp)}", CHART_STYLE["target"])
        if ta.entry_zone and len(ta.entry_zone) == 2:
            lo, hi = ta.entry_zone
            add(hi, f"вход {fmt_price(lo)}–{fmt_price(hi)}", CHART_STYLE["accent_long"], "bottom")
    for fl in getattr(ta, "fib_levels", None) or []:
        if fl.ratio in {0.5, 0.618}:
            add(fl.price, fl.label, CHART_STYLE["fib_key"])
    for lv in ta.levels[:2]:
        color = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
        add(lv.price, fmt_price(lv.price), color)
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


def _draw_candles(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    interval_minutes: int = 5,
    crisp: bool = False,
) -> None:
    if not bars:
        return
    # Чуть шире тело — иначе на широком Y/дырах выглядят как точки
    body_frac = 0.94 if crisp else 0.88
    width_minutes = max(interval_minutes * body_frac, 2.5)
    width_days = width_minutes / (24 * 60)
    wick_lw = 1.45 if crisp else 1.15
    edge_lw = 0.72 if crisp else 0.55
    for bar in bars:
        ts = datetime.fromtimestamp(bar.open_time, tz=timezone.utc)
        color = CHART_STYLE["up"] if bar.close >= bar.open else CHART_STYLE["down"]
        ax.plot([ts, ts], [bar.low, bar.high], color=color, linewidth=wick_lw, solid_capstyle="round")
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        wick = (bar.high - bar.low) if bar.high > bar.low else 0.0
        height = max(
            body_high - body_low,
            wick * 0.14 if wick > 0 else abs(bar.close) * 0.0006,
        )
        rect = Rectangle(
            (mdates.date2num(ts) - width_days / 2, body_low),
            width_days,
            height,
            facecolor=color,
            edgecolor=color,
            linewidth=edge_lw,
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
    alpha: float = 0.75,
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
                alpha=alpha,
                shrinkA=0,
                shrinkB=0,
            ),
        )
        x, y = next_x, tp


def _draw_wait_chart_paths(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """WAIT: лёгкие пути по lean; без «жирной» стрелки против текста ИТОГ."""
    if (getattr(ta, "verdict", "") or "").upper() != "WAIT":
        return
    if getattr(ta, "reading_draw_both_forecasts", True) is False:
        lean = (getattr(ta, "action_priority", "") or "").lower()
        if lean == "long":
            if ta.continuation_path and ta.continuation_path.waypoints:
                _draw_zigzag_forecast_path(
                    ax,
                    bars,
                    ta.continuation_path.waypoints,
                    color=CHART_STYLE["accent_long"],
                    label=ta.continuation_path.label,
                    alpha=0.55,
                    lw=1.0,
                )
            return
        if lean == "short":
            if ta.correction_path and ta.correction_path.waypoints:
                _draw_zigzag_forecast_path(
                    ax,
                    bars,
                    ta.correction_path.waypoints,
                    color="#ffa657",
                    label=ta.correction_path.label,
                    alpha=0.55,
                    lw=1.0,
                )
            return
        return
    lean = (getattr(ta, "action_priority", "") or "").lower()
    if lean not in {"long", "short"}:
        ps = (getattr(ta, "primary_scenario", "") or "").lower()
        if "вверх" in ps or "long" in ps or "пробой" in ps and "↓" not in ps:
            lean = "long"
        elif "вниз" in ps or "short" in ps:
            lean = "short"

    draw_both = getattr(ta, "reading_draw_both_forecasts", True)
    if ta.bullish_scenario and ta.bullish_scenario.target_prices:
        if draw_both or lean in {"long", ""}:
            _draw_scenario_path(
                ax,
                bars,
                ta.bullish_scenario,
                color=CHART_STYLE["scenario_bull"],
                alpha=0.75 if lean == "long" else (0.22 if lean == "short" else 0.35),
            )
    if ta.bearish_scenario and ta.bearish_scenario.target_prices:
        if draw_both or lean in {"short", ""}:
            _draw_scenario_path(
                ax,
                bars,
                ta.bearish_scenario,
                color=CHART_STYLE["scenario_bear"],
                alpha=0.75 if lean == "short" else (0.22 if lean == "long" else 0.35),
            )
    # Zigzag прогнозы — не дублируем обе стороны против lean
    if lean != "long" and ta.correction_path and ta.correction_path.waypoints:
        _draw_zigzag_forecast_path(
            ax,
            bars,
            ta.correction_path.waypoints,
            color="#ffa657",
            label=ta.correction_path.label,
            alpha=0.70 if lean == "short" else 0.35,
            lw=1.15,
        )
    if lean != "short" and ta.continuation_path and ta.continuation_path.waypoints:
        _draw_zigzag_forecast_path(
            ax,
            bars,
            ta.continuation_path.waypoints,
            color=CHART_STYLE["accent_long"],
            label=ta.continuation_path.label,
            alpha=0.70 if lean == "long" else 0.35,
            lw=1.15,
        )


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
    if not getattr(ta, "reading_accept_fib", True):
        return
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


def _remap_ew_points_ot(
    snaps: tuple[tuple[str, float, float], ...] | list[tuple[str, float, float]] | None,
    bars: list[KlineBar],
    *,
    max_skew_sec: float = 900.0,
) -> list:
    """Перенос точек алерта на индексы текущего окна баров по open_time."""
    from .elliott_wave import ElliottPoint

    if not snaps or not bars:
        return []
    by_t = {float(b.open_time): i for i, b in enumerate(bars)}
    times = [float(b.open_time) for b in bars]
    out: list = []
    for item in snaps:
        if len(item) < 3:
            continue
        label, t, price = str(item[0]), float(item[1]), float(item[2])
        if not label or price <= 0:
            continue
        idx = by_t.get(t)
        if idx is None and t > 0 and times:
            # ближайший бар по времени
            idx = min(range(len(times)), key=lambda i: abs(times[i] - t))
            if abs(times[idx] - t) > max_skew_sec:
                continue
        if idx is None:
            continue
        out.append(ElliottPoint(label, int(idx), float(price)))
    return out


def _apply_wave_snapshot_points(
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    *,
    draw_ot: tuple[tuple[str, float, float], ...] | None = None,
    global_ot: tuple[tuple[str, float, float], ...] | None = None,
    local_ot: tuple[tuple[str, float, float], ...] | None = None,
) -> bool:
    """Жёстко наложить точки с WaveEvent — график = то, что дало сигнал."""
    g = _remap_ew_points_ot(global_ot, bars)
    loc = _remap_ew_points_ot(local_ot, bars)
    draw = _remap_ew_points_ot(draw_ot, bars)
    if not draw and not g and not loc:
        return False
    if not draw:
        draw = list(g) + list(loc)
    if not g and draw:
        # отделить импульс 0-5 / ABC от локальных меток
        loc_labs = {"·0", "i", "ii", "iii", "iv", "v", "a", "b", "c", "d", "e", "w", "x", "y", "z", "x2"}
        g = [p for p in draw if p.label not in loc_labs]
        loc = loc or [p for p in draw if p.label in loc_labs]
    ta.elliott_global_draw_points = g
    ta.elliott_local_draw_points = loc
    ta.elliott_draw_points = draw
    return True


def _ta_elliott_point_lists(ta: TAAnalysisResult) -> tuple[list, list, list]:
    """(draw, global, local) — любые непустые слои для отрисовки."""
    draw = list(getattr(ta, "elliott_draw_points", None) or [])
    glob = list(getattr(ta, "elliott_global_draw_points", None) or [])
    loc = list(getattr(ta, "elliott_local_draw_points", None) or [])
    return draw, glob, loc


def _ta_elliott_indices(ta: TAAnalysisResult) -> list[int]:
    idxs: list[int] = []
    for bucket in _ta_elliott_point_lists(ta):
        for p in bucket:
            i = int(getattr(p, "index", -1))
            if i >= 0:
                idxs.append(i)
    return idxs


def _wave_chart_zoom_hours(
    *,
    bars: list[KlineBar],
    interval_minutes: int,
    analysis_hours: float,
    base_zoom: float,
    point_indices: list[int],
) -> float:
    """Зум обязан покрыть САМУЮ РАННЮЮ точку волны → сейчас (не только span).

    Раньше брали span(max−min): волны слева от окна 12ч оставались за xlim —
    легенда обещала круги, на скрине пусто.
    """
    zoom = float(base_zoom)
    if not bars or not point_indices or interval_minutes <= 0:
        return zoom
    from_idx = max(0, min(point_indices) - 8)
    need_bars = max(36, len(bars) - from_idx)
    need_h = (need_bars * interval_minutes) / 60.0
    return min(float(analysis_hours), max(zoom, need_h, 6.0))


def _ensure_wave_elliott_points(ta: TAAnalysisResult, bars: list[KlineBar]) -> None:
    """Для wave-chart: сырые точки EW без фильтра «боковик/шум».

    run_ta_analysis часто обнуляет elliott_*_draw_points через
    _filter_elliott_draw_points, оставляя label/phase — панели врут про круги.
    """
    if not bars:
        return
    from .elliott_wave import analyze_elliott_waves
    from .ta_analysis import find_swing_points

    swings = list(getattr(ta, "swings", None) or [])
    if len(swings) < 4:
        swings = find_swing_points(bars)
    if len(swings) < 4:
        return
    ew = analyze_elliott_waves(bars, swings)
    g = list(getattr(ew, "global_draw_points", None) or [])
    loc = list(getattr(ew, "local_draw_points", None) or [])
    draw = list(getattr(ew, "draw_points", None) or [])
    if not draw and ew.impulse is not None and ew.impulse.points:
        draw = list(ew.impulse.points)
        if ew.abc is not None and ew.abc.points:
            draw = draw + list(ew.abc.points)
    if not (g or loc or draw):
        return
    ta.elliott_global_draw_points = g
    ta.elliott_local_draw_points = loc
    ta.elliott_draw_points = draw if draw else (list(g) + list(loc))
    if ew.label_ru:
        ta.elliott_label = ew.label_ru
    if ew.phase:
        ta.elliott_phase = ew.phase
    if ew.confidence:
        ta.elliott_confidence = int(ew.confidence)
    if ew.global_label_ru:
        ta.elliott_global_label = ew.global_label_ru
    if ew.local_label_ru:
        ta.elliott_local_label = ew.local_label_ru
    if ew.structure_note_ru:
        ta.elliott_structure_note = ew.structure_note_ru
    ta.elliott_fib_classic_ok = bool(
        getattr(ew.impulse, "fib_classic_ok", False) if ew.impulse else False
    )
    if ew.impulse is not None:
        ta.elliott_fib_w2 = float(getattr(ew.impulse, "fib_w2_ratio", 0) or 0)
        ta.elliott_fib_w4 = float(getattr(ew.impulse, "fib_w4_ratio", 0) or 0)
    if ew.path_reason_ru and not getattr(ta, "elliott_path_reason", ""):
        ta.elliott_path_reason = ew.path_reason_ru
    if ew.path_prices:
        ta.elliott_path_prices = list(ew.path_prices)
        ta.elliott_path_labels = list(ew.path_labels or [])
        ta.elliott_path_bias = ew.path_bias or ta.elliott_path_bias
    if ew.triangle_obj is not None:
        ta.elliott_triangle_obj = ew.triangle_obj


def _draw_elliott_from_ta(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult, *, is_wait: bool) -> None:
    """Общий блок отрисовки EW (LTF + HTF) из полей TA."""
    from .elliott_wave import ElliottWaveResult

    ew_pts, g_pts, l_pts = _ta_elliott_point_lists(ta)
    # Раньше: if ew_pts — global/local при пустом draw игнорировались
    if ew_pts or g_pts or l_pts:
        if not ew_pts:
            ew_pts = list(g_pts) or list(l_pts)
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
            path_horizon_hours=float(getattr(ta, "elliott_path_horizon_hours", 0) or 0),
            path_scenario=str(getattr(ta, "elliott_path_scenario", "") or ""),
            path_invalidation=getattr(ta, "elliott_path_invalidation", None),
            triangle_obj=getattr(ta, "elliott_triangle_obj", None),
            global_draw_points=list(g_pts),
            local_draw_points=list(l_pts),
            global_label_ru=str(getattr(ta, "elliott_global_label", "") or ""),
            local_label_ru=str(getattr(ta, "elliott_local_label", "") or ""),
            has_global=bool(g_pts),
            has_local=bool(l_pts),
        )
        from .pro_invariants import sanitize_path_prices

        path_side = str(getattr(ew_stub, "path_bias", "") or "").lower()
        if path_side in {"long", "short"} and ew_stub.path_prices:
            cur = float(getattr(ta, "current_price", 0) or (bars[-1].close if bars else 0))
            pp, pl = sanitize_path_prices(
                path_side, cur, ew_stub.path_prices, ew_stub.path_labels,
            )
            ew_stub.path_prices = pp
            ew_stub.path_labels = pl
            if not pp:
                ew_stub.path_bias = ""
        # Wave-focus: всегда рисуем entry/path по EW, даже если Hot сказал WAIT
        force_plan = not is_wait or bool(getattr(ta, "_wave_focus", False))
        if not force_plan:
            ew_stub.entry_plan = None
        elif getattr(ta, "elliott_entry_price", None):
            from .elliott_wave import ElliottEntryPlan

            side = (getattr(ta, "wave_bias", "") or "").lower()
            if side not in {"long", "short"}:
                v = (getattr(ta, "verdict", "") or "").upper()
                side = "long" if v == "LONG" else "short" if v == "SHORT" else "long"
            mode = getattr(ta, "elliott_entry_mode", "") or "wait"
            if mode not in {"conservative", "aggressive"}:
                mode = "conservative"
            ew_stub.entry_plan = ElliottEntryPlan(
                mode=mode,
                side=side,
                entry_price=ta.elliott_entry_price,
                stop_price=getattr(ta, "elliott_stop_price", None),
                tp1=(ta.elliott_tp_prices[0] if ta.elliott_tp_prices else None),
                tp2=(ta.elliott_tp_prices[1] if len(ta.elliott_tp_prices) > 1 else None),
                ready=bool(getattr(ta, "elliott_entry_ready", False)),
            )
        draw_elliott_waves(ax, bars, ew_stub, emphasis=bool(getattr(ta, "_wave_focus", False)))

    htf_pts = getattr(ta, "htf_elliott_draw_points", None) or []
    if htf_pts and not getattr(ta, "_wave_focus", False):
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


def _draw_wave_focus_annotations(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Только волны + ключевые Fib + зона входа — крупные метки, без шума."""
    setattr(ta, "_wave_focus", True)
    is_wait = (getattr(ta, "verdict", "") or "").upper() == "WAIT"

    # Не дублируем Fib из wave_structure — уже золотая сетка EW
    inv = getattr(ta, "elliott_path_invalidation", None) or getattr(ta, "invalidation_price", None)
    if inv:
        ax.axhline(float(inv), color=CHART_STYLE["inv"], linestyle="--", linewidth=1.25, alpha=0.95)
        ax.text(
            _x_after_last_bar(bars, 10),
            float(inv),
            f" INV {fmt_price(float(inv))} ",
            color=CHART_STYLE["inv"],
            fontsize=8.5,
            va="bottom",
            ha="left",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.15", facecolor="#0d1117", edgecolor=CHART_STYLE["inv"], alpha=0.85),
        )

    # Entry/Stop всегда (даже если EW-слой пуст — уровни из алерта)
    entry = getattr(ta, "elliott_entry_price", None)
    stop = getattr(ta, "elliott_stop_price", None)
    x_last = _x_after_last_bar(bars, 4)
    if entry:
        ax.axhline(float(entry), color=CHART_STYLE["entry"], linestyle="--", linewidth=1.35, alpha=0.9)
        ax.plot(
            [_idx_to_date(bars, len(bars) - 1)],
            [float(entry)],
            marker="o",
            color=CHART_STYLE["entry"],
            markersize=9,
            markeredgecolor="#0d1117",
            zorder=12,
        )
        ax.text(
            x_last, float(entry), f" ВХОД {fmt_price(float(entry))} ",
            color=CHART_STYLE["entry"], fontsize=8.5, va="bottom", ha="left", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.15", facecolor="#0d1117", edgecolor=CHART_STYLE["entry"], alpha=0.85),
        )
    if stop:
        ax.axhline(float(stop), color=CHART_STYLE["stop"], linestyle=":", linewidth=1.2, alpha=0.9)
        ax.plot(
            [_idx_to_date(bars, len(bars) - 1)],
            [float(stop)],
            marker="o",
            color=CHART_STYLE["stop"],
            markersize=8,
            markeredgecolor="#0d1117",
            zorder=12,
        )
        ax.text(
            x_last, float(stop), f" СТОП {fmt_price(float(stop))} ",
            color=CHART_STYLE["stop"], fontsize=8.0, va="top", ha="left", fontweight="bold",
        )

    for i, tp in enumerate((getattr(ta, "elliott_tp_prices", None) or [])[:2]):
        if not tp:
            continue
        ax.axhline(float(tp), color=CHART_STYLE["target"], linestyle=":", linewidth=1.0, alpha=0.8)
        ax.text(
            _x_after_last_bar(bars, 10),
            float(tp),
            f" TP{i+1} {fmt_price(float(tp))} ",
            color=CHART_STYLE["target"],
            fontsize=8.0,
            va="center",
            ha="left",
            fontweight="bold",
        )


def _draw_oil_price_action_overlays(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
) -> None:
    """EMA 9/21/50 + зоны спроса/предложения + подпись режима PA."""
    try:
        from .oil_price_action import analyze_oil_price_action
    except Exception:
        return
    pa = analyze_oil_price_action(bars, ta)
    if pa is None:
        return
    from datetime import datetime, timezone

    times = [
        datetime.fromtimestamp(float(b.open_time), tz=timezone.utc) for b in bars
    ]
    n = len(times)
    if n >= 2 and len(pa.ema9_series) == n:
        ax.plot(
            times, list(pa.ema9_series),
            color="#58a6ff", linewidth=1.05, alpha=0.9, label="EMA9", zorder=4,
        )
        ax.plot(
            times, list(pa.ema21_series),
            color="#e3b341", linewidth=1.1, alpha=0.9, label="EMA21", zorder=4,
        )
        ax.plot(
            times, list(pa.ema50_series),
            color="#f85149", linewidth=1.2, alpha=0.85, label="EMA50", zorder=4,
        )
    # Order blocks / demand-supply
    for ob in (pa.demand, pa.supply):
        if ob is None:
            continue
        color = CHART_STYLE["accent_long"] if ob.side == "demand" else CHART_STYLE["accent_short"]
        i0 = max(0, min(ob.start_idx, n - 1))
        x0 = mdates.date2num(times[i0])
        x1 = mdates.date2num(times[-1])
        try:
            from matplotlib.patches import Rectangle

            ax.add_patch(
                Rectangle(
                    (x0, ob.bottom),
                    max(0.0001, x1 - x0),
                    max(1e-6, ob.top - ob.bottom),
                    facecolor=color,
                    edgecolor=color,
                    alpha=0.18,
                    linewidth=0.9,
                    zorder=2,
                )
            )
        except Exception:
            ax.axhspan(ob.bottom, ob.top, facecolor=color, alpha=0.12, zorder=1)
        ax.text(
            times[-1],
            (ob.top + ob.bottom) / 2.0,
            f" {ob.label_ru}",
            color=color,
            fontsize=6.5,
            fontweight="bold",
            va="center",
            ha="left",
            zorder=6,
        )
    # Режим PA в углу
    mode_color = (
        CHART_STYLE["accent_long"] if pa.bias == "long"
        else CHART_STYLE["accent_short"] if pa.bias == "short"
        else CHART_STYLE["warning"]
    )
    ax.text(
        0.01, 0.98,
        pa.line_ru,
        transform=ax.transAxes,
        color=mode_color,
        fontsize=7.2,
        fontweight="bold",
        va="top",
        ha="left",
        zorder=10,
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#0d1117",
            edgecolor=mode_color,
            alpha=0.88,
        ),
    )


def _draw_essential_oil_overlays(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    compact: bool = False,
) -> None:
    """Лёгкий анализ нефти: PA/EMA/зоны/Fib/фигуры — без боковых панелей."""
    if not bars:
        return
    is_wait = (getattr(ta, "verdict", "") or "").upper() == "WAIT"

    # EMA + order blocks + режим пробой/отскок/ретест
    try:
        _draw_oil_price_action_overlays(ax, bars, ta)
    except Exception:
        logger.debug("oil PA overlays skipped", exc_info=True)

    # FVG / SMC кратко
    if not compact:
        try:
            smc = getattr(ta, "smc", None)
            if smc is not None:
                _draw_smc_annotations(ax, bars, ta)
        except Exception:
            logger.debug("oil SMC overlays skipped", exc_info=True)

    # Зоны поддержки/сопротивления + buy/sell flat
    if not compact:
        try:
            draw_buy_flat_sell_zones(ax, bars, ta)
        except Exception:
            logger.debug("buy/flat/sell zones skipped", exc_info=True)
    try:
        _draw_zones(ax, bars, ta)
    except Exception:
        logger.debug("zones skipped", exc_info=True)
    if not compact:
        try:
            _draw_channel(ax, bars, ta)
        except Exception:
            logger.debug("channel skipped", exc_info=True)

    if ta.breakout_level:
        ax.axhline(
            ta.breakout_level, color=CHART_STYLE["entry"],
            linestyle="-", linewidth=1.0, alpha=0.85,
        )
    if ta.breakdown_level and (
        ta.breakout_level is None
        or abs(ta.breakdown_level - ta.breakout_level) > max(ta.current_price, 1e-9) * 0.0005
    ):
        ax.axhline(
            ta.breakdown_level, color=CHART_STYLE["accent_short"],
            linestyle="-", linewidth=0.9, alpha=0.8,
        )

    level_cap = 2 if compact else 3
    for lv in (ta.levels or [])[:level_cap]:
        color = (
            CHART_STYLE["level_support"]
            if getattr(lv, "kind", "") == "support"
            else CHART_STYLE["level_resistance"]
        )
        ax.axhline(lv.price, color=color, linestyle="-", linewidth=0.85, alpha=0.6)

    # Ключевые Fib — 38.2 / 50 / 61.8 (compact: только 0.618)
    for fl in getattr(ta, "fib_levels", None) or []:
        ratio = float(getattr(fl, "ratio", 0) or 0)
        if compact:
            if abs(ratio - 0.618) > 0.01:
                continue
        elif abs(ratio - 0.382) > 0.01 and abs(ratio - 0.5) > 0.01 and abs(ratio - 0.618) > 0.01:
            continue
        ax.axhline(
            float(fl.price),
            color=CHART_STYLE["fib_key"],
            linestyle="--",
            linewidth=0.8,
            alpha=0.6,
        )

    if ta.invalidation_price:
        ax.axhline(
            ta.invalidation_price, color=CHART_STYLE["inv"],
            linestyle="--", linewidth=1.05, alpha=0.85,
        )
    for tp in (ta.target_prices or [])[:2]:
        if is_wait:
            break
        if ta.verdict == "SHORT" and tp >= ta.current_price:
            continue
        if ta.verdict == "LONG" and tp <= ta.current_price:
            continue
        ax.axhline(tp, color=CHART_STYLE["target"], linestyle=":", linewidth=0.9, alpha=0.75)

    if ta.entry_zone:
        lo, hi = ta.entry_zone
        ax.axhspan(lo, hi, color=CHART_STYLE["accent_long"], alpha=0.12)

    try:
        _draw_signal_markers(ax, bars, ta)
    except Exception:
        logger.debug("signal markers skipped", exc_info=True)

    # Треугольник Vataga / BuyHold — профессионально на свечах
    try:
        from .chart_pattern_draw import draw_chart_patterns
        from .oil_triangle import TRIANGLE_KINDS, pick_oil_triangle_pattern
        from .pattern_specs import MIN_DRAW_CONFIDENCE

        primary = pick_oil_triangle_pattern(ta) or getattr(ta, "primary_chart_pattern", None)
        patterns = list(getattr(ta, "chart_patterns", None) or [])
        # Предпочитаем треугольник; иначе любая primary фигура
        force = primary
        if force is not None:
            pkind = (getattr(force, "kind", "") or "").lower()
            if pkind not in TRIANGLE_KINDS and pkind != "false_breakout":
                # всё равно рисуем primary, если это сильная фигура
                pass
        draw_chart_patterns(
            ax,
            bars,
            patterns,
            max_patterns=1,
            min_confidence=min(0.55, float(MIN_DRAW_CONFIDENCE)),
            force_primary=force,
            draw_target_labels=False,
        )
    except Exception:
        logger.debug("oil triangle draw skipped", exc_info=True)

    # Короткие ценники справа (не боковые «ИТОГ/ПЛАН»)
    try:
        _draw_right_price_labels(ax, bars, ta)
    except Exception:
        logger.debug("right labels skipped", exc_info=True)


def _draw_ta_annotations(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
) -> None:
    if not bars:
        return

    is_wait = (getattr(ta, "verdict", "") or "").upper() == "WAIT"
    has_chart_pattern = bool(getattr(ta, "primary_chart_pattern", None))

    draw_buy_flat_sell_zones(ax, bars, ta)
    _draw_zones(ax, bars, ta)
    _draw_smc_annotations(ax, bars, ta)
    if getattr(ta, "reading_accept_channel", True):
        _draw_channel(ax, bars, ta)
    _draw_extended_trend_lines(ax, bars, ta)
    _draw_consolidation_box(ax, bars, ta)
    if getattr(ta, "reading_accept_pattern", True):
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
    if getattr(ta, "reading_accept_htf_pattern", True):
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
        not has_setup_path
        and getattr(ta, "pattern_foresight_summary", "")
        and getattr(ta, "reading_accept_pattern", True)
    ):
        draw_pattern_foresight_path(
            ax,
            bars,
            current_price=float(getattr(ta, "current_price", 0) or (bars[-1].close if bars else 0)),
            pattern=getattr(ta, "primary_chart_pattern", None),
            horizon_hours=float(getattr(ta, "pattern_foresight_horizon", 0) or 0),
            bias=str(getattr(ta, "pattern_foresight_bias", "neutral") or "neutral"),
            watch_only=bool(getattr(ta, "pattern_foresight_watch_only", False)) or is_wait,
            status=str(getattr(ta, "pattern_foresight_status", "") or ""),
            quiet_labels=True,
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
    # RSI divergence lines on price — always (even WAIT)
    try:
        from .chart_pro_layers import draw_rsi_divergence_on_price

        draw_rsi_divergence_on_price(ax, bars, ta)
    except Exception:
        pass
    path_kind = draw_pro_chart_layers(ax, bars, ta)
    if is_wait:
        _draw_wait_chart_paths(ax, bars, ta)
    elif path_kind == "default":
        if not _draw_market_forecast_paths(ax, bars, ta):
            if direction == "long":
                _draw_scenario_path(ax, bars, ta.bullish_scenario, color=CHART_STYLE["scenario_bull"])
            elif direction == "short":
                _draw_scenario_path(ax, bars, ta.bearish_scenario, color=CHART_STYLE["scenario_bear"])
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


def _chart_reading_overlay_lines(ta: TAAnalysisResult) -> list[str]:
    """Короткая подсказка на графике — детали в подписи Telegram."""
    lines: list[str] = []
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    if stack:
        lines.append(stack)
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    trigger = str(getattr(ta, "setup_trigger", "") or "").strip()
    if trigger:
        lines.append(f"Триггер: {trigger[:90]}")
    elif seek:
        lines.append(f"Ждём: {seek[:90]}")
    v = (getattr(ta, "verdict", "") or "WAIT").upper()
    if v == "WAIT":
        lines.append("Не входим сейчас · SL/TP справа = черновик")
    return lines[:3]


_PDF_ZONE_COLORS: dict[str, str] = {
    "demand": "#3fb950",
    "supply": "#f85149",
    "sr_support": "#58a6ff",
    "sr_resistance": "#d29922",
    "ob_bull": "#2ea043",
    "ob_bear": "#da3633",
    "breaker_bull": "#a371f7",
    "breaker_bear": "#bc8cff",
}


def _draw_pdf_zone_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    current: float,
) -> None:
    metrics = getattr(ta, "market_metrics", None) or {}
    raw_zones = metrics.get("pdf_zones") if isinstance(metrics, dict) else None
    if not raw_zones or not bars:
        return
    from matplotlib.patches import Rectangle

    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    kind_short = {
        "demand": "спрос",
        "supply": "предл.",
        "ob_bull": "OB↑",
        "ob_bear": "OB↓",
        "sr_support": "S",
        "sr_resistance": "R",
        "breaker_bull": "BR↑",
        "breaker_bear": "BR↓",
    }
    for z in raw_zones[:4]:
        if not isinstance(z, dict):
            continue
        if not z.get("valid", False):
            continue
        top, bot = float(z.get("top", 0)), float(z.get("bottom", 0))
        if top <= bot or top <= 0:
            continue
        if abs((top + bot) / 2 - current) / current > 0.12:
            continue
        kind = str(z.get("kind", "demand"))
        base = _PDF_ZONE_COLORS.get(kind, "#8b949e")
        start_i = int(z.get("start_idx", max(0, len(bars) - 40)))
        x0 = mdates.date2num(_idx_to_date(bars, max(0, start_i)))
        ax.add_patch(
            Rectangle(
                (x0, bot),
                max(x1 - x0, 0.001),
                top - bot,
                facecolor=base,
                edgecolor=base,
                alpha=0.24,
                linewidth=1.0,
                zorder=1,
            )
        )
        tag = str(z.get("tf", "LTF"))
        lbl = kind_short.get(kind, kind[:6])
        note = str(z.get("label", "") or "")[:28]
        ax.text(
            x1,
            top,
            f" {tag}·{lbl} {note}".strip(),
            color=base,
            fontsize=6.2,
            fontweight="bold",
            va="bottom",
            ha="right",
            zorder=3,
            bbox=dict(
                boxstyle="round,pad=0.1",
                facecolor="#0d1117",
                edgecolor=base,
                alpha=0.85,
                linewidth=0.35,
            ),
        )
    poc_label = str(getattr(ta, "volume_poc_label", "") or "")
    if poc_label and "POC" in poc_label:
        import re

        m = re.search(r"POC\s*≈\s*([\d.]+)", poc_label)
        if m:
            poc = float(m.group(1))
            if abs(poc - current) / current <= 0.2:
                ax.axhline(
                    poc,
                    color="#8b949e",
                    linestyle=":",
                    linewidth=0.9,
                    alpha=0.75,
                    zorder=2,
                )
                ax.text(
                    x1,
                    poc,
                    " POC",
                    color="#8b949e",
                    fontsize=6,
                    va="center",
                    ha="right",
                )


def _draw_retest_markers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    current: float,
) -> None:
    metrics = getattr(ta, "market_metrics", None) or {}
    raw = metrics.get("retests") if isinstance(metrics, dict) else None
    if not raw or not bars:
        return
    x = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    for r in raw[:2]:
        if not isinstance(r, dict):
            continue
        price = float(r.get("price") or 0)
        if price <= 0 or abs(price - current) / current > 0.08:
            continue
        q = str(r.get("quality", ""))
        color = "#3fb950" if q == "ideal" else "#d29922" if q == "good" else "#8b949e"
        ax.plot(x, price, marker="o", color=color, markersize=5, zorder=9)
        ax.text(
            x,
            price,
            f" R",
            color=color,
            fontsize=6,
            va="center",
            ha="left",
        )


def _draw_clean_market_annotations(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    signal_chart: bool = False,
) -> None:
    """Show only the strongest structure, nearby levels, and actionable prices."""
    if not bars:
        return

    current = bars[-1].close
    try:
        draw_reference_horizontals(ax, bars, ta)
    except Exception:
        logger.debug("reference horizontals skipped", exc_info=True)
    if getattr(ta, "trend_lines", None):
        try:
            _draw_extended_trend_lines(ax, bars, ta)
        except Exception:
            logger.debug("trend lines skipped", exc_info=True)
    accept_pat = getattr(ta, "reading_accept_pattern", True)
    if signal_chart:
        accept_pat = accept_pat and ta.primary_chart_pattern is not None
    if accept_pat and ta.primary_chart_pattern is not None:
        draw_chart_patterns(
            ax,
            bars,
            [ta.primary_chart_pattern],
            max_patterns=1,
            min_confidence=MIN_DRAW_CONFIDENCE,
            force_primary=ta.primary_chart_pattern,
            draw_target_labels=False,
        )
    if getattr(ta, "reading_accept_htf_pattern", True):
        draw_htf_pattern_levels(
            ax,
            bars,
            ta.primary_htf_chart_pattern,
            conflict=bool(ta.pattern_foresight_htf_conflict),
            quiet=True,
        )

    smc = ta.smc
    if smc is not None:
        if smc.structure_break_level and abs(smc.structure_break_level / current - 1) <= 0.12:
            color = CHART_STYLE["accent_long"] if smc.structure_break_direction == "long" else CHART_STYLE["accent_short"]
            ax.axhline(
                smc.structure_break_level,
                color=color,
                linestyle="-.",
                linewidth=1.0,
                alpha=0.78,
                zorder=2,
            )
            bk = getattr(smc, "structure_break_kind", "bos") or "bos"
            ax.text(
                mdates.date2num(_idx_to_date(bars, len(bars) - 1)),
                smc.structure_break_level,
                f" {bk.upper()}",
                color=color,
                fontsize=6.5,
                va="bottom",
                ha="right",
                zorder=3,
            )
        if smc.liquidity_sweep:
            marker = next(
                (item for item in reversed(smc.markers) if item.kind == "sweep"),
                None,
            )
            if marker is not None and 0 <= marker.index < len(bars):
                when = _idx_to_date(bars, marker.index)
                color = CHART_STYLE["accent_long"] if marker.direction == "long" else CHART_STYLE["accent_short"]
                ax.annotate(
                    "снятие ликвидности",
                    xy=(when, marker.price),
                    xytext=(when, marker.price * (1.008 if marker.direction == "long" else 0.992)),
                    color=color,
                    fontsize=7,
                    arrowprops={"arrowstyle": "->", "color": color, "lw": 0.8},
                    zorder=8,
                )
        nearby_blocks = []
        if getattr(ta, "reading_accept_ob", True):
            nearby_blocks = [
                block for block in smc.order_blocks
                if not block.mitigated
                and abs(((block.top + block.bottom) / 2 - current) / current) <= 0.025
            ]
        if nearby_blocks:
            block = min(
                nearby_blocks,
                key=lambda item: abs((item.top + item.bottom) / 2 - current),
            )
            x0 = mdates.date2num(_idx_to_date(bars, max(0, block.start_idx)))
            x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
            color = CHART_STYLE["accent_long"] if block.direction == "bullish" else CHART_STYLE["accent_short"]
            ax.add_patch(Rectangle(
                (x0, block.bottom),
                max(x1 - x0, 0.001),
                max(block.top - block.bottom, 1e-9),
                facecolor=color,
                edgecolor=color,
                alpha=0.09,
                linewidth=0.8,
                zorder=1,
            ))
            ax.text(
                x1, block.top, " OB",
                color=color, fontsize=6.5, va="bottom", ha="right", zorder=3,
            )
        nearby_gaps = [
            gap for gap in smc.fvgs
            if abs(((gap.top + gap.bottom) / 2 - current) / current) <= 0.025
        ]
        if nearby_gaps:
            gap = nearby_gaps[-1]
            x0 = mdates.date2num(_idx_to_date(bars, max(0, gap.start_idx)))
            x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
            color = CHART_STYLE["accent_long"] if gap.direction == "bullish" else CHART_STYLE["accent_short"]
            ax.add_patch(Rectangle(
                (x0, gap.bottom),
                max(x1 - x0, 0.001),
                max(gap.top - gap.bottom, 1e-9),
                facecolor=color,
                edgecolor=color,
                alpha=0.13,
                linewidth=0.8,
                zorder=1,
            ))

    _draw_pdf_zone_layers(ax, bars, ta, current=current)
    _draw_retest_markers(ax, bars, ta, current=current)

    for label, price, color in (
        ("Поддержка", ta.nearest_support, CHART_STYLE["level_support"]),
        ("Сопротивление", ta.nearest_resistance, CHART_STYLE["level_resistance"]),
    ):
        if price is not None and price > 0 and abs(price / current - 1) <= 0.12:
            ax.axhline(price, color=color, linestyle=":", linewidth=0.9, alpha=0.75, zorder=2)

    if getattr(ta, "reading_accept_fib", True) and ta.fib_levels:
        for level in ta.fib_levels:
            if level.ratio not in {0.618, 0.705} or abs(level.price / current - 1) > 0.12:
                continue
            ax.axhline(
                level.price,
                color=CHART_STYLE["fib_key"],
                linestyle="-.",
                linewidth=0.9,
                alpha=0.72,
                zorder=2,
            )

    if ta.breakout_level and ta.verdict in {"LONG", "WAIT"}:
        ax.axhline(
            ta.breakout_level,
            color=CHART_STYLE["accent_long"],
            linewidth=1.1,
            alpha=0.85,
            zorder=3,
        )
    if ta.breakdown_level and ta.verdict in {"SHORT", "WAIT"}:
        ax.axhline(
            ta.breakdown_level,
            color=CHART_STYLE["accent_short"],
            linewidth=1.1,
            alpha=0.85,
            zorder=3,
        )
    from .human_trade_brief import preferred_trade_side

    plan_side = preferred_trade_side(ta)
    draw_plan = ta.verdict in {"LONG", "SHORT"} or (
        ta.verdict == "WAIT" and plan_side in {"long", "short"}
    )
    if draw_plan:
        if ta.invalidation_price:
            ax.axhline(
                ta.invalidation_price,
                color=CHART_STYLE["inv"],
                linestyle="--",
                linewidth=1.05,
                alpha=0.88,
                zorder=3,
            )
        for tp in (ta.target_prices or [])[:3]:
            ax.axhline(
                tp,
                color=CHART_STYLE["target"],
                linestyle=":",
                linewidth=0.95,
                alpha=0.82,
                zorder=3,
            )
        if ta.entry_zone and len(ta.entry_zone) == 2:
            lo, hi = ta.entry_zone
            ax.axhspan(lo, hi, color=CHART_STYLE["accent_long"], alpha=0.12, zorder=2)

    context = _chart_reading_overlay_lines(ta)
    if context:
        ax.text(
            0.012,
            0.985,
            "\n".join(context[:4]),
            transform=ax.transAxes,
            va="top",
            ha="left",
            color=CHART_STYLE["text"],
            fontsize=6.2,
            linespacing=1.12,
            zorder=10,
            bbox={
                "boxstyle": "round,pad=0.35",
                "facecolor": CHART_STYLE["panel"],
                "edgecolor": CHART_STYLE["panel_border"],
                "alpha": 0.93,
            },
        )


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
    if bear_text and ta.verdict in {"SHORT", "WAIT"}:
        bear_y = scenario_y - 0.22 if bull_text and ta.verdict == "WAIT" else scenario_y
        fig.text(rx, bear_y, bear_text, va="top", ha="right", **bear_style)

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


def _draw_wave_info_panels(fig: plt.Figure, ta: TAAnalysisResult, *, with_subpanels: bool = False) -> None:
    """Простые панели: что видим → что делать. Без жаргона Hot."""
    base_bbox = dict(
        boxstyle="round,pad=0.55",
        facecolor=CHART_STYLE["panel"],
        edgecolor=CHART_STYLE["panel_border"],
        alpha=0.94,
    )
    style = dict(
        transform=fig.transFigure,
        color=CHART_STYLE["text"],
        fontsize=8.2,
        linespacing=1.45,
        bbox=base_bbox,
    )
    lx, rx = 0.018, 0.982
    verdict = (getattr(ta, "verdict", "") or "WAIT").upper()
    side_color = (
        CHART_STYLE["accent_long"] if verdict == "LONG"
        else CHART_STYLE["accent_short"] if verdict == "SHORT"
        else CHART_STYLE["warning"]
    )

    phase = str(getattr(ta, "elliott_phase", "") or "")
    label = str(getattr(ta, "elliott_label", "") or "").strip()
    draw, glob, loc = _ta_elliott_point_lists(ta)
    all_pts = list(draw) + list(glob) + list(loc)
    labs = {str(getattr(p, "label", "")) for p in all_pts}
    has_impulse = bool(labs & {"0", "1", "2", "3", "4", "5", "i", "ii", "iii", "iv", "v"})
    has_abc = bool(labs & {"A", "B", "C", "D", "E", "a", "b", "c", "d", "e"})
    left = ["ЧТО НА ГРАФИКЕ"]
    if has_impulse:
        left.append("• импульс 1-2-3-4-5 (синие круги)")
    elif "impulse" in phase or "1-5" in label.lower() or "импульс" in label.lower():
        left.append("• импульс 1-2-3-4-5 (ищем разметку)")
    if has_abc:
        left.append("• коррекция A-B-C (оранжевые)")
    elif "abc" in phase or "ABC" in label or "abc" in label.lower():
        left.append("• коррекция A-B-C")
    if getattr(ta, "elliott_fib_classic_ok", False) or getattr(ta, "elliott_fib_w2", 0):
        left.append("• Fib 38.2 / 50 / 61.8% — зона отката")
    if not has_impulse and not has_abc:
        left.append("• разметка волн не наложена — смотри уровни справа")
    elif label:
        left.append(f"• {label[:70]}")
    fig.text(lx, 0.975, "\n".join(left), va="top", ha="left", **style)

    right = [f"ЧТО ДЕЛАТЬ · {verdict}"]
    expect = str(getattr(ta, "elliott_path_reason", "") or getattr(ta, "verdict_reason", "") or "").strip()
    if expect:
        right.append(f"1) {expect[:100]}")
    entry = getattr(ta, "elliott_entry_price", None)
    stop = getattr(ta, "elliott_stop_price", None)
    tps = list(getattr(ta, "elliott_tp_prices", None) or [])[:2]
    if entry:
        right.append(f"2) Вход ≈ {fmt_price(float(entry))}")
    if stop:
        right.append(f"3) Стоп ≈ {fmt_price(float(stop))}")
    if tps:
        right.append("4) Цели: " + " → ".join(fmt_price(float(t)) for t in tps))
    inv = getattr(ta, "elliott_path_invalidation", None) or getattr(ta, "invalidation_price", None)
    if inv:
        right.append(f"✗ Отмена если пробой {fmt_price(float(inv))}")
    fig.text(rx, 0.975, "\n".join(right), va="top", ha="right", **{**style, "color": side_color})

    if has_impulse or has_abc:
        fig.text(
            0.50, 0.018 if not with_subpanels else 0.125,
            "Синие круги = волны импульса  ·  Оранжевые = коррекция ABC  ·  Зелёный = вход",
            va="bottom",
            ha="center",
            fontsize=7.6,
            color=CHART_STYLE["text"],
            transform=fig.transFigure,
            bbox=dict(
                boxstyle="round,pad=0.4",
                facecolor=CHART_STYLE["panel"],
                edgecolor=CHART_STYLE["entry"],
                alpha=0.92,
            ),
        )
    else:
        fig.text(
            0.50, 0.018 if not with_subpanels else 0.125,
            "Зелёный = вход  ·  Красный = стоп  ·  Жёлтый/зелёный = TP  ·  волны не отрисованы",
            va="bottom",
            ha="center",
            fontsize=7.6,
            color=CHART_STYLE["warning"],
            transform=fig.transFigure,
            bbox=dict(
                boxstyle="round,pad=0.4",
                facecolor=CHART_STYLE["panel"],
                edgecolor=CHART_STYLE["warning"],
                alpha=0.92,
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

    show_bull = ta.bullish_scenario is not None and ta.verdict in {"LONG", "WAIT"}
    show_bear = ta.bearish_scenario is not None and ta.verdict in {"SHORT", "WAIT"}
    if ta.verdict == "LONG":
        show_bear = False
    elif ta.verdict == "SHORT":
        show_bull = False

    def _scenario_box(side: str, scenario, y: float) -> None:
        is_bull = side == "bull"
        lines = [
            "БЫЧИЙ СЦЕНАРИЙ" if is_bull else "МЕДВЕЖИЙ СЦЕНАРИЙ",
            f"Триггер: {'≥' if is_bull else '≤'} {fmt_price(scenario.trigger_price)}",
            f"TP1/TP2: {' / '.join(fmt_price(t) for t in scenario.target_prices[:2])}",
            f"SL: {fmt_price(scenario.stop_price)}",
        ]
        color = CHART_STYLE["accent_long"] if is_bull else CHART_STYLE["accent_short"]
        fc = "#0f1f17" if is_bull else "#231417"
        fig.text(
            right_x, y, "\n".join(lines),
            ha="right", va="top", fontsize=8.0, color=color,
            transform=fig.transFigure, linespacing=1.36,
            bbox=dict(boxstyle="round,pad=0.52", facecolor=fc, edgecolor=color, alpha=0.96),
        )

    if show_bull and show_bear and ta.verdict == "WAIT":
        if ta.bullish_scenario:
            _scenario_box("bull", ta.bullish_scenario, 0.50)
        if ta.bearish_scenario:
            _scenario_box("bear", ta.bearish_scenario, 0.22)
    elif show_bull and ta.bullish_scenario:
        _scenario_box("bull", ta.bullish_scenario, 0.36)
    elif show_bear and ta.bearish_scenario:
        _scenario_box("bear", ta.bearish_scenario, 0.36)


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


def _resolve_chart_height_scale(height_scale: float | None) -> float:
    return max(0.85, min(1.6, float(height_scale if height_scale is not None else 1.0)))


def _chart_figure_layout(
    *,
    enhanced: bool,
    pro_mode: bool,
    height_scale: float | None = None,
    wide_chart: bool = False,
) -> tuple[tuple[float, float], list[float] | None]:
    scale = _resolve_chart_height_scale(height_scale)
    if wide_chart:
        w, h = SIGNAL_CHART_FIG_SIZE
        return (w, h * scale), None
    if enhanced:
        candle_ratio = 4.0 + (scale - 1.0) * 3.0
        return (19.2, 10.8 * scale), [candle_ratio, 0.95, 1.15]
    if pro_mode:
        return (20.4, 10.2 * scale), None
    return (19.0, 8.5 * scale), None


def _draw_urals_inset(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    urals_price: float,
    brent_last: float,
    change_pct: float | None = None,
    display_hours: int = 18,
    interval_minutes: int = 15,
) -> None:
    """Маленькое окошко: Urals (российская нефть) — цена + мини-график."""
    if urals_price <= 0 or not bars:
        return
    vis = _visible_bars(bars, display_hours, interval_minutes)
    if len(vis) < 4:
        vis = bars[-min(48, len(bars)):]
    closes = [float(b.close) for b in vis]
    if brent_last <= 0:
        brent_last = closes[-1]
    discount = brent_last - urals_price
    spark = [max(1.0, c - discount) for c in closes]
    xs = list(range(len(spark)))

    try:
        inset = ax.inset_axes([0.015, 0.58, 0.26, 0.38])
    except Exception:
        return
    inset.set_facecolor("#121820")
    for spine in inset.spines.values():
        spine.set_color("#3d4f63")
        spine.set_linewidth(0.8)
    color = "#3dd68c" if (change_pct is None or change_pct >= 0) else "#f07178"
    inset.fill_between(xs, spark, min(spark), color=color, alpha=0.18, linewidth=0)
    inset.plot(xs, spark, color=color, linewidth=1.35, solid_capstyle="round")
    inset.set_xlim(0, max(xs[-1], 1))
    pad = (max(spark) - min(spark)) * 0.12 or 0.3
    inset.set_ylim(min(spark) - pad, max(spark) + pad)
    inset.set_xticks([])
    inset.set_yticks([])
    inset.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    chg_txt = ""
    if change_pct is not None:
        sign = "+" if change_pct >= 0 else ""
        chg_txt = f"  {sign}{change_pct:.1f}%"
    disc_txt = f"  Brent{discount:+.1f}" if abs(discount) < 40 else ""
    title = f"URALS · РФ  ${urals_price:.2f}{chg_txt}"
    inset.text(
        0.03, 0.96, title,
        transform=inset.transAxes, va="top", ha="left",
        fontsize=7.2, color="#e8eef5", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.18", facecolor="#0d1218", edgecolor="#3d4f63", alpha=0.92),
    )
    inset.text(
        0.03, 0.08, f"скидка к Brent{disc_txt}" if disc_txt else "российская нефть",
        transform=inset.transAxes, va="bottom", ha="left",
        fontsize=5.8, color="#9aabbc",
    )


def _draw_ut_bot_overlay(
    ax: plt.Axes,
    bars: list[KlineBar],
    ut: Any,
    *,
    interval_minutes: int = 5,
) -> None:
    """Метки Buy/Sell + trail UT прямо на свечах (как TradingView UT Bot Alerts)."""
    del interval_minutes
    if ut is None or not bars:
        return
    trails = getattr(ut, "trails", None) or ()
    buy_flags = getattr(ut, "buy_flags", None) or ()
    sell_flags = getattr(ut, "sell_flags", None) or ()
    bar_bull = getattr(ut, "bar_bull", None) or ()
    n_ut = len(trails)
    if n_ut < 2:
        return
    work = list(bars)
    if len(work) > n_ut:
        work = work[-n_ut:]
    o0 = n_ut - len(work)
    green = CHART_STYLE["accent_long"]
    red = CHART_STYLE["accent_short"]
    xs: list[float] = []
    ys: list[float] = []
    for i, b in enumerate(work):
        ui = o0 + i
        ts = datetime.fromtimestamp(float(b.open_time), tz=timezone.utc)
        x = mdates.date2num(ts)
        xs.append(x)
        ys.append(float(trails[ui]) if ui < n_ut else float(b.close))
        if ui < len(buy_flags) and buy_flags[ui]:
            ax.annotate(
                "Buy",
                xy=(x, float(b.low)),
                xytext=(0, -12),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=7,
                fontweight="bold",
                color="white",
                bbox=dict(
                    boxstyle="round,pad=0.18",
                    facecolor=green,
                    edgecolor=green,
                    alpha=0.95,
                ),
                zorder=8,
            )
        if ui < len(sell_flags) and sell_flags[ui]:
            ax.annotate(
                "Sell",
                xy=(x, float(b.high)),
                xytext=(0, 12),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=7,
                fontweight="bold",
                color="white",
                bbox=dict(
                    boxstyle="round,pad=0.18",
                    facecolor=red,
                    edgecolor=red,
                    alpha=0.95,
                ),
                zorder=8,
            )
    for i in range(1, len(xs)):
        ui = o0 + i
        bull = bool(bar_bull[ui]) if ui < len(bar_bull) else ys[i] <= float(work[i].close)
        ax.plot(
            xs[i - 1 : i + 1],
            ys[i - 1 : i + 1],
            color=green if bull else red,
            linewidth=1.15,
            solid_capstyle="round",
            zorder=7,
            alpha=0.95,
        )


def _render_chart_figure(
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    symbol: str,
    title_suffix: str,
    accent_color: str,
    interval_minutes: int = 5,
    pro_mode: bool = False,
    display_hours: int | None = None,
    height_scale: float | None = None,
    urals_price: float | None = None,
    urals_change_pct: float | None = None,
    ut_overlay: Any | None = None,
    clean_chart: bool = False,
    signal_chart: bool = False,
    manual_ta_chart: bool = False,
) -> bytes:
    """Рисует единый график без RSI/volume-панелей и боковых информационных колонок."""
    wide = manual_ta_chart or signal_chart
    fig_size, _ = _chart_figure_layout(
        enhanced=False,
        pro_mode=pro_mode,
        height_scale=height_scale,
        wide_chart=wide,
    )
    export_dpi = SIGNAL_CHART_DPI if wide else 120
    fig, ax = plt.subplots(figsize=fig_size, dpi=export_dpi)
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.set_facecolor(CHART_STYLE["bg"])
    if wide:
        fig.subplots_adjust(left=0.025, right=0.995, top=0.90, bottom=0.08)
    else:
        fig.subplots_adjust(left=0.08, right=0.97, top=0.92, bottom=0.10)

    ax.set_facecolor(CHART_STYLE["bg"])
    _draw_candles(ax, bars, interval_minutes=interval_minutes, crisp=wide)
    from .chart_display_policy import (
        ed_manual_ta_pa_chart_enabled,
        ed_signal_chart_pa_analysis_enabled,
    )

    signal_pa_layers = (
        signal_chart and ed_signal_chart_pa_analysis_enabled() and not manual_ta_chart
    ) or (manual_ta_chart and ed_manual_ta_pa_chart_enabled())
    if (manual_ta_chart or signal_chart) and not signal_pa_layers:
        try:
            from .chart_story_router import enrich_ta_for_chart_story

            ta = enrich_ta_for_chart_story(ta, bars)
        except Exception:
            logger.debug("Chart story enrich skipped", exc_info=True)
    pro_mode_chart: str | None = None
    if manual_ta_chart or (signal_chart and not signal_pa_layers):
        try:
            from .chart_pro import resolve_pro_chart_mode, signal_chart_legacy_enabled

            from .chart_unified import legacy_manual_chart_enabled

            pro_mode_chart = resolve_pro_chart_mode(
                ta,
                allow_legacy=legacy_manual_chart_enabled() and manual_ta_chart and not signal_chart,
            )
        except Exception:
            pro_mode_chart = "observation"
    story_minimal = (
        not signal_pa_layers
        and pro_mode_chart is not None
        and pro_mode_chart != "legacy_manual"
    )
    if signal_pa_layers:
        try:
            from .chart_clean_sr import draw_clean_sr_chart

            draw_clean_sr_chart(ax, bars, ta, interval_minutes=interval_minutes)
        except Exception:
            logger.debug("clean S/R chart failed", exc_info=True)
    elif clean_chart:
        try:
            _draw_essential_oil_overlays(ax, bars, ta, compact=False)
        except Exception:
            logger.debug("Essential oil overlays failed", exc_info=True)
    elif story_minimal:
        pass
    else:
        _draw_clean_market_annotations(ax, bars, ta, signal_chart=signal_chart)
    if not manual_ta_chart and not signal_chart:
        try:
            _draw_right_price_labels(ax, bars, ta)
        except Exception:
            logger.debug("right price labels skipped", exc_info=True)
    if ut_overlay is not None and not story_minimal:
        try:
            _draw_ut_bot_overlay(
                ax, bars, ut_overlay, interval_minutes=interval_minutes
            )
        except Exception:
            logger.debug("UT overlay draw failed", exc_info=True)
    current = bars[-1].close
    from .chart_display_policy import ed_chart_visual_only, ed_playbook_v3_enabled

    show_now = (
        not signal_pa_layers
        and not story_minimal
        and not (ed_playbook_v3_enabled() and ed_chart_visual_only())
    )
    if show_now:
        ax.axhline(current, color=accent_color, linestyle="--", linewidth=0.9, alpha=0.85)
        ax.text(
            _x_after_last_bar(bars, 14), current, f"сейчас {fmt_price(current)}",
            color=accent_color, fontsize=7, va="center", ha="left",
        )
    ut_sfx = " · UT" if (ut_overlay is not None and not story_minimal) else ""
    pb_title = None
    zoom_h = display_hours if display_hours and display_hours > 0 else None
    from .manual_ta import chart_display_hours

    zh = int(zoom_h) if zoom_h else chart_display_hours(interval_minutes)
    if signal_pa_layers:
        pb_title = f"{symbol} · {interval_minutes}m · {zh}ч"
    elif pro_mode or manual_ta_chart or story_minimal:
        try:
            from .chart_title_playbook import pro_chart_title

            pb_title = pro_chart_title(
                symbol,
                ta,
                interval_minutes=interval_minutes,
                hours_label=f"{zh}ч",
            )
        except Exception:
            pb_title = None
    if pb_title:
        ax.set_title(
            f"{pb_title}{ut_sfx}",
            color=CHART_STYLE["text"],
            fontsize=12 if pro_mode else 11,
            pad=14,
        )
    elif clean_chart:
        ax.set_title(
            f"{symbol}  ·  {ta.verdict} {ta_display_score(ta)}/10  ·  {title_suffix}{ut_sfx}",
            color=CHART_STYLE["text"], fontsize=11, pad=14,
        )
    else:
        mode_suffix = " · PRO" if (pro_mode or story_minimal) else ""
        wait_tag = " · наблюдение" if manual_ta_chart and (ta.verdict or "").upper() == "WAIT" else ""
        from .signal_locale import verdict_ru

        v_lbl = verdict_ru(ta.verdict) or (ta.verdict or "ожидание")
        ax.set_title(
            f"{symbol}  ·  {v_lbl} {ta_display_score(ta)}/10  ·  {title_suffix}{mode_suffix}{wait_tag}{ut_sfx}",
            color=CHART_STYLE["text"], fontsize=12 if pro_mode else 11, pad=14,
        )
    _style_axes(ax, bars)
    # Зум: анализ может быть на 18ч, экран — последние N часов (читаемые свечи)
    from .manual_ta import chart_display_hours

    zoom_h = display_hours if display_hours and display_hours > 0 else chart_display_hours(interval_minutes)
    trail = 0.22
    if pro_mode_chart:
        from .chart_pro import pro_trailing_fraction

        trail = pro_trailing_fraction(pro_mode_chart)  # type: ignore[arg-type]
        if pro_mode_chart != "legacy_manual":
            if manual_ta_chart or signal_chart:
                trail = 0.12 if manual_ta_chart else 0.16
            else:
                trail = max(trail, 0.48)
    _apply_display_zoom(
        ax,
        bars,
        display_hours=zoom_h,
        interval_minutes=interval_minutes,
        trailing=trail,
        set_ylim=True,
    )
    label_board = None
    if pro_mode_chart and not signal_pa_layers:
        try:
            from .chart_pro import draw_pro_layers, finalize_pro_viewport

            label_board = draw_pro_layers(
                ax, bars, ta, pro_mode_chart, interval_minutes=interval_minutes,  # type: ignore[arg-type]
            )
            finalize_pro_viewport(ax, bars, ta, pro_mode_chart)  # type: ignore[arg-type]
            if label_board is not None:
                from .chart_display_policy import chart_teaching_tags_enabled, ed_chart_visual_only
                from .chart_label_layout import draw_label_board

                if not ed_chart_visual_only() or chart_teaching_tags_enabled():
                    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
                    from .chart_display_policy import ed_chart_single_canvas_enabled

                    if not ed_chart_single_canvas_enabled():
                        max_l = 6 if chart_teaching_tags_enabled() else 4
                        draw_label_board(ax, bars, label_board, current=cur, max_labels=max_l)
        except Exception:
            logger.exception("Pro chart layers failed")
    if urals_price and urals_price > 0 and not clean_chart:
        try:
            _draw_urals_inset(
                ax,
                bars,
                urals_price=float(urals_price),
                brent_last=float(bars[-1].close),
                change_pct=urals_change_pct,
                display_hours=int(zoom_h),
                interval_minutes=interval_minutes,
            )
        except Exception:
            logger.debug("Urals inset draw failed", exc_info=True)
    fig.autofmt_xdate(rotation=0)

    # НЕ bbox_inches="tight": при зуме артисты вне осей раздувают PNG до миллионов px
    buffer = io.BytesIO()
    save_dpi = SIGNAL_CHART_DPI if wide else 120
    try:
        fig.savefig(
            buffer,
            format="png",
            facecolor=fig.get_facecolor(),
            dpi=save_dpi,
            pad_inches=0.06,
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
    bar_count = max(1, (hours * 60 + interval_minutes - 1) // interval_minutes)
    limit = max(24, min(bar_count + 8, 280))
    bars = await _kline_cache.get_klines(
        symbol,
        limit=limit,
        interval_minutes=interval_minutes,
    )
    if len(bars) < 12:
        return []
    return bars[-bar_count:]


# Область свечей на скриншоте TradingView (норм. координаты, 0=низ)
TV_CHART_Y_BOTTOM = 0.24
TV_CHART_Y_TOP = 0.84


def _price_to_axis_y(price: float, y_min: float, y_max: float) -> float:
    if y_max <= y_min:
        return (TV_CHART_Y_BOTTOM + TV_CHART_Y_TOP) / 2
    ratio = (price - y_min) / (y_max - y_min)
    ratio = max(0.0, min(1.0, ratio))
    # y=0 низ кадра. Раньше ось была зеркальной — уровни ложились не на свечи.
    return TV_CHART_Y_BOTTOM + ratio * (TV_CHART_Y_TOP - TV_CHART_Y_BOTTOM)


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
    market_metrics: dict[str, object] | None = None,
    pattern_detection_enabled: bool = True,
    pattern_min_confidence: float = 0.55,
    display_hours: int | None = None,
    height_scale: float | None = None,
    signal_chart: bool = False,
    manual_ta_chart: bool = False,
    as_of_bar_index: int | None = None,
    as_of_open_time_ms: int | float | None = None,
    as_of_price: float | None = None,
) -> tuple[bytes | None, TAAnalysisResult | None]:
    from .chart_unified import normalize_chart_source

    chart_source = normalize_chart_source(chart_source)
    # Analyze enough history for patterns, then zoom the display window.
    analysis_hours = max(hours, pattern_chart_hours(interval_minutes))
    if manual_ta_chart:
        analysis_hours = max(analysis_hours, hours)
    zoom_hours = chart_display_hours(interval_minutes, configured=display_hours)
    zoom_hours = min(zoom_hours, analysis_hours)
    bars = await _fetch_bars(symbol, analysis_hours, interval_minutes=interval_minutes)
    if not bars:
        return None, None

    btc_bars: list[KlineBar] | None = None
    mid_bars: list[KlineBar] | None = None
    htf_bars: list[KlineBar] | None = None
    macro_bars: list[KlineBar] | None = None
    history_bars: list[KlineBar] | None = bars
    if symbol.upper() not in {"BTCUSDT", "BTCUSD", "BTCUSDC"}:
        btc_bars = await _fetch_bars("BTCUSDT", analysis_hours, interval_minutes=interval_minutes)
    mid_interval_minutes = 15
    htf_interval_minutes = 60
    macro_interval_minutes = 240
    if interval_minutes <= 15:
        mid_bars = await _fetch_bars(
            symbol,
            max(24 * 3, analysis_hours),
            interval_minutes=mid_interval_minutes,
        )
        htf_bars = await _fetch_bars(
            symbol,
            max(24 * 10, analysis_hours * 2),
            interval_minutes=htf_interval_minutes,
        )
        macro_bars = await _fetch_bars(
            symbol,
            max(24 * 30, analysis_hours * 4),
            interval_minutes=macro_interval_minutes,
        )
    else:
        htf_interval_minutes = 240
        macro_interval_minutes = 0
        htf_bars = await _fetch_bars(
            symbol,
            max(24 * 30, analysis_hours * 2),
            interval_minutes=htf_interval_minutes,
        )

    taker_cvd = None
    if symbol:
        lookback_min = min(180.0, max(20.0, len(bars) * interval_minutes))
        try:
            taker_cvd = await get_taker_cvd_cache().get_cvd(
                symbol, lookback_minutes=lookback_min,
            )
        except Exception:
            logger.debug("Taker CVD fetch failed for %s", symbol, exc_info=True)

    metrics: dict[str, object] = dict(market_metrics or {})
    try:
        from .market_flow import market_context

        coinglass = await market_context(
            symbol,
            interval_minutes=interval_minutes,
            exchange=exchange,
        )
    except Exception:
        logger.exception("Market flow context request failed for %s", symbol)
        coinglass = {
            "coinglass_status": "ошибка запроса",
            "coinglass_available_metrics": [],
            "coinglass_missing_metrics": [
                "price", "oi", "funding", "account_ratio", "liquidations", "taker",
            ],
        }
    for metric_name in (
        "price_change_pct",
        "oi_change_pct",
        "oi_period_minutes",
        "funding_rate",
        "account_ratio",
        "liquidations",
        "taker_buy_ratio",
        "taker_buy_vol_usd",
        "taker_sell_vol_usd",
        "oi_usd",
        "futures_taker_buy_ratio",
        "futures_taker_buy_vol_usd",
        "futures_taker_sell_vol_usd",
        "futures_taker_window_minutes",
        "spot_taker_buy_ratio",
        "spot_taker_buy_vol_usd",
        "spot_taker_sell_vol_usd",
        "spot_taker_window_minutes",
    ):
        if (
            metric_name in {"price_change_pct", "oi_change_pct", "oi_period_minutes"}
            and (
                coinglass.get("price_change_pct") is None
                or coinglass.get("oi_change_pct") is None
            )
        ):
            continue
        value = coinglass.get(metric_name)
        if value is not None:
            metrics[metric_name] = value
    metrics["coinglass_status"] = coinglass.get("coinglass_status", "ошибка запроса")
    metrics["coinglass_available_metrics"] = coinglass.get("coinglass_available_metrics", [])
    metrics["coinglass_missing_metrics"] = coinglass.get("coinglass_missing_metrics", [])
    flow_source = coinglass.get("source")
    flow_provider = coinglass.get("flow_provider")
    if flow_provider:
        metrics["flow_provider"] = flow_provider
    if flow_source:
        metrics["source"] = flow_source
    elif metrics["coinglass_available_metrics"]:
        metrics["source"] = "CoinGlass V4 + Bybit fallback"
    elif not metrics.get("source"):
        metrics["source"] = exchange.title()
    market_metrics = metrics

    weekly_bars: list[KlineBar] | None = None
    try:
        weekly_bars = await asyncio.to_thread(
            fetch_bybit_klines_sync,
            symbol,
            interval="W",
            limit=54,
        )
        if weekly_bars is not None and len(weekly_bars) < 4:
            weekly_bars = None
    except Exception:
        logger.debug("Weekly klines failed for %s", symbol, exc_info=True)

    is_long = side == "long"
    ta = run_ta_analysis(
        bars,
        is_long=is_long,
        oi_bars=oi_bars,
        btc_bars=btc_bars,
        mid_bars=mid_bars,
        htf_bars=htf_bars,
        macro_bars=macro_bars,
        weekly_bars=weekly_bars,
        symbol=symbol,
        hours=analysis_hours,
        invalidation_price=invalidation_price,
        neutral=neutral,
        liq_context=liq_context,
        interval_minutes=interval_minutes,
        htf_interval_minutes=htf_interval_minutes,
        mid_interval_minutes=mid_interval_minutes,
        macro_interval_minutes=macro_interval_minutes,
        history_bars=history_bars,
        taker_cvd=taker_cvd,
        market_metrics=market_metrics,
        pattern_detection_enabled=pattern_detection_enabled,
        pattern_min_confidence=pattern_min_confidence,
        as_of_bar_index=as_of_bar_index,
        as_of_open_time_ms=as_of_open_time_ms,
        as_of_price=as_of_price,
    )
    if ta is not None and symbol:
        from .market_state_store import get_market_state_store

        get_market_state_store().put_from_ta(
            symbol, ta, interval_minutes=interval_minutes
        )
    if verdict_override:
        ta.verdict = verdict_override

    bars_chart = bars
    ta_chart = ta
    iv_chart = interval_minutes
    ah_chart = analysis_hours
    if ta and manual_ta_chart:
        from .chart_setup_interval import (
            plan_search_intervals,
            setup_chart_analysis_hours,
            ta_plan_readable,
        )

        picked_iv = interval_minutes
        picked_bars = bars
        picked_ta = ta
        picked_ah = analysis_hours
        for setup_iv in plan_search_intervals(ta, interval_minutes)[:4]:
            ah_setup = max(analysis_hours, setup_chart_analysis_hours(setup_iv))
            if setup_iv == interval_minutes and ta_plan_readable(ta):
                picked_iv, picked_bars, picked_ta, picked_ah = setup_iv, bars, ta, analysis_hours
                break
            bars_setup = await _fetch_bars(symbol, ah_setup, interval_minutes=setup_iv)
            if not bars_setup or len(bars_setup) < 20:
                continue
            ta_setup = run_ta_analysis(
                bars_setup,
                is_long=is_long,
                oi_bars=oi_bars,
                btc_bars=btc_bars,
                mid_bars=mid_bars,
                htf_bars=htf_bars,
                macro_bars=macro_bars,
                weekly_bars=weekly_bars,
                symbol=symbol,
                hours=ah_setup,
                invalidation_price=invalidation_price,
                neutral=neutral,
                liq_context=liq_context,
                interval_minutes=setup_iv,
                htf_interval_minutes=htf_interval_minutes,
                mid_interval_minutes=mid_interval_minutes,
                macro_interval_minutes=macro_interval_minutes,
                history_bars=bars_setup,
                taker_cvd=taker_cvd,
                market_metrics=market_metrics,
                pattern_detection_enabled=pattern_detection_enabled,
                pattern_min_confidence=pattern_min_confidence,
                as_of_bar_index=as_of_bar_index,
                as_of_open_time_ms=as_of_open_time_ms,
                as_of_price=as_of_price,
            )
            if verdict_override:
                ta_setup.verdict = verdict_override
            picked_iv, picked_bars, picked_ta, picked_ah = setup_iv, bars_setup, ta_setup, ah_setup
            if ta_plan_readable(ta_setup):
                break
        bars_chart = picked_bars
        ta_chart = picked_ta
        iv_chart = picked_iv
        ah_chart = picked_ah
        mm = dict(getattr(ta_chart, "market_metrics", None) or {})
        mm["chart_setup_interval"] = iv_chart
        mm["chart_scanner_interval"] = interval_minutes
        ta_chart.market_metrics = mm

    if (manual_ta_chart or signal_chart) and ta_chart and symbol:
        try:
            from .chart_ed_story import ed_story_min_analysis_hours, use_ed_story_chart

            if use_ed_story_chart(ta_chart):
                need_h = ed_story_min_analysis_hours(iv_chart)
                if need_h > ah_chart:
                    bars_deep = await _fetch_bars(symbol, need_h, interval_minutes=iv_chart)
                    if bars_deep and len(bars_deep) >= len(bars_chart or []):
                        ta_deep = run_ta_analysis(
                            bars_deep,
                            is_long=is_long,
                            oi_bars=oi_bars,
                            btc_bars=btc_bars,
                            mid_bars=mid_bars,
                            htf_bars=htf_bars,
                            macro_bars=macro_bars,
                            weekly_bars=weekly_bars,
                            symbol=symbol,
                            hours=need_h,
                            invalidation_price=invalidation_price,
                            neutral=neutral,
                            liq_context=liq_context,
                            interval_minutes=iv_chart,
                            htf_interval_minutes=htf_interval_minutes,
                            mid_interval_minutes=mid_interval_minutes,
                            macro_interval_minutes=macro_interval_minutes,
                            history_bars=bars_deep,
                            taker_cvd=taker_cvd,
                            market_metrics=market_metrics,
                            pattern_detection_enabled=pattern_detection_enabled,
                            pattern_min_confidence=pattern_min_confidence,
                            as_of_bar_index=as_of_bar_index,
                            as_of_open_time_ms=as_of_open_time_ms,
                            as_of_price=as_of_price,
                        )
                        if verdict_override:
                            ta_deep.verdict = verdict_override
                        bars_chart = bars_deep
                        ta_chart = ta_deep
                        ah_chart = need_h
        except Exception:
            logger.debug("Ed story deep history fetch skipped", exc_info=True)

    if manual_ta_chart or signal_chart:
        from .chart_display_policy import ed_manual_chart_full_history_enabled

        if ta_chart is not None:
            try:
                from .chart_display_policy import ed_playbook_v3_enabled

                if ed_playbook_v3_enabled():
                    from .core.playbook.cache import get_or_run_playbook

                    get_or_run_playbook(ta_chart, symbol=symbol or "")
            except Exception:
                logger.debug("Playbook chart warm-up skipped", exc_info=True)

        use_intraday_zoom = signal_chart or not ed_manual_chart_full_history_enabled()
        if use_intraday_zoom:
            dd = float(getattr(ta_chart, "drawdown_from_high_pct", 0) or 0) if ta_chart else 0.0
            cfg = display_hours if display_hours and int(display_hours) > 0 else None
            zoom_hours = intraday_chart_zoom_hours(
                iv_chart,
                analysis_hours=ah_chart,
                configured=int(cfg) if cfg else None,
                drawdown_pct=dd,
                bar_count=len(bars_chart or []),
            )
        else:
            from .chart_ed_story import use_ed_story_chart
            from .chart_range_wait import use_range_wait_chart
            from .core.playbook.chart_spec import (
                ed_story_zoom_hours_with_playbook,
                range_wait_zoom_hours_with_playbook,
                resolve_chart_zoom_hours,
            )

            if ta_chart and use_ed_story_chart(ta_chart):
                zoom_hours = ed_story_zoom_hours_with_playbook(
                    ta_chart,
                    bars_chart,
                    symbol=symbol or "",
                    interval_minutes=iv_chart,
                    analysis_hours=ah_chart,
                    configured=display_hours,
                )
            elif ta_chart and use_range_wait_chart(ta_chart):
                zoom_hours = range_wait_zoom_hours_with_playbook(
                    ta_chart,
                    bars_chart,
                    symbol=symbol or "",
                    interval_minutes=iv_chart,
                    analysis_hours=ah_chart,
                    configured=display_hours,
                )
            else:
                zoom_hours = resolve_chart_zoom_hours(
                    ta_chart,
                    bars_chart,
                    symbol=symbol or "",
                    interval_minutes=iv_chart,
                    analysis_hours=ah_chart,
                    configured=display_hours,
                )
    else:
        zoom_hours = structure_aware_display_hours(
            interval_minutes=interval_minutes,
            analysis_hours=analysis_hours,
            configured=display_hours,
            drawdown_pct=float(getattr(ta, "drawdown_from_high_pct", 0) or 0),
            structure_span_bars=0,
            fib_span_bars=0,
        )

    from .chart_display_policy import use_tradingview_chart_base

    if (
        bars_chart
        and ta_chart
        and symbol
        and use_tradingview_chart_base(
            chart_source,
            signal_chart=signal_chart,
            manual_ta_chart=manual_ta_chart,
        )
    ):
        try:
            from .chart_screenshot import chart_capture_service
            from .chart_tv_pro_overlay import compose_tradingview_pro_png

            tv_png = await chart_capture_service.capture_tradingview(
                exchange,
                symbol,
                interval_minutes=iv_chart,
            )
            if tv_png:
                composed = compose_tradingview_pro_png(
                    tv_png,
                    bars_chart,
                    ta_chart,
                    interval_minutes=iv_chart,
                    display_hours=zoom_hours,
                )
                if composed:
                    out_ta = ta_chart if ta_chart is not None else ta
                    if out_ta is not None and iv_chart != interval_minutes:
                        mm = dict(getattr(out_ta, "market_metrics", None) or {})
                        mm["chart_setup_interval"] = iv_chart
                        mm["chart_scanner_interval"] = interval_minutes
                        mm["chart_render_backend"] = "tradingview"
                        try:
                            from dataclasses import replace

                            out_ta = replace(
                                out_ta,
                                market_metrics=mm,
                                analysis_interval_minutes=iv_chart,
                            )
                        except Exception:
                            pass
                    logger.info("Chart %s %s: TradingView + PRO overlay (%sm)", exchange, symbol, iv_chart)
                    return composed, out_ta
        except Exception:
            logger.warning("TradingView chart failed for %s — matplotlib fallback", symbol, exc_info=True)

    accent = CHART_STYLE["accent_long"] if is_long else CHART_STYLE["accent_short"]
    if manual_ta_chart or signal_chart:
        pro_mode = True
    else:
        pro_mode = chart_source == "annotated_pro"
    if signal_chart and (height_scale is None or float(height_scale or 0) < 1.25):
        height_scale = max(1.35, float(height_scale or 1.0))
    title = f"Bybit {iv_chart}m · вид {zoom_hours}ч"
    if ah_chart > zoom_hours:
        title = f"{title} (история {ah_chart}ч)"
    if iv_chart != interval_minutes:
        title = f"{title} · сетап {iv_chart}m (сканер {interval_minutes}m)"
    png = _render_chart_figure(
        bars_chart, ta_chart,
        symbol=symbol,
        title_suffix=title,
        accent_color=accent,
        interval_minutes=iv_chart,
        pro_mode=pro_mode,
        display_hours=zoom_hours,
        height_scale=height_scale,
        signal_chart=signal_chart,
        manual_ta_chart=manual_ta_chart,
    )
    out_ta = ta_chart if ta_chart is not None else ta
    if out_ta is not None and iv_chart != interval_minutes:
        mm = dict(getattr(out_ta, "market_metrics", None) or {})
        mm["chart_setup_interval"] = iv_chart
        mm["chart_scanner_interval"] = interval_minutes
        mm["chart_render_backend"] = "unified_pro"
        try:
            from dataclasses import replace

            out_ta = replace(
                out_ta,
                market_metrics=mm,
                analysis_interval_minutes=iv_chart,
            )
        except Exception:
            pass
    return png, out_ta


def render_oil_chart(
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    symbol_label: str = "Brent",
    interval_minutes: int = 15,
    display_hours: int | None = None,
    height_scale: float = 1.45,
) -> bytes | None:
    """PNG график нефти (Bybit TradFi) — крупный зум, не вся неделя на 5m."""
    if not bars or len(bars) < 12:
        return None
    verdict = (ta.verdict or "WAIT").upper()
    accent = (
        CHART_STYLE["accent_long"] if verdict == "LONG"
        else CHART_STYLE["accent_short"] if verdict == "SHORT"
        else CHART_STYLE["warning"]
    )
    # Intraday: чуть отдалить — 15m ≈ 18–20ч, 5m ≈ 10ч (не «слипшиеся» и не микроскоп).
    im = max(5, min(60, int(interval_minutes)))
    drawdown = float(getattr(ta, "drawdown_from_high_pct", 0.0) or 0.0)
    zoom = intraday_chart_zoom_hours(
        im,
        analysis_hours=min(int(len(bars) * im / 60), 72 if im <= 15 else 120),
        configured=int(display_hours) if display_hours and int(display_hours) > 0 else None,
        drawdown_pct=drawdown,
        bar_count=len(bars),
    )
    title = f"OIL · {symbol_label} · {im}m · {zoom}ч"

    ut_overlay = None
    import os

    if os.environ.get("OIL_CHART_UT", "").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from .oil_ut_bot import compute_oil_ut_bot

            ut_overlay = compute_oil_ut_bot(
                bars,
                key_value=1.0,
                atr_period=10,
                exclude_forming=True,
            )
        except Exception:
            logger.debug("Oil UT overlay compute failed", exc_info=True)

    return _render_chart_figure(
        bars,
        ta,
        symbol=symbol_label,
        title_suffix=title,
        accent_color=accent,
        interval_minutes=im,
        pro_mode=True,
        display_hours=zoom,
        height_scale=max(1.35, float(height_scale or 1.45)),
        urals_price=None,
        urals_change_pct=None,
        ut_overlay=ut_overlay,
        clean_chart=False,
        signal_chart=True,
    )


def render_oil_ut_chart(
    bars: list[KlineBar],
    ut: Any,
    *,
    symbol_label: str = "UKOUSD",
    interval_minutes: int = 5,
    max_bars: int = 80,
) -> bytes | None:
    """PNG как на TradingView UT Bot: зелёные/красные свечи + Buy/Sell labels + trail."""
    if not bars or ut is None:
        return None
    from datetime import datetime, timezone

    n_ut = len(getattr(ut, "trails", ()) or ())
    if n_ut < 12:
        return None

    work = list(bars)
    # ut посчитан без forming — если bars длиннее на 1, отрежем хвост
    if len(work) > n_ut:
        work = work[-n_ut:]
    show = max(20, min(int(max_bars), len(work)))
    work = work[-show:]
    o0 = n_ut - len(work)

    times: list[datetime] = []
    for b in work:
        ts = float(getattr(b, "open_time", 0) or 0)
        times.append(
            datetime.fromtimestamp(ts, tz=timezone.utc)
            if ts > 0
            else datetime.now(tz=timezone.utc)
        )

    green = CHART_STYLE.get("accent_long", "#3fb950")
    red = CHART_STYLE.get("accent_short", "#f85149")
    bg = CHART_STYLE.get("bg", "#0d1117")
    grid = CHART_STYLE.get("grid", "#21262d")
    text_c = CHART_STYLE.get("text", "#c9d1d9")

    fig, ax = plt.subplots(figsize=(11.5, 5.8), dpi=120)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    trail_y: list[float] = []
    for i, b in enumerate(work):
        ui = o0 + i
        bull = bool(ut.bar_bull[ui]) if ui < len(ut.bar_bull) else (b.close >= b.open)
        color = green if bull else red
        t = mdates.date2num(times[i])
        width = (interval_minutes / 1440.0) * 0.7
        ax.plot([t, t], [b.low, b.high], color=color, linewidth=1.0, solid_capstyle="round")
        body_lo, body_hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(
            Rectangle(
                (t - width / 2, body_lo),
                width,
                max(body_hi - body_lo, 1e-6),
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
                zorder=3,
            )
        )
        trail_y.append(float(ut.trails[ui]) if ui < len(ut.trails) else float(b.close))

        if ui < len(ut.buy_flags) and ut.buy_flags[ui]:
            ax.annotate(
                "Buy",
                xy=(t, b.low),
                xytext=(0, -14),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=7,
                fontweight="bold",
                color="white",
                bbox=dict(boxstyle="round,pad=0.2", facecolor=green, edgecolor=green, alpha=0.95),
                zorder=5,
            )
        if ui < len(ut.sell_flags) and ut.sell_flags[ui]:
            ax.annotate(
                "Sell",
                xy=(t, b.high),
                xytext=(0, 14),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=7,
                fontweight="bold",
                color="white",
                bbox=dict(boxstyle="round,pad=0.2", facecolor=red, edgecolor=red, alpha=0.95),
                zorder=5,
            )

    # Trail как на TV: зелёный под ценой (long), красный над ценой (short)
    xs = [mdates.date2num(t) for t in times]
    for i in range(1, len(trail_y)):
        ui = o0 + i
        bull = bool(ut.bar_bull[ui]) if ui < len(ut.bar_bull) else trail_y[i] < float(work[i].close)
        ax.plot(
            xs[i - 1 : i + 1],
            trail_y[i - 1 : i + 1],
            color=green if bull else red,
            linewidth=1.4,
            solid_capstyle="round",
            zorder=4,
        )

    key = float(getattr(ut, "key_value", 1) or 1)
    atr_p = int(getattr(ut, "atr_period", 10) or 10)
    ax.set_title(
        f"OIL · {symbol_label} · UT Bot Alerts · {interval_minutes}m · Key {key:g} ATR {atr_p}",
        color=text_c,
        fontsize=11,
        fontweight="bold",
        pad=10,
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.tick_params(colors=text_c, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(grid)
    ax.grid(True, color=grid, alpha=0.45, linewidth=0.5)
    # Легенда как на TV
    ax.plot([], [], color=green, linewidth=1.4, label="UT trail (long)")
    ax.plot([], [], color=red, linewidth=1.4, label="UT trail (short)")
    ax.legend(loc="upper left", fontsize=8, facecolor=bg, edgecolor=grid, labelcolor=text_c)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


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
    market_metrics: dict[str, object] | None = None,
    chart_source: str = "annotated",
    exchange: str = "bybit",
    display_hours: int | None = None,
    height_scale: float | None = None,
    as_of_bar_index: int | None = None,
    as_of_open_time_ms: int | float | None = None,
    as_of_price: float | None = None,
) -> tuple[bytes | None, TAAnalysisResult | None]:
    png, ta = await render_annotated_chart(
        symbol,
        side=side,
        hours=hours,
        interval_minutes=interval_minutes,
        structure_warning=structure_warning,
        oi_bars=oi_bars,
        liq_context=liq_context,
        market_metrics=market_metrics,
        neutral=True,
        chart_source=chart_source,
        exchange=exchange,
        display_hours=display_hours,
        height_scale=height_scale,
        signal_chart=True,
        as_of_bar_index=as_of_bar_index,
        as_of_open_time_ms=as_of_open_time_ms,
        as_of_price=as_of_price,
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
    market_metrics: dict[str, object] | None = None,
    exchange: str = "bybit",
    height_scale: float | None = None,
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
        market_metrics=market_metrics,
        neutral=True,
        exchange=exchange,
        verdict_override=verdict_override,
        height_scale=height_scale,
        signal_chart=True,
        chart_source="annotated",
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
    market_metrics: dict[str, object] | None = None,
    display_hours: int | None = None,
    height_scale: float | None = None,
) -> tuple[bytes | None, str, TAAnalysisResult | None, str]:
    png, ta = await render_signal_chart(
        signal_symbol,
        side=side,
        hours=chart_hours,
        interval_minutes=chart_interval_minutes,
        structure_warning=structure_warning,
        probability_percent=probability_percent,
        oi_bars=oi_bars,
        liq_context=liq_context,
        market_metrics=market_metrics,
        chart_source="annotated",
        exchange=signal_exchange,
        display_hours=display_hours,
        height_scale=height_scale,
    )
    if png:
        return png, "annotated", ta, ""
    return None, "none", ta, "нет свечей Bybit или ошибка matplotlib"
