from __future__ import annotations

from bot.core.playbook.signal_dispatch import delivery_from_context, evaluate_signal_playbook
from bot.core.playbook.states import PlaybookState
from bot.ta_analysis import TAAnalysisResult


def test_delivery_rbr_watch_false_when_no_trade() -> None:
    ta = TAAnalysisResult(
        current_price=0.5,
        verdict="WAIT",
        analysis_interval_minutes=5,
        target_prices=[0.7],
        invalidation_price=1.12,
        market_metrics={
            "range_breakdown_retest": {
                "phase": "fade_top",
                "direction": "short",
                "range_top": 1.1,
                "range_bottom": 0.9,
                "entry_lo": 1.05,
                "entry_hi": 1.08,
                "stop": 1.12,
                "targets": [0.7],
            }
        },
    )
    ctx = evaluate_signal_playbook(ta, symbol="TESTUSDT")
    assert ctx is not None
    delivery = delivery_from_context(ctx)
    assert ctx.result.state == PlaybookState.NO_TRADE
    assert delivery.rbr_watch is False
