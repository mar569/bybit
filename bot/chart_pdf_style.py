"""Отрисовка по материалам docs/.cursor_pdf_pages: один сетап на график.

Приоритет: паттерн (линии + вход/SL/цель) → SMC (BOS, свип, OB) → range (поддержка/сопр.) → минимум swing.
Без RBR fade_top / synthetic ghost / каши слоёв.
"""
from __future__ import annotations

import logging
from typing import Literal

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_display_policy import chart_teaching_tags_enabled, ed_pdf_chart_style_enabled
from .chart_ed_story import _visible_x_span
from .chart_level_labels import level_tag
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

PdfChartMode = Literal["pattern", "smc", "range", "structure"]


def sanitize_rbr_for_pdf(ta: TAAnalysisResult, bars: list[KlineBar] | None) -> TAAnalysisResult:
    """Убрать synthetic RBR, если цена не у края range (как в PDF — без «отказ у потолка» в середине)."""
    if not ed_pdf_chart_style_enabled():
        return ta
    rbr = get_rbr_from_ta(ta)
    if not rbr or not bars:
        return ta
    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    if floor <= 0 or ceil <= floor or cur <= 0:
        return ta
    pos = (cur - floor) / (ceil - floor)
    phase = str(rbr.get("phase") or "")
    drop = bool(rbr.get("synthetic")) or phase == "fade_top"
    if drop and phase == "fade_top" and pos < 0.72:
        mm = dict(getattr(ta, "market_metrics", None) or {})
        mm.pop("range_breakdown_retest", None)
        ta.market_metrics = mm
        return ta
    if drop and phase == "await_break" and pos > 0.35 and pos < 0.65:
        mm = dict(getattr(ta, "market_metrics", None) or {})
        mm.pop("range_breakdown_retest", None)
        ta.market_metrics = mm
    return ta


def resolve_pdf_chart_mode(ta: TAAnalysisResult, bars: list[KlineBar]) -> PdfChartMode:
    if not ed_pdf_chart_style_enabled():
        return "structure"
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if bool(getattr(ta, "post_pump", False)) and brk > brdn > 0:
        return "range"
    if bool(getattr(ta, "post_pump", False)) and getattr(ta, "consolidation", None) is not None:
        return "range"
    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if getattr(ta, "reading_accept_pattern", True) and (primary or patterns):
        conf = float(getattr(primary, "confidence", 0) or 0) if primary else 0.0
        if primary and conf >= 0.58:
            return "pattern"
        if patterns:
            best = max(patterns, key=lambda p: float(getattr(p, "confidence", 0) or 0))
            if float(getattr(best, "confidence", 0) or 0) >= 0.62:
                return "pattern"

    smc = getattr(ta, "smc", None)
    if smc is not None:
        if getattr(smc, "structure_break", False) or getattr(smc, "liquidity_sweep", False):
            return "smc"
        if list(getattr(smc, "order_blocks", None) or [])[-1:]:
            return "smc"
        if list(getattr(smc, "markers", None) or []):
            return "smc"

    if getattr(ta, "consolidation", None) is not None:
        return "range"
    if brk > 0 and brdn > 0 and brk > brdn:
        return "range"
    return "structure"


def _draw_post_pump_read_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    """После пампа (PDF): нога импульса, local H, откат — не один OB."""
    if not bool(getattr(ta, "post_pump", False)) or len(bars) < 16:
        return 0
    import matplotlib.dates as mdates
    from datetime import datetime, timezone

    def _x(i: int) -> float:
        i = max(0, min(i, len(bars) - 1))
        return mdates.date2num(datetime.fromtimestamp(bars[i].open_time, tz=timezone.utc))

    win = min(len(bars), 96)
    seg = bars[-win:]
    peak_i = max(range(len(seg)), key=lambda j: float(seg[j].high))
    peak_i = len(bars) - win + peak_i
    peak = float(bars[peak_i].high)
    base_i = max(0, peak_i - min(40, peak_i))
    base = min(float(bars[j].low) for j in range(base_i, peak_i + 1))

    x0, x1 = _visible_x_span(ax, bars)
    drawn = 0
    ax.hlines(peak, x0, x1, colors="#f85149", linewidth=1.25, alpha=0.88, linestyle="-", zorder=4)
    if chart_teaching_tags_enabled():
        ax.text(x0, peak, f"  H {fmt_price(peak)}  ", color="#f85149", fontsize=7, va="bottom", zorder=5)
    drawn += 1

    x_base, x_peak = _x(base_i), _x(peak_i)
    ax.annotate(
        "",
        xy=(x_peak, peak),
        xytext=(x_base, base),
        arrowprops=dict(arrowstyle="->", color="#3fb950", lw=1.4, alpha=0.85),
        zorder=6,
    )
    if chart_teaching_tags_enabled():
        ax.text(
            x_base + (x_peak - x_base) * 0.35,
            base + (peak - base) * 0.55,
            "  импульс ↑  ",
            color="#3fb950",
            fontsize=6.8,
            fontweight="bold",
            zorder=7,
            bbox=dict(boxstyle="round,pad=0.15", facecolor="#161b22", edgecolor="#3fb950", alpha=0.9),
        )
    drawn += 1

    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    if cur < peak * 0.992 and chart_teaching_tags_enabled():
        ax.text(
            _x(len(bars) - 1),
            cur,
            "  откат  ",
            color="#ffd33d",
            fontsize=6.8,
            zorder=7,
            bbox=dict(boxstyle="round,pad=0.12", facecolor="#161b22", edgecolor="#ffd33d", alpha=0.9),
        )
        drawn += 1
    return drawn


def _draw_range_pdf(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    cons = getattr(ta, "consolidation", None)
    x0, x1 = _visible_x_span(ax, bars)
    w = max(x1 - x0, 0.001)
    top = bot = 0.0
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
    else:
        bot = float(getattr(ta, "breakdown_level", 0) or 0)
        top = float(getattr(ta, "breakout_level", 0) or 0)
    if top <= bot:
        return 0
    ax.add_patch(
        Rectangle(
            (x0, bot),
            w,
            top - bot,
            facecolor="#8b949e",
            alpha=0.07,
            edgecolor="#484f58",
            linewidth=0.8,
            linestyle="--",
            zorder=1,
        )
    )
    ax.hlines(top, x0, x1, colors="#f0c040", linewidth=1.2, alpha=0.9, zorder=4)
    ax.hlines(bot, x0, x1, colors="#3fb950", linewidth=1.2, alpha=0.9, zorder=4)
    if chart_teaching_tags_enabled():
        ax.text(x0, top, f"  {level_tag('break_up', top)}  ", color="#f0c040", fontsize=7, va="bottom", zorder=5)
        ax.text(x0, bot, f"  {level_tag('break_down', bot)}  ", color="#3fb950", fontsize=7, va="top", zorder=5)
    return 2


def _draw_pattern_pdf(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    from .chart_pattern_draw import draw_chart_patterns
    from .pattern_specs import MIN_DRAW_CONFIDENCE

    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    draw_chart_patterns(
        ax,
        bars,
        patterns,
        max_patterns=1,
        min_confidence=max(0.58, float(MIN_DRAW_CONFIDENCE) - 0.08),
        force_primary=primary,
        draw_target_labels=True,
    )
    return 3 if primary or patterns else 0


def _structure_tag(kind: str) -> str:
    from .chart_analysis_text import structure_break_label_ru

    return structure_break_label_ru(kind, short=True)


def _pdf_setup_hint(ta: TAAnalysisResult, bars: list[KlineBar]) -> str:
    smc = getattr(ta, "smc", None)
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    parts: list[str] = []
    if smc and getattr(smc, "structure_break_level", None):
        lv = float(smc.structure_break_level)
        tag = _structure_tag(str(getattr(smc, "structure_break_kind", "") or "bos"))
        if cur < lv * 0.999:
            parts.append(f"{tag} ↓ · цена под {fmt_price(lv)} · без market — ждём retest")
        elif cur > lv * 1.001:
            parts.append(f"{tag} ↑ · цена над {fmt_price(lv)} · лонг от retest")
        else:
            parts.append(f"{tag} · уровень {fmt_price(lv)} · ждём закреп")
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > brdn > 0 and not parts:
        if cur > brk:
            parts.append(f"Пробой ↑ {fmt_price(brk)} — не догонять")
        elif cur < brdn:
            parts.append(f"Пробой ↓ {fmt_price(brdn)} — retest, не в импульс")
        else:
            parts.append(f"Диапазон {fmt_price(brdn)}–{fmt_price(brk)} · вход у границы")
    primary = getattr(ta, "primary_chart_pattern", None)
    if primary:
        try:
            from .chart_patterns import format_chart_pattern_compact

            pl = format_chart_pattern_compact(primary)
            if pl:
                parts.append(pl[:48])
        except Exception:
            pass
    vol = str(getattr(ta, "volume_participation", "") or "").strip()
    if vol and len(vol) < 72:
        parts.append(vol)
    else:
        mpl = list(getattr(ta, "market_participation_lines", None) or [])
        if len(mpl) > 1 and mpl[1]:
            parts.append(str(mpl[1])[:72])
    return " · ".join(parts[:3])


def _draw_pdf_setup_hint(ax: plt.Axes, ta: TAAnalysisResult, bars: list[KlineBar]) -> None:
    from .chart_display_policy import chart_pdf_setup_hint_enabled

    if not chart_pdf_setup_hint_enabled():
        return
    hint = _pdf_setup_hint(ta, bars)
    if not hint:
        return
    ax.text(
        0.5,
        0.98,
        hint,
        transform=ax.transAxes,
        ha="center",
        va="top",
        color="#e6edf3",
        fontsize=7.2,
        zorder=15,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.94),
    )


def draw_smc_pdf_clean(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    composite: bool = False,
    clean: bool = False,
    evidence: bool = False,
) -> int:
    """SMC как в PDF: CHoCH/BOS + свип + OB, без серых «простыней»."""
    smc = getattr(ta, "smc", None)
    if smc is None or not bars:
        return 0
    import matplotlib.dates as mdates
    from datetime import datetime, timezone

    from .chart_education_visual import _x_mpl, _x_span

    show = chart_teaching_tags_enabled()
    x0, x1 = _x_span(bars, tail=min(72, len(bars)))
    w = max(x1 - x0, 0.001)
    x_narrow = x0 + w * 0.45
    drawn = 0
    cur = float(bars[-1].close)

    if getattr(smc, "structure_break_level", None) and not clean and not evidence:
        lv = float(smc.structure_break_level)
        tag = _structure_tag(str(getattr(smc, "structure_break_kind", "") or "bos"))
        ax.axhline(lv, color="#f0c040", linestyle="-", linewidth=1.35, alpha=0.92, zorder=5)
        if show:
            ax.text(x0, lv, f"  {tag} {fmt_price(lv)}  ", color="#f0c040", fontsize=7, va="bottom", fontweight="bold", zorder=6)
        drawn += 1

    markers = sorted(
        [m for m in list(getattr(smc, "markers", None) or []) if 0 <= m.index < len(bars)],
        key=lambda m: m.index,
        reverse=True,
    )
    seen_kind: set[str] = set()
    if evidence:
        max_markers = 3
        lookback = min(len(bars) - 4, 120)
    elif clean:
        max_markers = 2
        lookback = min(len(bars) - 4, 72)
    elif composite:
        max_markers = 6
        from .chart_display_policy import ed_manual_chart_full_history_enabled

        lookback = max(88, len(bars) - 6) if ed_manual_chart_full_history_enabled() else 88
    else:
        max_markers = 2
        lookback = 36
    for marker in markers:
        kind = str(getattr(marker, "kind", "") or "")
        if kind not in {"sweep", "bos", "mss", "expansion", "equal_highs", "equal_lows"}:
            continue
        if kind in seen_kind and kind not in {"equal_highs", "equal_lows"}:
            continue
        if marker.index < len(bars) - lookback:
            continue
        if kind in {"equal_highs", "equal_lows"}:
            ax.axhline(float(marker.price), color="#d2a8ff", linestyle=":", linewidth=0.85, alpha=0.72, zorder=3)
            if show:
                ax.text(x0, float(marker.price), "  EQ liq", color="#d2a8ff", fontsize=6.2, va="center", zorder=4)
            drawn += 1
            continue
        seen_kind.add(kind)
        if kind == "sweep":
            from .chart_event_pins import draw_sweep_pin_mpl

            draw_sweep_pin_mpl(ax, bars, marker)
        elif kind in {"bos", "mss", "expansion"}:
            from .chart_event_pins import draw_bos_pin_mpl

            brk_lv = getattr(smc, "structure_break_level", None) if kind != "expansion" else None
            lbl = _structure_tag("mss" if kind == "mss" else "bos")
            draw_bos_pin_mpl(ax, bars, marker, break_level=float(brk_lv) if brk_lv else None, kind_label=lbl)
        drawn += 1
        if len(seen_kind) >= max_markers:
            break

    if evidence:
        ob_tail = 3
    elif clean:
        ob_tail = 2
    else:
        ob_tail = 5 if composite else 1
    for ob in list(getattr(smc, "order_blocks", None) or [])[-ob_tail:]:
        try:
            top, bot = float(ob.top), float(ob.bottom)
        except (TypeError, ValueError, AttributeError):
            continue
        if top <= bot:
            continue
        idx = int(getattr(ob, "start_idx", None) or getattr(ob, "index", len(bars) - 8) or len(bars) - 8)
        brk = int(getattr(ob, "break_idx", idx) or idx)
        idx = max(0, min(idx, len(bars) - 1))
        brk = max(idx, min(brk, len(bars) - 1))
        x_ob0 = _x_mpl(bars, idx)
        x_ob1 = _x_mpl(bars, min(len(bars) - 1, brk + 2))
        col = "#3fb950" if getattr(ob, "direction", "") == "bullish" else "#f85149"
        ax.add_patch(
            Rectangle((x_ob0, bot), max(x_ob1 - x_ob0, w * 0.05), top - bot, facecolor=col, alpha=0.18, edgecolor=col, linewidth=0.9, zorder=3)
        )
        if show:
            tag = "OB buy" if col == "#3fb950" else "OB sell"
            ax.text(x_ob0, top, f"  {tag}", color=col, fontsize=6.5, va="bottom", zorder=6)
        drawn += 1

    liq_n = 0 if (clean or evidence) else (4 if composite else 2)
    liqs = sorted(
        [float(lv.price) for lv in list(getattr(smc, "liquidity_levels", None) or []) if float(getattr(lv, "price", 0) or 0) > 0],
        key=lambda p: abs(p - cur),
    )[:liq_n]
    for p in liqs:
        ax.axhline(p, color="#8899aa", linestyle=":", linewidth=0.75, alpha=0.6, zorder=3)
        if show:
            ax.text(x1, p, f"  liq {fmt_price(p)}  ", color="#8899aa", fontsize=6, ha="right", va="center", zorder=4)
        drawn += 1

    fvgs = list(getattr(smc, "fvgs", None) or [])
    if evidence:
        fvg_iter = fvgs[-2:]
    elif clean:
        fvg_iter = fvgs[-1:]
    else:
        fvg_iter = fvgs[-4:] if composite else fvgs[-1:]
    for gap in fvg_iter:
        if gap is None:
            continue
        xg0 = _x_mpl(bars, max(0, gap.start_idx))
        xg1 = _x_mpl(bars, min(len(bars) - 1, gap.end_idx))
        col = "#3ddc84" if getattr(gap, "direction", "") == "bullish" else "#ff6b6b"
        ax.add_patch(
            Rectangle((xg0, gap.bottom), max(xg1 - xg0, w * 0.03), gap.top - gap.bottom, facecolor=col, alpha=0.14, zorder=2)
        )
        drawn += 1

    if composite and not clean and getattr(smc, "equilibrium_50", None):
        eq = float(smc.equilibrium_50)
        ax.axhline(eq, color="#8b949e", linestyle=":", linewidth=0.75, alpha=0.65, zorder=3)
        if show:
            ax.text(x1, eq, "  50% EQ", color="#8b949e", fontsize=6.2, ha="right", va="center", zorder=4)
        drawn += 1

    return max(drawn, 1)


def _draw_smc_pdf(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    from .chart_market_read import draw_swing_structure_mpl

    n = draw_smc_pdf_clean(ax, bars, ta)
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > brdn > 0:
        n += _draw_range_pdf(ax, bars, ta)
    n += draw_swing_structure_mpl(ax, bars, ta)
    return max(n, 2)


def _draw_structure_minimal(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    from .chart_market_read import draw_swing_structure_mpl

    n = draw_swing_structure_mpl(ax, bars, ta)
    x0, x1 = _visible_x_span(ax, bars)
    for attr, kind, color in (("breakout_level", "break_up", "#f0c040"), ("breakdown_level", "break_down", "#3fb950")):
        p = float(getattr(ta, attr, 0) or 0)
        if p <= 0:
            continue
        ax.hlines(p, x0, x1, colors=color, linewidth=1.15, alpha=0.82, zorder=4)
        if chart_teaching_tags_enabled():
            ax.text(x0, p, f"  {level_tag(kind, p)}  ", color=color, fontsize=6.8, va="center", zorder=5)
        n += 1
    return max(n, 1)


def draw_pdf_style_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    if not bars:
        return 0
    ta = sanitize_rbr_for_pdf(ta, bars)
    drawn = _draw_post_pump_read_mpl(ax, bars, ta)
    from .chart_display_policy import ed_chart_composite_enabled

    if ed_chart_composite_enabled():
        from .chart_composite_layers import draw_composite_read_mpl

        drawn += draw_composite_read_mpl(ax, bars, ta)
        primary = getattr(ta, "primary_chart_pattern", None)
        if (
            primary
            and getattr(ta, "reading_accept_pattern", True)
            and str(getattr(ta, "pattern_foresight_summary", "") or "").strip()
        ):
            try:
                from .chart_pattern_draw import draw_pattern_foresight_path

                draw_pattern_foresight_path(
                    ax,
                    bars,
                    current_price=float(getattr(ta, "current_price", 0) or bars[-1].close),
                    pattern=primary,
                    horizon_hours=float(getattr(ta, "pattern_foresight_horizon", 0) or 0),
                    bias=str(getattr(ta, "pattern_foresight_bias", "neutral") or "neutral"),
                    watch_only=(getattr(ta, "verdict", "") or "").upper() == "WAIT",
                    status=str(getattr(ta, "pattern_foresight_status", "") or ""),
                    quiet_labels=True,
                )
                drawn += 1
            except Exception:
                pass
        _draw_pdf_setup_hint(ax, ta, bars)
        return max(drawn, 1)
    mode = resolve_pdf_chart_mode(ta, bars)
    if mode == "pattern":
        drawn += max(_draw_pattern_pdf(ax, bars, ta), 0)
    elif mode == "smc":
        drawn += max(_draw_smc_pdf(ax, bars, ta), 0)
    elif mode == "range":
        drawn += max(_draw_range_pdf(ax, bars, ta), 0)
        from .chart_market_read import draw_swing_structure_mpl

        drawn += draw_smc_pdf_clean(ax, bars, ta)
        drawn += draw_swing_structure_mpl(ax, bars, ta)
    else:
        drawn += _draw_structure_minimal(ax, bars, ta)
    _draw_pdf_setup_hint(ax, ta, bars)
    return max(drawn, 1)


def draw_pdf_style_tv(ax: plt.Axes, mapper: object, bars: list[KlineBar], ta: TAAnalysisResult) -> int:
    ta = sanitize_rbr_for_pdf(ta, bars)
    mode = resolve_pdf_chart_mode(ta, bars)
    layers = 0
    if mode == "pattern":
        from .chart_education_visual import draw_patterns_tv_full

        draw_patterns_tv_full(ax, mapper, bars, ta)
        layers = 3
    elif mode == "smc":
        from .chart_education_visual import draw_education_visuals_tv

        draw_education_visuals_tv(mapper, ax, ta)
        layers = 2
    elif mode == "range":
        from .chart_education_visual import draw_education_visuals_tv

        draw_education_visuals_tv(mapper, ax, ta)
        cons = getattr(ta, "consolidation", None)
        top = float(getattr(cons, "top", 0) or getattr(ta, "breakout_level", 0) or 0)
        bot = float(getattr(cons, "bottom", 0) or getattr(ta, "breakdown_level", 0) or 0)
        if top > bot:
            i0 = getattr(mapper, "vis_start", 0)
            mapper.rect(ax, i0, len(mapper.bars) - 1, bot, top, color="#8b949e", alpha=0.08)  # type: ignore[attr-defined]
            mapper.hline(ax, top, color="#f0c040", lw=1.2, alpha=0.88)  # type: ignore[attr-defined]
            mapper.hline(ax, bot, color="#3fb950", lw=1.2, alpha=0.88)  # type: ignore[attr-defined]
            layers = 4
    else:
        from .chart_tv_pro_overlay import _draw_observation_baseline_tv

        layers = _draw_observation_baseline_tv(ax, mapper, ta)
    return max(layers, 1)
