"""Пересчёт RBR по последним барам перед отрисовкой (не рисовать устаревший fade)."""
from __future__ import annotations

from .bybit_klines import KlineBar
from .range_breakdown_retest import evaluate_range_breakdown_retest, get_rbr_from_ta
from .ta_analysis import TAAnalysisResult


def _recent_close_above(bars: list[KlineBar], level: float, *, n: int = 8) -> bool:
    if not bars or level <= 0:
        return False
    tail = bars[-min(n, len(bars)) :]
    return any(float(b.close) > level * 1.0015 for b in tail)


def rbr_invalidated_by_price(ta: TAAnalysisResult, bars: list[KlineBar] | None) -> bool:
    rbr = get_rbr_from_ta(ta)
    if not rbr or not bars:
        return False
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    ceil = float(rbr.get("range_top") or 0)
    floor = float(rbr.get("range_bottom") or 0)
    direction = str(rbr.get("direction") or "short")
    if direction == "short" and ceil > 0:
        if cur > ceil * 1.004 or _recent_close_above(bars, ceil, n=4):
            return True
    if direction == "short" and floor > 0 and str(rbr.get("phase") or "") == "fade_top":
        if cur < floor * 0.992:
            return True
    return False


def refresh_rbr_for_chart(ta: TAAnalysisResult, bars: list[KlineBar] | None) -> TAAnalysisResult:
    """Снять или обновить range_breakdown_retest; сбросить playbook_v3 cache."""
    if not bars:
        return ta
    mm = dict(getattr(ta, "market_metrics", None) or {})
    changed = False

    if rbr_invalidated_by_price(ta, bars):
        if mm.pop("range_breakdown_retest", None) is not None:
            changed = True
    else:
        cons = getattr(ta, "consolidation", None)
        cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
        if cons is not None and cur > 0:
            setup = evaluate_range_breakdown_retest(
                bars,
                consolidation=cons,
                breakdown=getattr(ta, "breakdown_level", None),
                breakout=getattr(ta, "breakout_level", None),
                post_pump=bool(getattr(ta, "post_pump", False)),
                current=cur,
                repeat_spike_dump_risk=bool(getattr(ta, "repeat_spike_dump_risk", False)),
            )
            prev = mm.get("range_breakdown_retest")
            if setup is not None:
                new = setup.to_dict()
                if new != prev:
                    mm["range_breakdown_retest"] = new
                    changed = True
            elif prev is not None:
                mm.pop("range_breakdown_retest", None)
                changed = True

    if changed:
        mm.pop("playbook_v3", None)
        ta.market_metrics = mm
    return ta
