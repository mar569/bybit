from __future__ import annotations

from dataclasses import dataclass

from .chart_story_router import build_synthetic_rbr_dict, enrich_ta_for_chart_story, use_story_chart
from .living_analysis import build_living_analysis_html
from .ta_analysis import TAAnalysisResult


@dataclass
class _Cons:
    top: float
    bottom: float


def test_synthetic_rbr_enables_story_chart() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        action_priority="long",
        post_pump=True,
        current_price=103.97,
        breakout_level=104.5,
        breakdown_level=99.2,
        consolidation=_Cons(top=104.45, bottom=99.15),  # type: ignore[arg-type]
        target_prices=[101.2, 99.5],
        invalidation_price=105.5,
        market_participation_lines=["", "CVD buy 70%"],
    )
    synth = build_synthetic_rbr_dict(ta, bars=None)
    assert synth and synth["phase"] == "fade_top"
    enrich_ta_for_chart_story(ta, None)
    assert use_story_chart(ta)
    assert ta.action_priority == "short"


def test_living_general_wait_has_candles_and_rules() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=6,
        action_priority="long",
        breakout_level=0.0835,
        breakdown_level=0.076,
        reading_narrative="Цена в середине диапазона, ждём пробой.",
        recent_price_action_ru="Последние свечи: зелёная → красная. Close 0.080.",
    )
    html = build_living_analysis_html(ta, symbol="SANDUSDT")
    assert "🕯" in html
    assert "🟡" in html
    assert "📍 План" not in html


def test_living_html_oil_style_not_long_chase() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=6,
        action_priority="short",
        post_pump=True,
        current_price=103.97,
        drawdown_from_high_pct=0.5,
        market_metrics={
            "range_breakdown_retest": {
                "direction": "short",
                "phase": "fade_top",
                "range_top": 104.45,
                "range_bottom": 99.15,
                "entry_lo": 102.5,
                "entry_hi": 104.2,
                "stop": 105.51,
                "targets": [101.24, 99.2],
                "label_ru": "шорт от верха боковика",
                "story_ru": "После импульса консолидация; шорт у потолка.",
            }
        },
    )
    html = build_living_analysis_html(ta, symbol="BZUSDT")
    assert "шорт" in html.lower()
    assert "не market" in html.lower()
    assert "уклон в лонг" not in html.lower()
    assert "🟢" in html
