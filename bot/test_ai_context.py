"""Smoke tests for AI context helpers (no Gemini API calls)."""
from __future__ import annotations

from bot.ai_context import parse_hours_from_text, serialize_ta
from bot.ta_analysis import TAAnalysisResult


def test_parse_hours_from_text() -> None:
    assert parse_hours_from_text("разбор на сутки") == 24
    assert parse_hours_from_text("посмотри двое суток") == 48
    assert parse_hours_from_text("окно 12h") == 12
    assert parse_hours_from_text("просто вопрос", default=24) == 24


def test_serialize_ta_compact() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=6,
        current_price=100.5,
        phase="consolidation",
        phase_label="боковик",
        elliott_label="коррекция ABC",
        setup_grade="C",
        chart_patterns=[],
    )
    data = serialize_ta(ta)
    assert data["verdict"] == "WAIT"
    assert data["price"] == 100.5
    assert data["elliott"]["label"] == "коррекция ABC"
    assert "fib" in data
    assert "smc" in data
