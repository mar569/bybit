from __future__ import annotations

from .chart_unified import (
    normalize_chart_source,
    tradingview_experimental_enabled,
    unified_pro_chart,
    use_tradingview_experimental,
)


def test_normalize_aliases_to_annotated():
    assert normalize_chart_source("tv_annotated") == "annotated"
    assert normalize_chart_source("TV") == "annotated"
    assert normalize_chart_source(None) == "annotated"


def test_unified_pro_flags():
    assert unified_pro_chart(signal_chart=True)
    assert unified_pro_chart(manual_ta_chart=True)
    assert not unified_pro_chart()


def test_tv_off_by_default():
    import os

    os.environ.pop("ED_CHART_TV", None)
    assert not tradingview_experimental_enabled()
    assert not use_tradingview_experimental("tv_annotated", signal_chart=True)
