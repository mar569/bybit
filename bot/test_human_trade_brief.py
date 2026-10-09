from __future__ import annotations

from .human_trade_brief import (
    build_human_trade_brief,
    format_ed_range_wait_html,
    format_manual_ta_human_html,
    reconcile_verdict_with_scenario,
    scanner_side_blocked_by_scenario,
    use_range_wait_caption,
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
    assert "MON" in text
    assert "· ·" not in text
    assert "Сценарий C:" not in text
    assert "0.034" not in text


def test_entries_only_short_caption_when_not_ready(monkeypatch) -> None:
    monkeypatch.setenv("ED_ENTRIES_ONLY", "1")
    ta = TAAnalysisResult(
        verdict="WAIT",
        current_price=0.08,
        breakout_level=0.0835,
        breakdown_level=0.076,
    )
    html = format_manual_ta_human_html(ta, symbol="STRKUSDT")
    assert "вход не готов" in html
    assert "Наблюдение" not in html
    assert "INTEL" not in html


def test_manual_html_avoids_abcd_jargon() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=7,
        action_priority="short",
        current_price=0.08,
        reading_tf_stack="H4 вниз → H1 вверх",
        reading_seek_label="середина диапазона",
        setup_trigger="close 15m ≥ 0.08350",
        breakout_level=0.0835,
        breakdown_level=0.076,
        nearest_support=0.0775,
        entry_zone=(0.079, 0.0795),
        invalidation_price=0.084,
        target_prices=[0.078, 0.077],
        market_participation_lines=["Поток", "OI -0.8%"],
    )
    html = format_manual_ta_human_html(ta, symbol="SANDUSDT")
    assert "setup D" not in html
    assert "режим C" not in html.lower()
    assert "bias SHORT" not in html
    assert use_range_wait_caption(ta)
    assert "SAND" in html
    assert "🧠" not in html
    assert "📍 План" not in html
    assert "📊 <b>Поток</b>" not in html
    assert "INTEL" not in html
    assert "close 15m" not in html


def test_ed_range_wait_jup_style() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=7,
        action_priority="long",
        current_price=0.3802,
        breakout_level=0.381,
        breakdown_level=0.37187,
        reading_tf_stack="W1 боковик → H4 боковик → H1 вверх → M15 боковик",
        target_prices=[0.3882, 0.39269],
        invalidation_price=0.37096,
        entry_zone=(0.37982, 0.38058),
        market_participation_lines=[
            "Поток",
            "Funding +0.005% (нейтральный)",
            "CVD buy 73%",
        ],
    )
    html = format_ed_range_wait_html(ta, symbol="JUPUSDT")
    assert "JUP" in html
    assert "0.381" not in html
    assert "CVD" not in html
    assert "breakout_level" not in html
