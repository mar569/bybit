from __future__ import annotations

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
    ta_chart_panel_text,
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


def _draw_candles(ax: plt.Axes, bars: list[KlineBar]) -> None:
    if not bars:
        return
    width_minutes = 4.0
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
    x0 = mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 40)))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    width = max(x1 - x0, 0.001)
    for zone in ta.zones:
        color = CHART_STYLE["zone_resistance"] if zone.kind == "resistance" else CHART_STYLE["zone_support"]
        rect = Rectangle(
            (x0, zone.bottom),
            width,
            zone.top - zone.bottom,
            facecolor=color,
            edgecolor=color,
            alpha=0.14,
            linewidth=0.6,
            linestyle="--",
        )
        ax.add_patch(rect)
        ax.text(
            x0, zone.top, f"  {zone.label}",
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

    if ta.consolidation:
        z = ta.consolidation
        x0 = _idx_to_date(bars, z.start_idx)
        x1 = _idx_to_date(bars, z.end_idx)
        rect = Rectangle(
            (mdates.date2num(x0), z.bottom),
            mdates.date2num(x1) - mdates.date2num(x0),
            z.top - z.bottom,
            facecolor=CHART_STYLE["grid"],
            edgecolor=CHART_STYLE["grid"],
            alpha=0.1,
            linewidth=0.8,
            linestyle=":",
        )
        ax.add_patch(rect)

    if ta.breakout_level:
        ax.axhline(ta.breakout_level, color=CHART_STYLE["entry"], linestyle="-", linewidth=1.0, alpha=0.85)
        ax.text(
            mdates.date2num(x_end), ta.breakout_level,
            f" вход {fmt_price(ta.breakout_level)}",
            color=CHART_STYLE["entry"], fontsize=7, va="bottom",
        )

    for lv in ta.levels:
        color = CHART_STYLE["level_support"] if lv.kind == "support" else CHART_STYLE["level_resistance"]
        ax.axhline(lv.price, color=color, linestyle="-", linewidth=0.75, alpha=0.55)
        ax.text(
            mdates.date2num(x_end), lv.price, f" {fmt_price(lv.price)}",
            color=color, fontsize=6.5, va="center", ha="left",
        )

    for tl in ta.trend_lines:
        color = CHART_STYLE["trend_bull"] if tl.kind == "bull" else CHART_STYLE["trend_bear"]
        x0 = _idx_to_date(bars, tl.start_idx)
        x1 = _idx_to_date(bars, tl.end_idx)
        ax.plot([x0, x1], [tl.start_price, tl.end_price], color=color, linewidth=1.0, alpha=0.75)

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
            mdates.date2num(x_end), ta.invalidation_price, " STOP",
            color=CHART_STYLE["inv"], fontsize=7, va="center",
        )

    for j, tp in enumerate(ta.target_prices[:4]):
        ax.axhline(tp, color=CHART_STYLE["target"], linestyle=":", linewidth=0.75, alpha=0.65)
        ax.text(
            mdates.date2num(x_end), tp, f" TP{j + 1}",
            color=CHART_STYLE["target"], fontsize=6.5, va="center",
        )

    if ta.entry_zone:
        lo, hi = ta.entry_zone
        ax.axhspan(lo, hi, color=CHART_STYLE["accent_long"], alpha=0.07)


def _draw_info_panels(ax: plt.Axes, ta: TAAnalysisResult) -> None:
    panel_style = dict(
        transform=ax.transAxes,
        color=CHART_STYLE["text"],
        fontsize=6.5,
        linespacing=1.35,
        bbox=dict(
            boxstyle="round,pad=0.45",
            facecolor=CHART_STYLE["panel"],
            edgecolor=CHART_STYLE["panel_border"],
            alpha=0.93,
        ),
    )

    verdict_text = ta_chart_panel_text(ta)
    ax.text(0.99, 0.98, verdict_text, va="top", ha="right", **panel_style)

    if ta.trader_plan:
        plan_lines = [f"{i + 1}. {step}" for i, step in enumerate(ta.trader_plan[:5])]
        ax.text(
            0.01, 0.02, "ПЛАН:\n" + "\n".join(plan_lines),
            va="bottom", ha="left", **panel_style,
        )

    scenario_lines: list[str] = []
    if ta.bullish_scenario:
        bs = ta.bullish_scenario
        tps = " → ".join(fmt_price(t) for t in bs.target_prices[:3])
        scenario_lines.append(f"↑ LONG: {fmt_price(bs.trigger_price)}")
        scenario_lines.append(f"  {tps}")
    if ta.bearish_scenario:
        bs = ta.bearish_scenario
        tps = " → ".join(fmt_price(t) for t in bs.target_prices[:3])
        scenario_lines.append(f"↓ SHORT: {fmt_price(bs.trigger_price)}")
        scenario_lines.append(f"  {tps}")
    if scenario_lines:
        ax.text(
            0.99, 0.02, "СЦЕНАРИИ:\n" + "\n".join(scenario_lines),
            va="bottom", ha="right", **panel_style,
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
) -> bytes:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=120)
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.set_facecolor(CHART_STYLE["bg"])

    _draw_candles(ax, bars)
    _draw_ta_annotations(ax, bars, ta)

    current = bars[-1].close
    ax.axhline(current, color=accent_color, linestyle="--", linewidth=0.9, alpha=0.85)
    ax.set_title(
        f"{symbol}  ·  {ta.verdict} {ta.verdict_confidence}/10  ·  {title_suffix}",
        color=CHART_STYLE["text"], fontsize=11, pad=10,
    )
    _draw_info_panels(ax, ta)
    _style_axes(ax, bars)
    fig.autofmt_xdate(rotation=0)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


async def _fetch_bars(symbol: str, hours: int) -> list[KlineBar]:
    limit = max(24, min(hours * 12 + 2, 120))
    bars = await _kline_cache.get_klines(symbol, limit=limit)
    if len(bars) < 12:
        return []
    return bars[-hours * 12:]


async def render_annotated_chart(
    symbol: str,
    *,
    side: str = "long",
    hours: int = 5,
    structure_warning: str = "",
    oi_bars: list[FiveMinOiBar] | None = None,
    invalidation_price: float | None = None,
    verdict_override: str | None = None,
) -> tuple[bytes | None, TAAnalysisResult | None]:
    bars = await _fetch_bars(symbol, hours)
    if not bars:
        return None, None

    btc_bars: list[KlineBar] | None = None
    if symbol.upper() not in {"BTCUSDT", "BTCUSD", "BTCUSDC"}:
        btc_bars = await _fetch_bars("BTCUSDT", hours)

    is_long = side == "long"
    ta = run_ta_analysis(
        bars,
        is_long=is_long,
        oi_bars=oi_bars,
        btc_bars=btc_bars,
        symbol=symbol,
        hours=hours,
        invalidation_price=invalidation_price,
    )
    if verdict_override:
        ta.verdict = verdict_override

    accent = CHART_STYLE["accent_long"] if is_long else CHART_STYLE["accent_short"]
    png = _render_chart_figure(
        bars, ta,
        symbol=symbol,
        title_suffix=f"Bybit 5m · {hours}ч",
        accent_color=accent,
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
) -> tuple[bytes | None, str, TAAnalysisResult | None]:
    source = (chart_source or "annotated").lower()

    if source == "annotated":
        png, ta = await render_signal_chart(
            signal_symbol,
            side=side,
            hours=chart_hours,
            structure_warning=structure_warning,
            probability_percent=probability_percent,
            oi_bars=oi_bars,
        )
        return png, "annotated" if png else "none", ta

    if source == "tradingview":
        png = await chart_capture_service.capture_tradingview(
            signal_exchange,
            signal_symbol,
            interval_minutes=chart_interval_minutes,
        )
        if png:
            return png, "tradingview", None
    elif source == "coinglass" and coinglass_url:
        png = await chart_capture_service.capture_coinglass(coinglass_url)
        if png:
            return png, "coinglass", None

    if source not in {"generated", "annotated"}:
        logger.info("Real chart unavailable for %s, fallback to annotated", signal_symbol)

    png, ta = await render_signal_chart(
        signal_symbol,
        side=side,
        hours=chart_hours,
        structure_warning=structure_warning,
        probability_percent=probability_percent,
        oi_bars=oi_bars,
    )
    return png, "annotated" if png else "none", ta
