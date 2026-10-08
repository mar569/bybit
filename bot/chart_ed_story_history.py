"""История: поиск похожего sweep у сопр. и проекция ghost-баров (как Bar Pattern в TV)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar


@dataclass(frozen=True)
class SweepTemplate:
    """Нормализованные свечи: цены относительно якоря (close первой свечи = 0)."""
    ohlc: list[tuple[float, float, float, float]]
    peak_idx: int
    source_start: int


def _bar_ts(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def find_liquidity_sweep_template(
    bars: list[KlineBar],
    *,
    resistance: float,
    tolerance_pct: float = 0.018,
) -> SweepTemplate | None:
    """Ищем на истории фрагмент: вынос к/выше сопр. → откат вниз."""
    n = len(bars)
    if n < 48 or resistance <= 0:
        return None
    tol = max(tolerance_pct, 0.006)
    search_end = max(20, n - 8)
    best: tuple[float, int, int] | None = None  # score, start, end

    for start in range(8, search_end - 6):
        for end in range(start + 5, min(start + 16, search_end)):
            window = bars[start:end]
            peak_i = max(range(len(window)), key=lambda i: float(window[i].high))
            peak_hi = float(window[peak_i].high)
            if peak_hi < resistance * (1.0 - tol):
                continue
            if peak_hi > resistance * (1.0 + tol * 2.5):
                continue
            if peak_i >= len(window) - 2:
                continue
            end_close = float(window[-1].close)
            drop = (peak_hi - end_close) / max(peak_hi, 1e-12)
            if drop < 0.01:
                continue
            wick = peak_hi - max(float(window[peak_i].open), float(window[peak_i].close))
            body_rng = max(float(b.high) - float(b.low) for b in window) or 1e-9
            wick_score = wick / body_rng if body_rng else 0.0
            touch = 1.0 if peak_hi >= resistance * 0.998 else 0.55
            score = drop * 40.0 + wick_score * 8.0 + touch
            if best is None or score > best[0]:
                best = (score, start, end)

    if best is None:
        return None
    _, start, end = best
    window = bars[start:end]
    anchor = float(window[0].close)
    ohlc: list[tuple[float, float, float, float]] = []
    for b in window:
        ohlc.append(
            (
                float(b.open) - anchor,
                float(b.high) - anchor,
                float(b.low) - anchor,
                float(b.close) - anchor,
            )
        )
    peak_i = max(range(len(ohlc)), key=lambda i: ohlc[i][1])
    return SweepTemplate(ohlc=ohlc, peak_idx=peak_i, source_start=start)


def synthetic_sweep_template(
    *,
    resistance: float,
    floor: float,
    tp: float,
) -> SweepTemplate:
    """Если в истории нет аналога — типовой sweep→dump как на нефти."""
    width = max(resistance - floor, resistance * 0.02)
    leg = max(resistance - tp, width * 0.6)
    anchor = resistance - width * 0.35
    rel: list[tuple[float, float, float, float]] = []
    # поджатие
    for i, frac in enumerate((0.0, 0.08, 0.14, 0.20)):
        c = anchor + width * frac
        rel.append((c - width * 0.01, c + width * 0.02, c - width * 0.015, c + width * 0.012))
    # вынос в зону
    sweep_o = anchor + width * 0.22
    sweep_h = resistance + width * 0.06
    sweep_c = resistance - width * 0.01
    rel.append((sweep_o, sweep_h, sweep_o - width * 0.02, sweep_c))
    # импульс вниз
    steps = 6
    for j in range(1, steps + 1):
        t = j / steps
        c = sweep_c - leg * t
        h = c + leg * 0.08 * (1.0 - t)
        l = c - leg * 0.05
        o = c + leg * 0.04 * (1.0 - t)
        rel.append((o, h, l, c))
    peak_i = 4
    return SweepTemplate(ohlc=rel, peak_idx=peak_i, source_start=-1)


def resolve_sweep_template(
    bars: list[KlineBar],
    *,
    resistance: float,
    floor: float,
    tp: float,
) -> SweepTemplate:
    found = find_liquidity_sweep_template(bars, resistance=resistance)
    if found is not None:
        return found
    return synthetic_sweep_template(resistance=resistance, floor=floor, tp=tp)


def draw_ghost_bars_forward(
    ax: plt.Axes,
    bars: list[KlineBar],
    template: SweepTemplate,
    *,
    interval_minutes: int,
    resistance: float,
    tp: float,
) -> None:
    """Полупрозрачные «будущие» свечи от последнего бара."""
    if not bars or not template.ohlc:
        return
    peak_rel = template.ohlc[template.peak_idx][1]
    end_rel = template.ohlc[-1][3]
    if peak_rel <= 1e-12:
        return
    target_peak = resistance
    target_end = max(tp, resistance * 0.85)
    scale = (target_peak - target_end) / max(peak_rel - end_rel, 1e-12)
    anchor_price = float(bars[-1].close)

    width_min = max(interval_minutes * 0.88, 2.5)
    width_days = width_min / (24 * 60)
    x_start = mdates.date2num(_bar_ts(bars, len(bars) - 1)) + width_days * 0.55

    up = "#8b949e"
    down = "#6e7681"
    for i, (o, h, l, c) in enumerate(template.ohlc):
        x = x_start + i * width_days
        o_abs = anchor_price + o * scale
        h_abs = anchor_price + h * scale
        l_abs = anchor_price + l * scale
        c_abs = anchor_price + c * scale
        color = up if c_abs >= o_abs else down
        ax.plot([x, x], [l_abs, h_abs], color=color, linewidth=1.0, alpha=0.42, zorder=7)
        body_lo = min(o_abs, c_abs)
        body_hi = max(o_abs, c_abs)
        hgt = max(body_hi - body_lo, abs(h_abs - l_abs) * 0.12, anchor_price * 0.0004)
        ax.add_patch(
            Rectangle(
                (x - width_days / 2, body_lo),
                width_days,
                hgt,
                facecolor=color,
                edgecolor=color,
                alpha=0.38,
                linewidth=0.4,
                zorder=7,
            )
        )

    if template.source_start >= 0:
        xs = mdates.date2num(_bar_ts(bars, template.source_start))
        ax.axvline(xs, color="#484f58", linestyle=":", linewidth=0.9, alpha=0.55, zorder=3)
        ax.text(
            xs,
            0.92,
            "аналог",
            transform=ax.get_xaxis_transform(),
            color="#8b949e",
            fontsize=6.2,
            va="top",
            ha="center",
            rotation=90,
            zorder=8,
        )
