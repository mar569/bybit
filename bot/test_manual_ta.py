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
    assert manual_ta_hours(5) == 5
    assert manual_ta_hours(15) == 10
