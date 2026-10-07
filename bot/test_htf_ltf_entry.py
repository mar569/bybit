from __future__ import annotations

from bot.htf_ltf_entry import build_htf_ltf_entry_plan
from bot.scenario_report import ENTRY_B
from bot.ta_analysis import TAAnalysisResult


def test_htf_ltf_plan_has_trigger_for_long() -> None:
    ta = TAAnalysisResult(
        verdict="LONG",
        htf_bias="long",
        reading_tf_stack="H4 вверх → H1 вверх → M15 боковик → M5 вверх",
        breakout_level=1.05,
        entry_zone=(1.02, 1.03),
        setup_ideal_ready=True,
        setup_grade="B",
        setup_trigger="close 5m ≥ 1.05",
        analysis_interval_minutes=5,
    )
    plan = build_htf_ltf_entry_plan(ta)
    assert "M15" in plan.m15_zone_line or "1.02" in plan.m15_zone_line
    assert "5m" in plan.ltf_trigger_line.lower() or "close" in plan.ltf_trigger_line.lower()
    assert plan.entry_mode in {ENTRY_B, "A"}
