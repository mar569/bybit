"""Ed-style PNG: сценарий + учебник (паттерн, sweep, зона) — без метрик и дублей."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_label_layout import LabelBoard, format_level_text
from .chart_reference_levels import session_reference_levels
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

CHART_BG = "#0d1117"


def _price_near(a: float, b: float, ref: float, tol: float = 0.004) -> bool:
    if ref <= 0:
        ref = max(a, b, 1e-9)
    return abs(a - b) <= ref * tol


def add_minimal_context_labels(
    board: LabelBoard,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    mode: str,
) -> None:
    """Не более 2–3 подписей: только то, чего нет на story-слоях."""
    if not bars:
        return
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    if cur <= 0:
        return

    rbr = get_rbr_from_ta(ta)
    reserved: list[float] = list(board.reserved)
    if rbr:
        for k in ("range_top", "range_bottom", "entry_lo", "entry_hi", "stop"):
            v = float(rbr.get(k) or 0)
            if v > 0:
                reserved.append(v)

    def _skip(p: float) -> bool:
        return any(_price_near(p, r, cur) for r in reserved)

    # Один дневной экстремум — если не совпадает с коридором RBR
    for ref in session_reference_levels(bars):
        if ref.kind not in {"daily_high", "daily_low"}:
            continue
        if abs(ref.price - cur) / cur > 0.14 or _skip(ref.price):
            continue
        color = "#58a6ff" if ref.kind == "daily_high" else "#3fb950"
        board.add(
            ref.price,
            format_level_text(ref.label, ref.price),
            color,
            side="right",
            priority=80,
            kind=ref.kind,
            ref=cur,
            skip_if_reserved=False,
        )
        break

    if mode == "range_wait":
        brk = float(getattr(ta, "breakout_level", 0) or 0)
        brdn = float(getattr(ta, "breakdown_level", 0) or 0)
        if brk > 0 and not _skip(brk):
            board.add(brk, f"верх {fmt_price(brk)}", "#f0c040", side="right", priority=85, ref=cur, skip_if_reserved=False)
        if brdn > 0 and not _skip(brdn):
            board.add(brdn, f"низ {fmt_price(brdn)}", "#3fb950", side="right", priority=85, ref=cur, skip_if_reserved=False)


def _x_span_tail(ax: plt.Axes, bars: list[KlineBar], frac: float = 0.38) -> tuple[float, float]:
    from .chart_ed_story import _visible_x_span

    x0, x1 = _visible_x_span(ax, bars)
    w = x1 - x0
    return x0 + w * (1.0 - frac), x1


def draw_narrow_resistance_corridor(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    z_lo: float,
    z_hi: float,
    board: LabelBoard | None = None,
) -> None:
    """Коридор справа у цены — не на весь экран."""
    if z_hi <= z_lo:
        return
    x0, x1 = _x_span_tail(ax, bars, frac=0.42)
    ax.add_patch(
        Rectangle(
            (x0, z_lo),
            max(x1 - x0, 0.001),
            z_hi - z_lo,
            facecolor="#f0c040",
            edgecolor="#f0c040",
            alpha=0.10,
            linewidth=0.8,
            zorder=3,
        )
    )
    ax.hlines(z_hi, x0, x1, colors="#f0c040", linewidth=1.5, alpha=0.9, zorder=5)
    ax.hlines(z_lo, x0, x1, colors="#f0c040", linewidth=1.5, alpha=0.9, zorder=5)
    from .chart_display_policy import ed_chart_visual_only

    if board is not None:
        board.reserve(z_lo)
        board.reserve(z_hi)
        if not ed_chart_visual_only():
            mid = (z_lo + z_hi) / 2.0
            board.add(
                mid,
                f"сопр. {fmt_price(z_lo)}–{fmt_price(z_hi)}",
                "#f0c040",
                side="right",
                priority=92,
                kind="corridor",
                draw_line=False,
                ref=mid,
                skip_if_reserved=False,
            )


def _draw_one_context_zone(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
) -> None:
    """Одна валидная зона спроса/предложения — полоса без LTF-подписи."""
    if not bars:
        return
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    metrics = getattr(ta, "market_metrics", None) or {}
    raw = metrics.get("pdf_zones") if isinstance(metrics, dict) else None
    picked = None
    if isinstance(raw, list):
        for z in raw:
            if not isinstance(z, dict) or not z.get("valid", False):
                continue
            top, bot = float(z.get("top", 0) or 0), float(z.get("bottom", 0) or 0)
            if top <= bot or abs((top + bot) / 2 - cur) / cur > 0.10:
                continue
            picked = (bot, top, str(z.get("kind", "")))
            break
    if picked is None:
        from .chart_analysis_text import pick_chart_zone

        z = pick_chart_zone(ta, cur)
        if z and z.get("valid"):
            picked = (float(z["bottom"]), float(z["top"]), str(z.get("kind", "")))
    if picked is None:
        return
    bot, top, kind = picked
    color = "#3fb950" if "demand" in kind or "bull" in kind or "support" in kind else "#f85149"
    from datetime import datetime, timezone
    import matplotlib.dates as mdates

    i0 = max(0, len(bars) - min(len(bars), 56))
    x0 = mdates.date2num(datetime.fromtimestamp(bars[i0].open_time, tz=timezone.utc))
    x1 = mdates.date2num(datetime.fromtimestamp(bars[-1].open_time, tz=timezone.utc))
    ax.add_patch(
        Rectangle(
            (x0, bot), max(x1 - x0, 0.001), top - bot,
            facecolor=color, edgecolor=color, alpha=0.11, linewidth=0.7, zorder=1,
        )
    )


def draw_pro_teaching_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    mode: str,
) -> None:
    """Графический разбор бота: 1 паттерн, foresight/sweep, 1 зона — не дублирует SL/TP."""
    if not bars or mode == "legacy_manual":
        return
    from .pattern_specs import MIN_DRAW_CONFIDENCE

    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])
    if getattr(ta, "reading_accept_pattern", True) and (primary or patterns):
        from .chart_pattern_draw import draw_chart_patterns

        draw_chart_patterns(
            ax,
            bars,
            patterns,
            max_patterns=1,
            min_confidence=max(0.68, float(MIN_DRAW_CONFIDENCE)),
            force_primary=primary,
            draw_target_labels=False,
        )

    rbr = get_rbr_from_ta(ta)
    if (
        not rbr
        and primary
        and getattr(ta, "reading_accept_pattern", True)
        and str(getattr(ta, "pattern_foresight_summary", "") or "").strip()
    ):
        from .chart_pattern_draw import draw_pattern_foresight_path

        draw_pattern_foresight_path(
            ax,
            bars,
            current_price=float(getattr(ta, "current_price", 0) or bars[-1].close),
            pattern=primary,
            horizon_hours=float(getattr(ta, "pattern_foresight_horizon", 0) or 0),
            bias=str(getattr(ta, "pattern_foresight_bias", "neutral") or "neutral"),
            watch_only=bool(getattr(ta, "pattern_foresight_watch_only", False))
            or (getattr(ta, "verdict", "") or "").upper() == "WAIT",
            status=str(getattr(ta, "pattern_foresight_status", "") or ""),
            quiet_labels=True,
        )
    elif mode == "ed_story" and rbr and str(rbr.get("phase") or "") in {"fade_top", "await_break"}:
        pass  # ghost + forward box = путь сценария
    elif mode in {"range_wait", "observation"}:
        from .chart_readable import draw_probable_path

        draw_probable_path(ax, bars, ta)

    htf = getattr(ta, "primary_htf_chart_pattern", None)
    if htf and getattr(ta, "reading_accept_htf_pattern", True):
        from .chart_pattern_draw import draw_htf_pattern_levels

        draw_htf_pattern_levels(
            ax,
            bars,
            htf,
            conflict=bool(getattr(ta, "pattern_foresight_htf_conflict", False)),
            quiet=True,
        )

    _draw_one_context_zone(ax, bars, ta)

    from .chart_breakout_markers import draw_breakout_retest_markers
    from .chart_education_visual import draw_education_visuals_mpl

    draw_education_visuals_mpl(ax, bars, ta)
    draw_breakout_retest_markers(ax, bars, ta, max_markers=2)
