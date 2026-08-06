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
    bars: Sequence[Any] | None = None,
    flow: Any | None = None,
) -> OilForecast:
    """Склеивает новости + уровни + поток + ход цены → прогноз (без слепого 10/10)."""
    from .oil_news_discipline import assess_news_for_trade

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
    news_assess = assess_news_for_trade(
        news_items,
        news_bias=news_bias,
        bars=bars,
        hot_hours=0.5,
        warm_hours=2.0,
        priced_in_pct=0.35,
    )
    from .oil_signal_context import analyze_hormuz_context, analyze_inventory_from_news

    hormuz = analyze_hormuz_context(news_items)
    inv_ctx = analyze_inventory_from_news(news_items)

    from .oil_signal_gate import evaluate_oil_signal_gate, gate_apply_to_side

    gate = evaluate_oil_signal_gate(bars, interval_minutes=interval_minutes)
    notes: list[str] = list(gate.factors_ru[:3])
    if hormuz.line_ru:
        notes.append(hormuz.line_ru[:100])

    long_pts = short_pts = 0

    # Deal-tape: НИКОГДА не открывает SHORT сам — только фон, пока нет финала + гейт
    if scenario == "deal_tape":
        notes.append(hormuz.line_ru or "Ормуз deal — только фон")
        if (
            gate.allow_short
            and hormuz.for_entry
            and hormuz.status == "signed_claim"
            and news_assess.for_entry
            and news == "bearish"
        ):
            short_pts += 1
            notes.append("deal подписан HOT↓")
        else:
            notes.append("deal не даёт вход SHORT")
    elif scenario == "disruption":
        if gate.allow_long and news_assess.for_entry and news == "bullish":
            long_pts += 2
        elif gate.allow_long and news_assess.mode in {"hot", "warm"}:
            long_pts += 1
    elif scenario == "inventory":
        if inv_ctx.tone == "bearish" and gate.allow_short:
            short_pts += 2 if news_assess.for_entry else 1
            notes.append(inv_ctx.line_ru or "запасы build")
        elif inv_ctx.tone == "bullish" and gate.allow_long:
            long_pts += 2 if news_assess.for_entry else 1
            notes.append(inv_ctx.line_ru or "запасы draw")
        elif inv_ctx.tone == "mixed":
            notes.append(inv_ctx.line_ru or "запасы mixed")
        elif news == "bullish" and news_assess.for_entry and gate.allow_long:
            long_pts += 2
        elif news == "bearish" and news_assess.for_entry and gate.allow_short:
            short_pts += 2
    elif scenario == "opec_supply":
        if news == "bullish" and gate.allow_long:
            long_pts += 1 + (1 if news_assess.for_entry else 0)
        elif news == "bearish" and gate.allow_short:
            short_pts += 1 + (1 if news_assess.for_entry else 0)
    elif scenario == "mixed_geo":
        notes.append("geo mixed")

    if news == "bullish" and news_assess.for_entry and gate.allow_long:
        long_pts += 2 if abs(news_w) >= 3 else 1
    elif news == "bearish" and news_assess.for_entry and gate.allow_short:
        short_pts += 2 if abs(news_w) >= 3 else 1
    elif news == "bullish" and news_assess.mode == "warm" and gate.allow_long:
        long_pts += 1
    elif news == "bearish" and news_assess.mode == "warm" and gate.allow_short:
        short_pts += 1

    if news_assess.block_long or not gate.allow_long:
        long_pts = 0
        if not gate.allow_long:
            notes.append(gate.reason_ru)
    if news_assess.block_short or not gate.allow_short:
        short_pts = 0
        if not gate.allow_short:
            notes.append(gate.reason_ru)

    if ta_v == "LONG" and gate.allow_long:
        long_pts += 2 if ta_c >= 6 else 1
    elif ta_v == "SHORT" and gate.allow_short:
        short_pts += 2 if ta_c >= 6 else 1

    if bounce_plan is not None:
        if getattr(bounce_plan, "side", "") == "long" and gate.allow_long:
            long_pts += 2
        elif getattr(bounce_plan, "side", "") == "short" and gate.allow_short:
            short_pts += 2

    scalp_action = getattr(scalp_call, "action", "") if scalp_call else ""
    if scalp_action == "open_long" and gate.allow_long:
        long_pts += 1
    elif scalp_action == "open_short" and gate.allow_short:
        short_pts += 1

    flow_bias = (getattr(flow, "bias", "") or "").lower() if flow is not None else ""
    if flow_bias == "buy" and gate.allow_long:
        long_pts += 1
        notes.append("поток BUY")
    elif flow_bias == "sell" and gate.allow_short:
        short_pts += 1
        notes.append("поток SELL")

    if flow_bias == "buy" and short_pts > long_pts:
        short_pts = 0
        notes.append("конфликт: поток↑ — SHORT сброшен")
    elif flow_bias == "sell" and long_pts > short_pts:
        long_pts = 0
        notes.append("конфликт: поток↓ — LONG сброшен")

    if gate.macd_bias == "bull" and gate.allow_long:
        long_pts += 1
    elif gate.macd_bias == "bear" and gate.allow_short:
        short_pts += 1

    if scenario == "mixed_geo" or (
        abs(long_pts - short_pts) <= 1 and max(long_pts, short_pts) < 4
    ):
        bias = "WAIT"
    elif long_pts > short_pts:
        bias = "LONG"
    elif short_pts > long_pts:
        bias = "SHORT"
    else:
        bias = "WAIT"

    bias = gate_apply_to_side(gate, bias)
    if bias == "WAIT" and gate.force_wait:
        notes.append(gate.reason_ru)

    conf_cap_mixed = hormuz.oil_bias == "mixed" or scenario == "deal_tape"
    if conf_cap_mixed and bias == "SHORT":
        bias = "WAIT"
        notes.append("deal/Ормуз — без SHORT")

    edge = abs(long_pts - short_pts)
    # Потолок: без полного схождения ≤7; 9 редко; 10 из правил — никогда
    conf = min(7, max(3, 3 + edge + (1 if ta_c >= 6 else 0)))
    same_side_flow = (bias == "LONG" and flow_bias == "buy") or (
        bias == "SHORT" and flow_bias == "sell"
    )
    hot_aligned = news_assess.for_entry and (
        (bias == "LONG" and news == "bullish")
        or (bias == "SHORT" and news == "bearish")
    )
    if (
        bias in {"LONG", "SHORT"}
        and hot_aligned
        and same_side_flow
        and edge >= 3
        and ta_c >= 6
        and hormuz.for_entry
    ):
        conf = min(9, conf + 2)
    elif bias in {"LONG", "SHORT"} and hot_aligned and edge >= 2:
        conf = min(8, conf + 1)

    if bias == "WAIT":
        conf = min(conf, 5 if (gate.force_wait or conf_cap_mixed) else 6)

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
        if gate.force_wait:
            base = f"WAIT с ${px:.2f}: {gate.reason_ru}"
            entry = "снаружи · тренд/MACD против входа — не шортить рост"
        elif conf_cap_mixed:
            base = f"WAIT с ${px:.2f}: Ормуз/deal не финал — без chase"
            entry = "ждать финал сделки или уровень с MACD"
        else:
            base = (
                f"нет края с ${px:.2f} · только от уровня / "
                f"close {interval_minutes}m за range"
            )
            entry = (
                "снаружи без S/R или пробоя"
                + (f" · range {_lvl(s)}–{_lvl(r)}" if s and r else "")
            )
        alt = "вверх: срыв поставок · вниз: сделка / пробой↓"
        inv = "ждать: танкеры / запасы / MACD / согласование потока"

    if catalyst and news_assess.mode in {"hot", "warm"}:
        base = f"{base} · {catalyst}"
    if notes and bias == "WAIT":
        base = f"{base} · {notes[0]}"

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



def format_oil_forecast_block(
    fc: OilForecast,
    *,
    news_items: Sequence[Any] | None = None,
    flow: Any | None = None,
    price: float | None = None,
    bars: Sequence[Any] | None = None,
) -> str:
    """Понятный блок прогноза: почему / фон / план / отмена."""
    import re as _re

    from .oil_signal_context import build_signal_drivers, format_clear_signal_card

    drivers = build_signal_drivers(
        news_items=news_items,
        news_mode=fc.scenario if fc.scenario != "range" else "none",
        flow=flow,
        side=fc.bias,
        bars=bars,
    )
    if fc.bias in {"LONG", "SHORT"} and drivers.why_ru == "схождение факторов слабое":
        why = _strip_case_prefix(fc.base_case_ru)[:120]
        drivers = type(drivers)(
            hormuz=drivers.hormuz,
            inventory=drivers.inventory,
            news_mode=drivers.news_mode,
            flow_ru=drivers.flow_ru,
            why_ru=why,
            caution_ru=drivers.caution_ru,
            lines_ru=drivers.lines_ru
            or ((_strip_case_prefix(fc.headline_ru),) if fc.headline_ru else ()),
        )
    if fc.bias == "WAIT" and not drivers.caution_ru:
        drivers = type(drivers)(
            hormuz=drivers.hormuz,
            inventory=drivers.inventory,
            news_mode=drivers.news_mode,
            flow_ru=drivers.flow_ru,
            why_ru=drivers.why_ru,
            caution_ru=_strip_case_prefix(fc.base_case_ru)[:140],
            lines_ru=drivers.lines_ru,
        )

    px = float(price) if price and price > 0 else 0.0
    if px <= 0:
        m = _re.search(r"\$([0-9]+(?:\.[0-9]+)?)", fc.base_case_ru or "")
        if m:
            px = float(m.group(1))

    card = format_clear_signal_card(
        side=fc.bias,
        quality=fc.confidence,
        price=px,
        drivers=drivers,
        trigger_ru=_strip_case_prefix(fc.entry_hint_ru),
        horizon_ru=fc.horizon_ru,
        extra_ru=fc.gemini_ru,
    )
    inv = _strip_case_prefix(fc.invalidation_ru)
    if inv and "<b>Отмена:</b>" not in card:
        card = card + f"\n<b>Отмена:</b> {_esc(inv)[:140]}"
    return card


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
            "Сформулируй 4–7 строк по-русски:\n"
            "1) что главное сейчас,\n"
            "2) согласованы ли цена/поток с bias бота,\n"
            "3) открывать или WAIT (если бот WAIT — не уговаривай входить),\n"
            "4) что сломает сценарий.\n"
            "Не ставь 10/10. Не догоняй ход. Без англ. жаргона. Не финсовет."
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
