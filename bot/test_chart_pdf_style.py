from __future__ import annotations

from dataclasses import dataclass

from bot.chart_pdf_style import resolve_pdf_chart_mode, sanitize_rbr_for_pdf
from bot.ta_analysis import TAAnalysisResult


@dataclass
class _Cons:
    top: float
    bottom: float


def test_sanitize_drops_synthetic_fade_top_mid_range() -> None:
    from bot.bybit_klines import KlineBar

    def bar(c: float) -> KlineBar:
        return KlineBar(open_time=1, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)

    ta = TAAnalysisResult(
        current_price=100.0,
        market_metrics={
            "range_breakdown_retest": {
                "synthetic": True,
                "phase": "fade_top",
                "range_bottom": 96.0,
                "range_top": 104.0,
                "direction": "short",
            }
        },
    )
    bars = [bar(100.0) for _ in range(20)]
    ta = sanitize_rbr_for_pdf(ta, bars)
    assert "range_breakdown_retest" not in (ta.market_metrics or {})


def test_resolve_range_when_consolidation() -> None:
    ta = TAAnalysisResult(
        consolidation=_Cons(top=105.0, bottom=98.0),  # type: ignore[arg-type]
        breakout_level=105.0,
        breakdown_level=98.0,
    )
    from bot.bybit_klines import KlineBar

    def bar(c: float) -> KlineBar:
        return KlineBar(open_time=1, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)

    bars = [bar(102.0) for _ in range(30)]
    assert resolve_pdf_chart_mode(ta, bars) == "range"
