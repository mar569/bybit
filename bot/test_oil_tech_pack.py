"""Tech pack when no strong news."""
from __future__ import annotations

from types import SimpleNamespace

from bot.oil_confluence import score_oil_tech_pack


def test_tech_pack_elliott_and_triangle():
    ta = SimpleNamespace(
        elliott_entry_ready=True,
        elliott_confidence=8,
        elliott_label="W2 retrace",
        elliott_path_bias="long",
        elliott_entry_mode="long",
        elliott_entry_price=80.1,
        elliott_stop_price=79.4,
        elliott_tp_prices=[81.0, 82.0],
        wave_bias="long",
        wave_confidence=7,
        elliott_triangle_kind="contracting",
        elliott_triangle_bias="bullish",
        chart_patterns=[],
        primary_chart_pattern=None,
        phase_label="Импульс вверх",
        structure_label="бычий каркас",
        channel=None,
    )
    lo, sh, factors, levels = score_oil_tech_pack(ta, px=80.0, full_weight=True)
    assert lo > sh
    assert lo >= 4
    assert any("EW" in f or "Треугольник" in f for f in factors)
    assert levels["entry"] == 80.1
    assert levels["stop"] == 79.4


def test_tech_pack_lighter_when_news_hot():
    ta = SimpleNamespace(
        elliott_entry_ready=True,
        elliott_confidence=8,
        elliott_label="x",
        elliott_path_bias="short",
        elliott_entry_mode="short",
        elliott_entry_price=80.0,
        elliott_stop_price=80.5,
        elliott_tp_prices=[79.0],
        wave_bias="short",
        wave_confidence=6,
        elliott_triangle_kind="",
        elliott_triangle_bias="",
        chart_patterns=[],
        primary_chart_pattern=None,
        phase_label="",
        structure_label="",
        channel=None,
    )
    full = score_oil_tech_pack(ta, px=80.0, full_weight=True)
    light = score_oil_tech_pack(ta, px=80.0, full_weight=False)
    assert full[1] >= light[1]  # short pts
