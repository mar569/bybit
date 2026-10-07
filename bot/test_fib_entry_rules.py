from __future__ import annotations

from .fib_entry_rules import fib_blocks_market_entry
from .ta_analysis import TAAnalysisResult


def test_fib_blocks_late_impulse() -> None:
    ta = TAAnalysisResult(
        fib_status="late_impulse",
        fib_reject_reason="импульс без отката",
        reading_accept_fib=False,
    )
    assert fib_blocks_market_entry(ta, "long")


def test_fib_blocks_outside_zone_when_weak_setup() -> None:
    ta = TAAnalysisResult(
        setup_grade="C",
        reading_accept_fib=True,
        fib_status="ready",
        market_metrics={
            "fib_plan": {
                "checklist_ok": False,
                "in_zone": False,
                "rule": "слабый HTF → golden 0.5–0.71",
            }
        },
    )
    assert fib_blocks_market_entry(ta, "long")
