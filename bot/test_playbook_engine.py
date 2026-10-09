from __future__ import annotations

from bot.core.playbook.engine import run_playbook
from bot.core.playbook.states import PlaybookState
from bot.ta_analysis import TAAnalysisResult


def _ta(**kwargs: object) -> TAAnalysisResult:
    ta = TAAnalysisResult(
        current_price=1.0,
        verdict="WAIT",
        analysis_interval_minutes=5,
    )
    for key, value in kwargs.items():
        setattr(ta, key, value)
    return ta


def test_playbook_observe_without_rbr() -> None:
    ta = _ta()
    result = run_playbook(ta, symbol="TESTUSDT")
    assert result.state == PlaybookState.OBSERVE
    assert not result.alert_eligible
    assert "TEST" in result.body_html or "Test" in result.body_html


def test_playbook_no_trade_when_stale(monkeypatch) -> None:
    ta = _ta(
        current_price=0.5,
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
        target_prices=[0.7],
        invalidation_price=1.12,
    )
    result = run_playbook(ta)
    assert result.state == PlaybookState.NO_TRADE
    assert not result.alert_eligible


def test_playbook_armed_rbr_fade_top() -> None:
    ta = _ta(
        current_price=1.06,
        market_metrics={
            "range_breakdown_retest": {
                "phase": "fade_top",
                "direction": "short",
                "range_top": 1.1,
                "range_bottom": 0.95,
                "entry_lo": 1.04,
                "entry_hi": 1.08,
                "stop": 1.12,
                "targets": [0.85],
            }
        },
        target_prices=[0.85],
        invalidation_price=1.12,
    )
    result = run_playbook(ta)
    assert result.state == PlaybookState.WATCH
    assert result.alert_eligible
    assert "На графике" in result.body_html
    assert "Входа нет" in result.body_html
    assert result.chart_spec.show_range


def test_playbook_cache_reuses_result() -> None:
    ta = _ta(
        current_price=1.06,
        market_metrics={
            "range_breakdown_retest": {
                "phase": "fade_top",
                "direction": "short",
                "range_top": 1.1,
                "range_bottom": 0.95,
                "entry_lo": 1.04,
                "entry_hi": 1.08,
                "stop": 1.12,
                "targets": [0.85],
            }
        },
    )
    from bot.core.playbook.cache import get_or_run_playbook

    a = get_or_run_playbook(ta, symbol="XUSDT")
    b = get_or_run_playbook(ta, symbol="XUSDT")
    assert a.state == b.state
    assert (ta.market_metrics or {}).get("playbook_v3", {}).get("token") == "XUSDT"


def test_rbr_alert_uses_playbook() -> None:
    from bot.range_breakdown_retest import rbr_alert_eligible

    ta = _ta(
        current_price=1.06,
        market_metrics={
            "range_breakdown_retest": {
                "phase": "await_break",
                "direction": "short",
                "range_top": 1.1,
                "range_bottom": 0.95,
                "entry_lo": 1.04,
                "entry_hi": 1.08,
                "stop": 1.12,
                "targets": [0.85],
            }
        },
    )
    assert rbr_alert_eligible(ta)
