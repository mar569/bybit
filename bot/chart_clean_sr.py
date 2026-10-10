"""PNG сигналов: S/R 2px + находки TA (паттерн, SMC, range) без каши."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

SUPPORT_LINE = "#58a6ff"
RESIST_LINE = "#f85149"
LINE_W = 2.0
MAX_PER_SIDE = 2
PATTERN_MIN = 0.52


@dataclass(frozen=True)
class _SrLevel:
    price: float
    label: str
    side: str
    priority: int


def _x(bars: list[KlineBar], idx: int) -> float:
    idx = max(0, min(idx, len(bars) - 1))
    dt = datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)
    return mdates.date2num(dt)


def _visible_x(bars: list[KlineBar]) -> tuple[float, float]:
    i0 = max(0, len(bars) - min(len(bars), 96))
    return _x(bars, i0), _x(bars, len(bars) - 1)


def _collect_levels(bars: list[KlineBar], ta: TAAnalysisResult) -> list[_SrLevel]:
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    if cur <= 0:
        return []
    out: list[_SrLevel] = []

    def add(price: float | None, label: str, side: str, priority: int) -> None:
        if price is None or float(price) <= 0:
            return
        p = float(price)
        if abs(p - cur) / cur > 0.12:
            return
        out.append(_SrLevel(p, label.strip(), side, priority))

    add(getattr(ta, "breakdown_level", None), "поддержка", "support", 100)
    add(getattr(ta, "breakout_level", None), "сопротивление", "resistance", 100)
    add(getattr(ta, "nearest_support", None), "S", "support", 85)
    add(getattr(ta, "nearest_resistance", None), "R", "resistance", 85)

    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot and (top - bot) / cur <= 0.08:
            add(bot, "низ range", "support", 78)
            add(top, "верх range", "resistance", 78)

    tol = cur * 0.004
    merged: list[_SrLevel] = []
    for lv in sorted(out, key=lambda x: (-x.priority, x.price)):
        if any(abs(lv.price - m.price) <= tol for m in merged):
            continue
        merged.append(lv)

    supports = sorted([l for l in merged if l.side == "support"], key=lambda x: -x.priority)[:MAX_PER_SIDE]
    resists = sorted([l for l in merged if l.side == "resistance"], key=lambda x: -x.priority)[:MAX_PER_SIDE]
    return supports + resists


def _label(ax: plt.Axes, x1: float, price: float, text: str, color: str, *, cur: float) -> None:
    ax.text(
        x1,
        price,
        f" {text} {fmt_price(price)}",
        color=color,
        fontsize=7,
        fontweight="bold",
        va="bottom" if price >= cur else "top",
        ha="right",
        zorder=4,
        bbox=dict(
            boxstyle="round,pad=0.12",
            facecolor="#0d1117",
            edgecolor=color,
            alpha=0.88,
            linewidth=0.4,
        ),
    )


def _draw_sr_lines(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    x0, x1 = _visible_x(bars)
    for lv in _collect_levels(bars, ta):
        color = SUPPORT_LINE if lv.side == "support" else RESIST_LINE
        ax.hlines(lv.price, x0, x1, colors=color, linewidth=LINE_W, alpha=0.95, zorder=3)
        _label(ax, x1, lv.price, lv.label, color, cur=cur)


def _draw_range_outline(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    cons = getattr(ta, "consolidation", None)
    if cons is None:
        return
    top, bot = float(cons.top), float(cons.bottom)
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    if top <= bot or cur <= 0 or (top - bot) / cur > 0.12:
        return
    x0, x1 = _visible_x(bars)
    ax.add_patch(
        Rectangle(
            (x0, bot),
            max(x1 - x0, 0.001),
            top - bot,
            facecolor="none",
            edgecolor="#6e7681",
            linewidth=1.2,
            linestyle=(0, (4, 3)),
            zorder=2,
        )
    )
    ax.text(
        x0 + (x1 - x0) * 0.02,
        top,
        f"  боковик {fmt_price(bot)}–{fmt_price(top)}  ",
        color="#8b949e",
        fontsize=6.8,
        fontweight="bold",
        va="bottom",
        ha="left",
        zorder=4,
    )


def _draw_chart_patterns(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not getattr(ta, "reading_accept_pattern", True):
        return
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if not primary and not patterns:
        return
    from .chart_display_policy import signal_chart_max_patterns

    max_pat = signal_chart_max_patterns()
    try:
        from .chart_pattern_draw import draw_chart_patterns, draw_htf_pattern_levels

        draw_chart_patterns(
            ax,
            bars,
            patterns,
            max_patterns=max_pat,
            min_confidence=PATTERN_MIN,
            force_primary=primary,
            draw_target_labels=False,
        )
        htf = getattr(ta, "primary_htf_chart_pattern", None)
        if (
            htf
            and getattr(ta, "reading_accept_htf_pattern", True)
            and float(getattr(htf, "confidence", 0) or 0) >= 0.58
        ):
            draw_htf_pattern_levels(
                ax,
                bars,
                htf,
                conflict=bool(getattr(ta, "pattern_foresight_htf_conflict", False)),
                quiet=True,
            )
    except Exception:
        logger.debug("pattern draw skipped", exc_info=True)
        return

    labels: list[str] = []
    if primary and float(getattr(primary, "confidence", 0) or 0) >= PATTERN_MIN:
        labels.append(str(getattr(primary, "label_ru", "") or getattr(primary, "kind", "") or "паттерн"))
    seen: set[str] = set()
    for p in sorted(patterns, key=lambda x: -float(getattr(x, "confidence", 0) or 0)):
        if len(labels) >= max_pat:
            break
        if float(getattr(p, "confidence", 0) or 0) < PATTERN_MIN:
            continue
        name = str(getattr(p, "label_ru", "") or getattr(p, "kind", "") or "")
        if not name or name in seen:
            continue
        if primary and getattr(p, "kind", None) == getattr(primary, "kind", None):
            continue
        seen.add(name)
        labels.append(name)
    if labels:
        ax.text(
            0.02,
            0.96,
            " · ".join(l[:28] for l in labels[:max_pat]),
            transform=ax.transAxes,
            color="#c9d1d9",
            fontsize=7.2,
            fontweight="bold",
            va="top",
            ha="left",
            zorder=8,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#161b22", edgecolor="#484f58", alpha=0.92),
        )


def _draw_smc_findings(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if getattr(ta, "smc", None) is None:
        return
    try:
        from .chart_pdf_style import draw_smc_pdf_clean

        draw_smc_pdf_clean(ax, bars, ta, composite=False, clean=True, evidence=True)
    except Exception:
        logger.debug("smc findings skipped", exc_info=True)


def _draw_candle_findings(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    cps = list(getattr(ta, "patterns", None) or [])
    if not cps:
        return
    try:
        from .chart_composite_layers import draw_candle_patterns_mpl

        draw_candle_patterns_mpl(ax, bars, ta)
    except Exception:
        logger.debug("candle patterns skipped", exc_info=True)


def _draw_trend_line(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    lines = list(getattr(ta, "trend_lines", None) or [])[:1]
    if not lines:
        return
    last = len(bars) - 1
    tl = lines[0]
    color = "#3fb950" if getattr(tl, "kind", "") == "bull" else "#f85149"
    s_i, e_i = int(tl.start_idx), int(tl.end_idx)
    if e_i == s_i:
        e_i = s_i + 1
    slope = (float(tl.end_price) - float(tl.start_price)) / (e_i - s_i)
    ext = float(tl.start_price) + slope * (last - s_i)
    ax.plot(
        [_x(bars, s_i), _x(bars, last)],
        [tl.start_price, ext],
        color=color,
        linewidth=1.35,
        alpha=0.85,
        zorder=4,
    )


def draw_clean_sr_chart(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
) -> None:
    """S/R + паттерн, боковик, SMC, свечи — что подтвердил TA."""
    if not bars:
        return
    try:
        from .chart_pdf_style import sanitize_rbr_for_pdf

        ta = sanitize_rbr_for_pdf(ta, bars)
    except Exception:
        pass

    _draw_sr_lines(ax, bars, ta)
    _draw_range_outline(ax, bars, ta)
    _draw_chart_patterns(ax, bars, ta)
    _draw_smc_findings(ax, bars, ta)
    _draw_candle_findings(ax, bars, ta)
    _draw_trend_line(ax, bars, ta)
