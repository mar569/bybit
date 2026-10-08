from __future__ import annotations

from .plan_staleness import plan_is_stale, plan_staleness_plain
from .range_breakdown_retest import rbr_alert_eligible
from .ta_analysis import TAAnalysisResult


def test_short_at_tp_is_stale():
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="short",
        current_price=0.118,
        target_prices=[0.11807, 0.109],
        market_metrics={
            "range_breakdown_retest": {
                "direction": "short",
                "targets": [0.11807],
                "entry_lo": 0.136,
                "entry_hi": 0.138,
            }
        },
    )
    msg = plan_staleness_plain(ta)
    assert msg and "опоздал" in msg.lower()
    assert plan_is_stale(ta)
    assert not rbr_alert_eligible(ta)


def test_short_far_below_entry_zone_stale():
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="short",
        current_price=0.0425,
        target_prices=[0.041],
        market_metrics={
            "range_breakdown_retest": {
                "direction": "short",
                "phase": "await_break",
                "range_bottom": 0.043,
                "range_top": 0.048,
                "entry_lo": 0.047,
                "entry_hi": 0.0485,
                "targets": [0.041],
            }
        },
    )
    msg = plan_staleness_plain(ta)
    assert msg and ("устарел" in msg.lower() or "retest" in msg.lower())


def test_long_at_tp_stale():
    ta = TAAnalysisResult(
        verdict="LONG",
        action_priority="long",
        current_price=0.546,
        target_prices=[0.5435],
        invalidation_price=0.538,
    )
    msg = plan_staleness_plain(ta)
    assert msg and "опоздал" in msg.lower()
