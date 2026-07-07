from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SET_HELP = (
    "<b>Команда /set</b> (применяется сразу)\n\n"
    "Глобальные:\n"
    "/set period 15 — период LONG/SHORT (синхронно)\n"
    "/set long_period 15 — период только для LONG\n"
    "/set short_period 10 — период только для SHORT\n"
    "/set oi 5\n"
    "/set oi_drop 5\n"
    "/set price 1\n"
    "/set price_drop 1\n"
    "/set pulse_period 5 — ранний пульс (мин)\n"
    "/set pulse_oi 1 — OI% для пульса\n"
    "/set pulse_price 0.5 — цена% для пульса\n"
    "/set min_oi 100000 — мин. OI в USD\n"
    "/set min_oi_change 15000 — мин. приток OI в USD\n"
    "/set min_volume 0\n"
    "/set top 50 — топ монет по объёму (0 = все)\n"
    "/set cooldown 60\n"
    "/set mega_cooldown 45 — пауза между мега-сигналами\n"
    "/set prob 70 — мин. вероятность для уведомления\n"
    "/set prob_filter off — отключить фильтр вероятности\n"
    "/set score 1 — мин. сила сигнала\n"
    "/set signals off — остановить уведомления\n"
    "/set chart annotated — TA-график к сигналам (рекомендуется)\n"
    "/set chart on — включить графики к сигналам\n"
    "/set chart off — только текст без картинки\n"
    "/set manual_chart tv_annotated — ручной TA: TradingView + уровни\n"
    "/set manual_chart annotated — ручной TA: matplotlib\n"
    "Ликвидации (REKT-алерты в обычный чат):\n"
    "/set liq on — включить алерты по ликвидациям\n"
    "/set liq off — выключить\n"
    "/set liq_min 10000 — мин. сумма всплеска в USD\n"
    "/set liq_alt 10000 — порог для альтов (tier)\n"
    "/set liq_mid 10000 — порог для mid-cap (tier)\n"
    "/set liq_tier on — tier по OI (альт/mid/крупные)\n"
    "/set liq_window 2 — окно агрегации (сек)\n"
    "/set liq_cooldown 60 — пауза между алертами по монете\n"
    "/set liq_all on — все монеты Bybit (не только топ)\n\n"
    "Аналитический чат (разбор после ликвидаций):\n"
    "/set analysis on — включить умный разбор\n"
    "/set analysis off — выключить\n"
    "/set analysis_min 10000 — мин. кластер для анализа (USD)\n"
    "/set analysis_major 10000 — порог для мейджоров\n"
    "/set analysis_alt 10000 — порог для альтов\n"
    "/set analysis_oi 500000 — мин. OI монеты (сильные/объёмные)\n"
    "/set analysis_price 2 — мин. движение цены % от кластера\n"
    "/set analysis_trend 2 — мин. тренд 1h/4h %\n"
    "/set analysis_delay 90 — пауза перед разбором (сек)\n"
    "/set analysis_conf 48 — мин. уверенность %\n"
    "/set analysis_cd 3600 — cooldown по монете (сек)\n"
    "/set analysis_max_h 5 — макс. разборов в час\n\n"
    "По биржам (переопределяют глобальные):\n"
    "/set binance oi 3\n"
    "/set binance period 10\n"
    "/set binance long_period 15\n"
    "/set binance short_period 10\n"
    "/set binance price 2\n"
    "/set bybit oi_drop 4\n"
    "/set bybit price_drop 1.5\n"
    "/set binance reset — сбросить свои пороги"
)


GLOBAL_ALIASES: dict[str, str] = {
    "period": "oi_period_minutes",
    "long_period": "long_period_minutes",
    "short_period": "short_period_minutes",
    "oi": "oi_rise_percent",
    "oi_rise": "oi_rise_percent",
    "oi_drop": "oi_drop_percent",
    "price": "price_rise_percent",
    "price_rise": "price_rise_percent",
    "price_drop": "price_drop_percent",
    "pulse_period": "pulse_period_minutes",
    "pulse_oi": "pulse_oi_rise_percent",
    "pulse_oi_drop": "pulse_oi_drop_percent",
    "pulse_price": "pulse_price_rise_percent",
    "pulse_price_drop": "pulse_price_drop_percent",
    "min_oi": "min_open_interest",
    "min_oi_change": "min_oi_change_usd",
    "min_volume": "min_volume",
    "top": "top_n_symbols",
    "topn": "top_n_symbols",
    "cooldown": "signal_cooldown_seconds",
    "mega_cooldown": "mega_cooldown_seconds",
    "chart": "signal_chart_source",
    "chart_src": "signal_chart_source",
    "manual_chart": "manual_ta_chart_source",
    "manual_ta_chart": "manual_ta_chart_source",
    "compact": "signal_message_compact",
    "prob": "min_probability_percent",
    "prob_filter": "probability_filter_enabled",
    "actionable": "actionable_signals_only",
    "ready_entry": "actionable_signals_only",
    "actionable_ta": "actionable_min_ta_score",
    "actionable_dist": "actionable_max_trigger_dist_pct",
    "score": "min_signal_score",
    "priority": "priority_score_max",
    "signals": "signals_enabled",
    "liq": "liquidation_alerts_enabled",
    "liq_alerts": "liquidation_alerts_enabled",
    "liq_min": "liquidation_min_usd",
    "liq_alt": "liquidation_alt_min_usd",
    "liq_mid": "liquidation_mid_min_usd",
    "liq_tier": "liquidation_tier_enabled",
    "liq_window": "liquidation_burst_window_seconds",
    "liq_cooldown": "liquidation_cooldown_seconds",
    "liq_all": "liquidation_all_symbols",
    "liq_hint": "liquidation_show_reversal_hint",
    "analysis": "analysis_enabled",
    "analysis_min": "analysis_min_liq_usd",
    "analysis_major": "analysis_major_min_liq_usd",
    "analysis_alt": "analysis_alt_min_liq_usd",
    "analysis_oi": "analysis_min_oi_usd",
    "analysis_price": "analysis_min_price_move_pct",
    "analysis_trend": "analysis_min_trend_pct",
    "analysis_delay": "analysis_delay_seconds",
    "analysis_conf": "analysis_min_confidence",
    "analysis_cd": "analysis_cooldown_seconds",
    "analysis_max_h": "analysis_max_per_hour",
    "analysis_chart": "analysis_chart_enabled",
    "analysis_skip_alt": "analysis_skip_alt_tier",
    "analysis_signal": "analysis_signal_trigger_enabled",
}

EXCHANGE_ALIASES: dict[str, str] = {
    "period": "oi_period_minutes",
    "long_period": "long_period_minutes",
    "short_period": "short_period_minutes",
    "oi": "oi_rise_percent",
    "oi_rise": "oi_rise_percent",
    "oi_drop": "oi_drop_percent",
    "price": "price_rise_percent",
    "price_rise": "price_rise_percent",
    "price_drop": "price_drop_percent",
}

INT_FIELDS = {
    "oi_period_minutes",
    "long_period_minutes",
    "short_period_minutes",
    "pulse_period_minutes",
    "signal_cooldown_seconds",
    "mega_cooldown_seconds",
    "liquidation_cooldown_seconds",
    "analysis_delay_seconds",
    "analysis_cooldown_seconds",
    "analysis_max_per_hour",
    "top_n_symbols",
    "priority_score_max",
    "binance_oi_period_minutes",
    "binance_long_period_minutes",
    "binance_short_period_minutes",
    "bybit_oi_period_minutes",
    "bybit_long_period_minutes",
    "bybit_short_period_minutes",
}

FLOAT_FIELDS = {
    "oi_rise_percent",
    "oi_drop_percent",
    "price_rise_percent",
    "price_drop_percent",
    "pulse_oi_rise_percent",
    "pulse_oi_drop_percent",
    "pulse_price_rise_percent",
    "pulse_price_drop_percent",
    "min_open_interest",
    "min_oi_change_usd",
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
    "min_probability_percent",
    "liquidation_min_usd",
    "liquidation_alt_min_usd",
    "liquidation_mid_min_usd",
    "liquidation_burst_window_seconds",
    "analysis_min_liq_usd",
    "analysis_major_min_liq_usd",
    "analysis_alt_min_liq_usd",
    "analysis_min_oi_usd",
    "analysis_min_price_move_pct",
    "analysis_min_trend_pct",
    "analysis_min_confidence",
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
                f"{prefix}long_period_minutes": None,
                f"{prefix}short_period_minutes": None,
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

        if key_alias in {"chart", "chart_src"} and raw_value.lower() in {
            "on", "off", "1", "0", "true", "false", "yes", "no", "вкл", "выкл",
        }:
            field = "signal_chart_enabled"
            raw_value = raw_value.lower()

        if field == "top_n_symbols":
            value: Any = int(float(raw_value))
            if value <= 0:
                value = None
        elif field == "signals_enabled":
            value = raw_value.lower() in {"1", "on", "true", "yes", "вкл", "start", "resume"}
        elif field in {
            "liquidation_alerts_enabled",
            "liquidation_all_symbols",
            "liquidation_show_reversal_hint",
            "liquidation_tier_enabled",
            "analysis_enabled",
            "analysis_skip_alt_tier",
            "analysis_chart_enabled",
            "analysis_signal_trigger_enabled",
            "signal_chart_enabled",
        }:
            value = raw_value.lower() in {"1", "on", "true", "yes", "вкл"}
        elif field == "probability_filter_enabled":
            value = raw_value.lower() in {"1", "on", "true", "yes", "вкл"}
        elif field == "signal_message_compact":
            value = raw_value.lower() in {"1", "on", "true", "yes", "вкл"}
        elif field == "signal_chart_source":
            value = raw_value.lower()
            if value not in {"tradingview", "coinglass", "generated", "annotated"}:
                return SetResult(
                    False,
                    "График: <code>annotated</code> | <code>tradingview</code> | "
                    "<code>coinglass</code> | <code>generated</code>",
                    {},
                )
        elif field == "manual_ta_chart_source":
            value = raw_value.lower()
            if value not in {"tv_annotated", "tradingview", "annotated"}:
                return SetResult(
                    False,
                    "Ручной TA: <code>tv_annotated</code> | <code>tradingview</code> | "
                    "<code>annotated</code>",
                    {},
                )
        elif field in INT_FIELDS or field.endswith("_period_minutes"):
            value = int(float(raw_value))
            if field in {"oi_period_minutes", "long_period_minutes", "short_period_minutes", "pulse_period_minutes"}:
                if not 1 <= value <= 60:
                    return SetResult(False, "Период: от 1 до 60 минут.", {})
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
    updates = {field: value}
    if field == "oi_period_minutes":
        updates["long_period_minutes"] = value
        updates["short_period_minutes"] = value
        label = "период long/short"
    return SetResult(True, f"✅ {label} → <b>{display}</b>", updates)
