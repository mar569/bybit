from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

from .bybit_klines import BybitKlineCache, KlineBar
from .chart_screenshot import chart_capture_service
from .market_structure import FiveMinOiBar
from .ta_analysis import (
    TAAnalysisResult,
    TradeScenario,
    fmt_price,
    run_ta_analysis,
    ta_chart_key_levels_text,
    ta_chart_legend_text,
    ta_chart_context_text,
    ta_chart_plan_text,
    ta_chart_panel_text,
    ta_chart_scenario_text,
    ta_chart_summary_text,
    ta_display_score,
    primary_forecast_direction,
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
}


def _bar_times(bars: list[KlineBar]) -> list[datetime]:
    return [datetime.fromtimestamp(b.open_time, tz=timezone.utc) for b in bars]


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


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
    span = mdates.date2num(times[-1]) - mdates.date2num(times[max(0, len(bars) - 24)])
    step = span / max(len(scenario.target_prices) + 1, 2)
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
    arrow_dx = max((x1n - x0n) * 0.08, 0.0008)
    ax.annotate(
        "",
        xy=(x1n + arrow_dx, z.top),
        xytext=(x1n, mid_y),
        arrowprops=dict(arrowstyle="->", color=CHART_STYLE["accent_long"], lw=1.4, alpha=0.9),
    )
    ax.annotate(
        "",
        xy=(x1n + arrow_dx, z.bottom),
        xytext=(x1n, mid_y),
        arrowprops=dict(arrowstyle="->", color=CHART_STYLE["accent_short"], lw=1.4, alpha=0.9),
    )
    ax.text(x1n, z.top * 1.0003, " пробой ↑", color=CHART_STYLE["accent_long"], fontsize=6.5, va="bottom")
    ax.text(x1n, z.bottom * 0.9997, " пробой ↓", color=CHART_STYLE["accent_short"], fontsize=6.5, va="top")


def _draw_breakout_arrows(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return
    last_ts = _idx_to_date(bars, len(bars) - 1)
    x = mdates.date2num(last_ts)
    y = bars[-1].close
    span = max(mdates.date2num(last_ts) - mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 12))), 0.001)
    dx = span * 0.35

    if ta.breakout_level and y <= ta.breakout_level * 1.002:
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
            x + dx * 0.45, (y + ta.breakout_level) / 2,
            "LONG",
            color=CHART_STYLE["accent_long"], fontsize=7, fontweight="bold", ha="center",
        )

    if ta.breakdown_level and y >= ta.breakdown_level * 0.998:
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
        label = "SHORT ↓" if ta.action_priority == "short" or ta.verdict == "SHORT" else "SHORT"
        ax.text(
            x + dx * 0.45, (y + ta.breakdown_level) / 2,
            label,
            color=CHART_STYLE["accent_short"], fontsize=7, fontweight="bold", ha="center",
        )
    elif ta.momentum_label.startswith("импульс вниз") and ta.breakdown_level:
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

    for marker in smc.markers:
        if marker.index >= len(bars):
            continue
        ts = _idx_to_date(bars, marker.index)
        color = CHART_STYLE["accent_long"] if marker.direction == "long" else CHART_STYLE["accent_short"]
        ax.plot(ts, marker.price, marker="*", color=color, markersize=9, linestyle="None")
        ax.text(
            mdates.date2num(ts), marker.price, f" {marker.label}",
            color=color, fontsize=6.5, va="bottom",
        )


def _draw_ta_annotations(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return
    times = _bar_times(bars)
    x_end = times[-1]

    _draw_zones(ax, bars, ta)
    _draw_smc_annotations(ax, bars, ta)
    _draw_channel(ax, bars, ta)
    _draw_extended_trend_lines(ax, bars, ta)
    _draw_consolidation_box(ax, bars, ta)

    if ta.breakout_level:
        ax.axhline(ta.breakout_level, color=CHART_STYLE["entry"], linestyle="-", linewidth=1.0, alpha=0.85)
        ax.text(
            mdates.date2num(x_end), ta.breakout_level,
            f" LONG≥{fmt_price(ta.breakout_level)}",
            color=CHART_STYLE["entry"], fontsize=7, va="bottom",
        )

    if ta.breakdown_level and (
        ta.breakout_level is None
        or abs(ta.breakdown_level - ta.breakout_level) > max(ta.current_price, 1e-9) * 0.0005
    ):
        ax.axhline(ta.breakdown_level, color=CHART_STYLE["accent_short"], linestyle="-", linewidth=0.9, alpha=0.8)
        ax.text(
            mdates.date2num(x_end), ta.breakdown_level,
            f" SHORT≤{fmt_price(ta.breakdown_level)}",
            color=CHART_STYLE["accent_short"], fontsize=7, va="top",
        )

    for lv in ta.levels[:3]:
        color = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
        ax.axhline(lv.price, color=color, linestyle="-", linewidth=0.75, alpha=0.55)
        ax.text(
            mdates.date2num(x_end), lv.price, f" {fmt_price(lv.price)}",
            color=color, fontsize=6.5, va="center", ha="left",
        )

    _draw_level_hints(ax, bars, ta)
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

    for pat in ta.patterns[-2:]:
        bar = bars[pat.index]
        ts = _idx_to_date(bars, pat.index)
        y = bar.high * 1.001 if pat.bullish is not False else bar.low * 0.999
        marker = "^" if pat.bullish else "v" if pat.bullish is False else "o"
        ax.plot(ts, y, marker=marker, color=CHART_STYLE["pattern"], markersize=6, linestyle="None")

    direction = primary_forecast_direction(ta)
    if direction == "long":
        _draw_scenario_path(ax, bars, ta.bullish_scenario, color=CHART_STYLE["scenario_bull"])
    elif direction == "short":
        _draw_scenario_path(ax, bars, ta.bearish_scenario, color=CHART_STYLE["scenario_bear"])
    else:
        _draw_scenario_path(ax, bars, ta.bullish_scenario, color=CHART_STYLE["scenario_bull"])
        _draw_scenario_path(ax, bars, ta.bearish_scenario, color=CHART_STYLE["scenario_bear"])

    _draw_signal_markers(ax, bars, ta)

    if ta.invalidation_price:
        ax.axhline(
            ta.invalidation_price, color=CHART_STYLE["inv"],
            linestyle="--", linewidth=1.0, alpha=0.9,
        )
        ax.text(
            mdates.date2num(x_end), ta.invalidation_price,
            f" STOP {fmt_price(ta.invalidation_price)}",
            color=CHART_STYLE["inv"], fontsize=7, va="center",
        )

    for j, tp in enumerate(ta.target_prices[:3]):
        ax.axhline(tp, color=CHART_STYLE["target"], linestyle=":", linewidth=0.75, alpha=0.65)
        ax.text(
            mdates.date2num(x_end), tp, f" TP{j + 1} {fmt_price(tp)}",
            color=CHART_STYLE["target"], fontsize=6.5, va="center",
        )

    if ta.entry_zone:
        lo, hi = ta.entry_zone
        ax.axhspan(lo, hi, color=CHART_STYLE["accent_long"], alpha=0.1)
        ax.text(
            mdates.date2num(x_end), hi, " зона входа",
            color=CHART_STYLE["accent_long"], fontsize=6.5, va="bottom",
        )


def _draw_info_panels(fig: plt.Figure, ta: TAAnalysisResult) -> None:
    """Текстовые блоки в боковых полях — полная колонка слева/справа."""
    base_bbox = dict(
        boxstyle="round,pad=0.4",
        facecolor=CHART_STYLE["panel"],
        edgecolor=CHART_STYLE["panel_border"],
        alpha=0.94,
    )
    panel_style = dict(
        transform=fig.transFigure,
        color=CHART_STYLE["text"],
        fontsize=6.8,
        linespacing=1.28,
        bbox=base_bbox,
    )
    bull_style = {**panel_style, "color": CHART_STYLE["accent_long"]}
    bear_style = {**panel_style, "color": CHART_STYLE["accent_short"]}
    ctx_style = {**panel_style, "fontsize": 6.4}

    lx, rx = 0.006, 0.994

    fig.text(
        lx, 0.985, ta_chart_legend_text(),
        va="top", ha="left", fontsize=5.5, color=CHART_STYLE["text"],
        transform=fig.transFigure,
        bbox=dict(boxstyle="round,pad=0.25", facecolor=CHART_STYLE["panel"],
                  edgecolor=CHART_STYLE["panel_border"], alpha=0.88),
    )

    key_levels = ta_chart_key_levels_text(ta)
    if key_levels:
        fig.text(lx, 0.90, key_levels, va="top", ha="left", **panel_style)

    plan = ta_chart_plan_text(ta)
    if plan:
        fig.text(lx, 0.58, plan, va="top", ha="left", **panel_style)

    context = ta_chart_context_text(ta)
    if len(context.splitlines()) > 1:
        fig.text(lx, 0.22, context, va="top", ha="left", **ctx_style)

    fig.text(rx, 0.985, ta_chart_panel_text(ta), va="top", ha="right", **panel_style)

    bull_text = ta_chart_scenario_text(ta.bullish_scenario, title="БЫЧИЙ СЦЕНАРИЙ")
    if bull_text:
        fig.text(rx, 0.58, bull_text, va="top", ha="right", **bull_style)

    bear_text = ta_chart_scenario_text(ta.bearish_scenario, title="МЕДВЕЖИЙ СЦЕНАРИЙ")
    if bear_text:
        fig.text(rx, 0.22, bear_text, va="top", ha="right", **bear_style)

    summary = ta_chart_summary_text(ta)
    if summary:
        fig.text(
            0.50, 0.015, summary,
            va="bottom", ha="center", fontsize=6.5, color=CHART_STYLE["text"],
            transform=fig.transFigure,
            bbox=dict(
                boxstyle="round,pad=0.4",
                facecolor=CHART_STYLE["panel"],
                edgecolor=CHART_STYLE["warning"],
                alpha=0.93,
            ),
        )


def _draw_info_panels_pro(fig: plt.Figure, ta: TAAnalysisResult) -> None:
    """PRO-версия: крупнее блоки и более явная структура."""
    left_x, right_x = 0.010, 0.990
    text_color = CHART_STYLE["text"]
    panel_fc = "#101828"
    edge = CHART_STYLE["panel_border"]

    base = dict(
        transform=fig.transFigure,
        fontsize=7.4,
        color=text_color,
        linespacing=1.35,
        bbox=dict(boxstyle="round,pad=0.45", facecolor=panel_fc, edgecolor=edge, alpha=0.97),
    )

    fig.text(
        left_x,
        0.985,
        "КЛЮЧЕВЫЕ УРОВНИ\n" + (ta_chart_key_levels_text(ta) or "уровни не определены"),
        ha="left",
        va="top",
        **base,
    )
    fig.text(
        left_x,
        0.64,
        "ПЛАН ДЕЙСТВИЙ\n" + (ta_chart_plan_text(ta) or "ожидать подтверждения"),
        ha="left",
        va="top",
        **base,
    )
    fig.text(
        left_x,
        0.30,
        "КОНТЕКСТ\n" + (ta_chart_context_text(ta) or "контекст недоступен"),
        ha="left",
        va="top",
        fontsize=7.0,
        color=text_color,
        transform=fig.transFigure,
        linespacing=1.30,
        bbox=dict(boxstyle="round,pad=0.42", facecolor=panel_fc, edgecolor=edge, alpha=0.97),
    )

    fig.text(
        right_x,
        0.985,
        "ИТОГ\n" + ta_chart_panel_text(ta),
        ha="right",
        va="top",
        **base,
    )
    bull = ta_chart_scenario_text(ta.bullish_scenario, title="БЫЧИЙ СЦЕНАРИЙ")
    if bull:
        fig.text(
            right_x,
            0.66,
            bull,
            ha="right",
            va="top",
            fontsize=7.2,
            color=CHART_STYLE["accent_long"],
            transform=fig.transFigure,
            linespacing=1.32,
            bbox=dict(boxstyle="round,pad=0.45", facecolor="#0f1f17", edgecolor=CHART_STYLE["accent_long"], alpha=0.96),
        )
    bear = ta_chart_scenario_text(ta.bearish_scenario, title="МЕДВЕЖИЙ СЦЕНАРИЙ")
    if bear:
        fig.text(
            right_x,
            0.34,
            bear,
            ha="right",
            va="top",
            fontsize=7.2,
            color=CHART_STYLE["accent_short"],
            transform=fig.transFigure,
            linespacing=1.32,
            bbox=dict(boxstyle="round,pad=0.45", facecolor="#231417", edgecolor=CHART_STYLE["accent_short"], alpha=0.96),
        )

    summary = ta_chart_summary_text(ta)
    if summary:
        fig.text(
            0.50,
            0.018,
            "ИТОГ: " + summary,
            ha="center",
            va="bottom",
            fontsize=7.2,
            color=text_color,
            transform=fig.transFigure,
            bbox=dict(boxstyle="round,pad=0.45", facecolor="#181f2a", edgecolor=CHART_STYLE["warning"], alpha=0.97),
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
            pad = (peak - trough) * 0.08
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
) -> bytes:
    fig_size = (19.2, 10.2) if pro_mode else (17, 8.5)
    fig, ax = plt.subplots(figsize=fig_size, dpi=120)
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.set_facecolor(CHART_STYLE["bg"])
    if pro_mode:
        fig.subplots_adjust(left=0.14, right=0.86, top=0.93, bottom=0.09)
    else:
        fig.subplots_adjust(left=0.16, right=0.84, top=0.92, bottom=0.10)

    _draw_candles(ax, bars, interval_minutes=interval_minutes)
    _draw_ta_annotations(ax, bars, ta)
    if pro_mode:
        _draw_pro_market_zones(ax, bars, ta)
        _draw_pro_paths(ax, bars, ta)

    current = bars[-1].close
    ax.axhline(current, color=accent_color, linestyle="--", linewidth=0.9, alpha=0.85)
    last_ts = _idx_to_date(bars, len(bars) - 1)
    ax.text(
        mdates.date2num(last_ts), current, f"  сейчас {fmt_price(current)}",
        color=accent_color, fontsize=7, va="center", ha="left",
    )
    mode_suffix = " · PRO" if pro_mode else ""
    ax.set_title(
        f"{symbol}  ·  {ta.verdict} {ta_display_score(ta)}/10  ·  {title_suffix}{mode_suffix}",
        color=CHART_STYLE["text"], fontsize=12 if pro_mode else 11, pad=14,
    )
    if pro_mode:
        # Усиливаем визуальную разницу PRO-режима.
        for lv in ta.levels[:6]:
            c = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
            ax.axhline(lv.price, color=c, linestyle="-", linewidth=1.15, alpha=0.42)
        _draw_info_panels_pro(fig, ta)
    else:
        _draw_info_panels(fig, ta)
    _style_axes(ax, bars)
    fig.autofmt_xdate(rotation=0)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
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
    limit = max(24, min(hours * per_hour + 2, 200))
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
    return TV_CHART_Y_BOTTOM + (1.0 - ratio) * (TV_CHART_Y_TOP - TV_CHART_Y_BOTTOM)


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
    for p in (ta.breakout_level, ta.breakdown_level, ta.invalidation_price):
        if p:
            prices.append(p)
    for lv in ta.levels[:4]:
        prices.append(lv.price)
    if ta.consolidation:
        prices.extend([ta.consolidation.top, ta.consolidation.bottom])
    if ta.nearest_support:
        prices.append(ta.nearest_support)
    if ta.nearest_resistance:
        prices.append(ta.nearest_resistance)
    if not prices:
        return 0.0, 1.0
    y_min, y_max = min(prices), max(prices)
    span = max(y_max - y_min, y_min * 0.0005)
    pad = span * 0.035
    return y_min - pad, y_max + pad


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
        color=CHART_STYLE["warning"], fontsize=6.5, ha="center", va="bottom", fontweight="bold",
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
        ax.text(x1, y_at(ext_price), f" {label}", color=color, fontsize=6, va="bottom" if tl.kind == "bull" else "top")

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
    ax.text(0.07, y_at(ema20[-1]), " 20", color="#58a6ff", fontsize=5.5, va="center")
    ax.text(0.07, y_at(ema50[-1]), " 50", color="#f0883e", fontsize=5.5, va="center")


def _draw_tv_forecast_paths(
    ax: plt.Axes,
    ta: TAAnalysisResult,
    bars: list[KlineBar],
    current: float,
    y_at: Any,
) -> None:
    """Один приоритетный сценарий вправо от последней свечи."""
    n = len(bars)
    x0 = _bar_x_norm(n - 1, n, x_start=0.62, x_end=0.90)
    x1, x2 = x0 + 0.08, min(0.97, x0 + 0.16)
    y0 = y_at(current)
    ax.plot(x0, y0, "o", color="white", markersize=5, zorder=5)
    ax.text(x0 - 0.008, y0, " сейчас", color=CHART_STYLE["text"], fontsize=6, ha="right", va="center")

    direction = primary_forecast_direction(ta)

    def _draw_path(scenario: TradeScenario, *, color: str, label: str, va: str) -> None:
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
        ax.text(x2, y_tp, f" {label}", color=color, fontsize=6.5, va=va, fontweight="bold")

    if direction == "long" and ta.bullish_scenario and ta.bullish_scenario.target_prices:
        _draw_path(ta.bullish_scenario, color=CHART_STYLE["accent_long"], label="прогноз↑", va="bottom")
    elif direction == "short" and ta.bearish_scenario and ta.bearish_scenario.target_prices:
        _draw_path(ta.bearish_scenario, color=CHART_STYLE["accent_short"], label="прогноз↓", va="top")
    elif direction == "neutral":
        if ta.bullish_scenario and ta.bullish_scenario.target_prices:
            bs = ta.bullish_scenario
            ax.plot(
                [x0, x1], [y0, y_at(bs.trigger_price)],
                color=CHART_STYLE["accent_long"], linewidth=0.8, linestyle=":", alpha=0.45, zorder=2,
            )
        if ta.bearish_scenario and ta.bearish_scenario.target_prices:
            bs = ta.bearish_scenario
            ax.plot(
                [x0, x1], [y0, y_at(bs.trigger_price)],
                color=CHART_STYLE["accent_short"], linewidth=0.8, linestyle=":", alpha=0.45, zorder=2,
            )


def _tv_forecast_legend(ta: TAAnalysisResult) -> str:
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


def _tv_context_block(ta: TAAnalysisResult, *, interval_minutes: int, hours: int) -> str:
    """Короткий блок факторов, которыми руководствуется бот."""
    phase_text = ta.phase_label or ta.phase or "н/д"
    bits = [
        f"фаза: {phase_text}",
        f"моментум: {ta.momentum_label}",
    ]
    if ta.oi_narrative_label:
        bits.append(f"OI: {ta.oi_narrative_label}")
    if ta.btc_context:
        bits.append(f"BTC: {ta.btc_context}")
    if ta.momentum_pct:
        bits.append(f"импульс: {ta.momentum_pct:+.1f}%")
    bits.append(f"окно: {hours}ч / {interval_minutes}m")
    return "\n".join(bits[:6])


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

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.imshow(mpimg.imread(io.BytesIO(tv_png)), extent=[0, 1, 0, 1], aspect="auto", zorder=0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    y_min, y_max = _tv_visible_price_range(bars, ta)

    def y_at(price: float) -> float:
        return _price_to_axis_y(price, y_min, y_max)

    _draw_tv_range_box(ax, ta, bars, y_at)
    _draw_tv_trendlines(ax, ta, bars, y_at)
    _draw_tv_emas(ax, bars, y_at)

    if ta.breakout_level:
        y = y_at(ta.breakout_level)
        ax.axhline(y, xmin=0.03, xmax=0.87, color=CHART_STYLE["entry"], linewidth=1.6, alpha=0.92, zorder=2)
        ax.text(0.88, y, f" R {fmt_price(ta.breakout_level)}", color=CHART_STYLE["entry"], fontsize=7, va="center", fontweight="bold")
    if ta.breakdown_level:
        y = y_at(ta.breakdown_level)
        ax.axhline(y, xmin=0.03, xmax=0.87, color=CHART_STYLE["accent_short"], linewidth=1.6, alpha=0.92, zorder=2)
        ax.text(0.88, y, f" S {fmt_price(ta.breakdown_level)}", color=CHART_STYLE["accent_short"], fontsize=7, va="center", fontweight="bold")
    if ta.invalidation_price:
        y = y_at(ta.invalidation_price)
        ax.axhline(y, xmin=0.03, xmax=0.87, color=CHART_STYLE["inv"], linewidth=1.0, linestyle="--", alpha=0.8, zorder=2)
        ax.text(0.88, y, f" SL {fmt_price(ta.invalidation_price)}", color=CHART_STYLE["inv"], fontsize=6.5, va="center")
    for lv in ta.levels[:5]:
        color = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
        y = y_at(lv.price)
        ax.axhline(y, xmin=0.03, xmax=0.87, color=color, linewidth=0.7, alpha=0.45, linestyle=":", zorder=2)
        label = "S" if lv.kind == "support" else "R"
        ax.text(0.03, y, f" {label} {fmt_price(lv.price)}", color=color, fontsize=5.8, va="center", ha="left")

    for j, tp in enumerate(ta.target_prices[:3]):
        y = y_at(tp)
        ax.axhline(y, xmin=0.03, xmax=0.87, color=CHART_STYLE["target"], linewidth=0.95, alpha=0.72, linestyle="--", zorder=2)
        ax.text(0.88, y, f" TP{j + 1} {fmt_price(tp)}", color=CHART_STYLE["target"], fontsize=6.1, va="center")

    if ta.entry_zone:
        lo, hi = ta.entry_zone
        y_lo = y_at(lo)
        y_hi = y_at(hi)
        y_bottom = min(y_lo, y_hi)
        height = abs(y_hi - y_lo)
        rect = Rectangle(
            (0.03, y_bottom),
            0.84,
            max(height, 0.0015),
            facecolor=CHART_STYLE["accent_long"] if ta.verdict == "LONG" else CHART_STYLE["accent_short"],
            edgecolor="none",
            alpha=0.08,
            zorder=1,
        )
        ax.add_patch(rect)
        ax.text(0.03, max(y_lo, y_hi), " ENTRY ZONE", color=CHART_STYLE["text"], fontsize=5.8, va="bottom")

    current = bars[-1].close
    y_cur = y_at(current)
    ax.axhline(y_cur, xmin=0.03, xmax=0.75, color=CHART_STYLE["accent_long"], linewidth=0.8, linestyle=":", alpha=0.7, zorder=2)

    _draw_tv_forecast_paths(ax, ta, bars, current, y_at)

    header = f"{symbol} · {ta.verdict} {ta_display_score(ta)}/10 · {interval_minutes}m"
    if ta.dist_to_long_pct is not None or ta.dist_to_short_pct is not None:
        bits: list[str] = []
        if ta.dist_to_long_pct is not None:
            bits.append(f"L {ta.dist_to_long_pct:.1f}%")
        if ta.dist_to_short_pct is not None:
            bits.append(f"S {ta.dist_to_short_pct:.1f}%")
        header += f" · {' / '.join(bits)}"
    ax.text(
        0.02, 0.97, header,
        transform=ax.transAxes, va="top", ha="left", color=CHART_STYLE["text"], fontsize=8, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#161b22dd", edgecolor=CHART_STYLE["panel_border"]),
    )
    if ta.verdict == "WAIT" and "вход невыгоден" in (ta.verdict_reason or ""):
        ax.text(
            0.02, 0.90, "⛔ NO TRADE",
            transform=ax.transAxes, va="top", ha="left", color="#ff7b72", fontsize=8, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#161b22dd", edgecolor="#ff7b72"),
        )

    legend = _tv_forecast_legend(ta)
    if legend:
        ax.text(
            0.02, 0.14, legend,
            transform=ax.transAxes, va="bottom", ha="left", color=CHART_STYLE["text"], fontsize=6.5,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#161b22cc", edgecolor=CHART_STYLE["panel_border"]),
        )

    panel = ta_chart_panel_text(ta)
    ax.text(
        0.98, 0.97, panel,
        transform=ax.transAxes, va="top", ha="right", color=CHART_STYLE["text"], fontsize=7,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#161b22dd", edgecolor=CHART_STYLE["panel_border"]),
    )

    context = _tv_context_block(ta, interval_minutes=interval_minutes, hours=hours)
    ax.text(
        0.98, 0.13, context,
        transform=ax.transAxes, va="bottom", ha="right", color=CHART_STYLE["text"], fontsize=6.2,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#161b22cc", edgecolor=CHART_STYLE["panel_border"]),
    )

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.02)
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
) -> tuple[bytes | None, TAAnalysisResult | None]:
    bars = await _fetch_bars(symbol, hours, interval_minutes=interval_minutes)
    if not bars:
        return None, None

    btc_bars: list[KlineBar] | None = None
    htf_bars: list[KlineBar] | None = None
    history_bars: list[KlineBar] | None = None
    if symbol.upper() not in {"BTCUSDT", "BTCUSD", "BTCUSDC"}:
        btc_bars = await _fetch_bars("BTCUSDT", hours, interval_minutes=interval_minutes)
    if interval_minutes <= 15:
        htf_bars = await _fetch_bars(symbol, max(24, hours * 2), interval_minutes=60)
    # Для ручного TA (neutral=True) подтягиваем более глубокую историю паттернов.
    if neutral:
        hist_hours = 48 if interval_minutes <= 15 else 72
        history_bars = await _fetch_bars(symbol, hist_hours, interval_minutes=interval_minutes)

    is_long = side == "long"
    ta = run_ta_analysis(
        bars,
        is_long=is_long,
        oi_bars=oi_bars,
        btc_bars=btc_bars,
        htf_bars=htf_bars,
        symbol=symbol,
        hours=hours,
        invalidation_price=invalidation_price,
        neutral=neutral,
        liq_context=liq_context,
        interval_minutes=interval_minutes,
        history_bars=history_bars,
    )
    if verdict_override:
        ta.verdict = verdict_override

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
    png = _render_chart_figure(
        bars, ta,
        symbol=symbol,
        title_suffix=f"Bybit {interval_minutes}m · {hours}ч",
        accent_color=accent,
        interval_minutes=interval_minutes,
        pro_mode=pro_mode,
    )
    return png, ta


async def render_signal_chart(
    symbol: str,
    *,
    side: str = "long",
    hours: int = 5,
    structure_warning: str = "",
    probability_percent: float | None = None,
    oi_bars: list[FiveMinOiBar] | None = None,
) -> tuple[bytes | None, TAAnalysisResult | None]:
    png, ta = await render_annotated_chart(
        symbol,
        side=side,
        hours=hours,
        structure_warning=structure_warning,
        oi_bars=oi_bars,
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
    invalidation_price: float | None = None,
    oi_bars: list[FiveMinOiBar] | None = None,
) -> tuple[bytes | None, TAAnalysisResult | None]:
    is_long = direction != "short"
    verdict_override = "WAIT" if direction == "wait" else None
    return await render_annotated_chart(
        symbol,
        side="long" if is_long else "short",
        hours=hours,
        invalidation_price=invalidation_price,
        oi_bars=oi_bars,
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
) -> tuple[bytes | None, str, TAAnalysisResult | None, str]:
    source = (chart_source or "annotated").lower()
    fail_reason = ""

    if source == "annotated":
        png, ta = await render_signal_chart(
            signal_symbol,
            side=side,
            hours=chart_hours,
            structure_warning=structure_warning,
            probability_percent=probability_percent,
            oi_bars=oi_bars,
        )
        if png:
            return png, "annotated", ta, ""
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

    if source not in {"generated", "annotated"}:
        logger.info("Chart %s unavailable for %s (%s), fallback to annotated", source, signal_symbol, fail_reason)

    png, ta = await render_signal_chart(
        signal_symbol,
        side=side,
        hours=chart_hours,
        structure_warning=structure_warning,
        probability_percent=probability_percent,
        oi_bars=oi_bars,
    )
    if png:
        return png, "annotated", ta, fail_reason
    extra = fail_reason or "annotated fallback не удался"
    return None, "none", ta, extra
