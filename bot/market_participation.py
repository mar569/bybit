from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def signal_market_metrics(signal: Any) -> dict[str, Any]:
    details = getattr(signal, "details", None)
    details = details if isinstance(details, Mapping) else {}
    return {
        "source": str(getattr(signal, "exchange", "") or "Bybit"),
        "price_change_pct": getattr(signal, "price_change_percent", None),
        "oi_change_pct": getattr(signal, "oi_change_percent", None),
        "oi_period_minutes": getattr(signal, "oi_period_minutes", None),
        "funding_rate": getattr(signal, "funding_rate", None),
        "account_ratio": details.get("account_ratio"),
        "liquidations": details.get("liquidations"),
        "cvd_ratio": details.get("cvd_ratio"),
    }


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def market_participation_lines(
    metrics: Mapping[str, Any] | None,
    *,
    cvd_ratio: float | None = None,
    volume_participation: str = "",
) -> list[str]:
    """Create short, source-aware participation notes from observed market data."""
    data = metrics or {}
    lines: list[str] = []
    source = str(data.get("source") or "Bybit")

    price_change = _number(data.get("price_change_pct"))
    oi_change = _number(data.get("oi_change_pct"))
    oi_period = int(_number(data.get("oi_period_minutes")) or 0)
    oi_line = ""
    if price_change is not None and oi_change is not None:
        period = f" за {oi_period}м" if oi_period > 0 else ""
        if price_change >= 0.2 and oi_change >= 0.2:
            relation = "рост поддержан набором позиций"
        elif price_change >= 0.2 and oi_change <= -0.2:
            relation = "рост на закрытии позиций — продолжение не подтверждено"
        elif price_change <= -0.2 and oi_change >= 0.2:
            relation = "падение с набором позиций — давление продавцов"
        elif price_change <= -0.2 and oi_change <= -0.2:
            relation = "падение на закрытии позиций — возможен сброс плеча"
        else:
            relation = "цена и OI без явного совместного импульса"
        oi_line = f"Цена {price_change:+.1f}% / OI {oi_change:+.1f}%{period}: {relation}"

    participation: list[str] = []
    funding = _number(data.get("funding_rate"))
    if funding is not None:
        funding_pct = funding * 100.0
        if funding_pct >= 0.05:
            state = "лонги перегреты"
        elif funding_pct <= -0.05:
            state = "шорты перегреты"
        elif funding_pct > 0.01:
            state = "лонги платят"
        elif funding_pct < -0.01:
            state = "шорты платят"
        else:
            state = "нейтральный"
        participation.append(f"Funding {funding_pct:+.3f}% ({state})")

    account_ratio = data.get("account_ratio")
    if isinstance(account_ratio, Mapping):
        ratio = _number(account_ratio.get("long_short_ratio"))
        if ratio is not None and ratio > 0:
            if ratio >= 1.3:
                state = "перекос в лонг"
            elif ratio <= 0.77:
                state = "перекос в шорт"
            else:
                state = "без сильного перекоса"
            period = str(account_ratio.get("period") or "")
            period_label = f" {period}" if period else ""
            participation.append(f"L/S{period_label} {ratio:.2f} ({state})")
    if participation:
        lines.append(" · ".join(participation))

    liquidations = data.get("liquidations")
    if isinstance(liquidations, Mapping):
        long_liq = _number(liquidations.get("long_liq_usd"))
        short_liq = _number(liquidations.get("short_liq_usd"))
        if long_liq is not None and short_liq is not None and long_liq + short_liq > 0:
            if long_liq >= short_liq * 1.25:
                state = "преобладают ликвидации лонгов"
            elif short_liq >= long_liq * 1.25:
                state = "преобладают ликвидации шортов"
            else:
                state = "потоки смешанные"
            window = int(_number(liquidations.get("window_minutes")) or 15)
            lines.append(
                f"Ликвидации {window}м: L ${long_liq:,.0f} / S ${short_liq:,.0f} ({state})"
                .replace(",", " ")
            )

    if cvd_ratio is None:
        cvd_ratio = _number(data.get("cvd_ratio"))
    flow: list[str] = []
    if cvd_ratio is not None:
        if cvd_ratio >= 0.58:
            state = "агрессивные покупки"
        elif cvd_ratio <= 0.42:
            state = "агрессивные продажи"
        else:
            state = "баланс taker-потока"
        flow.append(f"CVD buy {cvd_ratio:.0%} ({state})")
    for market, label in (("futures", "фьючерсы"), ("spot", "спот")):
        ratio = _number(data.get(f"{market}_taker_buy_ratio"))
        buy = _number(data.get(f"{market}_taker_buy_vol_usd"))
        sell = _number(data.get(f"{market}_taker_sell_vol_usd"))
        window = int(_number(data.get(f"{market}_taker_window_minutes")) or 0)
        if ratio is not None:
            total = buy + sell if buy is not None and sell is not None else None
            volume = f" · ${total:,.0f}" if total is not None else ""
            duration = f"/{window}м" if window else ""
            flow.append(f"{label} taker{duration} buy {ratio:.0%}{volume}".replace(",", " "))
    if not flow:
        taker_ratio = _number(data.get("taker_buy_ratio"))
        if taker_ratio is not None:
            flow.append(f"futures taker now buy {taker_ratio:.0%}")
    if flow:
        lines.append("Поток: " + " · ".join(flow))

    if volume_participation:
        lines.append(volume_participation)

    status = str(data.get("coinglass_status") or "")
    if status and status != "данные получены":
        if status == "COINGLASS_API_KEY не настроен":
            status = "API key не настроен"
        missing = data.get("coinglass_missing_metrics")
        if isinstance(missing, list) and missing:
            labels = {
                "price": "цена",
                "oi": "OI",
                "funding": "funding",
                "account_ratio": "L/S",
                "liquidations": "liq",
                "futures_taker_history": "futures taker history",
                "spot_taker_history": "spot taker history",
                "taker": "taker",
            }
            absent = ", ".join(labels.get(str(item), str(item)) for item in missing)
            lines.append(f"CoinGlass: {status} · нет {absent}")
        else:
            lines.append(f"CoinGlass: {status}")

    if lines:
        lines.insert(0, f"Деривативы/поток · {source}")
        if oi_line:
            lines.insert(1, oi_line)
    return lines[:7]


def coinglass_breakdown_html(
    ta: Any,
    *,
    symbol: str = "",
    working_tf: str = "",
) -> str:
    """Отдельный компактный разбор деривативов/потока для follow-up сообщения.

    Берёт уже посчитанные ``market_participation_lines`` из TA (они собраны из
    CoinGlass V4 + Bybit fallback). Возвращает Telegram-HTML блок или пустую
    строку, если данных нет — тогда сигнал остаётся без второго сообщения.
    """
    from html import escape

    raw_lines = getattr(ta, "market_participation_lines", None) or []
    lines = [str(line).strip() for line in raw_lines if str(line).strip()]
    if not lines:
        metrics = getattr(ta, "market_metrics", None)
        if isinstance(metrics, Mapping):
            lines = market_participation_lines(metrics)
    if not lines:
        return ""

    title = "📊 <b>Coinglass-разбор"
    if symbol:
        title += f" · {escape(str(symbol).upper())}"
    if working_tf:
        title += f" · раб. {escape(str(working_tf))}"
    title += "</b>"
    body = "\n".join(escape(line) for line in lines[:7])
    return f"{title}\n{body}"
