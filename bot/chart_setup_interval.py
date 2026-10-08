"""ТФ графика: сначала сканер/анализ, иначе поиск 5→15→30 где есть план."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult

_STD = (5, 10, 15, 30, 60)


def _has_readable_plan(ta: "TAAnalysisResult") -> bool:
    from .range_breakdown_retest import get_rbr_from_ta

    if get_rbr_from_ta(ta):
        return True
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > 0 and brdn > 0 and brk > brdn:
        return True
    if getattr(ta, "consolidation", None) is not None and (brk > 0 or brdn > 0):
        return True
    if getattr(ta, "entry_zone", None) and getattr(ta, "invalidation_price", None):
        return True
    return False


def plan_search_intervals(ta: "TAAnalysisResult", requested: int) -> list[int]:
    """
    Порядок перебора ТФ: requested → старше → младше (без скачка сразу на 30m).
    """
    req = max(1, int(requested or 5))
    analyzed = int(getattr(ta, "analysis_interval_minutes", 0) or 0)
    if analyzed >= 5 and _has_readable_plan(ta):
        return [analyzed]

    if req not in _STD:
        return [req, 15, 30, 10, 5]

    i = _STD.index(req)
    order: list[int] = [req]
    for d in range(1, len(_STD)):
        if i + d < len(_STD):
            order.append(_STD[i + d])
        if i - d >= 0:
            order.append(_STD[i - d])
    seen: set[int] = set()
    out: list[int] = []
    for iv in order:
        if iv not in seen:
            seen.add(iv)
            out.append(iv)
    return out


def pick_setup_chart_interval(ta: "TAAnalysisResult", requested: int) -> int:
    """Первый кандидат для одного прогона TA (полный перебор — в chart_renderer)."""
    return plan_search_intervals(ta, requested)[0]


def ta_plan_readable(ta: "TAAnalysisResult") -> bool:
    return _has_readable_plan(ta)


def setup_chart_analysis_hours(interval_minutes: int) -> int:
    return {5: 18, 10: 24, 15: 36, 30: 48, 60: 72}.get(interval_minutes, 48)
