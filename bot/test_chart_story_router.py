from __future__ import annotations

from .chart_story_router import resolve_chart_story_kind
from .ta_analysis import TAAnalysisResult


def test_wait_uses_observation_not_full_manual() -> None:
    ta = TAAnalysisResult(verdict="WAIT", setup_grade="C", setup_clarity=5)
    assert resolve_chart_story_kind(ta) == "observation"


def test_ab_setup_keeps_full_manual() -> None:
    ta = TAAnalysisResult(verdict="LONG", setup_grade="A", setup_clarity=8)
    assert resolve_chart_story_kind(ta) == "full_manual"
