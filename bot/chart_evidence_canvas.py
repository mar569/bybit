"""PNG /ta: один оркестратор — все слои бота, но только если TA их подтвердила.

Полный стек анализа живёт в ta_analysis, smc, elliott, market_reading, RBR, playbook…
Здесь — маппинг «есть данные + reading_accept_* → один раз нарисовать», без L1/L2 и дублей S/R.
"""
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

# Справочник: что умеет бот (анализ) ↔ откуда рисуется на PNG в Evidence-режиме.
CHART_LAYER_CATALOG: tuple[tuple[str, str], ...] = (
    ("RBR / боковик / range", "chart_pdf_style._draw_range_pdf, range_breakdown_draw"),
    ("Графические паттерны (20+ типов)", "chart_pattern_draw.draw_chart_patterns"),
    ("HTF-паттерн / foresight", "draw_htf_pattern_levels, draw_pattern_foresight_path"),
    ("Канал", "chart_composite_layers.draw_channel_mpl"),
    ("Трендовые линии", "TA.trend_lines"),
    ("SMC/ICT (BOS, sweep, OB, FVG)", "chart_pdf_style.draw_smc_pdf_clean"),
    ("Зоны demand/supply (PDF)", "chart_composite_layers.draw_pdf_zones_mpl"),
    ("Fib / wave confluence", "chart_composite_layers.draw_fib_mpl"),
    ("Свечные паттерны", "chart_composite_layers.draw_candle_patterns_mpl"),
    ("Пробой / retest на барах", "chart_breakout_markers"),
    ("Elliott (импульс, ABC, треугольник)", "chart_elliott_draw"),
    ("RSI-дивергенция на цене", "chart_pro_layers.draw_rsi_divergence_on_price"),
    ("Liq magnet / swing liq", "chart_pro_layers.draw_swing_liquidity_marks"),
    ("Buy-flat-sell зоны", "chart_pro_layers.draw_buy_flat_sell_zones"),
    ("Сессия / дневные H-L", "chart_reference_levels, chart_readable"),
    ("План IN/SL/TP", "chart_position_boxes.draw_forward_plan_boxes"),
    ("RBR-сценарий (fade/retest)", "chart_range_breakdown_draw"),
    ("Playbook ChartSpec / TV overlay", "core.playbook — отдельный путь при ED_CHART_TV"),
    ("OI/CVD/flow в тексте", "playbook, chart_read — Telegram, не линии"),
    ("Scenario dump/bounce path", "chart_pro_layers — только ED_CHART_SCENARIO_PATH=1"),
)


def _near(a: float, b: float, *, ref: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    r = ref if ref > 0 else max(a, b)
    return abs(a - b) / r <= _LEVEL_DEDUPE_FRAC


def seed_evidence_level_board(board: LabelBoard, ta: TAAnalysisResult, bars: list[KlineBar]) -> None:
    """Правая шкала: ключевые уровни (R/S/BOS/боковик/цели), без nearest и swing L1."""
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
        if tgt > 0 and cur > 0 and abs(tgt - cur) / cur <= 0.22:
            if not any(_near(tgt, p, ref=cur) for p in board.reserved):
                board.add(tgt, format_level_text("цель фиг.", tgt), "#a371f7", priority=62, ref=cur)

    inv = float(getattr(ta, "invalidation_price", 0) or 0)
    if inv > 0 and cur > 0 and abs(inv - cur) / cur <= 0.15:
        if not any(_near(inv, p, ref=cur) for p in board.reserved):
            board.add(inv, format_level_text("SL", inv), "#f85149", priority=84, ref=cur)

    for i, tp in enumerate(list(getattr(ta, "target_prices", None) or [])[:2]):
        p = float(tp or 0)
        if p <= 0 or cur > 0 and abs(p - cur) / cur > 0.22:
            continue
        if any(_near(p, x, ref=cur) for x in board.reserved):
            continue
        board.add(p, format_level_text(f"TP{i + 1}", p), "#3fb950", priority=80 - i, ref=cur)

    below = float(getattr(ta, "liq_magnet_below", 0) or 0)
    above = float(getattr(ta, "liq_magnet_above", 0) or 0)
    if float(getattr(ta, "liq_magnet_strength", 0) or 0) >= 0.35:
        for p, lbl in ((above, "liq↑"), (below, "liq↓")):
            if p > 0 and cur > 0 and abs(p - cur) / cur <= 0.18:
                if not any(_near(p, x, ref=cur) for x in board.reserved):
                    board.add(p, format_level_text(lbl, p), "#8899aa", priority=55, ref=cur)


def _draw_trend_lines(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    from datetime import datetime, timezone

    import matplotlib.dates as mdates

    lines = list(getattr(ta, "trend_lines", None) or [])[:3]
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
    """Все подтверждённые слои TA на одном PNG (оркестратор, не урезанный список из 7 пунктов)."""
    if not bars:
        return 0
    drawn = 0
    from .chart_pdf_style import _draw_range_pdf, draw_smc_pdf_clean

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > brdn > 0 or getattr(ta, "consolidation", None) is not None:
        drawn += _draw_range_pdf(ax, bars, ta)

    try:
        from .range_breakdown_retest import get_rbr_from_ta
        from .chart_range_breakdown_draw import draw_range_breakdown_retest_path

        if get_rbr_from_ta(ta):
            draw_range_breakdown_retest_path(ax, bars, ta)
            drawn += 1
    except Exception:
        logger.debug("rbr story path skipped", exc_info=True)

    try:
        from .chart_pro_layers import draw_buy_flat_sell_zones

        draw_buy_flat_sell_zones(ax, bars, ta)
        drawn += 1
    except Exception:
        pass

    if getattr(ta, "channel", None) is not None and getattr(ta, "reading_accept_channel", True):
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

    htf = getattr(ta, "primary_htf_chart_pattern", None)
    if (
        htf
        and getattr(ta, "reading_accept_htf_pattern", True)
        and float(getattr(htf, "confidence", 0) or 0) >= 0.62
    ):
        from .chart_pattern_draw import draw_htf_pattern_levels

        draw_htf_pattern_levels(
            ax,
            bars,
            htf,
            conflict=bool(getattr(ta, "pattern_foresight_htf_conflict", False)),
            quiet=True,
        )
        drawn += 1

    setup_path = list(getattr(ta, "forecast_path_prices", None) or [])
    setup_grade = str(getattr(ta, "setup_grade", "") or "")
    has_setup_path = len(setup_path) >= 2 and setup_grade in {"A", "B", "C"}
    if (
        not has_setup_path
        and str(getattr(ta, "pattern_foresight_summary", "") or "").strip()
        and getattr(ta, "reading_accept_pattern", True)
    ):
        try:
            from .chart_pattern_draw import draw_pattern_foresight_path

            is_wait = str(getattr(ta, "verdict", "") or "").upper() == "WAIT"
            draw_pattern_foresight_path(
                ax,
                bars,
                current_price=float(getattr(ta, "current_price", 0) or bars[-1].close),
                pattern=getattr(ta, "primary_chart_pattern", None),
                horizon_hours=float(getattr(ta, "pattern_foresight_horizon", 0) or 0),
                bias=str(getattr(ta, "pattern_foresight_bias", "neutral") or "neutral"),
                watch_only=bool(getattr(ta, "pattern_foresight_watch_only", False)) or is_wait,
                status=str(getattr(ta, "pattern_foresight_status", "") or ""),
                quiet_labels=True,
            )
            drawn += 1
        except Exception:
            logger.debug("pattern foresight skipped", exc_info=True)

    _draw_trend_lines(ax, bars, ta)

    if getattr(ta, "smc", None) is not None:
        drawn += draw_smc_pdf_clean(ax, bars, ta, composite=True, evidence=True)

    if getattr(ta, "reading_accept_ob", True):
        from .chart_composite_layers import draw_pdf_zones_mpl

        drawn += draw_pdf_zones_mpl(ax, bars, ta)

    if getattr(ta, "reading_accept_fib", True) and list(getattr(ta, "fib_levels", None) or []):
        from .chart_composite_layers import draw_fib_mpl

        drawn += draw_fib_mpl(ax, bars, ta)

    if list(getattr(ta, "patterns", None) or []):
        from .chart_composite_layers import draw_candle_patterns_mpl

        drawn += draw_candle_patterns_mpl(ax, bars, ta)

    try:
        from .chart_breakout_markers import draw_breakout_retest_markers

        draw_breakout_retest_markers(ax, bars, ta, max_markers=2)
        drawn += 1
    except Exception:
        logger.debug("breakout markers skipped", exc_info=True)

    if list(getattr(ta, "rsi_divergences", None) or []):
        try:
            from .chart_pro_layers import draw_rsi_divergence_on_price

            draw_rsi_divergence_on_price(ax, bars, ta)
            drawn += 1
        except Exception:
            logger.debug("rsi div skipped", exc_info=True)

    if float(getattr(ta, "liq_magnet_strength", 0) or 0) >= 0.3:
        try:
            from .chart_pro_layers import draw_swing_liquidity_marks

            draw_swing_liquidity_marks(ax, bars, ta)
            drawn += 1
        except Exception:
            pass

    try:
        from .chart_reference_levels import draw_reference_horizontals

        draw_reference_horizontals(ax, bars, ta)
        drawn += 1
    except Exception:
        pass

    if _elliott_visible(ta):
        try:
            from .chart_renderer import _draw_elliott_from_ta

            verdict = str(getattr(ta, "verdict", "") or "").upper()
            _draw_elliott_from_ta(ax, bars, ta, is_wait=(verdict == "WAIT"))
            drawn += 1
        except Exception:
            logger.debug("elliott evidence draw skipped", exc_info=True)

    try:
        from .chart_plan_chart_gate import plan_ok_to_draw_on_chart
        from .chart_display_policy import chart_trade_plan_on_chart_enabled

        if plan_ok_to_draw_on_chart(ta) and chart_trade_plan_on_chart_enabled():
            from .chart_position_boxes import draw_forward_plan_boxes

            draw_forward_plan_boxes(ax, bars, ta, use_xlim=True)
            drawn += 1
    except Exception:
        logger.debug("plan boxes skipped", exc_info=True)

    return max(drawn, 1)
