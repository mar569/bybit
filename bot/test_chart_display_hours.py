from __future__ import annotations

from bot.manual_ta import chart_display_hours, manual_ta_hours


def test_15m_default_visible_48h() -> None:
    assert chart_display_hours(15) == 48
    assert manual_ta_hours(15) == 48


def test_configured_not_capped_at_24() -> None:
    assert chart_display_hours(15, configured=48) == 48
