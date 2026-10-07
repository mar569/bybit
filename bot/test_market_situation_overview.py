from __future__ import annotations

import time

from bot.market_situation_overview import build_situation_overview_html, group_snapshots_by_situation
from bot.market_state_store import CachedMarketSnapshot


def _snap(sym: str, kind: str, *, verdict: str = "WAIT") -> CachedMarketSnapshot:
    return CachedMarketSnapshot(
        symbol=sym,
        updated_at=time.time(),
        verdict=verdict,
        scenario_id="",
        scenario_action="",
        scenario_quality="",
        methodology_grade="",
        human_brief="",
        verdict_scenario_note="",
        situation_kind=kind,
        situational_snippet=f"тест {kind}",
        current_price=1.0,
    )


def test_groups_by_situation_kind() -> None:
    snaps = [
        _snap("AAAUSDT", "inside_consolidation"),
        _snap("BBBUSDT", "inside_consolidation"),
        _snap("CCCUSDT", "htf_resistance_short", verdict="SHORT"),
    ]
    buckets = group_snapshots_by_situation(snaps)
    assert len(buckets["inside_consolidation"]) == 2
    assert buckets["htf_resistance_short"][0].symbol == "CCCUSDT"


def test_overview_html_lists_buckets() -> None:
    html = build_situation_overview_html(
        [
            _snap("AVAAIUSDT", "htf_resistance_short"),
            _snap("AAVEUSDT", "inside_consolidation"),
        ],
        max_per_kind=3,
    )
    assert "Supply" in html or "htf" in html.lower() or "шорт" in html
    assert "AVAAI" in html
    assert "консолидации" in html.lower() or "Консолидации" in html
