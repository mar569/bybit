"""PNG /ta: один алгоритм — рисуем только то, что TA реально нашла (без дублей слоёв)."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .chart_analysis_text import structure_break_label_ru
from .chart_display_policy import chart_teaching_tags_enabled
from .chart_label_layout import LabelBoard, format_level_text
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

EVIDENCE_PATTERN_MIN = 0.45
_LEVEL_DEDUPE_FRAC = 0.0038


def _near(a: float, b: float, *, ref: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    r = ref if ref > 0 else max(a, b)
    return abs(a - b) / r <= _LEVEL_DEDUPE_FRAC


def seed_evidence_level_board(board: LabelBoard, ta: TAAnalysisResult, bars: list[KlineBar]) -> None:
    """Правая шкала: только ключевые уровни сценария (без «ближ. S/R» и swing L1)."""
    if not bars:
        return
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
        if not (_near(lv, brk, ref=cur) or _near(lv, brdn, ref=cur)):
            tag = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"), short=True)
            board.add(lv, format_level_text(tag, lv), "#f0c040", priority=88, ref=cur)

    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot:
            if brk <= 0 or not _near(top, brk, ref=cur):
                board.add(top, format_level_text("боковик ↑", top), "#6e7681", priority=70, ref=cur)
            if brdn <= 0 or not _near(bot, brdn, ref=cur):
                board.add(bot, format_level_text("боковик ↓", bot), "#6e7681", priority=70, ref=cur)

    primary = getattr(ta, "primary_chart_pattern", None)
    if primary and float(getattr(primary, "confidence", 0) or 0) >= EVIDENCE_PATTERN_MIN:
        tgt = float(getattr(primary, "target_price", 0) or 0)
        if tgt > 0 and cur > 0 and abs(tgt - cur) / cur <= 0.2:
            if not any(_near(tgt, p, ref=cur) for p in board.reserved):
                board.add(tgt, format_level_text("цель фиг.", tgt), "#a371f7", priority=62, ref=cur)


def _draw_trend_lines(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    from datetime import datetime, timezone

    import matplotlib.dates as mdates

    lines = list(getattr(ta, "trend_lines", None) or [])[:2]
    if not lines:
        return
    last = len(bars) - 1

    def _dt(idx: int) -> datetime:
        idx = max(0, min(idx, len(bars) - 1))
        return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)

    for tl in lines:
        color = "#3fb950" if getattr(tl, "kind", "") == "bull" else "#f85149"
        s_i, e_i = int(tl.start_idx), int(tl.end_idx)
        if e_i == s_i:
            e_i = s_i + 1
        slope = (float(tl.end_price) - float(tl.start_price)) / (e_i - s_i)
        ext = float(tl.start_price) + slope * (last - s_i)
        ax.plot(
            [_dt(s_i), _dt(last)],
            [tl.start_price, ext],
            color=color,
            linewidth=1.35,
            alpha=0.88,
            zorder=4,
        )
        if chart_teaching_tags_enabled():
            lbl = str(getattr(tl, "label", "") or ("тренд ↑" if color == "#3fb950" else "тренд ↓"))
            ax.text(
                mdates.date2num(_dt(last)),
                ext,
                f" {lbl[:20]}",
                color=color,
                fontsize=6.8,
                va="bottom",
                zorder=5,
            )


def _patterns_visible(ta: TAAnalysisResult) -> bool:
    if not getattr(ta, "reading_accept_pattern", True):
        return False
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if primary and float(getattr(primary, "confidence", 0) or 0) >= EVIDENCE_PATTERN_MIN:
        return True
    return any(float(getattr(p, "confidence", 0) or 0) >= EVIDENCE_PATTERN_MIN for p in patterns)


def _elliott_visible(ta: TAAnalysisResult) -> bool:
    conf = int(getattr(ta, "elliott_confidence", 0) or 0)
    pts = list(getattr(ta, "elliott_draw_points", None) or [])
    if bool(getattr(ta, "elliott_entry_ready", False)):
        return True
    return conf >= 52 and len(pts) >= 4


def draw_evidence_analysis_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    """Слои по фактам TA: range → канал → паттерн (если есть) → тренд → SMC → Elliott (если уверенно)."""
    if not bars:
        return 0
    drawn = 0
    from .chart_pdf_style import _draw_range_pdf, draw_smc_pdf_clean

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > brdn > 0 or getattr(ta, "consolidation", None) is not None:
        drawn += _draw_range_pdf(ax, bars, ta)

    if getattr(ta, "channel", None) is not None:
        from .chart_composite_layers import draw_channel_mpl

        drawn += draw_channel_mpl(ax, bars, ta)

    if _patterns_visible(ta):
        from .chart_pattern_draw import draw_chart_patterns

        draw_chart_patterns(
            ax,
            bars,
            list(getattr(ta, "chart_patterns", None) or []),
            max_patterns=2,
            min_confidence=EVIDENCE_PATTERN_MIN,
            force_primary=getattr(ta, "primary_chart_pattern", None),
            draw_target_labels=bool(getattr(ta, "primary_chart_pattern", None)),
        )
        drawn += 1

    _draw_trend_lines(ax, bars, ta)

    if getattr(ta, "smc", None) is not None:
        drawn += draw_smc_pdf_clean(ax, bars, ta, composite=True, evidence=True)

    htf = getattr(ta, "primary_htf_chart_pattern", None)
    if (
        htf
        and getattr(ta, "reading_accept_htf_pattern", True)
        and float(getattr(htf, "confidence", 0) or 0) >= 0.62
    ):
        try:
            from .chart_pattern_draw import draw_htf_pattern_levels

            draw_htf_pattern_levels(
                ax,
                bars,
                htf,
                conflict=bool(getattr(ta, "pattern_foresight_htf_conflict", False)),
                quiet=True,
            )
            drawn += 1
        except Exception:
            logger.debug("htf pattern draw skipped", exc_info=True)

    if _elliott_visible(ta):
        try:
            from .chart_renderer import _draw_elliott_from_ta

            verdict = str(getattr(ta, "verdict", "") or "").upper()
            _draw_elliott_from_ta(ax, bars, ta, is_wait=(verdict == "WAIT"))
            drawn += 1
        except Exception:
            logger.debug("elliott evidence draw skipped", exc_info=True)

    return max(drawn, 1)
