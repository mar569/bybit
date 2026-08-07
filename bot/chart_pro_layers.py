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


def _bar_width_days(bars: list[KlineBar]) -> float:
    times = _bar_times(bars)
    if len(times) < 2:
        return 5.0 / (24 * 60)
    widths = [
        mdates.date2num(times[i]) - mdates.date2num(times[i - 1])
        for i in range(1, len(times))
    ]
    return max(sum(widths) / len(widths), 1e-6)


def _forecast_path_xs(bars: list[KlineBar], n_points: int) -> list[float]:
    if not bars or n_points < 1:
        return []
    times = _bar_times(bars)
    start_x = mdates.date2num(times[-1])
    step = _bar_width_days(bars) * 5.5
    return [start_x + step * i for i in range(n_points)]


def _draw_zigzag_path(
    ax: plt.Axes,
    bars: list[KlineBar],
    waypoints: list[float],
    *,
    color: str,
    label: str,
    alpha: float = 0.88,
    lw: float = 1.35,
    linestyle: str = "--",
) -> None:
    if not bars or len(waypoints) < 2:
        return
    xs = _forecast_path_xs(bars, len(waypoints))
    ax.plot(xs, waypoints, color=color, linestyle=linestyle, linewidth=lw, alpha=alpha, zorder=4)
    ax.annotate(
        "",
        xy=(xs[-1], waypoints[-1]),
        xytext=(xs[-2], waypoints[-2]),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, linestyle="dashed", alpha=alpha),
    )
    va = "top" if waypoints[-1] < waypoints[0] else "bottom"
    label_x = xs[-1] + _bar_width_days(bars) * 2.0
    ax.text(label_x, waypoints[-1], label, color=color, fontsize=7, fontweight="bold", va=va, ha="left")


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


def draw_trend_dump_path(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    """Активный слив после тренда: OI/liq/momentum → яркий путь вниз."""
    if ta.verdict == "WAIT":
        return False
    if ta.verdict == "LONG":
        return False
    if not bars or ta.momentum_pct > -0.8:
        return False
    if not (ta.post_pump or ta.repeat_spike_dump_risk or ta.drawdown_from_high_pct >= 2.5):
        return False
    corr = ta.correction_path
    cont = ta.continuation_path
    corr_wins = bool(
        corr and (cont is None or corr.confidence >= cont.confidence or ta.momentum_pct <= -1.2)
    )
    if not corr_wins:
        return False
    if corr and len(corr.waypoints) >= 3:
        _draw_zigzag_path(
            ax, bars, corr.waypoints,
            color="#ff7b72", label=corr.label or "слив ↓", lw=1.65, alpha=0.95,
        )
        return True
    px = bars[-1].close
    tgt = ta.nearest_support or ta.breakdown_level or px * 0.94
    if tgt >= px * 0.998:
        tgt = px * 0.96
    waypoints = [px, px * 0.992, px * 0.978, tgt]
    _draw_zigzag_path(ax, bars, waypoints, color="#ff7b72", label="слив ↓", lw=1.65, alpha=0.95)
    return True


def draw_trend_dump_risk_path(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> bool:
    """Превентивный путь: перегрев у хая, слив ещё не подтверждён."""
    if ta.verdict == "WAIT":
        return False
    if not bars:
        return False
    if not (ta.post_pump or ta.repeat_spike_dump_risk):
        return False
    if ta.drawdown_from_high_pct >= 2.5 or ta.momentum_pct <= -0.8:
        return False
    if ta.drawdown_from_high_pct > 1.8 and ta.momentum_pct < -0.3:
        return False

    px = bars[-1].close
    tgt = ta.nearest_support or ta.breakdown_level or px * 0.94
    if tgt >= px * 0.998:
        tgt = px * 0.965
    waypoints = [px, px * 0.997, px * 0.988, px * 0.978, tgt]
    _draw_zigzag_path(
        ax, bars, waypoints,
        color="#ffa657", label="риск слива ↓", lw=1.15, alpha=0.62, linestyle=":",
    )
    return True


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
        if target >= px:
            target = ta.breakdown_level * 0.995
        waypoints = [px, z.top, mid, z.bottom, ta.breakdown_level, target]
        label = "flat→short"
    elif ta.verdict == "LONG" and ta.breakout_level:
        target = ta.target_prices[0] if ta.target_prices else ta.breakout_level * 1.008
        if target <= px:
            target = ta.breakout_level * 1.008
        waypoints = [px, z.bottom, mid, z.top, ta.breakout_level, target]
        label = "flat→long"
    elif ta.verdict == "WAIT" and ta.breakdown_level and ta.breakout_level:
        px = bars[-1].close
        short_tgt = ta.bearish_scenario.target_prices[0] if (
            ta.bearish_scenario and ta.bearish_scenario.target_prices
        ) else ta.breakdown_level * 0.995
        long_tgt = ta.bullish_scenario.target_prices[0] if (
            ta.bullish_scenario and ta.bullish_scenario.target_prices
        ) else ta.breakout_level * 1.008
        if short_tgt >= px:
            short_tgt = ta.breakdown_level * 0.995
        if long_tgt <= px:
            long_tgt = ta.breakout_level * 1.008
        short_wp = [px, z.top, mid, z.bottom, ta.breakdown_level, short_tgt]
        long_wp = [px, z.bottom, mid, z.top, ta.breakout_level, long_tgt]
        lean = (getattr(ta, "action_priority", "") or "").lower()
        if lean not in {"long", "short"}:
            ps = (getattr(ta, "primary_scenario", "") or "").lower()
            if "вверх" in ps or "long" in ps:
                lean = "long"
            elif "вниз" in ps or "short" in ps:
                lean = "short"
        # Не красим short ярко-красным при «приоритет вверх» — выглядит как сигнал SHORT
        if lean == "long":
            _draw_zigzag_path(ax, bars, long_wp, color="#3fb950", label="flat→long", alpha=0.72, lw=1.25)
            _draw_zigzag_path(ax, bars, short_wp, color="#8b949e", label="alt↓", alpha=0.22, lw=0.9)
        elif lean == "short":
            _draw_zigzag_path(ax, bars, short_wp, color="#f85149", label="flat→short", alpha=0.72, lw=1.25)
            _draw_zigzag_path(ax, bars, long_wp, color="#8b949e", label="alt↑", alpha=0.22, lw=0.9)
        else:
            _draw_zigzag_path(ax, bars, short_wp, color="#8b949e", label="flat→short", alpha=0.38, lw=1.0)
            _draw_zigzag_path(ax, bars, long_wp, color="#8b949e", label="flat→long", alpha=0.38, lw=1.0)
        return True
    elif ta.verdict == "WAIT" and ta.breakdown_level:
        target = ta.bearish_scenario.target_prices[0] if (
            ta.bearish_scenario and ta.bearish_scenario.target_prices
        ) else ta.breakdown_level * 0.995
        if target >= px:
            target = ta.breakdown_level * 0.995
        waypoints = [px, z.top, mid, z.bottom, ta.breakdown_level, target]
        _draw_zigzag_path(ax, bars, waypoints, color="#8b949e", label="flat→short", alpha=0.55, lw=1.1)
        return True
    elif ta.verdict == "WAIT" and ta.breakout_level:
        target = ta.bullish_scenario.target_prices[0] if (
            ta.bullish_scenario and ta.bullish_scenario.target_prices
        ) else ta.breakout_level * 1.008
        if target <= px:
            target = ta.breakout_level * 1.008
        waypoints = [px, z.bottom, mid, z.top, ta.breakout_level, target]
        _draw_zigzag_path(ax, bars, waypoints, color="#8b949e", label="flat→long", alpha=0.55, lw=1.1)
        return True
    else:
        # без уровней пробоя — не рисуем «путь в никуда»
        return False
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
    """Метки объёма у swing high/low (ликвидность) — только крупные, с отступом."""
    if not bars or not ta.swings:
        return
    # На WAIT меньше шума у правого края
    max_marks = 2 if (getattr(ta, "verdict", "") or "").upper() == "WAIT" else 4
    bar_w = _bar_width_days(bars)
    shown = 0
    for swing in reversed(ta.swings[-6:]):
        if swing.index >= len(bars) or shown >= max_marks:
            continue
        # не клеить метки к последним 4 свечам (там path/уровни)
        if swing.index >= len(bars) - 4:
            continue
        bar = bars[swing.index]
        vol_k = bar.volume / 1000.0
        if vol_k < 8.0:
            continue
        ts = mdates.date2num(_idx_to_date(bars, swing.index))
        y = swing.price
        color = "#58a6ff" if swing.kind == "low" else "#f0883e"
        va = "top" if swing.kind == "high" else "bottom"
        offset = y * 0.004 if swing.kind == "high" else -y * 0.004
        x_shift = bar_w * (1.2 if shown % 2 else -1.2)
        ax.text(
            ts + x_shift, y + offset, f"{vol_k:.0f}K",
            color=color, fontsize=6.2, ha="center", va=va,
            bbox=dict(boxstyle="round,pad=0.18", facecolor=CHART_BG, edgecolor=color, alpha=0.82),
        )
        shown += 1


def draw_volume_panel(ax: plt.Axes, bars: list[KlineBar]) -> None:
    if not bars:
        return
    times = _bar_times(bars)
    vols = [max(0.0, float(getattr(b, "volume", 0) or 0)) for b in bars]
    # TradFi/MT5 часто даёт пустой/плоский volume — тогда activity = диапазон свечи
    nonzero = sum(1 for v in vols if v > 0)
    use_proxy = nonzero < max(3, len(vols) // 4)
    if not use_proxy and max(vols) > 0:
        vmin = min(vols)
        if vmin > 0 and (max(vols) / vmin) < 1.08:
            use_proxy = True
    if use_proxy:
        vals = [
            max(1e-9, float(b.high) - float(b.low)) * 100.0
            + abs(float(b.close) - float(b.open)) * 50.0
            for b in bars
        ]
        ylabel = "Act"
    else:
        vals = vols
        ylabel = "Vol"
    colors = [("#26a69a" if b.close >= b.open else "#ef5350") for b in bars]
    width = max(_bar_width_days(bars) * 0.7, 0.0008)
    ax.bar(times, vals, width=width, color=colors, alpha=0.75)
    ax.set_ylabel(ylabel, color=CHART_TEXT, fontsize=7)
    ax.tick_params(colors=CHART_TEXT, labelsize=6)
    ax.set_facecolor(CHART_BG)
    ax.grid(True, color=CHART_GRID, linewidth=0.35, alpha=0.6)


def draw_rsi_panel(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    divergences: list | None = None,
    rsi_values: list[float] | None = None,
    rsi_sma: list[float] | None = None,
) -> None:
    """RSI 14 + SMA + divergence; TV-style purple 30–70 band + white line."""
    if not bars:
        return
    from .rsi_divergence import RsiDivergence, compute_rsi_wilder, compute_sma

    times = _bar_times(bars)
    rsi = list(rsi_values) if rsi_values and len(rsi_values) == len(bars) else compute_rsi_wilder(
        [b.close for b in bars], 14,
    )
    sma = list(rsi_sma) if rsi_sma and len(rsi_sma) == len(bars) else compute_sma(rsi, 14)

    # TradingView-like mid band (30–70) — main visual anchor
    ax.axhspan(30, 70, facecolor="#5c4a8a", alpha=0.42, zorder=0, linewidth=0)
    # Soft OB/OS tint outside the band
    ax.axhspan(70, 100, facecolor="#f85149", alpha=0.07, zorder=0, linewidth=0)
    ax.axhspan(0, 30, facecolor="#3fb950", alpha=0.07, zorder=0, linewidth=0)

    # Boundary + mid dashed levels (like TV dashed grid)
    for lvl, lw, alpha in (
        (70, 0.95, 0.75),
        (60, 0.55, 0.40),
        (50, 0.75, 0.55),
        (40, 0.55, 0.40),
        (30, 0.95, 0.75),
    ):
        ax.axhline(
            lvl,
            color="#c9b8f0" if lvl in (30, 70) else "#8b949e",
            linestyle=(0, (3.5, 2.8)),
            linewidth=lw,
            alpha=alpha,
            zorder=1,
        )

    ax.plot(times, sma, color="#e3b341", linewidth=0.9, alpha=0.55, label="SMA", zorder=2)
    # High-contrast RSI line (white, thicker)
    ax.plot(times, rsi, color="#ffffff", linewidth=1.55, alpha=1.0, label="RSI", zorder=3, solid_capstyle="round")

    # Divergence lines + Bull/Bear labels (last few for clarity)
    divs = [d for d in (divergences or []) if isinstance(d, RsiDivergence)]
    for d in divs[-5:]:
        if d.idx_a < 0 or d.idx_b >= len(times) or d.idx_a >= len(times):
            continue
        color = "#3fb950" if d.is_bullish else "#f85149"
        ax.plot(
            [times[d.idx_a], times[d.idx_b]],
            [d.rsi_a, d.rsi_b],
            color=color,
            linewidth=1.25,
            alpha=0.95,
            zorder=4,
        )
        ax.scatter(
            [times[d.idx_a], times[d.idx_b]],
            [d.rsi_a, d.rsi_b],
            color=color,
            s=14,
            zorder=5,
        )
        va = "bottom" if d.is_bullish else "top"
        y_off = 3.5 if d.is_bullish else -3.5
        ax.text(
            times[d.idx_b],
            min(97, max(3, d.rsi_b + y_off)),
            d.label,
            color=color,
            fontsize=6.2,
            fontweight="bold",
            ha="center",
            va=va,
            zorder=6,
            bbox=dict(
                boxstyle="round,pad=0.15",
                facecolor="#0d1117",
                edgecolor=color,
                alpha=0.88,
                linewidth=0.7,
            ),
        )

    ax.set_ylim(0, 100)
    ax.set_yticks([0, 30, 50, 70, 100])
    last = float(rsi[-1])
    ax.text(
        0.01,
        0.96,
        f"RSI 14  {last:.2f}".replace(".", ","),
        transform=ax.transAxes,
        color="#e6edf3",
        fontsize=7.2,
        fontweight="bold",
        va="top",
        ha="left",
        zorder=7,
    )
    ax.tick_params(colors=CHART_TEXT, labelsize=6)
    ax.set_facecolor(CHART_BG)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
        spine.set_linewidth(0.6)


def draw_htf_inset(
    fig: plt.Figure,
    htf_bars: list[KlineBar] | None,
    ta: TAAnalysisResult,
    *,
    interval_label: str = "2h",
) -> None:
    if not htf_bars or len(htf_bars) < 8:
        return
    axins = fig.add_axes([0.54, 0.72, 0.24, 0.18])
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


def draw_rsi_divergence_on_price(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    """Connecting lines on price between divergence pivots (TV-style)."""
    from .rsi_divergence import RsiDivergence

    divs = getattr(ta, "rsi_divergences", None) or []
    if not bars or not divs:
        return
    times = _bar_times(bars)
    for d in divs[-4:]:
        if not isinstance(d, RsiDivergence):
            continue
        if d.idx_a < 0 or d.idx_b >= len(times):
            continue
        color = "#3fb950" if d.is_bullish else "#f85149"
        ax.plot(
            [times[d.idx_a], times[d.idx_b]],
            [d.price_a, d.price_b],
            color=color,
            linewidth=1.0,
            linestyle="--",
            alpha=0.75,
            zorder=3,
        )
        ax.scatter(
            [times[d.idx_a], times[d.idx_b]],
            [d.price_a, d.price_b],
            color=color,
            s=18,
            zorder=4,
            marker="o",
            edgecolors="white",
            linewidths=0.4,
        )


def draw_pro_chart_layers(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> str:
    """
    Рисует PRO-слои (пути, sweep, liq) на основном графике.
    Возвращает kind основного пути: bounce_short | flat_breakout | default
    """
    draw_swing_liquidity_marks(ax, bars, ta)
    draw_sweep_circles(ax, bars, ta)
    if draw_trend_dump_path(ax, bars, ta):
        return "trend_dump"
    if draw_trend_dump_risk_path(ax, bars, ta):
        return "trend_dump_risk"
    if draw_bounce_short_path(ax, bars, ta):
        return "bounce_short"
    if draw_flat_breakout_path(ax, bars, ta):
        return "flat_breakout"
    return "default"
