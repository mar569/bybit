from __future__ import annotations

import os

from .minimal_ed_voice import build_minimal_ed_voice_html, build_minimal_ed_voice_plain
from .ta_analysis import TAAnalysisResult


def test_minimal_voice_no_prices():
    os.environ["ED_MINIMAL_VOICE"] = "1"
    ta = TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=7,
        action_priority="short",
        phase_label="боковик",
        breakout_level=0.0835,
        breakdown_level=0.076,
        current_price=0.080,
        reading_narrative="Цена 0.080 в середине диапазона 0.076–0.083.",
    )
    plain = build_minimal_ed_voice_plain(ta, symbol="SANDUSDT")
    assert "0.08" not in plain
    assert "SAND" in plain
    html = build_minimal_ed_voice_html(ta, symbol="SANDUSDT")
    assert "🧠" not in html
    assert "CVD" not in html
