"""Чистые слои анализа: зоны, день, путь, план — подписи через LabelBoard."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_label_layout import LabelBoard, format_level_text
from .chart_reference_levels import session_reference_levels
from .ta_analysis import TAAnalysisResult, fmt_price


def _draw_zone_band(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    bottom: float,
    top: float,
    color: str,
    start_idx: int | None = None,
) -> None:
    if top <= bottom or not bars:
        return
    from datetime import datetime, timezone
    import matplotlib.dates as mdates

    i0 = max(0, start_idx if start_idx is not None else len(bars) - min(len(bars), 80))
    x0 = mdates.date2num(datetime.fromtimestamp(bars[i0].open_time, tz=timezone.utc))
    x1 = mdates.date2num(datetime.fromtimestamp(bars[-1].open_time, tz=timezone.utc))
    ax.add_patch(
        Rectangle(
            (x0, bottom),
            max(x1 - x0, 0.001),
            top - bottom,
            facecolor=color,
            edgecolor=color,
            alpha=0.16,
            linewidth=0.9,
            zorder=1,
        )
    )


def collect_structure_labels(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    board: LabelBoard,
    *,
    draw_zones: bool = True,
) -> None:
    """День min/max, зоны спроса/предл., пробой — без дублей на одной цене."""
    if not bars:
        return
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    if cur <= 0:
        return

    if draw_zones:
        metrics = getattr(ta, "market_metrics", None) or {}
        raw_zones = metrics.get("pdf_zones") if isinstance(metrics, dict) else None
        drawn = 0
        if isinstance(raw_zones, list):
            for z in raw_zones:
                if drawn >= 2 or not isinstance(z, dict) or not z.get("valid", False):
                    continue
                top, bot = float(z.get("top", 0) or 0), float(z.get("bottom", 0) or 0)
                if top <= bot or abs((top + bot) / 2 - cur) / cur > 0.12:
                    continue
                if board.occupied((top + bot) / 2, ref=cur, tol=0.008):
                    continue
                kind = str(z.get("kind", "") or "")
                color = "#3fb950" if "demand" in kind or "bull" in kind or "support" in kind else "#f85149"
                start_i = int(z.get("start_idx", max(0, len(bars) - 48)))
                _draw_zone_band(ax, bars, bottom=bot, top=top, color=color, start_idx=start_i)
                note = str(z.get("label", "") or "")[:24]
                tf = str(z.get("tf", "") or "")
                tag = "спрос" if color == "#3fb950" else "предл."
                text = " · ".join(p for p in (tf, tag, note) if p)
                board.add(
                    top, text or tag, color,
                    side="left", priority=68, kind="zone", draw_line=False, ref=cur,
                )
                board.reserve(bot)
                drawn += 1

        if drawn < 2:
            for zone in list(getattr(ta, "zones", None) or [])[:2]:
                top, bot = float(getattr(zone, "top", 0) or 0), float(getattr(zone, "bottom", 0) or 0)
                if top <= bot or board.occupied((top + bot) / 2, ref=cur, tol=0.008):
                    continue
                kind = str(getattr(zone, "kind", "") or "")
                color = "#f85149" if "resist" in kind else "#3fb950"
                _draw_zone_band(ax, bars, bottom=bot, top=top, color=color)
                board.add(
                    top,
                    str(getattr(zone, "label", "") or ("сопр." if color == "#f85149" else "поддерж.")),
                    color,
                    side="left",
                    priority=62,
                    kind="zone",
                    draw_line=False,
                    ref=cur,
                )
                drawn += 1
                if drawn >= 2:
                    break

    for ref in session_reference_levels(bars):
        if ref.kind not in {"daily_high", "daily_low"}:
            continue
        if abs(ref.price - cur) / cur > 0.16:
            continue
        color = "#58a6ff" if ref.kind == "daily_high" else "#3fb950"
        board.add(
            ref.price,
            format_level_text(ref.label, ref.price),
            color,
            side="right",
            priority=86,
            kind=ref.kind,
            ref=cur,
        )

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > 0 and abs(brk - cur) / cur <= 0.12:
        board.add(
            brk, f"пробой ↑ {fmt_price(brk)}", "#3fb950",
            side="right", priority=82, kind="breakout", ref=cur,
        )
    if brdn > 0 and abs(brdn - cur) / cur <= 0.12:
        board.add(
            brdn, f"пробой ↓ {fmt_price(brdn)}", "#f85149",
            side="right", priority=82, kind="breakdown", ref=cur,
        )

    ns = float(getattr(ta, "nearest_support", 0) or 0)
    nr = float(getattr(ta, "nearest_resistance", 0) or 0)
    if ns > 0:
        board.add(ns, f"поддерж. {fmt_price(ns)}", "#3fb950", side="right", priority=70, kind="support", ref=cur)
    if nr > 0:
        board.add(nr, f"сопр. {fmt_price(nr)}", "#f85149", side="right", priority=70, kind="resist", ref=cur)

    for kl in list(getattr(ta, "key_levels", None) or [])[:4]:
        p = float(getattr(kl, "price", 0) or 0)
        if p <= 0 or abs(p - cur) / cur > 0.10:
            continue
        role = str(getattr(kl, "role", "") or "")
        color = "#f85149" if "resist" in role or "breakout" in role else "#3fb950"
        board.add(
            p,
            str(getattr(kl, "label", "") or role)[:28],
            color,
            side="right",
            priority=48,
            kind="key",
            ref=cur,
        )


def draw_probable_path(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
) -> bool:
    """Один путь «куда вероятнее» — без второго/третьего сценария поверх."""
    if not bars:
        return False
    try:
        from .chart_pro_layers import (
            draw_bounce_short_path,
            draw_flat_breakout_path,
            draw_trend_dump_path,
            draw_trend_dump_risk_path,
        )

        if draw_trend_dump_path(ax, bars, ta):
            return True
        if draw_bounce_short_path(ax, bars, ta):
            return True
        if draw_flat_breakout_path(ax, bars, ta):
            return True
        if draw_trend_dump_risk_path(ax, bars, ta):
            return True
    except Exception:
        return False
    return False


def draw_readable_overlays(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    board: LabelBoard,
    *,
    include_plan: bool = True,
    include_path: bool = True,
    draw_zones: bool = True,
) -> None:
    collect_structure_labels(ax, bars, ta, board, draw_zones=draw_zones)
    if include_plan:
        from .chart_position_boxes import draw_forward_plan_boxes

        draw_forward_plan_boxes(ax, bars, ta, use_xlim=True)
    if include_path:
        draw_probable_path(ax, bars, ta)
