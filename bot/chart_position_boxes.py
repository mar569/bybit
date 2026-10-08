"""Блоки «позиция» как в TradingView: зелёный тейк / красный стоп от линии входа."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .human_trade_brief import preferred_trade_side
from .pro_invariants import bias_side, ensure_minimum_targets, resolve_wait_plan_levels
from .ta_analysis import TAAnalysisResult, fmt_price


def chart_plan_targets(
    ta: TAAnalysisResult,
    *,
    entry_lo: float | None,
    entry_hi: float | None,
) -> list[float]:
    current = float(getattr(ta, "current_price", 0) or 0)
    side = bias_side(getattr(ta, "verdict", ""), getattr(ta, "action_priority", ""))
    if side not in {"long", "short"}:
        side = preferred_trade_side(ta) or "long"
    entry = None
    if entry_lo is not None and entry_hi is not None and entry_hi > entry_lo:
        entry = (entry_lo, entry_hi)
    raw = list(getattr(ta, "target_prices", None) or []) or list(getattr(ta, "setup_tps", None) or [])
    _, _, trigger = resolve_wait_plan_levels(ta)
    return ensure_minimum_targets(side, current, raw, entry_zone=entry, trigger=trigger)

_BOX_GREEN = "#3fb950"
_BOX_RED = "#f85149"


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _entry_stop_tp(ta: TAAnalysisResult) -> tuple[str, float, float, float] | None:
    side = preferred_trade_side(ta)
    if side not in {"long", "short"}:
        v = (getattr(ta, "verdict", "") or "").upper()
        if v == "LONG":
            side = "long"
        elif v == "SHORT":
            side = "short"
        else:
            side = "long"

    entry_lo = entry_hi = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        entry_lo, entry_hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    elif getattr(ta, "setup_entry", None):
        e = float(ta.setup_entry)
        entry_lo, entry_hi = e * 0.9985, e * 1.0015
    if entry_lo is None or entry_hi is None:
        cur = float(getattr(ta, "current_price", 0) or 0)
        if cur <= 0:
            return None
        entry_lo, entry_hi = cur * 0.999, cur * 1.001

    entry = (entry_lo + entry_hi) / 2.0
    stop = getattr(ta, "invalidation_price", None) or getattr(ta, "setup_stop", None)
    if not stop or float(stop) <= 0:
        return None
    stop = float(stop)

    tps = chart_plan_targets(ta, entry_lo=entry_lo, entry_hi=entry_hi)
    if not tps:
        return None
    tp = float(tps[0])

    if side == "long":
        if stop >= entry or tp <= entry:
            return None
    else:
        if stop <= entry or tp >= entry:
            return None
    return side, entry, stop, tp


def draw_position_risk_boxes(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    plan = _entry_stop_tp(ta)
    if plan is None or not bars:
        return
    side, entry, stop, tp = plan

    i0 = max(0, len(bars) - min(len(bars), 72))
    i1 = len(bars) - 1
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, i1))
    width = max((x1 - x0) * 0.22, 0.0008)
    x_box = x1 - width * 0.95

    if side == "long":
        tp_rect = Rectangle((x_box, entry), width, tp - entry, facecolor=_BOX_GREEN, edgecolor=_BOX_GREEN, alpha=0.22, zorder=2)
        sl_rect = Rectangle((x_box, stop), width, entry - stop, facecolor=_BOX_RED, edgecolor=_BOX_RED, alpha=0.28, zorder=2)
        tp_lbl_y, sl_lbl_y = (entry + tp) / 2, (entry + stop) / 2
    else:
        sl_rect = Rectangle((x_box, entry), width, stop - entry, facecolor=_BOX_RED, edgecolor=_BOX_RED, alpha=0.28, zorder=2)
        tp_rect = Rectangle((x_box, tp), width, entry - tp, facecolor=_BOX_GREEN, edgecolor=_BOX_GREEN, alpha=0.22, zorder=2)
        tp_lbl_y, sl_lbl_y = (entry + tp) / 2, (entry + stop) / 2

    ax.add_patch(tp_rect)
    ax.add_patch(sl_rect)
    ax.axhline(entry, xmin=0.72, xmax=0.98, color="#e6edf3", linewidth=1.0, linestyle="-", alpha=0.85, zorder=3)

    ax.text(
        x_box + width * 0.02, tp_lbl_y,
        f"тейк {fmt_price(tp)}",
        color=_BOX_GREEN, fontsize=7.5, fontweight="bold", va="center", ha="left", zorder=6,
    )
    ax.text(
        x_box + width * 0.02, sl_lbl_y,
        f"стоп {fmt_price(stop)}",
        color=_BOX_RED, fontsize=7.5, fontweight="bold", va="center", ha="left", zorder=6,
    )
    ax.text(
        x_box + width * 0.02, entry,
        f"вход {fmt_price(entry)}",
        color="#e6edf3", fontsize=7.5, fontweight="bold", va="bottom", ha="left", zorder=6,
        bbox=dict(boxstyle="round,pad=0.15", facecolor="#0d1117", edgecolor="#484f58", alpha=0.9),
    )
