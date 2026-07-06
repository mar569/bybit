from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone

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
    ta_chart_panel_text,
    ta_chart_scenario_text,
    ta_chart_summary_text,
    ta_display_score,
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


def _draw_ta_annotations(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return
    times = _bar_times(bars)
    x_end = times[-1]

    _draw_zones(ax, bars, ta)
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


def _draw_info_panels(ax: plt.Axes, ta: TAAnalysisResult) -> None:
    panel_style = dict(
        transform=ax.transAxes,
        color=CHART_STYLE["text"],
        fontsize=6.8,
        linespacing=1.35,
        bbox=dict(
            boxstyle="round,pad=0.4",
            facecolor=CHART_STYLE["panel"],
            edgecolor=CHART_STYLE["panel_border"],
            alpha=0.93,
        ),
    )
    bull_style = {**panel_style, "color": CHART_STYLE["accent_long"]}
    bear_style = {**panel_style, "color": CHART_STYLE["accent_short"]}

    verdict_text = ta_chart_panel_text(ta)
    ax.text(0.99, 0.98, verdict_text, va="top", ha="right", **panel_style)

    key_levels = ta_chart_key_levels_text(ta)
    if key_levels:
        ax.text(0.01, 0.72, key_levels, va="top", ha="left", **panel_style)

    ax.text(
        0.01, 0.98, ta_chart_legend_text(),
        va="top", ha="left", fontsize=5.8, color=CHART_STYLE["text"],
        transform=ax.transAxes,
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor=CHART_STYLE["panel"],
            edgecolor=CHART_STYLE["panel_border"],
            alpha=0.85,
        ),
    )

    if ta.trader_plan:
        plan_lines = [f"{i + 1}. {step}" for i, step in enumerate(ta.trader_plan[:6])]
        ax.text(
            0.01, 0.38, "ПЛАН ДЕЙСТВИЙ:\n" + "\n".join(plan_lines),
            va="top", ha="left", **panel_style,
        )

    bull_text = ta_chart_scenario_text(ta.bullish_scenario, title="БЫЧИЙ СЦЕНАРИЙ")
    if bull_text:
        ax.text(0.99, 0.58, bull_text, va="top", ha="right", **bull_style)

    bear_text = ta_chart_scenario_text(ta.bearish_scenario, title="МЕДВЕЖИЙ СЦЕНАРИЙ")
    if bear_text:
        ax.text(0.99, 0.30, bear_text, va="top", ha="right", **bear_style)

    summary = ta_chart_summary_text(ta)
    if summary:
        ax.text(
            0.5, 0.01, summary,
            va="bottom", ha="center", fontsize=6.5, color=CHART_STYLE["text"],
            transform=ax.transAxes,
            bbox=dict(
                boxstyle="round,pad=0.35",
                facecolor=CHART_STYLE["panel"],
                edgecolor=CHART_STYLE["warning"],
                alpha=0.92,
            ),
        )


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
) -> bytes:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=120)
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.set_facecolor(CHART_STYLE["bg"])

    _draw_candles(ax, bars, interval_minutes=interval_minutes)
    _draw_ta_annotations(ax, bars, ta)

    current = bars[-1].close
    ax.axhline(current, color=accent_color, linestyle="--", linewidth=0.9, alpha=0.85)
    last_ts = _idx_to_date(bars, len(bars) - 1)
    ax.text(
        mdates.date2num(last_ts), current, f"  сейчас {fmt_price(current)}",
        color=accent_color, fontsize=7, va="center", ha="left",
    )
    ax.set_title(
        f"{symbol}  ·  {ta.verdict} {ta_display_score(ta)}/10  ·  {title_suffix}",
        color=CHART_STYLE["text"], fontsize=11, pad=14,
    )
    _draw_info_panels(ax, ta)
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
) -> tuple[bytes | None, TAAnalysisResult | None]:
    bars = await _fetch_bars(symbol, hours, interval_minutes=interval_minutes)
    if not bars:
        return None, None

    btc_bars: list[KlineBar] | None = None
    if symbol.upper() not in {"BTCUSDT", "BTCUSD", "BTCUSDC"}:
        btc_bars = await _fetch_bars("BTCUSDT", hours, interval_minutes=interval_minutes)

    is_long = side == "long"
    ta = run_ta_analysis(
        bars,
        is_long=is_long,
        oi_bars=oi_bars,
        btc_bars=btc_bars,
        symbol=symbol,
        hours=hours,
        invalidation_price=invalidation_price,
        neutral=neutral,
    )
    if verdict_override:
        ta.verdict = verdict_override

    accent = CHART_STYLE["accent_long"] if is_long else CHART_STYLE["accent_short"]
    png = _render_chart_figure(
        bars, ta,
        symbol=symbol,
        title_suffix=f"Bybit {interval_minutes}m · {hours}ч",
        accent_color=accent,
        interval_minutes=interval_minutes,
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
