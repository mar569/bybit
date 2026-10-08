from __future__ import annotations

from bot.ai_providers import parse_ai_provider_order, text_ai_available


def test_parse_order_default(monkeypatch) -> None:
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    assert parse_ai_provider_order() == ("gemini", "relay", "groq")


def test_parse_order_relay_first(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER_ORDER", "relay,gemini")
    assert parse_ai_provider_order() == ("relay", "gemini")


def test_text_ai_available_relay_only(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("RELAY_API_KEY", "rk-test")
    assert text_ai_available(None)
    assert text_ai_available("")
