"""План для ручного TA: уровни в зоне видимости графика, согласованы с PNG."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult

from .chart_position_boxes import chart_plan_targets
from .human_trade_brief import preferred_trade_side
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import fmt_price


def _width_pct(top: float, bot: float) -> float:
    mid = (top + bot) / 2.0
    if mid <= 0 or top <= bot:
        return 999.0
    return (top - bot) / mid * 100.0


def manual_display_stop_and_targets(
    ta: "TAAnalysisResult",
    *,
    max_target_dist_pct: float = 0.16,
) -> tuple[float | None, list[float]]:
    cur = float(getattr(ta, "current_price", 0) or 0)
    side = preferred_trade_side(ta) or "short"
    rbr = get_rbr_from_ta(ta)
    if rbr:
        stop = float(rbr["stop"]) if rbr.get("stop") else None
        tgs = [float(t) for t in (rbr.get("targets") or []) if t]
        if stop and tgs and cur > 0:
            tgs = [t for t in tgs if abs(t - cur) / cur <= max(max_target_dist_pct, 0.22)]
            return stop, tgs[:3]

    entry_lo = entry_hi = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        entry_lo, entry_hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    stop_raw = getattr(ta, "invalidation_price", None) or getattr(ta, "setup_stop", None)
    stop = float(stop_raw) if stop_raw else None
    cons = getattr(ta, "consolidation", None)
    if cons is not None and cur > 0 and stop:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot and bot <= cur <= top * 1.02 and _width_pct(top, bot) <= 12.0:
            if side == "short":
                cap = top * 1.018
                if stop > cap:
                    stop = cap
            elif side == "long":
                floor = bot * 0.982
                if stop < floor:
                    stop = floor

    tps = chart_plan_targets(ta, entry_lo=entry_lo, entry_hi=entry_hi)
    if cur > 0 and tps:
        tps = [t for t in tps if abs(t - cur) / cur <= max_target_dist_pct]
    return stop, tps[:3]


def manual_level_line(ta: "TAAnalysisResult", side: str) -> str:
    cur = float(getattr(ta, "current_price", 0) or 0)
    brk = getattr(ta, "breakout_level", None)
    brdn = getattr(ta, "breakdown_level", None)
    stop, tps = manual_display_stop_and_targets(ta)

    bits: list[str] = []
    if side == "short" and brdn and cur > 0 and float(brdn) < cur * 1.05:
        bits.append(f"триггер шорта — закреп ниже {fmt_price(float(brdn))}")
    elif side == "long" and brk and cur > 0 and float(brk) > cur * 0.95:
        bits.append(f"триггер лонга — закреп выше {fmt_price(float(brk))}")
    if stop:
        bits.append(f"стоп/отмена — {fmt_price(float(stop))}")
    if tps:
        bits.append(f"ориентир — {', '.join(fmt_price(float(t)) for t in tps[:2])}")
    return "; ".join(bits)
