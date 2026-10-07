from __future__ import annotations

from bot.signal_locale import (
    polish_user_copy,
    quality_tier_html,
    side_ru,
    verdict_ru,
)


def test_quality_tier_russian() -> None:
    assert "ENTRY" not in quality_tier_html("entry")
    assert "наблюд" in quality_tier_html("watch").lower()


def test_polish_strips_jargon() -> None:
    raw = "👀 <b>WATCH</b> · setup B · HTF ok · SL 1.2"
    out = polish_user_copy(raw)
    assert "WATCH" not in out
    assert "ENTRY" not in out
    assert "стоп" in out.lower() or "SL" not in out


def test_side_verdict_ru() -> None:
    assert side_ru("long", cap=True) == "Лонг"
    assert verdict_ru("WAIT") == "Ждать"
