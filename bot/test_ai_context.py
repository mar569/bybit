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


def test_meaningful_levels_skips_micro_noise() -> None:
    from bot.ai_context import build_meaningful_levels

    ta = {
        "price": 3.475,
        "support": 3.474,  # ~0.03% — noise
        "resistance": 3.50,  # ~0.7% — below 1% min
        "targets": [3.42, 3.35],
        "liq_magnet": {"above": 3.476, "below": 3.35},
        "key_levels": [{"label": "day_low", "price": 3.336}],
        "fib": [{"ratio": 1.272, "price": 3.30, "kind": "extension"}],
        "elliott": {"tps": [3.25], "entry": 3.52, "stop": 3.58},
    }
    levels = build_meaningful_levels(ta, min_pct=1.0)
    prices = [x["price"] for x in levels["below"] + levels["above"]]
    assert 3.474 not in prices
    assert 3.50 not in prices
    assert any(p <= 3.42 for p in prices)
    assert any(abs(p - 3.30) < 0.01 for p in prices)
