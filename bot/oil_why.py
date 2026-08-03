"""Честный ответ «почему UKOUSD» — простым языком, без жаргона."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .bybit_klines import KlineBar
from .oil_flow import OilFlowProxy, compute_oil_flow_proxy


@dataclass(frozen=True)
class OilWhyReport:
    direction: str  # up | down | flat
    move_1h_pct: float
    move_4h_pct: float
    price: float
    confidence: int
    plain_ru: str  # 2–3 фразы «суть»
    facts_ru: tuple[str, ...]
    news_plain_ru: tuple[str, ...]
    careful_ru: tuple[str, ...]
    do_now_ru: str
    ai_now_ru: str = ""  # свежий ИИ-сбор «прямо сейчас»


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _move_from_bars(bars: Sequence[KlineBar], *, bars_back: int) -> float:
    if not bars or len(bars) < 2:
        return 0.0
    i = max(0, len(bars) - 1 - bars_back)
    return _pct(float(bars[-1].close), float(bars[i].close))


def _explain_headline(title: str) -> tuple[str, str, str]:
    """(коротко_по-русски, влияние_на_цену, side: up|down|mix)."""
    low = title.lower()
    # Явное движение цены в заголовке — выше geo-слов
    if any(
        k in low
        for k in (
            "tumble", "slump", "plunge", "crash", "falls", "fell", "drops",
            "dropped", "decline", "обвал", "обруш", "паден", "рухн",
        )
    ):
        return (
            "В заголовке — падение цены нефти",
            "Рынок уже реагирует вниз (снятие geo-premium / деэскалация / фиксация)",
            "down",
        )
    if any(
        k in low
        for k in (
            "surge", "soar", "rally", "jumps", "spikes", "rises", "climb",
            "подскоч", "взлет", "взлёт", "рекорд",
        )
    ):
        return (
            "В заголовке — рост цены нефти",
            "Рынок уже реагирует вверх (страх поставок / эскалация)",
            "up",
        )
    # Отмена ударов / TACO → вниз (проверять ДО attack/strike)
    if any(
        k in low
        for k in (
            "taco", "chickens out", "backs off", "calls off", "called off",
            "cancels", "cancel", "pauses", "pause", "suspends", "suspend",
            "отмен", "струсил", "приостан", "отказался",
        )
    ) and any(k in low for k in ("attack", "strike", "удар", "атак", "bomb", "iran", "иран")):
        return (
            "Трамп/США отложили или отменили удары по Ирану (деэскалация)",
            "Страх войны слабеет → war-premium обычно снимают → нефть вниз",
            "down",
        )
    # Сделка / открытие пролива → обычно вниз
    if any(
        k in low
        for k in (
            "offer to open", "reopen", "open strait", "open hormuz", "flows recover",
            "more crude flows", "ceasefire", "peace deal", "mou", "diplom",
            "сделк", "открыт", "перемир",
        )
    ):
        return (
            "США/Иран обсуждают открытие Ормузского пролива / сделку",
            "Если танкеры пойдут свободно — нефти на рынке больше → цена обычно падает",
            "down",
        )
    if any(k in low for k in ("sanction", "санкц", "tariff", "extortion")):
        return (
            "Новые санкции / давление на Иран",
            "Риск снова перекроет поставки → цена часто растёт или не даёт упасть",
            "up",
        )
    if any(
        k in low
        for k in (
            "attack", "strike", "missile", "blockade", "close strait", "halt",
            "атак", "удар", "блок", "закрыт",
        )
    ):
        return (
            "Удары / блок поставок / эскалация войны",
            "Страх, что нефть не пройдёт через Ормуз → цена обычно растёт",
            "up",
        )
    if any(k in low for k in ("eia", "inventory", "запас", "stock")):
        if any(k in low for k in ("build", "rise", "рост запас", "избыт")):
            return ("Запасы нефти в США выросли", "Нефти много на складах → давление вниз", "down")
        if any(k in low for k in ("draw", "fall", "сниж", "упал")):
            return ("Запасы нефти в США упали", "Нефти на складах меньше → поддержка цены вверх", "up")
        return ("Вышли данные по запасам США", "Если запасы сильно удивили рынок — цена может дёрнуться на часы", "mix")
    if any(k in low for k in ("opec", "опек", "quota", "квот")):
        return ("Новости OPEC по добыче", "Режут добычу → вверх; повышают → вниз", "mix")
    # Обрезка длинного EN-заголовка + нейтраль
    short = title.strip()
    if len(short) > 90:
        short = short[:87] + "…"
    return (f"Новость: {short}", "Влияние на цену неоднозначное — смотри контекст Ормуза", "mix")


def _pick_news(
    items: Sequence[Any],
    *,
    limit: int = 5,
    prefer_direction: str = "",
    max_age_hours: float = 12.0,
    now_ts: float | None = None,
) -> list[Any]:
    """Свежие важные сюжеты: сначала «здесь и сейчас», гео/импульс выше."""
    import time

    from .oil_monitor import news_critical_score, oil_news_freshness_weight

    now = now_ts if now_ts is not None else time.time()
    scored: list[tuple[float, Any]] = []
    for it in items:
        title = (getattr(it, "title", "") or "").strip()
        if not title:
            continue
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts > 0 and (now - ts) > max_age_hours * 3600:
            continue
        theme = getattr(it, "theme", "") or ""
        bonus = 5 if theme == "iran_geo" else 2 if theme in {"inventory", "opec", "analyst"} else 0
        base = news_critical_score(title, source=getattr(it, "source", "")) + bonus
        fresh_w = oil_news_freshness_weight(ts if ts > 0 else None, now=now)
        # Свежесть критична для «почему сейчас»
        score = base * (0.35 + 0.65 * fresh_w)
        _, _, side = _explain_headline(title)
        if prefer_direction and side == prefer_direction:
            score += 4
        elif prefer_direction and side != "mix" and side != prefer_direction:
            score -= 1
        # Суперсвежие (<2ч) — отдельный буст
        if ts > 0 and (now - ts) <= 2 * 3600:
            score += 3
        scored.append((score, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[Any] = []
    seen: set[str] = set()
    for _, it in scored:
        key = (getattr(it, "title", "") or "").lower()[:70]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def build_oil_why_report(
    bars: Sequence[KlineBar],
    *,
    news_items: Sequence[Any] | None = None,
    news_bias: Any | None = None,
    forecast: Any | None = None,
    flow: OilFlowProxy | None = None,
    interval_minutes: int = 15,
    ai_now_ru: str = "",
) -> OilWhyReport | None:
    if not bars or len(bars) < 8:
        return None
    px = float(bars[-1].close)
    b1h = max(1, int(round(60 / max(5, interval_minutes))))
    b4h = max(b1h + 1, int(round(240 / max(5, interval_minutes))))
    m1 = _move_from_bars(bars, bars_back=b1h)
    m4 = _move_from_bars(bars, bars_back=min(b4h, len(bars) - 1))

    primary_move = m1 if abs(m1) >= 0.08 else m4
    if primary_move >= 0.12:
        direction = "up"
    elif primary_move <= -0.12:
        direction = "down"
    else:
        direction = "flat"

    items = list(news_items or [])
    if flow is None:
        flow = compute_oil_flow_proxy(bars, lookback=12 if interval_minutes <= 15 else 8)

    news = getattr(news_bias, "bias", "neutral") if news_bias else "neutral"
    fc_scen = (getattr(forecast, "scenario", "") or "") if forecast else ""

    facts: list[str] = []
    news_plain: list[str] = []
    careful: list[str] = []

    # --- цена простым языком ---
    if direction == "up":
        facts.append(
            f"Сейчас нефть (UKOUSD) около <b>${px:.2f}</b>. "
            f"За час выросла примерно на <b>{abs(m1):.2f}%</b>, за 4 часа — на <b>{abs(m4):.2f}%</b>."
        )
    elif direction == "down":
        facts.append(
            f"Сейчас нефть (UKOUSD) около <b>${px:.2f}</b>. "
            f"За час упала примерно на <b>{abs(m1):.2f}%</b>, за 4 часа — на <b>{abs(m4):.2f}%</b>."
        )
    else:
        facts.append(
            f"Сейчас нефть около <b>${px:.2f}</b>. За час почти без движения "
            f"({m1:+.2f}%) — это скорее колебание, не сильный тренд."
        )

    # --- пролив Ормуз — главная тема мира ---
    has_geo = any((getattr(it, "theme", "") or "") == "iran_geo" for it in items)
    if has_geo or any("hormuz" in (getattr(it, "title", "") or "").lower() or "ормуз" in (getattr(it, "title", "") or "").lower() for it in items):
        facts.append(
            "Главная тема мира по нефти сейчас — <b>Ормузский пролив</b> "
            "(узкое место, через которое идёт огромная часть нефти с Ближнего Востока). "
            "Если его боятся закрыть — цена растёт. Если танкеры снова ходят — цена часто падает."
        )

    # --- новости: сначала то, что совпадает с движением цены ---
    picked = _pick_news(items, limit=5, prefer_direction=direction, max_age_hours=12.0)
    up_news = down_news = mix_news = 0
    for it in picked:
        title = getattr(it, "title", "") or ""
        what, means, side = _explain_headline(title)
        src = (getattr(it, "source", "") or "").strip()
        age = ""
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts > 0:
            import time as _t

            age_h = (_t.time() - ts) / 3600.0
            if age_h < 1:
                age = f" · {int(age_h * 60)}м"
            elif age_h < 48:
                age = f" · {age_h:.1f}ч"
        tag = f" <i>({src}{age})</i>" if src or age else ""
        news_plain.append(f"<b>{what}</b>{tag}\n  → {means}")
        if side == "up":
            up_news += 1
        elif side == "down":
            down_news += 1
        else:
            mix_news += 1

    if not news_plain:
        news_plain.append(
            "За последние часы сильных primary-заголовков мало — "
            "движение могло быть от уровня/алго, а не от новой новости."
        )

    # --- поток без жаргона ---
    if flow is not None:
        if flow.bias == "buy":
            facts.append(
                "По свечам видно, что <b>покупатели активнее продавцов</b> "
                f"(доля покупок около {flow.buy_share_pct:g}%). "
                "Это поддерживает рост, но это оценка по свечам, не «стакан биржи»."
            )
        elif flow.bias == "sell":
            facts.append(
                "По свечам видно, что <b>продавцы активнее покупателей</b> "
                f"(доля покупок только {flow.buy_share_pct:g}%). "
                "Это давит цену вниз."
            )
        else:
            facts.append("По объёму свечей нет явного перевеса покупателей или продавцов.")

    # --- сценарий ---
    if fc_scen == "deal_tape":
        careful.append(
            "Бот видит фон «переговоры / больше поставок» — такой фон обычно "
            "<b>мешает росту</b> надолго, даже если час-два цена зелёная."
        )
    elif fc_scen == "disruption":
        facts.append(
            "Фон «сбои поставок / эскалация» — рынок может держать цену высокой, пока страх не спадёт."
        )
    elif fc_scen == "mixed_geo":
        careful.append(
            "Сигналы по Ормузу смешанные (и сделка, и угрозы) — цена может резко ходить в обе стороны."
        )

    # --- конфликты простым языком ---
    if direction == "up" and down_news > up_news:
        careful.append(
            "В новостях больше про «открыть пролив / сделку» (это обычно вниз), "
            "а цена всё равно растёт. Часто это <b>короткий отскок</b>, а не новый долгий рост. "
            "Не стоит слепо гнаться за лонгом."
        )
        conf = 4
    elif direction == "down" and up_news > down_news:
        careful.append(
            "Новости больше про угрозы/санкции (обычно вверх), а цена падает — "
            "возможно, рынок уже «выдохнул» страх или танкеры всё же идут."
        )
        conf = 4
    elif direction == "up" and news == "bearish":
        careful.append(
            "Общий тон новостей скорее «давит цену вниз», а график зелёный — "
            "осторожно, рост может быстро сдуться."
        )
        conf = 4
    elif direction == "flat":
        conf = 5
    elif picked:
        conf = 7
    else:
        conf = 3
        careful.append("Мало свежих новостей — нельзя уверенно сказать «из-за чего».")

    careful.append(
        "Мы не видим живую ленту сделок на большой бирже нефти (ICE). "
        "График бота старается брать MT5 <b>UKOUSD.s</b> (Brent Crude Oil Cash) — "
        "тот же инструмент, что в Bybit TradFi. Без MT5 будет другой feed (не Cash)."
    )

    # --- суть ---
    if direction == "up":
        if up_news >= down_news and has_geo:
            plain = (
                f"Нефть растёт (сейчас ≈${px:.2f}, за час +{abs(m1):.2f}%), "
                "потому что рынок снова нервничает из-за Ирана и Ормузского пролива: "
                "боятся перебоев с поставками. "
                + (
                    "Но в ленте есть и новости про возможную сделку/открытие пролива — "
                    "из-за этого рост может быть нестойким."
                    if down_news
                    else "Пока страх поставок жив — рост выглядит логичным."
                )
            )
        else:
            plain = (
                f"Нефть сейчас растёт (≈${px:.2f}, +{abs(m1):.2f}% за час), "
                "но по новостям картина неоднозначная. "
                "Часто так бывает на коротком отскоке — сначала смотри уровни, потом заголовки."
            )
        do_now = (
            "Не шортить против сильного страха по Ормузу. "
            "Если новости про сделку/открытие пролива — лонг только от поддержки, без погони за ценой."
        )
    elif direction == "down":
        plain = (
            f"Нефть падает (≈${px:.2f}, за час −{abs(m1):.2f}%). "
            "Чаще всего так бывает, когда рынок думает: «танкеры снова пойдут / война чуть отступила» — "
            "страх поставок слабеет и цену отпускают вниз."
        )
        do_now = (
            "Не ловить ножи лонгом. Покупать только от сильной поддержки или когда новости снова про блок/удары."
        )
    else:
        plain = (
            f"Нефть около ${px:.2f} почти стоит. "
            "Нет одного ясного драйвера — лучше подождать уровень или новую новость по Ормузу."
        )
        do_now = "Не выдумывать причину. Ждать касания уровня или свежий сильный заголовок."

    return OilWhyReport(
        direction=direction,
        move_1h_pct=round(m1, 2),
        move_4h_pct=round(m4, 2),
        price=px,
        confidence=conf,
        plain_ru=plain,
        facts_ru=tuple(facts[:5]),
        news_plain_ru=tuple(news_plain[:5]),
        careful_ru=tuple(careful[:4]),
        do_now_ru=do_now,
        ai_now_ru=(ai_now_ru or "").strip(),
    )


def format_oil_why_report(rep: OilWhyReport) -> str:
    mark = {"up": "📈", "down": "📉", "flat": "➡️"}.get(rep.direction, "➡️")
    dir_ru = {"up": "растёт", "down": "падает", "flat": "почти стоит"}.get(rep.direction, "")
    lines = [
        f"{mark} <b>Почему нефть {dir_ru}</b> · понятность {rep.confidence}/10",
        f"<i>${rep.price:.2f} · за 1ч {rep.move_1h_pct:+.2f}% · за 4ч {rep.move_4h_pct:+.2f}%</i>",
        "",
        "<b>Суть простыми словами</b>",
        _esc(rep.plain_ru),
    ]
    if rep.ai_now_ru:
        lines.append("")
        lines.append("🔥 <b>Прямо сейчас (свежий сбор)</b>")
        lines.append(_esc(rep.ai_now_ru))
    lines.append("")
    lines.append("<b>Факты</b>")
    for f in rep.facts_ru:
        lines.append(f"• {_soft_html(f)}")
    lines.append("")
    lines.append("<b>Что вышло по новостям (свежее сверху)</b>")
    for n in rep.news_plain_ru:
        lines.append(f"• {_soft_html(n)}")
    lines.append("")
    lines.append("<b>Осторожно</b>")
    for c in rep.careful_ru:
        lines.append(f"• {_esc(c)}")
    lines.append("")
    lines.append(f"<b>Что делать сейчас:</b> {_esc(rep.do_now_ru)}")
    lines.append("")
    lines.append(
        "<i>Собрано заново при нажатии «Почему так». "
        "Кнопки ниже: Запасы / Ормуз. Не финансовый совет.</i>"
    )
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _soft_html(text: str) -> str:
    """Экранирует всё, кроме простых <b>...</b> и <i>...</i>."""
    import re

    parts = re.split(r"(</?[bi]>)", text or "")
    out: list[str] = []
    for p in parts:
        if p in {"<b>", "</b>", "<i>", "</i>"}:
            out.append(p)
        else:
            out.append(_esc(p))
    return "".join(out)


async def enrich_why_with_gemini(
    *,
    direction: str,
    move_1h: float,
    move_4h: float,
    price: float,
    headlines: Sequence[str],
    extra_context: str = "",
    api_key: str | None,
    model: str = "gemini-3.6-flash",
) -> str:
    """Свежий ИИ-разбор: что ИМЕННО сейчас двигает цену."""
    if not api_key:
        return ""
    try:
        from .ai_analyst import ask_gemini, sanitize_ai_reply_for_telegram

        dir_ru = {"up": "растёт", "down": "падает", "flat": "почти стоит"}.get(
            direction, direction
        )
        heads = "\n".join(f"- {h}" for h in headlines[:8]) or "- (мало заголовков)"
        ctx = (
            "Ты профессиональный аналитик нефти Brent/UKOUSD. "
            "Отвечай ТОЛЬКО по-русски, коротко и по делу.\n"
            f"Цена сейчас ≈${price:.2f}, за 1ч {move_1h:+.2f}%, за 4ч {move_4h:+.2f}% "
            f"(нефть {dir_ru}).\n"
            f"Свежие заголовки (только что собраны):\n{heads}\n"
            f"Доп.контекст: {extra_context or 'нет'}\n"
            "Задача: объяснить ЧТО ПРЯМО СЕЙЧАС двигает цену. "
            "Если заголовки старые/не объясняют ход — скажи честно.\n"
            "Не выдумывай entry/stop/TP. Не финансовый совет."
        )
        user = (
            "5–8 строк:\n"
            "1) Главный драйвер прямо сейчас (1–2 предложения)\n"
            "2) Какие 1–2 новости это подтверждают (или «лента пустая»)\n"
            "3) Риск сюрприза ±2–5% в ближайшие часы: низкий/средний/высокий\n"
            "4) Что смотреть дальше (1 фраза)"
        )
        result = await ask_gemini(
            api_key=api_key,
            model=model,
            context_text=ctx,
            user_text=user,
        )
        text = sanitize_ai_reply_for_telegram(result.text or "").strip()
        if result.error or not text:
            return ""
        if len(text) > 1200:
            text = text[:1197] + "…"
        return text
    except Exception:
        return ""
