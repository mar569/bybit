"""TA-график к алерту: один ТФ с основным LONG/SHORT окном сканера."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import ScannerSettings


def effective_signal_chart_interval_minutes(
    settings: ScannerSettings,
    *,
    exchange: str = "",
) -> int:
    """Не рисуем 5m PNG, если сканер и разбор завязаны на 15m LONG/SHORT."""
    chart_iv = int(getattr(settings, "signal_chart_interval_minutes", 15) or 15)
    sync = bool(getattr(settings, "signal_chart_sync_long_period", True))
    if not sync:
        return max(5, min(chart_iv, 60))
    long_iv = int(getattr(settings, "long_period_minutes", 15) or 15)
    ex = (exchange or "").strip().lower()
    if ex:
        try:
            long_iv = int(settings.for_exchange(ex).long_period_minutes)
        except Exception:
            pass
    return max(5, min(max(chart_iv, long_iv), 60))
