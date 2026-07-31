"""Профессиональный прогноз UKOUSD: правила (новости+TA) + опционально Gemini."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Sequence

from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

# Сценарии рынка нефти (фундамент → bias)
_SCENARIO_LABEL = {
    "deal_tape": "Deal-tape (Ормуз/перемирие → снятие premium)",
    "disruption": "Disruption (блок/атаки → geo-premium)",
    "inventory": "Inventory (EIA/SPR → спрос/предложение)",
    "opec_supply": "OPEC/supply (квоты/добыча)",
    "range": "Range (нет сильного катализатора)",
    "mixed_geo": "Mixed geo (сигналы в обе стороны)",
}


@dataclass(frozen=True)
class OilForecast:
    """Торговый прогноз для Новостника / дайджеста — не банковский STEO."""

    bias: str  # LONG | SHORT | WAIT
    scenario: str
    confidence: int  # 1–10
    horizon_ru: str
    headline_ru: str
    base_case_ru: str
    alt_case_ru: str
    invalidation_ru: str
    watch_list_ru: tuple[str, ...]
    entry_hint_ru: str
    gemini_ru: str = ""
    source: str = "rules"  # rules | rules+gemini


def detect_oil_scenario(news_items: Sequence[Any] | None) -> str:
    """Главный фундаментальный режим по свежим заголовкам."""
    items = list(news_items or [])
    if not items:
        return "range"

    deal = disrupt = inv = opec = 0
    for it in items:
        low = f"{getattr(it, 'title', '')} {getattr(it, 'theme', '')}".lower()
        if any(k in low for k in ("hormuz", "ормуз", "iran", "иран")):
            if any(
                k in low
                for k in (
                    "deal", "reopen", "ceasefire", "mou", "accord", "diplom",
                    "сделк", "перемир", "открыт", "unwind", "premium",
                )
            ):
                deal += 2
            elif any(
                k in low
                for k in (
                    "block", "attack", "strike", "close", "halt", "impasse",
                    "атак", "блок", "закрыт", "удар",
                )
            ):
                disrupt += 2
            else:
                disrupt += 1
                deal += 1
        if any(k in low for k in ("eia", "inventory", "запас", "spr", "steo")):
            inv += 2
        if any(k in low for k in ("opec", "опек", "quota", "квот")):
            opec += 2
        theme = getattr(it, "theme", "") or ""
        if theme == "analyst" and any(
            k in low for k in ("cut", "slash", "lower", "сниж", "прогноз")
        ):
            # Банки/EIA режут прогноз → чаще deal/oversupply контекст
            deal += 1

    scores = {
        "deal_tape": deal,
        "disruption": disrupt,
        "inventory": inv,
        "opec_supply": opec,
    }
    best = max(scores, key=scores.get)
    if scores[best] <= 0:
        return "range"
    if deal > 0 and disrupt > 0 and abs(deal - disrupt) <= 1:
        return "mixed_geo"
    return best


def build_oil_forecast(
    snap: Any,
    ta: TAAnalysisResult | None,
    *,
    news_bias: Any | None = None,
    news_items: Sequence[Any] | None = None,
    bounce_plan: Any | None = None,
    scalp_call: Any | None = None,
    market_mood: str = "",
    interval_minutes: int = 15,
    ta_verdict_raw: str | None = None,
    ta_confidence_raw: int | None = None,
) -> OilForecast:
    """Склеивает новости + уровни + scalp → один торговый прогноз."""
    px = float(getattr(snap, "price", 0) or 0)
    s = getattr(snap, "support", None)
    r = getattr(snap, "resistance", None)
    bd = getattr(snap, "breakdown", None)
    bo = getattr(snap, "breakout", None)
    ta_v = (ta_verdict_raw or getattr(snap, "verdict", None) or "WAIT").upper()
    ta_c = int(
        ta_confidence_raw
        if ta_confidence_raw is not None
        else (getattr(snap, "confidence", 0) or 0)
    )
    news = getattr(news_bias, "bias", "neutral") if news_bias else "neutral"
    news_w = float(getattr(news_bias, "weighted_score", 0) or 0) if news_bias else 0.0
    scenario = detect_oil_scenario(news_items)

    # ---- bias scoring ----
    long_pts = short_pts = 0
    if scenario == "deal_tape":
        short_pts += 3
    elif scenario == "disruption":
        long_pts += 3
    elif scenario == "inventory":
        # Зависит от тона новостей; без тона — нейтрально
        if news == "bullish":
            long_pts += 2
        elif news == "bearish":
            short_pts += 2
    elif scenario == "opec_supply":
        if news == "bullish":
            long_pts += 2
        elif news == "bearish":
            short_pts += 2
    elif scenario == "mixed_geo":
        pass  # ждём clarifier

    if news == "bullish":
        long_pts += 2 if abs(news_w) >= 3 else 1
    elif news == "bearish":
        short_pts += 2 if abs(news_w) >= 3 else 1

    if ta_v == "LONG":
        long_pts += 2 if ta_c >= 6 else 1
    elif ta_v == "SHORT":
        short_pts += 2 if ta_c >= 6 else 1

    if bounce_plan is not None:
        if getattr(bounce_plan, "side", "") == "long":
            long_pts += 2
        elif getattr(bounce_plan, "side", "") == "short":
            short_pts += 2

    scalp_action = getattr(scalp_call, "action", "") if scalp_call else ""
    if scalp_action == "open_long":
        long_pts += 1
    elif scalp_action == "open_short":
        short_pts += 1

    if scenario == "mixed_geo" or (abs(long_pts - short_pts) <= 1 and max(long_pts, short_pts) < 4):
        bias = "WAIT"
    elif long_pts > short_pts:
        bias = "LONG"
    elif short_pts > long_pts:
        bias = "SHORT"
    else:
        bias = "WAIT"

    conf = min(10, max(3, 4 + abs(long_pts - short_pts) + (1 if ta_c >= 6 else 0)))
    if bias == "WAIT":
        conf = min(conf, 6)

    horizon = "intraday 4–12ч"
    if scenario in {"deal_tape", "disruption", "mixed_geo"}:
        horizon = "свинг 1–3д · intraday по уровням"
    elif scenario == "inventory":
        horizon = "до следующего EIA / 1–2 сессии"

    scen_ru = _SCENARIO_LABEL.get(scenario, scenario)
    catalyst = ""
    if news_bias and getattr(news_bias, "top_catalyst", ""):
        catalyst = str(news_bias.top_catalyst)[:120]

    if bias == "SHORT":
        headline = f"Bias SHORT · {scen_ru}"
        base = (
            f"База: давление вниз с ${px:.2f}. "
            f"Искать продажи от сопротивления"
            + (f" {fmt_price(r)}" if r else "")
            + (f" / отката к R" if r else "")
            + ". Geo-premium сжимается — не ловить каждое дно."
        )
        alt = (
            "Альт: срыв deal / блок Ормуза → разворот в LONG "
            + (f"выше {fmt_price(bo)}" if bo else "на срыве переговоров")
            + "."
        )
        inv = (
            "Отмена SHORT: "
            + (f"закрытие {interval_minutes}m выше {fmt_price(bo or r)}" if (bo or r) else "сильный geo-spike без отката")
            + "."
        )
        entry = (
            "План: short от R / после failed breakout; "
            + (f"стоп над {fmt_price(bo or (r * 1.004 if r else px * 1.005))}; " if (bo or r) else "")
            + (f"цели {fmt_price(s)} / {fmt_price(bd)}" if s or bd else "цели — ближайшие S / breakdown")
            + "."
        )
    elif bias == "LONG":
        headline = f"Bias LONG · {scen_ru}"
        base = (
            f"База: давление вверх с ${px:.2f}. "
            f"Покупки от поддержки"
            + (f" {fmt_price(s)}" if s else "")
            + " или после подтверждённого пробоя↑."
        )
        alt = (
            "Альт: deal-tape / сильный build запасов → откат "
            + (f"к {fmt_price(s or bd)}" if (s or bd) else "к поддержке")
            + " или WAIT."
        )
        inv = (
            "Отмена LONG: "
            + (f"закрытие {interval_minutes}m ниже {fmt_price(bd or s)}" if (bd or s) else "слом структуры вниз")
            + "."
        )
        entry = (
            "План: long от S / close выше breakout; "
            + (f"стоп под {fmt_price(bd or (s * 0.996 if s else px * 0.995))}; " if (bd or s) else "")
            + (f"цели {fmt_price(r)} / {fmt_price(bo)}" if r or bo else "цели — R / breakout")
            + "."
        )
    else:
        headline = f"Bias WAIT · {scen_ru}"
        base = (
            f"База: нет чистого края с ${px:.2f}. "
            f"Торговать только от уровня или после close {interval_minutes}m за границей range."
        )
        alt = (
            "Альт LONG: disruption / пробой↑. "
            "Альт SHORT: deal-tape / пробой↓."
        )
        inv = "Ждать clarifier: танкеры Ормуз, EIA, тон переговоров."
        entry = (
            f"План: без касания S/R или пробоя — снаружи. "
            + (f"Range {fmt_price(s)}–{fmt_price(r)}." if s and r else "")
        )

    if catalyst:
        base = f"{base} Катализатор: «{catalyst}»."

    watch: list[str] = [
        "Танкеры / статус Ормуза (deal vs block)",
        "EIA weekly (ср/чт) + Cushing",
        "OPEC+ квоты / комментарии Саудов",
    ]
    if scenario == "inventory":
        watch.insert(0, "Сюрприз EIA draw/build vs consensus")
    if scenario in {"deal_tape", "disruption", "mixed_geo"}:
        watch.insert(0, "Дипломатия Muscat / MOU / удары по инфраструктуре")
    if market_mood:
        watch.append(f"Режим графика: {market_mood.split('—')[0].strip()[:48]}")

    return OilForecast(
        bias=bias,
        scenario=scenario,
        confidence=conf,
        horizon_ru=horizon,
        headline_ru=headline,
        base_case_ru=base,
        alt_case_ru=alt,
        invalidation_ru=inv,
        watch_list_ru=tuple(watch[:5]),
        entry_hint_ru=entry,
        source="rules",
    )


def format_oil_forecast_block(fc: OilForecast) -> str:
    """HTML-блок для Telegram дайджеста."""
    bias_mark = {
        "LONG": "🟢",
        "SHORT": "🔴",
        "WAIT": "⚪",
    }.get(fc.bias, "⚪")
    src = "правила+AI" if fc.source == "rules+gemini" else "правила бота"
    lines = [
        f"🎯 <b>Прогноз UKOUSD</b> · {bias_mark} <b>{fc.bias}</b> · {fc.confidence}/10",
        f"<i>{fc.headline_ru}</i>",
        f"<i>Горизонт: {fc.horizon_ru} · {src}</i>",
        "",
        f"• <b>База:</b> {_esc(fc.base_case_ru)}",
        f"• <b>Альт:</b> {_esc(fc.alt_case_ru)}",
        f"• <b>Отмена:</b> {_esc(fc.invalidation_ru)}",
        f"• <b>Как торговать:</b> {_esc(fc.entry_hint_ru)}",
    ]
    if fc.watch_list_ru:
        lines.append("• <b>Следить:</b>")
        for w in fc.watch_list_ru:
            lines.append(f"  – {_esc(w)}")
    if fc.gemini_ru:
        lines.append("")
        lines.append(f"🤖 <b>AI:</b> {_esc(fc.gemini_ru)}")
    lines.append("")
    lines.append(
        "<i>Сценарий, не гарантия. Сверяй с графиком UKOUSD и свежими заголовками.</i>"
    )
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _forecast_context_for_gemini(fc: OilForecast, snap: Any, news_items: Sequence[Any] | None) -> str:
    titles = []
    for it in list(news_items or [])[:8]:
        t = getattr(it, "title", "") or ""
        imp = getattr(it, "impact", "") or ""
        if t:
            titles.append(f"- [{imp}] {t[:140]}")
    news_block = "\n".join(titles) if titles else "(нет свежих приоритетных новостей)"
    return (
        "Ты — профессиональный трейдер сырой нефти (Brent / UKOUSD CFD). "
        "Пиши коротко по-русски для Новостника. Не выдумывай цены уровней. "
        "Не противоречь bias бота без явной причины. Не markdown.\n\n"
        f"BIAS бота: {fc.bias} (confidence {fc.confidence}/10)\n"
        f"Сценарий: {fc.scenario} — {fc.headline_ru}\n"
        f"Цена: ${float(getattr(snap, 'price', 0) or 0):.2f}\n"
        f"S={getattr(snap, 'support', None)} R={getattr(snap, 'resistance', None)} "
        f"BD={getattr(snap, 'breakdown', None)} BO={getattr(snap, 'breakout', None)}\n"
        f"База бота: {fc.base_case_ru}\n"
        f"Отмена: {fc.invalidation_ru}\n\n"
        f"Новости:\n{news_block}"
    )


async def enrich_oil_forecast_with_gemini(
    fc: OilForecast,
    snap: Any,
    *,
    news_items: Sequence[Any] | None = None,
    api_key: str | None = None,
    model: str = "gemini-3.6-flash",
) -> OilForecast:
    """Добавляет короткий AI-комментарий; при ошибке возвращает исходный прогноз."""
    if not api_key:
        return fc
    try:
        from .ai_analyst import ask_gemini, sanitize_ai_reply_for_telegram

        ctx = _forecast_context_for_gemini(fc, snap, news_items)
        user = (
            "Сформулируй 4–6 строк: 1) сценарий дня одной фразой, "
            "2) что делать трейдеру UKOUSD сейчас, "
            "3) что сломает сценарий, "
            "4) один риск. Без списков markdown, без эмодзи-спама."
        )
        result = await ask_gemini(
            api_key=api_key,
            model=model,
            context_text=ctx,
            user_text=user,
            history=None,
            images=None,
        )
        text = sanitize_ai_reply_for_telegram(result.text or "")
        if result.error or not text:
            logger.info("Oil Gemini forecast skip: %s", result.error or "empty")
            return fc
        # Обрезаем для Telegram
        text = text.strip()
        if len(text) > 900:
            text = text[:897] + "…"
        return replace(fc, gemini_ru=text, source="rules+gemini")
    except Exception:
        logger.exception("Oil Gemini forecast failed")
        return fc
