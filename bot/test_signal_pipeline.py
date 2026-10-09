from __future__ import annotations

from bot.signal_pipeline import (
    merge_alert_caption,
    should_attach_signal_chart,
    should_route_pro_analysis,
)
from bot.ta_analysis import TAAnalysisResult


class _Settings:
    signal_chart_enabled = True
    signal_chart_on_watch = False
    analysis_enabled = True
    signal_pro_to_analysis_chat = False
    trade_decision_min_entry_score = 62


class _Decision:
    action = "entry"
    setup_score = 70


def test_watch_tier_attaches_chart() -> None:
    ta = TAAnalysisResult(setup_grade="B", setup_score=8)
    assert should_attach_signal_chart(
        ta,
        quality_tier="watch",
        trade_decision=_Decision(),
        settings=_Settings(),
    )


def test_entry_b_attaches_chart() -> None:
    ta = TAAnalysisResult(setup_grade="B", setup_score=9)
    assert should_attach_signal_chart(
        ta,
        quality_tier="entry",
        trade_decision=_Decision(),
        settings=_Settings(),
    )


def test_route_pro_on_entry_b_without_flag() -> None:
    ta = TAAnalysisResult(setup_grade="B", setup_score=9)
    assert should_route_pro_analysis(
        ta,
        quality_tier="entry",
        trade_decision=_Decision(),
        settings=_Settings(),
    )


def test_merge_alert_includes_reading() -> None:
    ta = TAAnalysisResult(
        reading_narrative="H4 вниз, M15 сжатие",
        reading_present=["M5: диапазон"],
        reading_absent=["свипа нет"],
    )
    out = merge_alert_caption("Header", ta, quality_tier="watch")
    assert "H4" in out or "диапазон" in out
