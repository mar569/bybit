"""Единый PRO-график: сигналы, manual TA, разбор — один matplotlib pipeline."""
from __future__ import annotations

import os

# Единственный пользовательский режим (TV/legacy — только явный override).
UNIFIED_CHART_SOURCE = "annotated"


def normalize_chart_source(chart_source: str | None) -> str:
    """Любой старый alias → annotated."""
    src = (chart_source or UNIFIED_CHART_SOURCE).strip().lower()
    if src in {
        "annotated",
        "tv",
        "tv_annotated",
        "tradingview",
        "annotated_pro",
        "pro",
        "matplotlib",
        "mpl",
    }:
        return UNIFIED_CHART_SOURCE
    return UNIFIED_CHART_SOURCE


def unified_pro_chart(*, signal_chart: bool = False, manual_ta_chart: bool = False) -> bool:
    return bool(signal_chart or manual_ta_chart)


def tradingview_experimental_enabled() -> bool:
    """TV-подложка выключена по умолчанию — один стабильный PRO."""
    return os.environ.get("ED_CHART_TV", "0").strip().lower() in {"1", "true", "yes", "on"}


def use_tradingview_experimental(
    chart_source: str | None,
    *,
    signal_chart: bool = False,
    manual_ta_chart: bool = False,
) -> bool:
    if not tradingview_experimental_enabled():
        return False
    src = (chart_source or "").strip().lower()
    return src in {"tv", "tv_annotated", "tradingview"}


def legacy_manual_chart_enabled() -> bool:
    return os.environ.get("SIGNAL_CHART_LEGACY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
