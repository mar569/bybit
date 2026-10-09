"""Заголовок PNG — только playbook (без LONG 8/10)."""
from __future__ import annotations

from .ta_analysis import TAAnalysisResult


def pro_chart_title(
    symbol: str,
    ta: TAAnalysisResult,
    *,
    interval_minutes: int,
    hours_label: str = "",
) -> str | None:
    from .chart_display_policy import ed_playbook_v3_enabled

    if not ed_playbook_v3_enabled():
        return None
    try:
        from .core.playbook.cache import get_or_run_playbook

        sym = (symbol or getattr(ta, "symbol", "") or "").strip().upper()
        pb = get_or_run_playbook(ta, symbol=sym)
    except Exception:
        return None
    tf = f"{interval_minutes}m"
    h = hours_label.strip() or ""
    meta = " · ".join(p for p in (tf, h) if p)
    badge = pb.state.badge_ru
    if meta:
        return f"{sym or symbol} · {meta} · {badge}"
    return f"{sym or symbol} · {badge}"
