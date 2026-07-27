from __future__ import annotations

from bot.manual_ta import (
    build_mta_callback,
    build_mtw_callback,
    manual_ta_hours,
    normalize_symbol,
    parse_manual_ta_input,
    parse_mta_callback,
    parse_mtw_callback,
)


def test_normalize_symbol() -> None:
    assert normalize_symbol("grass") == "GRASSUSDT"
    assert normalize_symbol("BTCUSDT") == "BTCUSDT"


def test_parse_manual_ta_input_with_tf() -> None:
    symbol, interval = parse_manual_ta_input("GRASS 10m")
    assert symbol == "GRASSUSDT"
    assert interval == 10


def test_parse_manual_ta_input_symbol_only() -> None:
    symbol, interval = parse_manual_ta_input("CRWVUSDT")
    assert symbol == "CRWVUSDT"
    assert interval is None


def test_mta_callback_roundtrip() -> None:
    data = build_mta_callback("GRASSUSDT", 15)
    parsed = parse_mta_callback(data)
    assert parsed == ("GRASSUSDT", 15)


def test_mtw_callback_roundtrip() -> None:
    data = build_mtw_callback("BTCUSDT", 5)
    parsed = parse_mtw_callback(data)
    assert parsed == ("BTCUSDT", 5)


def test_manual_ta_hours() -> None:
    assert manual_ta_hours(5) == 18
    assert manual_ta_hours(15) == 24


def test_chart_display_default_12h() -> None:
    from bot.manual_ta import chart_display_hours, structure_aware_display_hours

    assert chart_display_hours(5) == 12
    assert structure_aware_display_hours(
        interval_minutes=5,
        analysis_hours=18,
        configured=12,
        drawdown_pct=70.0,
        elliott_span_bars=120,
    ) >= 12


def test_chart_height_scale_layout() -> None:
    from bot.chart_renderer import _chart_figure_layout, _resolve_chart_height_scale

    assert _resolve_chart_height_scale(1.3) == 1.3
    assert _resolve_chart_height_scale(2.0) == 1.6
    assert _resolve_chart_height_scale(0.5) == 0.85

    default_size, default_ratios = _chart_figure_layout(enhanced=True, pro_mode=False, height_scale=1.0)
    tall_size, tall_ratios = _chart_figure_layout(enhanced=True, pro_mode=False, height_scale=1.3)

    assert default_size == (19.2, 10.8)
    assert tall_size == (19.2, 10.8 * 1.3)
    assert default_ratios is not None and tall_ratios is not None
    assert tall_ratios[0] > default_ratios[0]
