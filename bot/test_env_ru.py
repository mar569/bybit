import os

from bot.env_ru import apply_env_ru_aliases


def test_ru_telegram_token_maps_to_en(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("ТЕЛЕГРАМ_ТОКЕН", "test-token-123")
    apply_env_ru_aliases()
    assert os.environ.get("TELEGRAM_TOKEN") == "test-token-123"


def test_en_telegram_token_not_overwritten_by_ru(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "latin")
    monkeypatch.setenv("ТЕЛЕГРАМ_ТОКЕН", "cyrillic")
    apply_env_ru_aliases()
    assert os.environ.get("TELEGRAM_TOKEN") == "latin"
