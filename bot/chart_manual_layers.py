"""Разметка ручного TA: рабочие уровни даже без «valid» зоны — как на ручном разборе."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_reference_levels import session_reference_levels
from .chart_analysis_text import collect_reason_and_confirmations, pick_chart_zone, structure_break_label_ru
from .chart_position_boxes import chart_plan_targets, draw_position_risk_boxes
from .chart_story_labels import draw_story_frame
from .human_trade_brief import preferred_trade_side
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

CHART_BG = "#0d1117"
CHART_TEXT = "#e6edf3"


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x_span(bars: list[KlineBar], *, tail: int | None = None) -> tuple[float, float]:
    if tail is None:
        tail = min(len(bars), max(96, int(len(bars) * 0.92)))
    i0 = max(0, len(bars) - tail)
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    return x0, max(x1, x0 + 0.001)


def _hline_label(
    ax: plt.Axes,
    x1: float,
    price: float,
    text: str,
    *,
    color: str,
    lw: float = 1.2,
    ls: str = "-",
    fontsize: float = 7.5,
    fontweight: str = "normal",
    draw_line: bool = True,
) -> None:
    if draw_line and lw > 0:
        ax.axhline(price, color=color, linestyle=ls, linewidth=lw, alpha=0.92, zorder=4)
    ax.text(
        x1,
        price,
        f"  {text}  ",
        color=color,
        fontsize=fontsize,
        fontweight=fontweight,
        va="bottom" if any(k in text.upper() for k in ("MAX", "SL", "LONG", "СОПР")) else "top",
        ha="right",
        zorder=5,
        bbox=dict(
            boxstyle="round,pad=0.15",
            facecolor=CHART_BG,
            edgecolor=color,
            alpha=0.9,
            linewidth=0.6,
        ),
    )


def draw_education_overlays(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    manual_compact: bool = False,
) -> None:
    """Фигуры, стрелки сценария, подписи «поддержка/манипуляция» поверх manual layers."""
    if not bars:
        return
    from .chart_pattern_draw import draw_chart_patterns, draw_pattern_foresight_path
    from .pattern_specs import MIN_DRAW_CONFIDENCE

    from .range_breakdown_retest import rbr_alert_eligible

    rbr_mode = rbr_alert_eligible(ta)
    accept_pat = getattr(ta, "reading_accept_pattern", True) and not rbr_mode
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if accept_pat and (primary or patterns):
        draw_chart_patterns(
            ax,
            bars,
            patterns,
            max_patterns=1,
            min_confidence=max(0.58, MIN_DRAW_CONFIDENCE - 0.08),
            force_primary=primary,
            draw_target_labels=False,
        )
    is_wait = (getattr(ta, "verdict", "") or "").upper() == "WAIT"
    setup_path = getattr(ta, "forecast_path_prices", None) or []
    setup_grade = getattr(ta, "setup_grade", "") or ""
    has_setup_path = len(setup_path) >= 2 and setup_grade in {"A", "B", "C"}
    if (
        not has_setup_path
        and getattr(ta, "pattern_foresight_summary", "")
        and getattr(ta, "reading_accept_pattern", True)
    ):
        draw_pattern_foresight_path(
            ax,
            bars,
            current_price=float(getattr(ta, "current_price", 0) or bars[-1].close),
            pattern=primary,
            horizon_hours=float(getattr(ta, "pattern_foresight_horizon", 0) or 0),
            bias=str(getattr(ta, "pattern_foresight_bias", "neutral") or "neutral"),
            watch_only=bool(getattr(ta, "pattern_foresight_watch_only", False)) or is_wait,
            status=str(getattr(ta, "pattern_foresight_status", "") or ""),
            quiet_labels=False,
        )

    smc = getattr(ta, "smc", None)
    if smc:
        for ob in list(getattr(smc, "order_blocks", None) or [])[-2:]:
            try:
                top, bot = float(ob.top), float(ob.bottom)
            except (TypeError, ValueError, AttributeError):
                continue
            if top <= bot:
                continue
            x0, x1 = _x_span(bars, tail=min(120, len(bars)))
            col = "#3fb950" if getattr(ob, "direction", "") == "bullish" else "#f85149"
            tag = "бычий ордер-блок" if col == "#3fb950" else "медвежий ордер-блок"
            ax.add_patch(
                Rectangle(
                    (x0, bot), x1 - x0, top - bot,
                    facecolor=col, edgecolor=col, alpha=0.14, linewidth=0.9, zorder=2,
                )
            )
            ax.text(x0, top, f"  {tag}  ", color=col, fontsize=7, fontweight="bold", va="bottom", ha="left", zorder=3)

    if not manual_compact:
        side = preferred_trade_side(ta)
        if side == "long" and ta.nearest_support:
            p = float(ta.nearest_support)
            ax.text(
                _x_span(bars)[0], p, "  Поддержка  ",
                color="#3fb950", fontsize=7, fontweight="bold", va="top", ha="left", zorder=8,
                bbox=dict(boxstyle="round,pad=0.12", facecolor=CHART_BG, edgecolor="#3fb950", alpha=0.9),
            )
        elif side == "short" and ta.nearest_resistance:
            p = float(ta.nearest_resistance)
            ax.text(
                _x_span(bars)[0], p, "  Сопротивление  ",
                color="#f85149", fontsize=7, fontweight="bold", va="bottom", ha="left", zorder=8,
                bbox=dict(boxstyle="round,pad=0.12", facecolor=CHART_BG, edgecolor="#f85149", alpha=0.9),
            )

        reason, confirms = collect_reason_and_confirmations(ta)
        conf_txt = " · ".join(f"✓ {c}" for c in confirms[:3]) if confirms else ""
        body = f"Причина: {reason}" if reason else "Причина: структура и уровни на графике"
        if conf_txt:
            body = f"{body}\n{conf_txt}"
        ax.text(
            0.5, 0.98, body,
            transform=ax.transAxes, va="top", ha="center", color=CHART_TEXT, fontsize=7.2, zorder=11,
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.96),
        )
    draw_story_frame(ax, bars, ta)
    try:
        from .chart_range_breakdown_draw import draw_range_breakdown_retest_path

        draw_range_breakdown_retest_path(ax, bars, ta)
    except Exception:
        pass
    draw_position_risk_boxes(ax, bars, ta)


def _draw_consolidation(ax, bars, ta, x0: float, x1: float) -> None:
    cons = getattr(ta, "consolidation", None)
    if cons is None:
        return
    top, bot = float(cons.top), float(cons.bottom)
    if top <= bot:
        return
    ax.add_patch(
        Rectangle(
            (x0, bot),
            x1 - x0,
            top - bot,
            facecolor="#8b949e",
            edgecolor="#8b949e",
            alpha=0.12,
            linewidth=0.9,
            linestyle="--",
            zorder=1,
        )
    )
    tag = "диапазон"
    lbl = str(getattr(cons, "label", "") or "")
    if lbl.startswith(("H1:", "M15:", "H4:", "H4 ")):
        tag = lbl.split(":")[0].strip() or tag
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close if bars else 0)
    if preferred_trade_side(ta) == "short" and cur >= top * 0.93:
        tag = f"сопр. {tag}"
    ax.text(
        x0,
        top,
        f"  {tag}  ",
        color="#8b949e",
        fontsize=7,
        va="bottom",
        ha="left",
        zorder=3,
    )


def draw_manual_ta_layers(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not bars:
        return
    current = float(getattr(ta, "current_price", 0) or bars[-1].close)
    x0, x1 = _x_span(bars)
    side = preferred_trade_side(ta)
    rbr = get_rbr_from_ta(ta)
    story_focus = bool(rbr and str(rbr.get("phase") or "") in {"fade_top", "await_break"})
    near_pct = 0.06 if story_focus else 0.12
    drawn_prices: list[float] = []

    def _near(p: float) -> bool:
        if p <= 0:
            return True
        return any(abs(p - d) / max(p, 1e-9) < 0.0015 for d in drawn_prices)

    def _mark(p: float) -> None:
        drawn_prices.append(p)

    # 1) Сессия + локальный экстремум на экране
    for ref in session_reference_levels(bars):
        if ref.kind not in {"daily_high", "daily_low"}:
            continue
        col = "#58a6ff" if ref.kind == "daily_high" else "#3fb950"
        ax.hlines(ref.price, x0, x1, colors=col, linewidth=1.35, alpha=0.85, zorder=2)
        lbl = "день макс." if ref.kind == "daily_high" else "день мин."
        _hline_label(ax, x1, ref.price, f"{lbl} {fmt_price(ref.price)}", color=col, fontweight="bold")
        _mark(ref.price)

    seg = bars[-min(96, len(bars)) :]
    loc_hi = max(b.high for b in seg)
    loc_lo = min(b.low for b in seg)
    def _too_close(a: float, b: float) -> bool:
        return abs(a - b) / max(a, 1e-9) < 0.0025

    day_hi = next((r.price for r in session_reference_levels(bars) if r.kind == "daily_high"), None)
    day_lo = next((r.price for r in session_reference_levels(bars) if r.kind == "daily_low"), None)
    if not _near(loc_hi) and not (day_hi and _too_close(loc_hi, day_hi)):
        _hline_label(
            ax, x1, loc_hi, f"лок. макс. {fmt_price(loc_hi)}",
            color="#79c0ff", lw=1.0, ls=":", fontweight="bold",
        )
        _mark(loc_hi)
    if (
        not story_focus
        and not _near(loc_lo)
        and not (day_lo and _too_close(loc_lo, day_lo))
    ):
        _hline_label(
            ax, x1, loc_lo, f"лок. мин. {fmt_price(loc_lo)}",
            color="#56d364", lw=1.0, ls=":", fontweight="bold",
        )
        _mark(loc_lo)

    # 2) Поддержка / сопротивление (всегда если есть)
    for price, label, col in (
        (getattr(ta, "nearest_resistance", None), "Сопр.", "#f85149"),
        (getattr(ta, "nearest_support", None), "Поддерж.", "#3fb950"),
    ):
        if price and float(price) > 0 and abs(float(price) - current) / current <= near_pct:
            p = float(price)
            if not _near(p):
                _hline_label(ax, x1, p, f"{label} {fmt_price(p)}", color=col, lw=1.15)
                _mark(p)

    if len(drawn_prices) < 5:
        for lv in (getattr(ta, "levels", None) or [])[:2]:
            p = float(lv.price)
            if p <= 0 or abs(p - current) / current > 0.12 or _near(p):
                continue
            col = "#3fb950" if getattr(lv, "kind", "") == "support" else "#f85149"
            _hline_label(ax, x1, p, fmt_price(p), color=col, lw=0.9, ls=":")
            _mark(p)

    _draw_consolidation(ax, bars, ta, x0, x1)

    # 3) Зона (valid или кандидат) — не дублируем уже нарисованный боковик
    zone = pick_chart_zone(ta, current)
    cons = getattr(ta, "consolidation", None)
    if zone and cons is not None:
        c_top, c_bot = float(cons.top), float(cons.bottom)
        z_top, z_bot = float(zone["top"]), float(zone["bottom"])
        overlap = max(0.0, min(c_top, z_top) - max(c_bot, z_bot))
        span = max(c_top - c_bot, z_top - z_bot, 1e-9)
        if overlap / span >= 0.55:
            zone = None
    if zone:
        top, bot = float(zone["top"]), float(zone["bottom"])
        kind = str(zone.get("kind", "demand"))
        valid = bool(zone.get("valid"))
        col = "#3fb950" if "demand" in kind or "bull" in kind or "support" in kind else "#f85149"
        ax.add_patch(
            Rectangle(
                (x0, bot), x1 - x0, top - bot,
                facecolor=col, edgecolor=col,
                alpha=0.22 if valid else 0.1,
                linewidth=1.0, zorder=1,
            )
        )
        tf = str(zone.get("tf", "младший ТФ"))
        tag = "зона" if valid else "кандидат зоны"
        ax.text(
            x0, top, f"  {tag} · {tf}  ",
            color=col, fontsize=7, fontweight="bold", va="bottom", ha="left", zorder=3,
            bbox=dict(boxstyle="round,pad=0.12", facecolor=CHART_BG, edgecolor=col, alpha=0.85),
        )

    # 4) Триггеры — оба направления (ручной разбор)
    br_dist = 0.08 if story_focus else 0.15
    if ta.breakout_level and abs(float(ta.breakout_level) - current) / current <= br_dist:
        p = float(ta.breakout_level)
        if not _near(p):
            _hline_label(
                ax, x1, p, f"пробой лонг ≥ {fmt_price(p)}",
                color="#3fb950", lw=1.6, fontweight="bold",
            )
            _mark(p)
    if ta.breakdown_level and abs(float(ta.breakdown_level) - current) / current <= br_dist:
        p = float(ta.breakdown_level)
        if not _near(p):
            _hline_label(
                ax, x1, p, f"пробой шорт ≤ {fmt_price(p)}",
                color="#f85149", lw=1.6, fontweight="bold",
            )
            _mark(p)

    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        p = float(smc.structure_break_level)
        if p > 0 and abs(p - current) / current <= 0.12 and not _near(p):
            bk = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"))
            _hline_label(ax, x1, p, f"{bk} {fmt_price(p)}", color="#d29922", lw=1.2, ls="-.")
            _mark(p)

    # 5) План входа / SL / TP (setup + ta fields)
    entry_lo = entry_hi = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        entry_lo, entry_hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    elif getattr(ta, "setup_entry", None):
        e = float(ta.setup_entry)
        entry_lo, entry_hi = e * 0.9985, e * 1.0015

    if entry_lo is not None and entry_hi is not None and entry_hi > entry_lo:
        col = "#3fb950" if side != "short" else "#f85149"
        ax.add_patch(
            Rectangle(
                (x0, entry_lo), x1 - x0, entry_hi - entry_lo,
                facecolor=col, edgecolor=col, alpha=0.16, linewidth=1.2, linestyle="--", zorder=3,
            )
        )
        _hline_label(
            ax, x1, entry_hi, f"вход {fmt_price(entry_lo)}–{fmt_price(entry_hi)}",
            color=col, lw=0, draw_line=False,
        )

    if len(drawn_prices) < 7:
        tps = chart_plan_targets(ta, entry_lo=entry_lo, entry_hi=entry_hi)
        for i, tp in enumerate(tps[1:2], start=2):
            if not tp or _near(float(tp)):
                continue
            _hline_label(
                ax, x1, float(tp), f"цель {i} {fmt_price(float(tp))}",
                color="#d29922", lw=1.0, ls=":", fontweight="normal",
            )
            _mark(float(tp))

    # 6) Тренд
    tls = list(getattr(ta, "trend_lines", None) or [])
    if tls:
        tl = tls[0]
        start_i = max(0, int(tl.start_idx))
        end_i = max(start_i + 1, int(tl.end_idx))
        last_i = len(bars) - 1
        slope = (tl.end_price - tl.start_price) / (end_i - start_i)
        ext_price = tl.start_price + slope * (last_i - start_i)
        t0 = _idx_to_date(bars, start_i)
        t1 = _idx_to_date(bars, last_i)
        col = "#3fb950" if tl.kind == "bull" else "#f85149"
        ax.plot([t0, t1], [tl.start_price, ext_price], color=col, linewidth=1.4, alpha=0.75, zorder=3)

    if smc and getattr(smc, "liquidity_sweep", False):
        marker = next(
            (m for m in reversed(getattr(smc, "markers", []) or []) if getattr(m, "kind", "") == "sweep"),
            None,
        )
        if marker is not None and 0 <= marker.index < len(bars):
            when = _idx_to_date(bars, marker.index)
            ax.annotate(
                "манипуляция",
                xy=(when, marker.price),
                xytext=(when, marker.price * (1.008 if getattr(marker, "direction", "") == "long" else 0.992)),
                color="#3fb950" if getattr(marker, "direction", "") == "long" else "#f85149",
                fontsize=8, fontweight="bold",
                arrowprops=dict(arrowstyle="->", lw=1.0), zorder=8,
            )

    stack = str(getattr(ta, "reading_tf_stack", "") or "")[:100]
    if stack:
        ax.text(
            0.015, 0.02, stack,
            transform=ax.transAxes, va="bottom", ha="left", color=CHART_TEXT, fontsize=6.5, zorder=10,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#161b22cc", edgecolor="#484f58", alpha=0.9),
        )
