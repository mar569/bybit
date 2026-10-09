from __future__ import annotations

from .chart_unified import (
    normalize_chart_source,
    tradingview_experimental_enabled,
    unified_pro_chart,
    use_tradingview_experimental,
)


def test_normalize_chart_source():
    assert normalize_chart_source("tv_annotated") == "tv_annotated"
    assert normalize_chart_source("TV") == "tv_annotated"
    assert normalize_chart_source(None) == "annotated"
    assert normalize_chart_source("annotated_pro") == "annotated"


def test_unified_pro_flags():
    assert unified_pro_chart(signal_chart=True)
    assert unified_pro_chart(manual_ta_chart=True)
    assert not unified_pro_chart()


def test_tv_on_for_pro_by_default():
    import os

    os.environ["ED_CHART_TV"] = "1"
    os.environ.pop("ED_CHART_MPL", None)
    os.environ.pop("ED_CHART_COMPOSITE", None)
    assert tradingview_experimental_enabled()
    # ED_CHART_COMPOSITE=1 по умолчанию — /ta и сигналы на mpl, не TV+2 слоя
    assert not use_tradingview_experimental("annotated", signal_chart=True)
    assert not use_tradingview_experimental("annotated", manual_ta_chart=True)


def test_tv_pro_when_composite_disabled():
    import os

    os.environ["ED_CHART_TV"] = "1"
    os.environ["ED_CHART_COMPOSITE"] = "0"
    os.environ["ED_CHART_SINGLE_CANVAS"] = "0"
    os.environ.pop("ED_CHART_MPL", None)
    assert use_tradingview_experimental("annotated", manual_ta_chart=True)


def test_mpl_override():
    import os

    os.environ["ED_CHART_MPL"] = "1"
    assert not use_tradingview_experimental("annotated", signal_chart=True)
