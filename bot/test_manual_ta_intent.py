from __future__ import annotations

from bot.manual_ta import parse_mta_mute_callback, parse_user_trade_intent
from bot.ta_analysis import (
    ForecastPath,
    TAAnalysisResult,
    ta_user_intent_html,
)


def test_parse_user_trade_intent_short() -> None:
    assert parse_user_trade_intent("хочу шорт") == "short"
    assert parse_user_trade_intent("открыть SHORT") == "short"
    assert parse_user_trade_intent("шорт") == "short"


def test_parse_user_trade_intent_long() -> None:
    assert parse_user_trade_intent("хочу long") == "long"
    assert parse_user_trade_intent("открыть лонг") == "long"
    assert parse_user_trade_intent("long") == "long"


def test_parse_mta_mute_callback() -> None:
    assert parse_mta_mute_callback("mtam|BLURUSDT|10|mute") == ("BLURUSDT", 10, "mute")
    assert parse_mta_mute_callback("mtam|BTC|5|stop") == ("BTCUSDT", 5, "stop")

    assert parse_user_trade_intent("GRASSUSDT 10m") is None


def _post_pump_wait_ta(*, price: float, breakout: float) -> TAAnalysisResult:
    return TAAnalysisResult(
        verdict="WAIT",
        verdict_confidence=6,
        setup_clarity=10,
        current_price=price,
        breakout_level=breakout,
        breakdown_level=2.8738,
        post_pump=True,
        action_priority="short",
        flow_correction=55,
        flow_continuation=30,
        correction_path=ForecastPath(
            kind="correction",
            confidence=7,
            waypoints=[price, price * 0.97, price * 0.94],
            label="откат",
            reason="test",
        ),
        continuation_path=ForecastPath(
            kind="continuation",
            confidence=4,
            waypoints=[price, price * 1.02],
            label="продолжение",
            reason="test",
        ),
        nearest_resistance=breakout * 1.02 if breakout else None,
        nearest_support=2.87,
        range_position=0.92,
        drawdown_from_high_pct=1.5,
    )


def test_intent_long_says_level_taken_when_price_above_sticky() -> None:
    """Как EVAA: раньше вход ≥3.0172, цена уже 3.07 — не «ещё 0.3% до нового хая»."""
    ta = _post_pump_wait_ta(price=3.0751, breakout=3.0849)
    html = ta_user_intent_html(ta, "long", sticky_breakout=3.0172)
    low = html.lower()
    assert "уже" in low and ("взят" in low or "пройден" in low)
    assert "3.0172" in html or "3,0172" in html
    assert "ещё ~0.3%" not in low
    assert "закрепление 5m ≥" not in html or "уже" in low


def test_intent_long_below_level_still_asks_breakout() -> None:
    ta = _post_pump_wait_ta(price=3.0085, breakout=3.0172)
    html = ta_user_intent_html(ta, "long", sticky_breakout=3.0172)
    assert "≥" in html
    assert "3.0172" in html or "3,0172" in html
    assert "против" in html.lower() or "риска" in html.lower() or "хае" in html.lower()
