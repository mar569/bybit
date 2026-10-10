"""Русские имена переменных в .env → стандартные ключи для кода."""
from __future__ import annotations

import os

# Русское имя → имя в коде (латиница). Если заданы оба, приоритет у латиницы.
ENV_RU_TO_EN: dict[str, str] = {
    "ТЕЛЕГРАМ_ТОКЕН": "TELEGRAM_TOKEN",
    "ТЕЛЕГРАМ_ID_АДМИНА": "TELEGRAM_ADMIN_ID",
    "ТЕЛЕГРАМ_ЧАТ_АЛЕРТЫ": "TELEGRAM_ALERT_CHAT_ID",
    "ТЕЛЕГРАМ_ЧАТ_АНАЛИЗ": "TELEGRAM_ANALYSIS_CHAT_ID",
    "ТЕЛЕГРАМ_ЧАТ_РУЧНОЙ_TA": "TELEGRAM_MANUAL_TA_CHAT_ID",
    "ТЕЛЕГРАМ_ЧАТ_НЕФТЬ": "TELEGRAM_OIL_NEWS_CHAT_ID",
    "КЛЮЧ_GEMINI": "GEMINI_API_KEY",
    "МОДЕЛЬ_GEMINI": "GEMINI_MODEL",
    "КЛЮЧ_GROQ": "GROQ_API_KEY",
    "МОДЕЛЬ_GROQ": "GROQ_MODEL",
    "КЛЮЧ_RELAY": "RELAY_API_KEY",
    "МОДЕЛЬ_RELAY": "RELAY_MODEL",
    "АДРЕС_RELAY": "RELAY_BASE_URL",
    "ИИ_ПОРЯДОК": "AI_PROVIDER_ORDER",
    "КЛЮЧ_COINGLASS": "COINGLASS_API_KEY",
    "ПОТОК_РЫНКА": "MARKET_FLOW_PROVIDER",
    "ЧТЕНИЕ_РЫНКА_ПО_ДОКАЗАТЕЛЬСТВАМ": "EVIDENCE_READING_ENABLED",
    "КЛЮЧ_BINANCE": "BINANCE_API_KEY",
    "СЕКРЕТ_BINANCE": "BINANCE_API_SECRET",
    "КЛЮЧ_BYBIT": "BYBIT_API_KEY",
    "СЕКРЕТ_BYBIT": "BYBIT_API_SECRET",
    "ИНТЕРВАЛ_СКАНА_СЕК": "SCAN_INTERVAL_SECONDS",
    "ПАУЗА_СИГНАЛ_СЕК": "SIGNAL_COOLDOWN_SECONDS",
    "КЛЮЧ_HORMUZ": "HORMUZ_API_KEY",
    "КЛЮЧ_X": "X_BEARER_TOKEN",
    "АДРЕС_REDIS": "REDIS_URL",
    "HUMMINGBOT_API_ЛОГИН": "HUMMINGBOT_API_USERNAME",
    "HUMMINGBOT_API_ПАРОЛЬ": "HUMMINGBOT_API_PASSWORD",
    "АДРЕС_HUMMINGBOT_API": "HUMMINGBOT_API_URL",
    "ЛОГИН_HUMMINGBOT_API": "HUMMINGBOT_API_USERNAME",
    "ПАРОЛЬ_HUMMINGBOT_API": "HUMMINGBOT_API_PASSWORD",
    "MT5_ПУТЬ": "MT5_TERMINAL_PATH",
    "MT5_ЛОГИН": "MT5_LOGIN",
    "MT5_ПАРОЛЬ": "MT5_PASSWORD",
    "MT5_СЕРВЕР": "MT5_SERVER",
}


def apply_env_ru_aliases() -> None:
    for ru, en in ENV_RU_TO_EN.items():
        raw = os.environ.get(ru)
        if raw is None:
            continue
        if str(raw).strip() == "":
            continue
        if not os.environ.get(en):
            os.environ[en] = raw
