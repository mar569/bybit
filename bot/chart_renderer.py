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

logger = logging.getLogger(__name__)

_kline_cache = BybitKlineCache(ttl_seconds=60.0)

CHART_STYLE = {
    "bg": "#0d1117",
    "grid": "#21262d",
    "text": "#c9d1d9",
    "up": "#26a69a",
    "down": "#ef5350",
    "accent_long": "#3fb950",
    "accent_short": "#f85149",
    "warning": "#d29922",
}


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


async def render_signal_chart(
    symbol: str,
    *,
    side: str = "long",
    hours: int = 5,
    structure_warning: str = "",
    probability_percent: float | None = None,
) -> bytes | None:
    """Свечной график из публичных klines Bybit — API-ключ не нужен."""
    limit = max(24, min(hours * 12 + 2, 120))
    bars = await _kline_cache.get_klines(symbol, limit=limit)
    if len(bars) < 12:
        return None

    max_bars = hours * 12
    bars = bars[-max_bars:]

    fig, ax = plt.subplots(figsize=(10, 5), dpi=120)
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.set_facecolor(CHART_STYLE["bg"])

    _draw_candles(ax, bars)

    current = bars[-1].close
    peak = max(bar.high for bar in bars)
    trough = min(bar.low for bar in bars)
    accent = CHART_STYLE["accent_long"] if side == "long" else CHART_STYLE["accent_short"]
    side_label = "LONG" if side == "long" else "SHORT"

    ax.axhline(current, color=accent, linestyle="--", linewidth=0.9, alpha=0.85)
    ax.axhline(peak, color=CHART_STYLE["warning"], linestyle=":", linewidth=0.7, alpha=0.6)

    prob_text = f" | {probability_percent:.0f}%" if probability_percent is not None else ""
    title = f"{symbol}  {side_label}{prob_text}  ·  Bybit {hours}ч"
    ax.set_title(title, color=CHART_STYLE["text"], fontsize=12, pad=10)

    dd = (peak - current) / peak * 100 if peak > 0 else 0
    subtitle = f"цена {current:.5g}  |  хай {peak:.5g}  |  −{dd:.1f}% от хая"
    if structure_warning:
        subtitle += f"\n⚠ {structure_warning[:90]}"
    ax.text(
        0.01, 0.98, subtitle,
        transform=ax.transAxes,
        va="top", ha="left",
        color=CHART_STYLE["text"],
        fontsize=8,
    )

    ax.grid(True, color=CHART_STYLE["grid"], linewidth=0.4, alpha=0.7)
    ax.tick_params(colors=CHART_STYLE["text"], labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(CHART_STYLE["grid"])

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=0)
    ax.set_ylabel("USDT", color=CHART_STYLE["text"], fontsize=9)

    ymin = trough * 0.998
    ymax = peak * 1.002
    if ymax > ymin:
        ax.set_ylim(ymin, ymax)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


async def get_signal_chart_png(
    signal_exchange: str,
    signal_symbol: str,
    *,
    chart_source: str = "tradingview",
    chart_hours: int = 5,
    chart_interval_minutes: int = 5,
    side: str = "long",
    structure_warning: str = "",
    probability_percent: float | None = None,
    coinglass_url: str = "",
) -> tuple[bytes | None, str]:
    """
    Возвращает (png, label).
    label: tradingview | coinglass | generated | none
    """
    source = (chart_source or "tradingview").lower()

    if source == "tradingview":
        png = await chart_capture_service.capture_tradingview(
            signal_exchange,
            signal_symbol,
            interval_minutes=chart_interval_minutes,
        )
        if png:
            return png, "tradingview"
    elif source == "coinglass" and coinglass_url:
        png = await chart_capture_service.capture_coinglass(coinglass_url)
        if png:
            return png, "coinglass"

    if source != "generated":
        logger.info(
            "Real chart unavailable for %s, fallback to generated",
            signal_symbol,
        )

    png = await render_signal_chart(
        signal_symbol,
        side=side,
        hours=chart_hours,
        structure_warning=structure_warning,
        probability_percent=probability_percent,
    )
    return png, "generated" if png else "none"
