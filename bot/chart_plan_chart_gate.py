"""Когда план можно рисовать на PNG (без конфликта RBR vs verdict)."""
from __future__ import annotations

from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult


def chart_plan_side(ta: TAAnalysisResult) -> str:
    from .human_trade_brief import preferred_trade_side

    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("direction") or "") in {"long", "short"}:
        return str(rbr["direction"])
    side = preferred_trade_side(ta) or str(getattr(ta, "action_priority", "") or "").lower()
    v = (getattr(ta, "verdict", "") or "").upper()
    if v == "LONG":
        return "long"
    if v == "SHORT":
        return "short"
    return side if side in {"long", "short"} else ""


def plan_ok_to_draw_on_chart(ta: TAAnalysisResult) -> bool:
    from .chart_position_boxes import plan_for_display
    from .plan_staleness import plan_is_stale

    if plan_is_stale(ta):
        return False
    raw = plan_for_display(ta)
    if raw is None:
        return False
    side, _entry, entry_lo, entry_hi, stop, tp = raw
    if side not in {"long", "short"}:
        return False
    if entry_hi <= entry_lo or stop <= 0 or tp <= 0:
        return False
    aligned = chart_plan_side(ta)
    if aligned and aligned != side:
        return False
    if side == "long" and not (stop < entry_lo and tp > entry_hi):
        return False
    if side == "short" and not (stop > entry_hi and tp < entry_lo):
        return False
    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("phase") or "") in {"fade_top", "await_break", "broken"}:
        return False
    return True
