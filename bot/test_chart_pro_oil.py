"""Oil / wide charts use PRO pipeline (no legacy manual by default)."""
from __future__ import annotations

import os
from unittest.mock import patch

from bot.chart_pro import resolve_pro_chart_mode, signal_chart_legacy_enabled
from bot.ta_analysis import TAAnalysisResult


def test_legacy_manual_off_by_default():
    assert signal_chart_legacy_enabled() is False


def test_manual_ta_without_legacy_env_is_observation_or_story():
    ta = TAAnalysisResult(verdict="WAIT", setup_grade="C", setup_clarity=5, current_price=100.0)
    with patch.dict(os.environ, {"SIGNAL_CHART_LEGACY": ""}, clear=False):
        mode = resolve_pro_chart_mode(ta, allow_legacy=False)
    assert mode in {"observation", "range_wait", "ed_story"}


def test_legacy_only_when_env_and_allow():
    ta = TAAnalysisResult(verdict="WAIT", setup_grade="C", setup_clarity=5, current_price=100.0)
    with patch.dict(os.environ, {"SIGNAL_CHART_LEGACY": "1"}, clear=False):
        mode = resolve_pro_chart_mode(ta, allow_legacy=True)
    assert mode in {"observation", "range_wait", "ed_story", "legacy_manual"}
