from __future__ import annotations

from .market_state_store import MarketStateStore
from .ta_analysis import TAAnalysisResult


def test_cache_side_conflict() -> None:
    store = MarketStateStore(default_ttl_seconds=60.0)
    ta = TAAnalysisResult(
        verdict="WAIT",
        scenario_engine_action="SHORT",
        scenario_engine_quality="B",
        market_metrics={"symbol": "MONUSDT"},
    )
    store.put_from_ta("MONUSDT", ta)
    note = store.side_conflict_note("MONUSDT", "long")
    assert note and "SHORT" in note
