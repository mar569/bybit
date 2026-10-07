from __future__ import annotations

from bot.situational_brief import build_situational_brief_plain, classify_situation
from bot.ta_analysis import CandlePattern, ConsolidationZone, TAAnalysisResult


def test_consolidation_short_plan() -> None:
    ta = TAAnalysisResult(
        current_price=177.0,
        verdict="WAIT",
        verdict_confidence=7,
        action_priority="short",
        consolidation=ConsolidationZone(
            top=179.0,
            bottom=176.0,
            start_idx=10,
            end_idx=80,
            label="M15: боковик",
        ),
        breakdown_level=175.5,
        invalidation_price=182.0,
        target_prices=[157.0],
        reading_live_scenario="range",
    )
    assert classify_situation(ta) == "inside_consolidation"
    text = build_situational_brief_plain(ta, symbol="AAVEUSDT")
    assert "консолидации" in text.lower()
    assert "176" in text
    assert "шорт" in text.lower()


def test_htf_resistance_ladder_short_like_avaai() -> None:
    ta = TAAnalysisResult(
        current_price=0.01125,
        verdict="SHORT",
        verdict_confidence=7,
        action_priority="short",
        phase="impulse_up",
        post_pump=True,
        range_position=0.91,
        reading_tf_stack="H4 медв. bias · M15 перегрев",
        reading_live_scenario="exhaustion",
        consolidation=ConsolidationZone(
            top=0.01241,
            bottom=0.01145,
            start_idx=5,
            end_idx=40,
            label="H4: боковик 8%",
        ),
        nearest_resistance=0.01241,
        invalidation_price=0.01255,
        target_prices=[0.0105, 0.0098],
        analysis_interval_minutes=5,
        mid_interval_minutes=15,
        patterns=[
            CandlePattern(99, "bear_engulf", "медв. поглощение", False),
        ],
        market_metrics={
            "account_ratio": {"long_short_ratio": 3.0, "period": "24h"},
        },
    )
    assert classify_situation(ta) == "htf_resistance_short"
    text = build_situational_brief_plain(ta, symbol="AVAAIUSDT")
    assert "лесенк" in text.lower()
    assert "0.011" in text or "0.012" in text
    assert "L/S" in text or "лонг" in text.lower()
    assert "поглощ" in text.lower()
