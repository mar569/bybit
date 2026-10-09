from __future__ import annotations

from bot.living_analysis import build_living_analysis_html
from bot.ta_analysis import TAAnalysisResult, ta_signal_caption_html


def test_signal_caption_uses_playbook_when_v3() -> None:
    ta = TAAnalysisResult(
        verdict="WAIT",
        post_pump=True,
        momentum_label="импульс вверх",
        analysis_interval_minutes=5,
    )
    html = ta_signal_caption_html(ta, signal_side="long", symbol="BTCUSDT")
    assert html
    assert "INTEL" not in html
    assert "Слежу" in html or "Наблюдение" in html or "BTC" in html.upper()
    assert html == build_living_analysis_html(ta, symbol="BTCUSDT")
