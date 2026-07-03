from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SET_HELP = (
    "<b>Команда /set</b> (применяется сразу)\n\n"
    "Глобальные:\n"
    "/set period 15\n"
    "/set oi 5\n"
    "/set oi_drop 5\n"
    "/set price 1\n"
    "/set price_drop 1\n"
    "/set min_oi 100000\n"
    "/set min_volume 0\n"
    "/set top 50 — топ монет по объёму (0 = все)\n"
    "/set cooldown 60\n"
    "/set score 1 — мин. сила сигнала\n"
    "/set signals off — остановить уведомления\n"
    "/set signals on — возобновить уведомления\n\n"
    "По биржам (переопределяют глобальные):\n"
    "/set binance oi 3\n"
    "/set binance period 10\n"
    "/set binance price 2\n"
    "/set bybit oi_drop 4\n"
    "/set bybit price_drop 1.5\n"
    "/set binance reset — сбросить свои пороги"
)


GLOBAL_ALIASES: dict[str, str] = {
    "period": "oi_period_minutes",
    "oi": "oi_rise_percent",
    "oi_rise": "oi_rise_percent",
    "oi_drop": "oi_drop_percent",
    "price": "price_rise_percent",
    "price_rise": "price_rise_percent",
    "price_drop": "price_drop_percent",
    "min_oi": "min_open_interest",
    "min_volume": "min_volume",
    "top": "top_n_symbols",
    "topn": "top_n_symbols",
    "cooldown": "signal_cooldown_seconds",
    "score": "min_signal_score",
    "priority": "priority_score_max",
    "signals": "signals_enabled",
}

EXCHANGE_ALIASES: dict[str, str] = {
    "period": "oi_period_minutes",
    "oi": "oi_rise_percent",
    "oi_rise": "oi_rise_percent",
    "oi_drop": "oi_drop_percent",
    "price": "price_rise_percent",
    "price_rise": "price_rise_percent",
    "price_drop": "price_drop_percent",
}

INT_FIELDS = {
    "oi_period_minutes",
    "signal_cooldown_seconds",
    "top_n_symbols",
    "priority_score_max",
    "binance_oi_period_minutes",
    "bybit_oi_period_minutes",
}

FLOAT_FIELDS = {
    "oi_rise_percent",
    "oi_drop_percent",
    "price_rise_percent",
    "price_drop_percent",
    "min_open_interest",
    "min_volume",
    "min_signal_score",
    "binance_oi_rise_percent",
    "binance_oi_drop_percent",
    "binance_price_rise_percent",
    "binance_price_drop_percent",
    "bybit_oi_rise_percent",
    "bybit_oi_drop_percent",
    "bybit_price_rise_percent",
    "bybit_price_drop_percent",
}


@dataclass
class SetResult:
    ok: bool
    message: str
    updates: dict[str, Any]


def parse_set_command(args: list[str]) -> SetResult:
    if not args:
        return SetResult(False, SET_HELP, {})

    if args[0].lower() in {"help", "?"}:
        return SetResult(True, SET_HELP, {})

    exchange = None
    if args[0].lower() in {"binance", "bybit"}:
        exchange = args[0].lower()
        args = args[1:]
        if not args:
            return SetResult(False, "Укажите параметр. Пример: /set binance oi 5", {})

        if args[0].lower() == "reset":
            prefix = f"{exchange}_"
            resets = {
                f"{prefix}oi_period_minutes": None,
                f"{prefix}oi_rise_percent": None,
                f"{prefix}oi_drop_percent": None,
                f"{prefix}price_rise_percent": None,
                f"{prefix}price_drop_percent": None,
            }
            return SetResult(True, f"✅ Пороги {exchange.capitalize()} сброшены к глобальным.", resets)

    if len(args) < 2:
        return SetResult(False, "Формат: /set <параметр> <значение>", {})

    key_alias = args[0].lower()
    raw_value = args[1]

    try:
        if exchange:
            field_key = EXCHANGE_ALIASES.get(key_alias)
            if not field_key:
                return SetResult(False, f"Неизвестный параметр биржи: {key_alias}", {})
            field = f"{exchange}_{field_key}"
        else:
            field = GLOBAL_ALIASES.get(key_alias)
            if not field:
                return SetResult(False, f"Неизвестный параметр: {key_alias}. /set help", {})

        if field == "top_n_symbols":
            value: Any = int(float(raw_value))
            if value <= 0:
                value = None
        elif field == "signals_enabled":
            value = raw_value.lower() in {"1", "on", "true", "yes", "вкл", "start", "resume"}
        elif field in INT_FIELDS or field.endswith("_oi_period_minutes"):
            value = int(float(raw_value))
            if field == "oi_period_minutes" and not 1 <= value <= 30:
                return SetResult(False, "Период: от 1 до 30 минут.", {})
        elif field in FLOAT_FIELDS or field.endswith("_percent"):
            value = float(raw_value)
            if value < 0:
                return SetResult(False, "Значение не может быть отрицательным.", {})
        else:
            value = float(raw_value)
    except ValueError:
        return SetResult(False, f"Некорректное значение: {raw_value}", {})

    label = field.replace("_", " ")
    display = "все монеты" if field == "top_n_symbols" and value is None else value
    return SetResult(True, f"✅ {label} → <b>{display}</b>", {field: value})
