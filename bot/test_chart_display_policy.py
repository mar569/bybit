from __future__ import annotations

from .chart_display_policy import (
    chart_entry_zone_tags_enabled,
    chart_plan_glyphs_enabled,
    chart_trade_plan_on_chart_enabled,
)


def test_trade_plan_off_by_default():
    assert chart_trade_plan_on_chart_enabled() is False
    assert chart_plan_glyphs_enabled() is False
    assert chart_entry_zone_tags_enabled() is False
