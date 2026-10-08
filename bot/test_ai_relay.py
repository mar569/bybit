from __future__ import annotations

from bot.ai_analyst import (
    DEFAULT_RELAY_BASE_URL,
    DEFAULT_RELAY_MODEL,
    _env_relay_base_url,
    _env_relay_model,
    ai_provider_hint,
    fallback_llm_configured,
    relay_configured,
)


def test_relay_env_defaults(monkeypatch) -> None:
    monkeypatch.delenv("RELAY_MODEL", raising=False)
    monkeypatch.delenv("RELAY_BASE_URL", raising=False)
    assert _env_relay_model() == DEFAULT_RELAY_MODEL
    assert _env_relay_base_url() == DEFAULT_RELAY_BASE_URL


def test_fallback_llm_includes_relay(monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("RELAY_API_KEY", "rk-test")
    assert relay_configured()
    assert fallback_llm_configured()
    assert "Relay" in ai_provider_hint()


def test_ai_provider_hint_all_three(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setenv("RELAY_API_KEY", "r")
    hint = ai_provider_hint()
    assert "Gemini" in hint
    assert "Groq" in hint
    assert "Relay" in hint
