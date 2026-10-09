from __future__ import annotations

from .chart_display_policy import (
    chart_entry_zone_tags_enabled,
    chart_plan_glyphs_enabled,
    chart_trade_plan_on_chart_enabled,
    ed_chart_trader_canvas_enabled,
)


def test_trade_plan_off_by_default():
    assert chart_trade_plan_on_chart_enabled() is False
    assert chart_plan_glyphs_enabled() is False
    assert chart_entry_zone_tags_enabled() is False


def test_trader_canvas_on_with_single_canvas(monkeypatch):
    monkeypatch.delenv("ED_CHART_TRADER_CANVAS", raising=False)
    monkeypatch.setenv("ED_CHART_SINGLE_CANVAS", "1")
    assert ed_chart_trader_canvas_enabled() is True
