"""Выбор ТФ графика: 5m шумно → 15m/30m для сетапа (консолидация, post-pump)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult


def pick_setup_chart_interval(ta: "TAAnalysisResult", requested: int) -> int:
    """
    Точка входа часто читается на 15–30m; 5m оставляем для импульса/сканера.
    """
    if requested >= 30:
        return requested
    cons = getattr(ta, "consolidation", None)
    post = bool(getattr(ta, "post_pump", False))
    phase = str(getattr(ta, "phase", "") or "").lower()
    structure = str(getattr(ta, "structure", "") or "").lower()

    in_range = cons is not None or "боков" in structure or "range" in phase or phase == "consolidation"
    if post and in_range:
        return 30
    if in_range and requested <= 5:
        return 30
    if in_range and requested <= 15:
        return 15
    if post and requested <= 15:
        return 15
    return requested


def setup_chart_analysis_hours(interval_minutes: int) -> int:
    """~2 суток истории на ТФ сетапа."""
    return {5: 18, 10: 24, 15: 36, 30: 48, 60: 72}.get(interval_minutes, 48)
