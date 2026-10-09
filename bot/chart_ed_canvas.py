"""Единый PNG /ta: один пайплайн, уровни через LabelBoard (без «пачки» линий)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_analysis_text import structure_break_label_ru
from .chart_display_policy import chart_teaching_tags_enabled, chart_pdf_setup_hint_enabled
from .chart_label_layout import LabelBoard, draw_label_board, format_level_text
from .chart_market_read import recent_swings
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

CANVAS_MAX_LEVELS = 5
PATTERN_MIN = 0.48


def _idx_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x(bars: list[KlineBar], idx: int) -> float:
    return mdates.date2num(_idx_date(bars, idx))


def _visible_x(bars: list[KlineBar], ax: plt.Axes) -> tuple[float, float]:
    x0, x1 = ax.get_xlim()
    if x1 > x0:
        return float(x0), float(x1)
    i0 = max(0, len(bars) - min(len(bars), 96))
    return _x(bars, i0), _x(bars, len(bars) - 1)


def _seed_level_board(board: LabelBoard, ta: TAAnalysisResult, bars: list[KlineBar]) -> None:
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    board.reserve(cur)

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > 0:
        board.add(brk, format_level_text("R", brk), "#d29922", priority=92, ref=cur)
    if brdn > 0:
        board.add(brdn, format_level_text("S", brdn), "#58a6ff", priority=92, ref=cur)

    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        lv = float(smc.structure_break_level)
        tag = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"), short=True)
        board.add(lv, format_level_text(tag, lv), "#f0c040", priority=88, ref=cur)

    nr = float(getattr(ta, "nearest_resistance", 0) or 0)
    ns = float(getattr(ta, "nearest_support", 0) or 0)
    if nr > 0 and cur > 0 and abs(nr - cur) / cur <= 0.12:
        board.add(nr, format_level_text("R ближ.", nr), "#8b949e", priority=72, ref=cur)
    if ns > 0 and cur > 0 and abs(ns - cur) / cur <= 0.12:
        board.add(ns, format_level_text("S ближ.", ns), "#8b949e", priority=72, ref=cur)

    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot:
            if brk <= 0 or abs(top - brk) / cur > 0.002:
                board.add(top, format_level_text("боковик ↑", top), "#6e7681", priority=70, ref=cur)
            if brdn <= 0 or abs(bot - brdn) / cur > 0.002:
                board.add(bot, format_level_text("боковик ↓", bot), "#6e7681", priority=70, ref=cur)

    if getattr(ta, "reading_accept_fib", True):
        for fl in getattr(ta, "fib_levels", None) or []:
            ratio = float(getattr(fl, "ratio", 0) or 0)
            if ratio not in {0.5, 0.618}:
                continue
            p = float(getattr(fl, "price", 0) or 0)
            if p <= 0 or cur > 0 and abs(p - cur) / cur > 0.1:
                continue
            board.add(p, f"Fib {ratio:g}", "#e3b341", priority=64, ref=cur, draw_line=True)
            break

    for m in recent_swings(bars, max_highs=1, max_lows=1):
        if cur > 0 and abs(m.price - cur) / cur > 0.11:
            continue
        if any(abs(m.price - p) / cur < 0.004 for p in board.reserved if cur > 0):
            continue
        if m.kind == "high":
            board.add(m.price, format_level_text("H", m.price), "#f85149", priority=58, ref=cur)
        else:
            board.add(m.price, format_level_text("L", m.price), "#3fb950", priority=58, ref=cur)


def _draw_range_box(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    cons = getattr(ta, "consolidation", None)
    if cons is None:
        return
    top, bot = float(cons.top), float(cons.bottom)
    if top <= bot:
        return
    x0, x1 = _visible_x(bars, ax)
    i0 = max(0, int(getattr(cons, "start_idx", len(bars) - 40)))
    x0 = max(x0, _x(bars, i0))
    ax.add_patch(
        Rectangle((x0, bot), max(x1 - x0, 0.001), top - bot, facecolor="#8b949e", alpha=0.06, edgecolor="#484f58", linewidth=0.6, linestyle="--", zorder=1)
    )


def _draw_scenario_path(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    try:
        from .chart_readable import draw_probable_path

        draw_probable_path(ax, bars, ta)
    except Exception:
        logger.debug("scenario path skipped", exc_info=True)


def _draw_trend_lines(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    lines = list(getattr(ta, "trend_lines", None) or [])[:2]
    if not lines and getattr(ta, "channel", None) is not None:
        from .chart_composite_layers import draw_channel_mpl

        draw_channel_mpl(ax, bars, ta)
        return
    last = len(bars) - 1
    for tl in lines:
        color = "#3fb950" if getattr(tl, "kind", "") == "bull" else "#f85149"
        s_i, e_i = int(tl.start_idx), int(tl.end_idx)
        if e_i == s_i:
            e_i = s_i + 1
        slope = (float(tl.end_price) - float(tl.start_price)) / (e_i - s_i)
        ext = float(tl.start_price) + slope * (last - s_i)
        ax.plot([_idx_date(bars, s_i), _idx_date(bars, last)], [tl.start_price, ext], color=color, linewidth=1.35, alpha=0.9, zorder=4)
        if chart_teaching_tags_enabled():
            lbl = str(getattr(tl, "label", "") or ("тренд ↑" if color == "#3fb950" else "тренд ↓"))
            ax.text(_x(bars, last), ext, f" {lbl}", color=color, fontsize=6.8, va="bottom", zorder=5)


def _draw_pattern(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not getattr(ta, "reading_accept_pattern", True):
        return
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if not primary and not patterns:
        return
    from .chart_pattern_draw import draw_chart_patterns

    draw_chart_patterns(
        ax,
        bars,
        patterns,
        max_patterns=1,
        min_confidence=PATTERN_MIN,
        force_primary=primary,
        draw_target_labels=False,
    )


def _draw_order_blocks(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None:
        return
    cur = float(bars[-1].close)
    shown = 0
    for ob in reversed(list(getattr(smc, "order_blocks", None) or [])):
        if shown >= 2:
            break
        try:
            top, bot = float(ob.top), float(ob.bottom)
        except (TypeError, ValueError, AttributeError):
            continue
        if top <= bot:
            continue
        mid = (top + bot) / 2
        if cur > 0 and abs(mid - cur) / cur > 0.14:
            continue
        idx = int(getattr(ob, "start_idx", len(bars) - 6))
        brk = int(getattr(ob, "break_idx", idx + 3))
        idx = max(0, min(idx, len(bars) - 1))
        brk = max(idx, min(brk, len(bars) - 1))
        col = "#3fb950" if getattr(ob, "direction", "") == "bullish" else "#f85149"
        ax.add_patch(
            Rectangle((_x(bars, idx), bot), max(_x(bars, brk) - _x(bars, idx), 0.001), top - bot, facecolor=col, alpha=0.2, edgecolor=col, linewidth=1.0, zorder=3)
        )
        shown += 1


def _draw_smc_pins(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None:
        return
    from .chart_event_pins import draw_bos_pin_mpl, draw_sweep_pin_mpl

    markers = list(getattr(smc, "markers", None) or [])
    sweeps = [m for m in markers if getattr(m, "kind", "") == "sweep"]
    bos = [m for m in markers if getattr(m, "kind", "") in {"bos", "mss"}]
    if sweeps:
        best = max(sweeps, key=lambda m: m.index)
        if best.index >= len(bars) - 96:
            draw_sweep_pin_mpl(ax, bars, best, draw_level_line=False)
    if bos:
        best = max(bos, key=lambda m: m.index)
        if best.index >= len(bars) - 120:
            brk = float(getattr(smc, "structure_break_level", 0) or 0)
            tag = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"), short=True)
            draw_bos_pin_mpl(ax, bars, best, break_level=brk or None, kind_label=tag, draw_level_line=False)


def _draw_setup_hint(ax: plt.Axes, ta: TAAnalysisResult, bars: list[KlineBar]) -> None:
    if not chart_pdf_setup_hint_enabled():
        return
    from .chart_pdf_style import _pdf_setup_hint

    hint = _pdf_setup_hint(ta, bars)
    if not hint:
        return
    ax.text(
        0.5,
        0.98,
        hint[:140],
        transform=ax.transAxes,
        ha="center",
        va="top",
        color="#e6edf3",
        fontsize=7.2,
        zorder=15,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.94),
    )


def _draw_canvas_footer(ax: plt.Axes) -> None:
    import os

    if os.environ.get("ED_CHART_CANVAS_TAG", "1").strip().lower() not in {"1", "true", "yes"}:
        return
    ax.text(
        0.012,
        0.02,
        "Ed canvas",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        color="#484f58",
        fontsize=5.5,
        zorder=3,
        alpha=0.85,
    )


def draw_ed_analysis_canvas_mpl(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    interval_minutes: int = 15,
) -> LabelBoard:
    """Единственная разметка для manual/signal PRO."""
    board = LabelBoard()
    if not bars:
        return board
    from .chart_rbr_refresh import refresh_rbr_for_chart
    from .chart_pdf_style import sanitize_rbr_for_pdf

    from .chart_story_router import enrich_ta_for_chart_story
    from .core.chart_read import get_or_build_chart_read
    from .core.chart_read_draw import apply_chart_read_to_canvas, seed_board_from_read

    ta = enrich_ta_for_chart_story(
        sanitize_rbr_for_pdf(refresh_rbr_for_chart(ta, bars), bars),
        bars,
    )
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    read = get_or_build_chart_read(ta, bars, symbol=symbol or getattr(ta, "symbol", "") or "")

    if read.levels:
        seed_board_from_read(board, read, ref=cur)
    else:
        _seed_level_board(board, ta, bars)
    _draw_range_box(ax, bars, ta)
    _draw_trend_lines(ax, bars, ta)
    _draw_pattern(ax, bars, ta)
    _draw_order_blocks(ax, bars, ta)
    _draw_smc_pins(ax, bars, ta)
    draw_label_board(ax, bars, board, current=cur, max_labels=CANVAS_MAX_LEVELS)
    apply_chart_read_to_canvas(ax, bars, ta, read, board)
    _draw_canvas_footer(ax)
    return board
