from __future__ import annotations

from bot.manual_ta import parse_user_trade_intent


def test_parse_user_trade_intent_short() -> None:
    assert parse_user_trade_intent("хочу шорт") == "short"
    assert parse_user_trade_intent("открыть SHORT") == "short"
    assert parse_user_trade_intent("шорт") == "short"


def test_parse_user_trade_intent_long() -> None:
    assert parse_user_trade_intent("хочу long") == "long"
    assert parse_user_trade_intent("открыть лонг") == "long"
    assert parse_user_trade_intent("long") == "long"


def test_parse_user_trade_intent_none_for_ticker() -> None:
    assert parse_user_trade_intent("GRASSUSDT 10m") is None
