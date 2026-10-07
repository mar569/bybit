from __future__ import annotations

from dataclasses import dataclass

from .scenario_report import (
    ENTRY_B,
    ENTRY_C,
    PUMP_EVENT_TYPES,
    build_scenario_report,
    cap_quality_for_scanner_event,
)
from .ta_analysis import TAAnalysisResult


@dataclass
class _Sig:
    signal_type: str = "mega_pump"
    side: str = "long"


def test_cap_quality_pump_forces_watch_without_setup() -> None:
    ta = TAAnalysisResult(setup_grade="C", setup_score=4)
    assert cap_quality_for_scanner_event("mega_pump", "entry", ta) == "watch"
    assert cap_quality_for_scanner_event("mega_pump", "skip", ta) == "skip"


def test_cap_quality_pump_allows_entry_with_b_setup() -> None:
    ta = TAAnalysisResult(setup_grade="B", setup_score=8)
    assert cap_quality_for_scanner_event("mega_pump", "entry", ta) == "entry"


def test_build_scenario_report_reading_fields() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=6,
        reading_narrative="Цена у хая без объёма",
        reading_present=["слабый хай"],
        reading_absent=["подтверждённый пробой"],
        reading_seek_label="ждать откат к поддержке",
        market_participation_lines=["OI flat", "CVD weak"],
    )
    report = build_scenario_report(ta, symbol="ETHUSDT")
    html = report.to_html_compact()
    assert "Есть" in html
    assert "Нет" in html
    assert report.entry_mode == ENTRY_C
    assert "ETHUSDT" == report.symbol or report.symbol == "ETHUSDT"


def test_pump_event_types_nonempty() -> None:
    assert "mega_pump" in PUMP_EVENT_TYPES
    assert "trend_dump" in PUMP_EVENT_TYPES
