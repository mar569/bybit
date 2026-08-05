"""Профессиональный прогноз UKOUSD: правила (новости+TA) + опционально Gemini."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Sequence

from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)


def _lvl(price: float | None) -> str:
    """fmt_price, безопасный к None."""
    if price is None:
        return "—"
    return fmt_price(float(price))


def _lvls(*prices: float | None, sep: str = " / ") -> str:
    parts = [_lvl(p) for p in prices if p is not None]
    return sep.join(parts) if parts else "—"

# Сценарии рынка нефти (фундамент → bias) — коротко для чата
_SCENARIO_LABEL = {
    "deal_tape": "сделка Ормуз → снятие premium",
    "disruption": "блок/атаки → geo-premium",
    "inventory": "запасы США",
    "opec_supply": "ОПЕК / добыча",
    "range": "range · без сильного катализатора",
    "mixed_geo": "geo mixed",
}


def _strip_case_prefix(text: str) -> str:
    """Убрать «База:/Альт:/Отмена:/План:» из текста — метка уже в форматтере."""
    t = (text or "").strip()
    for pref in (
        "База: ",
        "База:",
        "Альт: ",
        "Альт:",
        "Отмена LONG: ",
        "Отмена SHORT: ",
        "Отмена: ",
        "Отмена ",
        "План: ",
        "План:",
    ):
        if t.startswith(pref):
            return t[len(pref) :].strip()
    return t


def _short_catalyst(raw: str, *, max_len: int = 72) -> str:
    """Не тащить длинный EN-заголовок в карточку."""
    t = " ".join((raw or "").split())
    if not t:
        return ""
    # Если почти весь текст латиницей — только короткий хвост
    letters = [c for c in t if c.isalpha()]
    latin = sum(1 for c in letters if "A" <= c.upper() <= "Z")
    if letters and latin / len(letters) >= 0.7:
        low = t.lower()
        if "hormuz" in low or "ормуз" in low:
            return "Ормуз / сделка"
        if "opec" in low or "опек" in low:
            return "ОПЕК"
        if "inventor" in low or "eia" in low or "запас" in low:
            return "запасы США"
        return t[:40].rstrip(" .,:;") + ("…" if len(t) > 40 else "")
    if len(t) > max_len:
        return t[: max_len - 1].rstrip() + "…"
    return t


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
                    "taco", "cancels", "cancel", "pauses", "pause", "suspend",
                    "chickens out", "backs off", "calls off", "отмен", "струсил",
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
                # «cancels attack» уже попало в deal выше; чистая эскалация → disrupt
                disrupt += 2
            else:
                disrupt += 1
                deal += 1
        # Явный обвал цены в заголовке усиливает deal_tape (снятие premium)
        if any(
            k in low
            for k in ("tumble", "slump", "plunge", "crash", "обвал", "обруш")
        ):
            deal += 2
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

    horizon = "4–12ч"
    if scenario in {"deal_tape", "disruption", "mixed_geo"}:
        horizon = "1–3д / intraday"
    elif scenario == "inventory":
        horizon = "до отчёта запасов"

    scen_ru = _SCENARIO_LABEL.get(scenario, scenario)
    catalyst = ""
    if news_bias and getattr(news_bias, "top_catalyst", ""):
        catalyst = _short_catalyst(str(news_bias.top_catalyst))

    if bias == "SHORT":
        headline = f"SHORT · {scen_ru}"
        base = (
            f"давление вниз с ${px:.2f}"
            + (f" · продажи от R {_lvl(r)}" if r else "")
        )
        alt = (
            "срыв deal / блок Ормуза → LONG"
            + (f" выше {_lvl(bo)}" if bo else "")
        )
        inv = (
            f"close {interval_minutes}m выше {_lvl(bo or r)}"
            if (bo or r)
            else "сильный geo-spike без отката"
        )
        entry = (
            "short от R / failed BO"
            + (
                f" · SL {_lvl(bo or (r * 1.004 if r else px * 1.005))}"
                if (bo or r)
                else ""
            )
            + (f" · TP {_lvls(s, bd)}" if (s or bd) else "")
        )
    elif bias == "LONG":
        headline = f"LONG · {scen_ru}"
        base = (
            f"давление вверх с ${px:.2f}"
            + (f" · покупки от S {_lvl(s)}" if s else "")
            + " / пробой↑"
        )
        alt = (
            "сделка / рост запасов → откат"
            + (f" к {_lvl(s or bd)}" if (s or bd) else "")
            + " / WAIT"
        )
        inv = (
            f"close {interval_minutes}m ниже {_lvl(bd or s)}"
            if (bd or s)
            else "слом структуры вниз"
        )
        entry = (
            "long от S / close выше BO"
            + (
                f" · SL {_lvl(bd or (s * 0.996 if s else px * 0.995))}"
                if (bd or s)
                else ""
            )
            + (f" · TP {_lvls(r, bo)}" if (r or bo) else "")
        )
    else:
        headline = f"WAIT · {scen_ru}"
        base = (
            f"нет края с ${px:.2f} · только от уровня / close {interval_minutes}m за range"
        )
        alt = "вверх: срыв поставок · вниз: сделка / пробой↓"
        inv = "ждать: танкеры / запасы / тон переговоров"
        entry = (
            "снаружи без S/R или пробоя"
            + (f" · range {_lvl(s)}–{_lvl(r)}" if s and r else "")
        )

    if catalyst:
        base = f"{base} · {catalyst}"

    # watch_list оставляем в данных, в чат не спамим
    watch: list[str] = []
    if scenario in {"deal_tape", "disruption", "mixed_geo"}:
        watch.append("Ормуз / дипломатия")
    if scenario == "inventory":
        watch.append("отчёт запасов")
    if market_mood:
        watch.append(market_mood.split("—")[0].strip()[:40])

    return OilForecast(
        bias=bias,
        scenario=scenario,
        confidence=conf,
        horizon_ru=horizon,
        headline_ru=headline,
        base_case_ru=base,
        alt_case_ru=alt,
        invalidation_ru=inv,
        watch_list_ru=tuple(watch[:3]),
        entry_hint_ru=entry,
        source="rules",
    )


def format_oil_forecast_block(fc: OilForecast) -> str:
    """Короткий HTML-блок для Telegram — без простыни «Следить»."""
    bias_mark = {
        "LONG": "🟢",
        "SHORT": "🔴",
        "WAIT": "⚪",
    }.get(fc.bias, "⚪")
    lines = [
        f"🎯 {bias_mark} <b>{fc.bias}</b> · {fc.confidence}/10 · {_esc(fc.headline_ru)}",
        f"<i>{_esc(fc.horizon_ru)}</i>",
        f"• {_esc(_strip_case_prefix(fc.base_case_ru))}",
        f"• альт: {_esc(_strip_case_prefix(fc.alt_case_ru))}",
        f"• ✖ {_esc(_strip_case_prefix(fc.invalidation_ru))}",
        f"• ▶ {_esc(_strip_case_prefix(fc.entry_hint_ru))}",
    ]
    if fc.gemini_ru:
        g = " ".join(fc.gemini_ru.split())
        if len(g) > 160:
            g = g[:157] + "…"
        lines.append(f"🤖 {_esc(g)}")
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
        "Ты — профессиональный трейдер сырой нефти (Brent / UKOUSD). "
        "Пиши по-русски простыми словами для Telegram. "
        "Выжимай важное, с деталями, без простыни и без англ. жаргона. "
        "Не выдумывай цены уровней. Не противоречь bias бота без причины.\n\n"
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
        from .ai_analyst import ask_gemini, gemini_in_cooldown, sanitize_ai_reply_for_telegram

        if gemini_in_cooldown():
            return fc
        ctx = _forecast_context_for_gemini(fc, snap, news_items)
        user = (
            "Сформулируй 6–10 строк по-русски простыми словами (не страница):\n"
            "1) что сейчас главное для нефти,\n"
            "2) самое громкое ПОЧЕМУ,\n"
            "3) куда скорее цена (вверх/вниз/ждать),\n"
            "4) что делать трейдеру UKOUSD сейчас,\n"
            "5) что сломает сценарий.\n"
            "Без англ. жаргона, без воды. Не финсовет."
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
