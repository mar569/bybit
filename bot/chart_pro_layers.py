"""Дополнительные PRO-слои на matplotlib-графиках: зоны, пути, RSI, HTF, sweep."""
from __future__ import annotations

from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, Rectangle

from .bybit_klines import KlineBar
from .ta_analysis import TAAnalysisResult, _short_trigger_state, fmt_price

CHART_BG = "#0d1117"
CHART_TEXT = "#c9d1d9"
CHART_GRID = "#21262d"


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _bar_times(bars: list[KlineBar]) -> list[datetime]:
    return [datetime.fromtimestamp(b.open_time, tz=timezone.utc) for b in bars]


def _draw_zigzag_path(
    ax: plt.Axes,
    bars: list[KlineBar],
    waypoints: list[float],
    *,
    color: str,
    label: str,
    alpha: float = 0.88,
    lw: float = 1.35,
) -> None:
    if not bars or len(waypoints) < 2:
        return
    times = _bar_times(bars)
    start_x = mdates.date2num(times[-1])
    span = mdates.date2num(times[-1]) - mdates.date2num(times[max(0, len(bars) - 24)])
    step = span / max(len(waypoints), 2)
    xs = [start_x + step * i for i in range(len(waypoints))]
    ax.plot(xs, waypoints, color=color, linestyle="--", linewidth=lw, alpha=alpha, zorder=4)
    ax.annotate(
        "",
        xy=(xs[-1], waypoints[-1]),
        xytext=(xs[-2], waypoints[-2]),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, linestyle="dashed", alpha=alpha),
    )
    va = "top" if waypoints[-1] < waypoints[0] else "bottom"
    ax.text(xs[-1], waypoints[-1], f" {label}", color=color, fontsize=6.5, fontweight="bold", va=va)


def _compute_rsi(bars: list[KlineBar], period: int = 14) -> list[float]:
    if len(bars) < period + 1:
        return [50.0] * len(bars)
    closes = [b.close for b in bars]
    rsi: list[float] = [50.0] * len(closes)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    if len(gains) < period:
        return rsi
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def draw_buy_flat_sell_zones(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Горизонтальные зоны BUY / flat / SELL (SMC premium/discount или range)."""
    if not bars:
        return
    x0 = mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 55)))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    width = max(x1 - x0, 0.002)

    sell_lo = sell_hi = flat_lo = flat_hi = buy_lo = buy_hi = None

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
            return
        sell_lo, sell_hi = hi - span * 0.28, hi
        buy_lo, buy_hi = lo, lo + span * 0.28
        flat_lo, flat_hi = buy_hi, sell_lo

    def _band(lo: float, hi: float, color: str, label: str) -> None:
        if hi <= lo:
            return
        ax.add_patch(
            Rectangle(
                (x0, lo), width, hi - lo,
                facecolor=color, edgecolor="none", alpha=0.11, zorder=0,
            )
        )
        ax.text(x0 + width * 0.02, (lo + hi) / 2, f" {label}",
                color=color, fontsize=7, fontweight="bold", va="center", alpha=0.9)

    _band(buy_lo, buy_hi, "#3fb950", "BUY")
    _band(flat_lo, flat_hi, "#8b949e", "flat")
    _band(sell_lo, sell_hi, "#f85149", "SELL")


def draw_bounce_short_path(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    """Путь отскок → пробой вниз (EPIC-подобные сетапы)."""
    if ta.verdict != "SHORT" or not ta.breakdown_level or not bars:
        return False
    state, _ = _short_trigger_state(ta)
    if state == "ready":
        return False
    px = bars[-1].close
    bd = ta.breakdown_level
    if px <= bd * 1.001:
        return False
    resist = ta.breakout_level or ta.nearest_resistance
    if not resist or resist <= px * 1.0005:
        resist = px * (1.006 if px > 0 else 1.006)
    tp = ta.target_prices[0] if ta.target_prices else bd * 0.992
    mid_pull = (px + resist) / 2.0
    waypoints = [px, mid_pull, resist, bd, tp]
    _draw_zigzag_path(ax, bars, waypoints, color="#c9d1d9", label="отскок→short", lw=1.5)
    return True


def draw_flat_breakout_path(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    """Серый путь: боковик → пробой (3–4 ноги)."""
    if not bars or not ta.consolidation:
        return False
    z = ta.consolidation
    px = bars[-1].close
    mid = (z.top + z.bottom) / 2.0
    if ta.verdict == "SHORT" and ta.breakdown_level:
        target = ta.target_prices[0] if ta.target_prices else ta.breakdown_level * 0.995
        waypoints = [px, z.top, mid, z.bottom, ta.breakdown_level, target]
        label = "flat→short"
    elif ta.verdict == "LONG" and ta.breakout_level:
        target = ta.target_prices[0] if ta.target_prices else ta.breakout_level * 1.008
        waypoints = [px, z.bottom, mid, z.top, ta.breakout_level, target]
        label = "flat→long"
    else:
        waypoints = [px, z.top, mid, z.bottom, mid]
        label = "flat"
    _draw_zigzag_path(ax, bars, waypoints, color="#8b949e", label=label, alpha=0.75, lw=1.2)
    return True


def draw_sweep_circles(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Кружки на свипах ликвидности у экстремумов / тренда."""
    smc = ta.smc
    if smc is None or not bars:
        return
    ref = bars[-1].close or 1.0
    w_days = max(0.012, len(bars) * 0.0008)
    h_price = ref * 0.006

    for marker in smc.markers:
        if marker.kind != "sweep" or marker.index >= len(bars):
            continue
        ts = mdates.date2num(_idx_to_date(bars, marker.index))
        color = "#ffd33d" if marker.direction == "long" else "#ff7b72"
        ax.add_patch(
            Ellipse(
                (ts, marker.price), w_days, h_price,
                fill=False, edgecolor=color, linewidth=2.0, linestyle="-", zorder=6,
            )
        )
        ax.text(
            ts, marker.price + h_price * 0.6, " sweep",
            color=color, fontsize=6, ha="center", va="bottom", fontweight="bold",
        )

    if smc.liquidity_sweep and not any(m.kind == "sweep" for m in smc.markers):
        for swing in ta.swings[-4:]:
            if swing.index >= len(bars):
                continue
            bar = bars[swing.index]
            swept = (
                (swing.kind == "high" and bar.high > swing.price * 1.0008)
                or (swing.kind == "low" and bar.low < swing.price * 0.9992)
            )
            if not swept:
                continue
            ts = mdates.date2num(_idx_to_date(bars, swing.index))
            ax.add_patch(
                Ellipse(
                    (ts, swing.price), w_days, h_price,
                    fill=False, edgecolor="#ffd33d", linewidth=1.6, linestyle="--", zorder=6,
                )
            )


def draw_swing_liquidity_marks(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Метки объёма у swing high/low (ликвидность)."""
    if not bars or not ta.swings:
        return
    for swing in ta.swings[-8:]:
        if swing.index >= len(bars):
            continue
        bar = bars[swing.index]
        vol_k = bar.volume / 1000.0
        if vol_k < 0.5:
            continue
        ts = mdates.date2num(_idx_to_date(bars, swing.index))
        y = swing.price
        color = "#58a6ff" if swing.kind == "low" else "#f0883e"
        va = "top" if swing.kind == "high" else "bottom"
        offset = y * 0.0015 if swing.kind == "high" else -y * 0.0015
        ax.text(
            ts, y + offset, f"{vol_k:.1f}K",
            color=color, fontsize=5.5, ha="center", va=va,
            bbox=dict(boxstyle="round,pad=0.15", facecolor=CHART_BG, edgecolor=color, alpha=0.85),
        )


def draw_volume_panel(ax: plt.Axes, bars: list[KlineBar]) -> None:
    if not bars:
        return
    times = _bar_times(bars)
    colors = [("#26a69a" if b.close >= b.open else "#ef5350") for b in bars]
    ax.bar(times, [b.volume for b in bars], width=0.003, color=colors, alpha=0.75)
    ax.set_ylabel("Vol", color=CHART_TEXT, fontsize=7)
    ax.tick_params(colors=CHART_TEXT, labelsize=6)
    ax.set_facecolor(CHART_BG)
    ax.grid(True, color=CHART_GRID, linewidth=0.35, alpha=0.6)


def draw_rsi_panel(ax: plt.Axes, bars: list[KlineBar]) -> None:
    if not bars:
        return
    times = _bar_times(bars)
    rsi = _compute_rsi(bars)
    ax.plot(times, rsi, color="#a371f7", linewidth=1.0, alpha=0.9)
    ax.axhline(70, color="#f85149", linestyle=":", linewidth=0.6, alpha=0.5)
    ax.axhline(30, color="#3fb950", linestyle=":", linewidth=0.6, alpha=0.5)
    ax.fill_between(times, rsi, 70, where=[v >= 70 for v in rsi], color="#f85149", alpha=0.12)
    ax.fill_between(times, rsi, 30, where=[v <= 30 for v in rsi], color="#3fb950", alpha=0.12)
    ax.set_ylim(0, 100)
    ax.set_ylabel("RSI", color=CHART_TEXT, fontsize=7)
    last = rsi[-1]
    ax.text(times[-1], last, f" {last:.0f}", color="#a371f7", fontsize=6, va="center")
    ax.tick_params(colors=CHART_TEXT, labelsize=6)
    ax.set_facecolor(CHART_BG)
    ax.grid(True, color=CHART_GRID, linewidth=0.35, alpha=0.6)


def draw_htf_inset(
    fig: plt.Figure,
    htf_bars: list[KlineBar] | None,
    ta: TAAnalysisResult,
    *,
    interval_label: str = "2h",
) -> None:
    if not htf_bars or len(htf_bars) < 8:
        return
    axins = fig.add_axes([0.70, 0.70, 0.27, 0.20])
    axins.set_facecolor("#161b22")
    seg = htf_bars[-min(36, len(htf_bars)) :]
    xs = list(range(len(seg)))
    for i, bar in enumerate(seg):
        color = "#26a69a" if bar.close >= bar.open else "#ef5350"
        axins.plot([i, i], [bar.low, bar.high], color=color, linewidth=0.8, alpha=0.9)
        body_lo = min(bar.open, bar.close)
        body_hi = max(bar.open, bar.close)
        axins.add_patch(
            Rectangle((i - 0.35, body_lo), 0.7, max(body_hi - body_lo, 1e-12),
                      facecolor=color, edgecolor=color, alpha=0.95)
        )
    htf_label = ""
    if ta.smc and ta.smc.htf_structure_label:
        htf_label = ta.smc.htf_structure_label
    elif ta.structure_label:
        htf_label = ta.structure_label
    bias = ta.market_bias or "—"
    axins.set_title(f"HTF {interval_label} · {htf_label[:22]} · {bias}", color=CHART_TEXT, fontsize=6.2, pad=2)
    axins.tick_params(colors=CHART_TEXT, labelsize=5, length=2)
    axins.grid(True, color=CHART_GRID, linewidth=0.3, alpha=0.5)
    for spine in axins.spines.values():
        spine.set_color("#30363d")


def draw_pro_chart_layers(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> str:
    """
    Рисует PRO-слои (пути, sweep, liq) на основном графике.
    Возвращает kind основного пути: bounce_short | flat_breakout | default
    """
    draw_swing_liquidity_marks(ax, bars, ta)
    draw_sweep_circles(ax, bars, ta)
    if draw_bounce_short_path(ax, bars, ta):
        return "bounce_short"
    if draw_flat_breakout_path(ax, bars, ta):
        return "flat_breakout"
    return "default"
