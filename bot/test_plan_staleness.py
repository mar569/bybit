from __future__ import annotations

from .plan_staleness import plan_staleness_plain
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
