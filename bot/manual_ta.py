"""Ручной TA-анализ: парсинг тикера и таймфрейма для отдельного чата."""
from __future__ import annotations

import re

MANUAL_TA_TIMEFRAMES: tuple[int, ...] = (5, 10, 15, 60)
MTA_CALLBACK_PREFIX = "mta|"
MTW_CALLBACK_PREFIX = "mtw|"
MTW_CANCEL_CALLBACK = "mtw|cancel|0"
MTA_WIZARD_KEY = "mta_wizard"


def normalize_symbol(raw: str) -> str | None:
    text = raw.strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    if not text:
        return None
    if text.endswith("USDT"):
        base = text[:-4]
    elif text.endswith("USD"):
        base = text[:-3]
    else:
        base = text
    base = re.sub(r"[^A-Z0-9]", "", base)
    if len(base) < 2 or len(base) > 20:
        return None
    return f"{base}USDT"


def parse_manual_ta_input(text: str) -> tuple[str | None, int | None]:
    """Возвращает (symbol, interval_minutes или None)."""
    if not text:
        return None, None
    cleaned = text.strip()
    interval: int | None = None
    tf_match = re.search(
        r"\b(5|10|15|60)\s*m(?:in(?:ute)?s?)?\b|(?:^|\s)(1)\s*h(?:our)?s?\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if tf_match:
        interval = 60 if tf_match.group(2) else int(tf_match.group(1))
        cleaned = cleaned[: tf_match.start()] + cleaned[tf_match.end() :]
    cleaned = cleaned.strip(" ,.;:")
    if not cleaned:
        return None, interval
    token_match = re.search(r"([A-Za-z0-9]{2,20}(?:USDT|USD)?)", cleaned.replace("/", ""))
    if not token_match:
        return None, interval
    return normalize_symbol(token_match.group(1)), interval


def manual_ta_hours(interval_minutes: int) -> int:
    return {5: 5, 10: 8, 15: 10, 60: 48}.get(interval_minutes, 5)


def bars_per_hour(interval_minutes: int) -> int:
    return max(1, 60 // interval_minutes)


def build_mta_callback(symbol: str, interval_minutes: int) -> str:
    return f"{MTA_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}"


def build_mtw_callback(symbol: str, interval_minutes: int) -> str:
    return f"{MTW_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}"


def _parse_mta_style_callback(data: str, prefix: str) -> tuple[str, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data.split("|")
    if len(parts) != 3 or parts[0] != prefix.rstrip("|"):
        return None
    try:
        interval = int(parts[2])
    except ValueError:
        return None
    if interval not in MANUAL_TA_TIMEFRAMES:
        return None
    symbol = normalize_symbol(parts[1])
    if not symbol:
        return None
    return symbol, interval


def parse_mta_callback(data: str) -> tuple[str, int] | None:
    return _parse_mta_style_callback(data, MTA_CALLBACK_PREFIX)


def parse_mtw_callback(data: str) -> tuple[str, int] | None:
    return _parse_mta_style_callback(data, MTW_CALLBACK_PREFIX)


def manual_ta_help_text() -> str:
    return (
        "<b>📐 Чат ручного TA-анализа</b>\n\n"
        "Отправьте <b>скрин</b> с подписью тикера или просто текст:\n"
        "• <code>GRASSUSDT</code>\n"
        "• <code>GRASS 10m</code>\n"
        "• <code>BTC 15m</code>\n\n"
        "Если таймфрейм не указан — выберите кнопку:\n"
        "<b>5m</b> · <b>10m</b> · <b>15m</b>\n\n"
        "Бот построит график с уровнями, каналом, сценариями и планом "
        "по живым данным Bybit."
    )


def manual_ta_wizard_start_text() -> str:
    return (
        "<b>📐 Ручной анализ</b>\n\n"
        "Отправьте данные <b>в любом порядке</b>:\n\n"
        "1️⃣ <b>Одним сообщением</b> — фото + подпись:\n"
        "   <code>GRASS 10m</code> или <code>BTCUSDT 15m</code>\n\n"
        "2️⃣ <b>Два шага</b> — сначала скрин, потом тикер текстом\n"
        "   (или наоборот: тикер → скрин)\n\n"
        "3️⃣ Если TF не указан — выберите кнопку <b>5m / 10m / 15m</b>\n\n"
        "Готовый разбор с графиком уйдёт в <b>чат ручного TA</b>.\n"
        "На графике: уровни, тренд, боковик, стрелки пробоя, сценарии.\n\n"
        "Отмена: <code>/cancel</code>"
    )
