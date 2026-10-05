"""Tech pack when no strong news."""
from __future__ import annotations

from types import SimpleNamespace

from bot.oil_confluence import score_oil_tech_pack


def test_tech_pack_fib_context():
    ta = SimpleNamespace(
        wave_has_confluence=True,
        wave_bias="long",
        wave_confidence=7,
        entry_zone=(80.0, 80.2),
        invalidation_price=79.4,
        target_prices=[81.0, 82.0],
        chart_patterns=[],
        primary_chart_pattern=None,
        phase_label="Импульс вверх",
        structure_label="бычий каркас",
        channel=None,
    )
    lo, sh, factors, levels = score_oil_tech_pack(ta, px=80.0, full_weight=True)
    assert lo > sh
    assert any("Fib confluence" in f for f in factors)
    assert levels["entry"] == 80.1
    assert levels["stop"] == 79.4


def test_tech_pack_lighter_when_news_hot():
    ta = SimpleNamespace(
        wave_has_confluence=True,
        wave_bias="short",
        wave_confidence=6,
        entry_zone=(79.9, 80.1),
        invalidation_price=80.5,
        target_prices=[79.0],
        chart_patterns=[],
        primary_chart_pattern=None,
        phase_label="",
        structure_label="",
        channel=None,
    )
    full = score_oil_tech_pack(ta, px=80.0, full_weight=True)
    light = score_oil_tech_pack(ta, px=80.0, full_weight=False)
    assert full[1] >= light[1]  # short pts
