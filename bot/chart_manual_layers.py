"""Разметка ручного TA: рабочие уровни даже без «valid» зоны — как на ручном разборе."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .chart_reference_levels import session_reference_levels
from .human_trade_brief import preferred_trade_side
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


def _pick_zone(ta: TAAnalysisResult, current: float) -> dict | None:
    metrics = getattr(ta, "market_metrics", None) or {}
    raw = metrics.get("pdf_zones") if isinstance(metrics, dict) else None
    if not raw:
        return None
    best: dict | None = None
    best_score = -1.0
    for z in raw:
        if not isinstance(z, dict):
            continue
        top, bot = float(z.get("top", 0)), float(z.get("bottom", 0))
        if top <= bot:
            continue
        mid = (top + bot) / 2
        if abs(mid - current) / current > 0.14:
            continue
        valid = bool(z.get("valid"))
        tf = str(z.get("tf", "LTF"))
        score = 20.0 - abs(mid - current) / current * 100.0
        if valid:
            score += 25.0
        if tf in {"H4", "H1", "W1"}:
            score += 6.0
        if score > best_score:
            best_score = score
            best = z
    return best


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
        lbl = "День MAX" if ref.kind == "daily_high" else "День MIN"
        _hline_label(ax, x1, ref.price, f"{lbl} {fmt_price(ref.price)}", color=col, fontweight="bold")
        _mark(ref.price)

    seg = bars[-min(96, len(bars)) :]
    loc_hi = max(b.high for b in seg)
    loc_lo = min(b.low for b in seg)
    if not _near(loc_hi):
        _hline_label(
            ax, x1, loc_hi, f"Лок.MAX {fmt_price(loc_hi)}",
            color="#79c0ff", lw=1.0, ls=":", fontweight="bold",
        )
        _mark(loc_hi)
    if not _near(loc_lo):
        _hline_label(
            ax, x1, loc_lo, f"Лок.MIN {fmt_price(loc_lo)}",
            color="#56d364", lw=1.0, ls=":", fontweight="bold",
        )
        _mark(loc_lo)

    # 2) Поддержка / сопротивление (всегда если есть)
    for price, label, col in (
        (getattr(ta, "nearest_resistance", None), "Сопр.", "#f85149"),
        (getattr(ta, "nearest_support", None), "Поддерж.", "#3fb950"),
    ):
        if price and float(price) > 0 and abs(float(price) - current) / current <= 0.12:
            p = float(price)
            if not _near(p):
                _hline_label(ax, x1, p, f"{label} {fmt_price(p)}", color=col, lw=1.15)
                _mark(p)

    for lv in (getattr(ta, "levels", None) or [])[:3]:
        p = float(lv.price)
        if p <= 0 or abs(p - current) / current > 0.12 or _near(p):
            continue
        col = "#3fb950" if getattr(lv, "kind", "") == "support" else "#f85149"
        _hline_label(ax, x1, p, fmt_price(p), color=col, lw=0.9, ls=":")
        _mark(p)

    _draw_consolidation(ax, bars, ta, x0, x1)

    # 3) Зона (valid или кандидат)
    zone = _pick_zone(ta, current)
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
        tag = "зона" if valid else "кандидат"
        ax.text(
            x0, top, f"  {tag} {zone.get('tf', 'LTF')}  ",
            color=col, fontsize=7, fontweight="bold", va="bottom", ha="left", zorder=3,
            bbox=dict(boxstyle="round,pad=0.12", facecolor=CHART_BG, edgecolor=col, alpha=0.85),
        )

    # 4) Триггеры — оба направления (ручной разбор)
    if ta.breakout_level and abs(float(ta.breakout_level) - current) / current <= 0.15:
        p = float(ta.breakout_level)
        if not _near(p):
            _hline_label(
                ax, x1, p, f"Пробой LONG ≥ {fmt_price(p)}",
                color="#3fb950", lw=1.6, fontweight="bold",
            )
            _mark(p)
    if ta.breakdown_level and abs(float(ta.breakdown_level) - current) / current <= 0.15:
        p = float(ta.breakdown_level)
        if not _near(p):
            _hline_label(
                ax, x1, p, f"Пробой SHORT ≤ {fmt_price(p)}",
                color="#f85149", lw=1.6, fontweight="bold",
            )
            _mark(p)

    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        p = float(smc.structure_break_level)
        if p > 0 and abs(p - current) / current <= 0.12 and not _near(p):
            _hline_label(ax, x1, p, f"BOS {fmt_price(p)}", color="#d29922", lw=1.2, ls="-.")
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
            ax, x1, entry_hi, f"ВХОД {fmt_price(entry_lo)}–{fmt_price(entry_hi)}",
            color=col, lw=0, draw_line=False,
        )

    inv = getattr(ta, "invalidation_price", None) or getattr(ta, "setup_stop", None)
    if inv and float(inv) > 0:
        _hline_label(
            ax, x1, float(inv), f"SL {fmt_price(float(inv))}",
            color="#ff7b72", lw=1.8, ls="--", fontweight="bold",
        )

    tps = list(getattr(ta, "target_prices", None) or []) or list(getattr(ta, "setup_tps", None) or [])
    for i, tp in enumerate(tps[:3]):
        if not tp:
            continue
        _hline_label(
            ax, x1, float(tp), f"TP{i + 1} {fmt_price(float(tp))}",
            color="#d29922", lw=1.3, ls=":", fontweight="bold" if i == 0 else "normal",
        )

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
                "сняли ликвидность",
                xy=(when, marker.price),
                xytext=(when, marker.price * (1.006 if getattr(marker, "direction", "") == "long" else 0.994)),
                color="#3fb950" if getattr(marker, "direction", "") == "long" else "#f85149",
                fontsize=7, fontweight="bold",
                arrowprops=dict(arrowstyle="->", lw=0.9), zorder=8,
            )

    trigger = str(getattr(ta, "setup_trigger", "") or "")[:100]
    stack = str(getattr(ta, "reading_tf_stack", "") or "")[:100]
    ax.text(
        0.015, 0.02,
        " · ".join(x for x in (stack, trigger, "ручной разбор — уровни на графике") if x),
        transform=ax.transAxes, va="bottom", ha="left", color=CHART_TEXT, fontsize=6.8, zorder=10,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.95),
    )
