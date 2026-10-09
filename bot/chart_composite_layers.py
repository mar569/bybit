"""Композитная разметка PNG: паттерны + SMC + уровни + канал + свечи (как в PDF-материалах)."""
from __future__ import annotations

from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_display_policy import chart_teaching_tags_enabled
from .chart_ed_story import _visible_x_span
from .chart_level_labels import level_tag
from .ta_analysis import TAAnalysisResult, fmt_price

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

# Показ на графике ниже порога «входа» (detect часто 0.55–0.65)
COMPOSITE_PATTERN_MIN_CONF = 0.50


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


def draw_ta_levels_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    if not bars:
        return 0
    x0, x1 = _visible_x_span(ax, bars)
    show = chart_teaching_tags_enabled()
    drawn = 0
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    for lv in list(getattr(ta, "levels", None) or [])[:4]:
        p = float(getattr(lv, "price", 0) or 0)
        if p <= 0:
            continue
        if cur > 0 and abs(p - cur) / cur > 0.14:
            continue
        kind = str(getattr(lv, "kind", "") or "support")
        color = "#58a6ff" if kind == "support" else "#d29922"
        ax.hlines(p, x0, x1, colors=color, linewidth=0.95, alpha=0.72, linestyles="-", zorder=3)
        if show:
            tag = "поддержка" if kind == "support" else "сопротивление"
            ax.text(x0, p, f"  {tag} {fmt_price(p)}  ", color=color, fontsize=6.5, va="center", zorder=4)
        drawn += 1
    for p, tag_ru in (
        (float(getattr(ta, "nearest_support", 0) or 0), "ближ. S"),
        (float(getattr(ta, "nearest_resistance", 0) or 0), "ближ. R"),
    ):
        if p <= 0 or cur > 0 and abs(p - cur) / cur > 0.12:
            continue
        ax.hlines(p, x0, x1, colors="#8b949e", linewidth=0.8, alpha=0.55, linestyles=":", zorder=2)
        if show:
            ax.text(x1, p, f"  {tag_ru} {fmt_price(p)}  ", color="#8b949e", fontsize=6.2, ha="right", va="center", zorder=4)
        drawn += 1
    return drawn


def draw_channel_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    ch = getattr(ta, "channel", None)
    if ch is None or not bars:
        return 0
    color = "#a371f7"
    upper = _extend_channel_line(
        bars, ch.upper_start_idx, ch.upper_start_price, ch.upper_end_idx, ch.upper_end_price,
    )
    lower = _extend_channel_line(
        bars, ch.lower_start_idx, ch.lower_start_price, ch.lower_end_idx, ch.lower_end_price,
    )
    ax.plot([upper[0], upper[2]], [upper[1], upper[3]], color=color, linewidth=1.35, alpha=0.88, zorder=4)
    ax.plot([lower[0], lower[2]], [lower[1], lower[3]], color=color, linewidth=1.35, alpha=0.88, zorder=4)
    if chart_teaching_tags_enabled():
        ax.text(
            mdates.date2num(upper[2]), upper[3], f" {getattr(ch, 'label', 'канал')}",
            color=color, fontsize=6.8, va="bottom", zorder=5,
        )
    return 2


def draw_fib_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    if not getattr(ta, "reading_accept_fib", True):
        return 0
    levels = getattr(ta, "fib_levels", None) or []
    if not levels or not bars:
        return 0
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    drawn = 0
    for fl in levels:
        p = float(getattr(fl, "price", 0) or 0)
        ratio = float(getattr(fl, "ratio", 0) or 0)
        if p <= 0 or cur > 0 and abs(p - cur) / cur > 0.12:
            continue
        is_key = ratio in {0.5, 0.618}
        color = "#e3b341" if is_key else "#6e7681"
        ax.axhline(p, color=color, linestyle="-." if is_key else ":", linewidth=1.0 if is_key else 0.6, alpha=0.82 if is_key else 0.45, zorder=3)
        if is_key and chart_teaching_tags_enabled():
            ratio_lbl = "0.5" if abs(ratio - 0.5) < 1e-9 else "0.618"
            ax.text(x1, p, f" Fib {ratio_lbl} ", color=color, fontsize=6.5, va="bottom", ha="left", zorder=4)
        drawn += 1
    return drawn


def draw_candle_patterns_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    pats = list(getattr(ta, "patterns", None) or [])
    if not pats or not bars:
        return 0
    drawn = 0
    for pat in pats[-4:]:
        idx = int(getattr(pat, "index", -1))
        if idx < 0 or idx >= len(bars):
            continue
        visible_tail = min(len(bars) - 1, max(48, int(len(bars) * 0.72)))
        if idx < len(bars) - visible_tail:
            continue
        bar = bars[idx]
        ts = mdates.date2num(_idx_to_date(bars, idx))
        bullish = getattr(pat, "bullish", None)
        y = float(bar.high) * 1.0012 if bullish is not False else float(bar.low) * 0.9988
        marker = "^" if bullish else "v" if bullish is False else "o"
        col = "#3fb950" if bullish else "#f85149" if bullish is False else "#ffd33d"
        ax.plot(ts, y, marker=marker, color=col, markersize=7, linestyle="None", zorder=7)
        if chart_teaching_tags_enabled():
            name = str(getattr(pat, "name", "") or getattr(pat, "label", "") or "свеча")[:18]
            ax.text(ts, y, f" {name}", color=col, fontsize=6, va="bottom" if bullish is not False else "top", zorder=8)
        drawn += 1
    return drawn


def draw_pdf_zones_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    metrics = getattr(ta, "market_metrics", None) or {}
    raw_zones = metrics.get("pdf_zones") if isinstance(metrics, dict) else None
    if not raw_zones or not bars:
        return 0
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    kind_short = {
        "demand": "спрос",
        "supply": "предложение",
        "ob_bull": "OB↑",
        "ob_bear": "OB↓",
        "sr_support": "S",
        "sr_resistance": "R",
    }
    drawn = 0
    for z in raw_zones[:5]:
        if not isinstance(z, dict) or not z.get("valid", False):
            continue
        top, bot = float(z.get("top", 0)), float(z.get("bottom", 0))
        if top <= bot or top <= 0:
            continue
        if cur > 0 and abs((top + bot) / 2 - cur) / cur > 0.14:
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
                alpha=0.22,
                linewidth=0.9,
                zorder=2,
            )
        )
        if chart_teaching_tags_enabled():
            lbl = kind_short.get(kind, kind[:8])
            ax.text(x0, top, f"  {lbl}  ", color=base, fontsize=6.5, va="bottom", zorder=5)
        drawn += 1
    return drawn


def draw_patterns_composite_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    if not getattr(ta, "reading_accept_pattern", True):
        return 0
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if not primary and not patterns:
        return 0
    from .chart_pattern_draw import draw_chart_patterns

    draw_chart_patterns(
        ax,
        bars,
        patterns,
        max_patterns=2,
        min_confidence=COMPOSITE_PATTERN_MIN_CONF,
        force_primary=primary,
        draw_target_labels=bool(primary),
    )
    return 2 if primary or patterns else 0


def draw_composite_read_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    """Слои TA поверх свечей (без дубля post_pump / setup hint — их рисует chart_pdf_style)."""
    if not bars:
        return 0
    drawn = 0
    from .chart_market_read import draw_swing_structure_mpl

    from .chart_pdf_style import _draw_range_pdf, draw_smc_pdf_clean  # lazy: после init chart_pdf_style

    drawn += draw_swing_structure_mpl(ax, bars, ta)
    drawn += draw_ta_levels_mpl(ax, bars, ta)
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > brdn > 0 or getattr(ta, "consolidation", None) is not None:
        drawn += _draw_range_pdf(ax, bars, ta)
    drawn += draw_channel_mpl(ax, bars, ta)
    drawn += draw_patterns_composite_mpl(ax, bars, ta)
    drawn += draw_smc_pdf_clean(ax, bars, ta, composite=True)
    drawn += draw_pdf_zones_mpl(ax, bars, ta)
    drawn += draw_fib_mpl(ax, bars, ta)
    drawn += draw_candle_patterns_mpl(ax, bars, ta)
    try:
        from .chart_breakout_markers import draw_breakout_retest_markers

        draw_breakout_retest_markers(ax, bars, ta, max_markers=3)
        drawn += 1
    except Exception:
        pass
    htf = getattr(ta, "primary_htf_chart_pattern", None)
    if htf and getattr(ta, "reading_accept_htf_pattern", True):
        try:
            from .chart_pattern_draw import draw_htf_pattern_levels

            draw_htf_pattern_levels(
                ax, bars, htf, conflict=bool(getattr(ta, "pattern_foresight_htf_conflict", False)), quiet=True,
            )
            drawn += 1
        except Exception:
            pass
    return max(drawn, 1)
