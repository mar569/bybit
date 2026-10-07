from __future__ import annotations

from .human_trade_brief import (
    build_human_trade_brief,
    reconcile_verdict_with_scenario,
    scanner_side_blocked_by_scenario,
)
from .scenario_engine import ScenarioPick, SCENARIO_CONTINUATION
from .ta_analysis import TAAnalysisResult


def test_reconcile_conflict_long_vs_short_scenario() -> None:
    sc = ScenarioPick(
        SCENARIO_CONTINUATION,
        "Continuation short",
        "SHORT",
        "B",
        "медвежий HTF",
    )
    v, conf, _, note = reconcile_verdict_with_scenario(
        verdict="LONG",
        confidence=7,
        reason="локальный пробой",
        scenario=sc,
        methodology_grade="C",
        setup_grade="C",
        setup_score=5,
    )
    assert v == "WAIT"
    assert conf <= 6
    assert note


def test_reconcile_watch_scenario_caps_long() -> None:
    sc = ScenarioPick(
        SCENARIO_CONTINUATION,
        "Наблюдение",
        "WATCH",
        "C",
        "мало confluence",
    )
    v, _, _, note = reconcile_verdict_with_scenario(
        verdict="LONG",
        confidence=8,
        reason="",
        scenario=sc,
        methodology_grade="C",
        setup_grade="C",
        setup_score=4,
    )
    assert v == "WAIT"
    assert "наблюдение" in note.lower() or "не исполняем" in note.lower()


def test_scanner_block_when_scenario_short() -> None:
    ta = TAAnalysisResult(
        scenario_engine_action="SHORT",
        scenario_engine_quality="B",
        market_metrics={
            "scenario_engine": {"title": "Continuation short", "action": "SHORT"},
        },
    )
    msg = scanner_side_blocked_by_scenario(ta, "long")
    assert msg and "SHORT" in msg


def test_human_brief_is_sentences_not_bullet_soup() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=6,
        current_price=0.034,
        reading_tf_stack="H4 вниз → H1 вниз → M15 боковик",
        reading_seek_label="откат к supply и реакция",
        scenario_engine_id="continuation",
        market_metrics={
            "scenario_engine": {
                "title": "Continuation — слабый откат в тренде HTF",
                "quality": "C",
            }
        },
        reading_absent=["подтверждённый пробой вверх"],
    )
    text = build_human_trade_brief(ta, symbol="MONUSDT")
    assert "MONUSDT" in text
    assert "· ·" not in text
    assert "Сценарий C:" not in text
    assert "Итог:" in text
