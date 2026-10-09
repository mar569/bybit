"""Единый PRO: одни слои разметки; подложка TV (качество) или matplotlib fallback."""
from __future__ import annotations

import os

UNIFIED_CHART_SOURCE = "annotated"
TV_CHART_SOURCE = "tv_annotated"


def normalize_chart_source(chart_source: str | None) -> str:
    src = (chart_source or UNIFIED_CHART_SOURCE).strip().lower()
    if src in {"tv", "tv_annotated", "tradingview"}:
        return TV_CHART_SOURCE
    return UNIFIED_CHART_SOURCE


def unified_pro_chart(*, signal_chart: bool = False, manual_ta_chart: bool = False) -> bool:
    return bool(signal_chart or manual_ta_chart)


def matplotlib_only_for_pro() -> bool:
    return os.environ.get("ED_CHART_MPL", "0").strip().lower() in {"1", "true", "yes", "on"}


def tradingview_base_enabled() -> bool:
    """TV-свечи + те же PRO-слои (crop 1280×704)."""
    return os.environ.get("ED_CHART_TV", "1").strip().lower() in {"1", "true", "yes", "on"}


def prefer_tradingview_for_pro(
    chart_source: str | None,
    *,
    signal_chart: bool = False,
    manual_ta_chart: bool = False,
) -> bool:
    if not unified_pro_chart(signal_chart=signal_chart, manual_ta_chart=manual_ta_chart):
        return False
    if matplotlib_only_for_pro():
        return False
    if manual_ta_chart or signal_chart:
        try:
            from .chart_display_policy import ed_chart_composite_enabled, ed_chart_single_canvas_enabled

            if ed_chart_single_canvas_enabled() or ed_chart_composite_enabled():
                return False
        except Exception:
            pass
    if not tradingview_base_enabled():
        return False
    return True


def legacy_manual_chart_enabled() -> bool:
    return os.environ.get("SIGNAL_CHART_LEGACY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# back-compat aliases
def tradingview_experimental_enabled() -> bool:
    return tradingview_base_enabled()


def use_tradingview_experimental(
    chart_source: str | None,
    *,
    signal_chart: bool = False,
    manual_ta_chart: bool = False,
) -> bool:
    return prefer_tradingview_for_pro(
        chart_source,
        signal_chart=signal_chart,
        manual_ta_chart=manual_ta_chart,
    )
