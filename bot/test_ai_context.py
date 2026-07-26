"""Smoke tests for AI context helpers (no Gemini API calls)."""
from __future__ import annotations

from bot.ai_context import parse_hours_from_text, serialize_ta
from bot.ta_analysis import TAAnalysisResult


def test_bot_position_call_long_votes() -> None:
    from bot.ai_context import build_bot_position_call

    pack = {
        "ta": {
            "price": 10.0,
            "verdict": "LONG",
            "action_priority": "long",
            "confidence": 7,
            "flow_continuation": 70,
            "flow_correction": 40,
            "targets": [10.5, 11.0],
            "invalidation": 9.5,
            "resistance": 10.2,
            "support": 9.7,
            "pattern_foresight": {"bias": "long", "status": "confirmed", "watch_only": False, "summary": "флаг ↑"},
            "setup_confluence": {"side": "long", "grade": "B", "label_ru": "setup B"},
            "elliott": {"path_bias": "long", "entry_ready": True, "label": "волна 3"},
            "rsi": {"bias": "long"},
            "primary_pattern": {"direction": "bullish", "kind": "flag"},
            "primary_htf_pattern": {"direction": "bullish", "kind": "triangle_ascending"},
        },
        "decision_gate": {"action": "ENTRY"},
        "playbook": {
            "side": "long",
            "aligned": True,
            "entry": 10.15,
            "stop": 9.6,
            "tp1": 10.5,
            "tp2": 11.0,
            "entry_op": ">=",
        },
        "volatility_regime": {"horizon": "20–60м", "tp1_min_pct": 2.5},
    }
    call = build_bot_position_call(pack)
    assert call["position"] == "LONG"
    assert call["votes"]["long"] > call["votes"]["short"]
    assert call["levels"]["entry"] == 10.15
    assert "instruction" in call


def test_bot_position_call_compression_dual() -> None:
    from bot.ai_context import build_bot_position_call

    pack = {
        "ta": {
            "price": 1.0,
            "verdict": "WAIT",
            "action_priority": "short",
            "post_pump": True,
            "candle_compression": True,
            "flow_continuation": 50,
            "flow_correction": 55,
            "pattern_foresight": {"bias": "short", "watch_only": True},
            "setup_confluence": {"side": "neutral", "grade": "D"},
            "elliott": {},
            "rsi": {"bias": "neutral"},
            "primary_pattern": {},
            "primary_htf_pattern": {},
        },
        "decision_gate": {"action": "WATCH"},
        "playbook": {"side": "short", "aligned": False},
        "volatility_regime": {"horizon": "15–45м", "tp1_min_pct": 3.0},
    }
    call = build_bot_position_call(pack)
    assert call["how"] == "dual_breakout_close"
    assert call["position"] in {"WAIT", "NO_TRADE", "SHORT", "LONG"}


def test_parse_hours_from_text() -> None:
    assert parse_hours_from_text("разбор на сутки") == 24
    assert parse_hours_from_text("посмотри двое суток") == 48
    assert parse_hours_from_text("окно 12h") == 12
    assert parse_hours_from_text("просто вопрос", default=24) == 24


def test_parse_interval_from_text() -> None:
    from bot.ai_context import parse_interval_from_text

    assert parse_interval_from_text("на 15m") == 15
    assert parse_interval_from_text("таймфрейм 1h") == 60
    assert parse_interval_from_text("посмотри 15s", default=5) == 1
    assert parse_interval_from_text("просто вопрос", default=10) == 10


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


def test_volatility_regime_hot_alt() -> None:
    from bot.ai_context import build_meaningful_levels, build_volatility_regime

    ta = {
        "price": 4.64,
        "momentum_pct": 5.2,
        "drawdown_from_high_pct": 28.0,
        "targets": [4.70, 4.90],
        "support": 4.20,
        "resistance": 4.73,
    }
    vol = build_volatility_regime(ta)
    assert vol["regime"] in {"hot", "extreme"}
    assert vol["tp1_min_pct"] >= 2.5
    assert "1–3ч" not in vol["horizon"] or vol["regime"] == "calm"
    levels = build_meaningful_levels(ta)
    assert levels["tp1_min_pct"] >= 2.5
    assert levels["horizon"] == vol["horizon"]
