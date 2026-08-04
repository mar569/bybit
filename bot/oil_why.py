"""Честный ответ «почему UKOUSD» — коротко, по-русски, без шума."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .bybit_klines import KlineBar
from .oil_flow import OilFlowProxy, compute_oil_flow_proxy

# Вторичные зеркала — не тащим в «почему сейчас»
_WEAK_WHY_SRC = (
    "india today",
    "indiatoday",
    "kurdistan24",
    "ottumwa",
    "toronto sun",
    "ynet",
    "msn.com",
    "edexlive",
)


@dataclass(frozen=True)
class OilWhyReport:
    direction: str  # up | down | flat
    move_1h_pct: float
    move_4h_pct: float
    price: float
    confidence: int
    plain_ru: str
    drivers_ru: tuple[str, ...]  # до 3 уникальных драйверов
    do_now_ru: str
    ai_now_ru: str = ""
    gap_note_ru: str = ""  # сильный ход, но свежей новости нет
    freshest_age_h: float | None = None


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _is_strong_move(m1: float, m4: float) -> bool:
    """Сильный ход «прямо сейчас» — нужна свежая причина, не фон 2–6ч назад."""
    return abs(m1) >= 0.25 or abs(m4) >= 0.70


def _freshest_age_hours(items: Sequence[Any], *, now: float | None = None) -> float | None:
    import time

    now_ts = now if now is not None else time.time()
    ages: list[float] = []
    for it in items:
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts > 0:
            ages.append((now_ts - ts) / 3600.0)
    if not ages:
        return None
    return min(ages)


def _move_from_bars(bars: Sequence[KlineBar], *, bars_back: int) -> float:
    if not bars or len(bars) < 2:
        return 0.0
    i = max(0, len(bars) - 1 - bars_back)
    return _pct(float(bars[-1].close), float(bars[i].close))


def _explain_headline(title: str) -> tuple[str, str, str]:
    """(коротко_по-русски, влияние_на_цену, side: up|down|mix)."""
    low = title.lower()
    if any(
        k in low
        for k in (
            "tumble", "slump", "plunge", "crash", "falls", "fell", "drops",
            "dropped", "decline", "обвал", "обруш", "паден", "рухн",
        )
    ):
        return (
            "В ленте — падение цены нефти",
            "Рынок уже снимает страх поставок / фиксирует прибыль вниз",
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
            "В ленте — рост цены нефти",
            "Рынок снова боится перебоев поставок",
            "up",
        )
    if any(
        k in low
        for k in (
            "taco", "chickens out", "backs off", "calls off", "called off",
            "cancels", "cancel", "pauses", "pause", "suspends", "suspend",
            "отмен", "струсил", "приостан", "отказался",
        )
    ) and any(k in low for k in ("attack", "strike", "удар", "атак", "bomb", "iran", "иран")):
        return (
            "США отложили или отменили удары по Ирану",
            "Страх войны слабеет → нефть обычно вниз",
            "down",
        )
    if any(
        k in low
        for k in (
            "offer to open", "reopen", "open strait", "open hormuz", "flows recover",
            "more crude flows", "ceasefire", "peace deal", "mou", "diplom",
            "agreement", "talks", "progress", "negotiat", "bessent",
            "сделк", "открыт", "перемир", "переговор", "прогресс",
        )
    ) and any(
        k in low
        for k in ("hormuz", "ормуз", "iran", "иран", "strait", "пролив")
    ):
        return (
            "США и Иран продвигают сделку / открытие Ормуза",
            "Если танкеры пойдут свободно — нефти больше → цена обычно падает",
            "down",
        )
    if any(k in low for k in ("sanction", "санкц", "tariff", "extortion")):
        return (
            "Новые санкции / давление на Иран",
            "Риск перекрытия поставок → цена часто растёт",
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
            "Удары / блок поставок / эскалация",
            "Страх, что нефть не пройдёт Ормуз → цена обычно растёт",
            "up",
        )
    if any(k in low for k in ("eia", "inventory", "запас", "stock")):
        if any(k in low for k in ("build", "rise", "рост запас", "избыт")):
            return ("Запасы нефти в США выросли", "Нефти много на складах → вниз", "down")
        if any(k in low for k in ("draw", "fall", "сниж", "упал")):
            return ("Запасы нефти в США упали", "Нефти на складах меньше → вверх", "up")
        return ("Данные по запасам США", "Сильный сюрприз может дёрнуть цену на часы", "mix")
    if any(k in low for k in ("opec", "опек", "quota", "квот")):
        return ("Новости ОПЕК по добыче", "Режут добычу → вверх; повышают → вниз", "mix")
    return ("Сюжет по нефти без ясного знака", "Смотри Ормуз и уровни на графике", "mix")


def _pick_news(
    items: Sequence[Any],
    *,
    limit: int = 3,
    prefer_direction: str = "",
    max_age_hours: float = 6.0,
    prefer_fresh_hours: float | None = None,
    strong_move: bool = False,
    now_ts: float | None = None,
) -> list[Any]:
    """Свежие уникальные сюжеты (без зеркал и дублей).

    При strong_move сначала берём только ≤prefer_fresh_hours (обычно 2ч).
    Старый фон не объясняет сильный ход «прямо сейчас».
    """
    import time

    from .oil_monitor import (
        _news_story_key,
        news_critical_score,
        oil_news_freshness_weight,
    )

    now = now_ts if now_ts is not None else time.time()
    fresh_cap = prefer_fresh_hours
    if strong_move and fresh_cap is None:
        fresh_cap = 2.0

    def _score_pool(pool_max_h: float) -> list[tuple[float, Any]]:
        scored: list[tuple[float, Any]] = []
        for it in items:
            title = (getattr(it, "title", "") or "").strip()
            if not title:
                continue
            src = (getattr(it, "source", "") or "").lower()
            if any(w in src for w in _WEAK_WHY_SRC):
                continue
            ts = float(getattr(it, "published_ts", 0) or 0)
            if ts > 0 and (now - ts) > pool_max_h * 3600:
                continue
            theme = getattr(it, "theme", "") or ""
            bonus = 5 if theme == "iran_geo" else 2 if theme in {"inventory", "opec", "analyst"} else 0
            if any(
                k in src
                for k in (
                    "reuters",
                    "bloomberg",
                    "wsj",
                    "ap ",
                    "nyt",
                    "financialjuice",
                    "forexlive",
                    "investinglive",
                )
            ):
                bonus += 3
            # X / wire — часто раньше Google News
            if src.startswith("x @") or " x @" in f" {src}" or src.startswith("x "):
                bonus += 4
            if any(k in src for k in ("financialjuice", "forexlive", "investinglive")):
                bonus += 2
            base = news_critical_score(title, source=getattr(it, "source", "")) + bonus
            fresh_w = oil_news_freshness_weight(ts if ts > 0 else None, now=now)
            score = base * (0.25 + 0.75 * fresh_w)
            _, _, side = _explain_headline(title)
            if prefer_direction and side == prefer_direction:
                score += 4
            elif prefer_direction and side != "mix" and side != prefer_direction:
                score -= 2
            age_h = (now - ts) / 3600.0 if ts > 0 else 99.0
            if age_h <= 0.5:
                score += 8
            elif age_h <= 1.0:
                score += 6
            elif age_h <= 2.0:
                score += 3
            elif strong_move and age_h > 2.0:
                score -= 6
            scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _dedupe(scored: list[tuple[float, Any]]) -> list[Any]:
        out: list[Any] = []
        seen_story: set[str] = set()
        for _, it in scored:
            story = _news_story_key(getattr(it, "title", "") or "")
            if story in seen_story:
                continue
            seen_story.add(story)
            out.append(it)
            if len(out) >= limit:
                break
        return out

    # Сильный ход → сначала только ультрасвежие
    if fresh_cap is not None and fresh_cap > 0:
        fresh_pool = _dedupe(_score_pool(fresh_cap))
        if fresh_pool:
            return fresh_pool
        if strong_move:
            # fallback шире, но вызывающий код покажет gap_note
            return _dedupe(_score_pool(max_age_hours))
    return _dedupe(_score_pool(max_age_hours))


def build_oil_why_report(
    bars: Sequence[KlineBar],
    *,
    news_items: Sequence[Any] | None = None,
    news_bias: Any | None = None,
    forecast: Any | None = None,
    flow: OilFlowProxy | None = None,
    interval_minutes: int = 15,
    ai_now_ru: str = "",
    inventory_ru: str = "",
) -> OilWhyReport | None:
    if not bars or len(bars) < 8:
        return None
    import time as _t

    now = _t.time()
    px = float(bars[-1].close)
    b1h = max(1, int(round(60 / max(5, interval_minutes))))
    b4h = max(b1h + 1, int(round(240 / max(5, interval_minutes))))
    m1 = _move_from_bars(bars, bars_back=b1h)
    m4 = _move_from_bars(bars, bars_back=min(b4h, len(bars) - 1))
    strong = _is_strong_move(m1, m4)

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
    has_geo = any((getattr(it, "theme", "") or "") == "iran_geo" for it in items)

    # При сильном ходе — только сюжеты ≤2ч; иначе фон 6ч
    prefer_h = 2.0 if (strong or direction != "flat") else None
    picked = _pick_news(
        items,
        limit=3,
        prefer_direction=direction,
        max_age_hours=6.0,
        prefer_fresh_hours=prefer_h,
        strong_move=strong,
        now_ts=now,
    )
    freshest = _freshest_age_hours(picked, now=now)
    gap_note = ""
    if strong and (freshest is None or freshest > 2.0):
        gap_note = (
            "Сильный ход цены сейчас, а в ленте (X / FinancialJuice / Reuters) "
            "свежей причины ≤2ч не видно — старые сюжеты это не объясняют. "
            "Смотри уровни или жди новый flash."
        )
        conf_base = 3
    elif strong and freshest is not None and freshest <= 1.0:
        conf_base = 8
    elif picked:
        conf_base = 7
    else:
        conf_base = 3

    drivers: list[str] = []
    up_news = down_news = 0
    for it in picked:
        title = getattr(it, "title", "") or ""
        what, means, side = _explain_headline(title)
        src = (getattr(it, "source", "") or "").strip()
        age = ""
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts > 0:
            age_h = (now - ts) / 3600.0
            if age_h < 1:
                age = f"{int(age_h * 60)}м"
            elif age_h < 48:
                age = f"{age_h:.0f}ч"
        meta = " · ".join(x for x in (src, age) if x)
        # Старые драйверы при сильном ходе помечаем
        stale_tag = ""
        if strong and ts > 0 and (now - ts) > 2 * 3600:
            stale_tag = " · <i>уже не свежий фон</i>"
        drivers.append(
            f"<b>{what}</b>"
            + (f" <i>({meta})</i>" if meta else "")
            + stale_tag
            + f"\n→ {means}"
        )
        if side == "up":
            up_news += 1
        elif side == "down":
            down_news += 1

    if not drivers:
        if strong:
            drivers.append(
                "Живой сбор (X, wire, RSS) не нашёл свежего драйвера под этот ход — "
                "скорее техника/уровень или новость ещё не в открытых лентах."
            )
        else:
            drivers.append("Свежих сильных заголовков мало — ход скорее от графика/уровня.")

    if inventory_ru and not gap_note:
        drivers.append(inventory_ru.strip())
    elif inventory_ru and not strong:
        drivers.append(inventory_ru.strip())

    conflict = ""
    conf = conf_base
    if direction == "up" and down_news > up_news and not gap_note:
        conflict = "В ленте больше про сделку/Ормуз (обычно вниз), а цена растёт — часто короткий отскок."
        conf = min(conf, 4)
    elif direction == "down" and up_news > down_news and not gap_note:
        conflict = "В ленте больше угроз (обычно вверх), а цена падает — рынок уже мог «выдохнуть» страх."
        conf = min(conf, 4)
    elif direction == "up" and news == "bearish" and not gap_note:
        conflict = "Фон новостей скорее давит вниз — рост может быстро сдуться."
        conf = min(conf, 4)
    elif direction == "flat":
        conf = min(conf, 5)

    if fc_scen == "deal_tape" and direction == "up" and not gap_note:
        conflict = conflict or "Фон «переговоры / больше поставок» обычно мешает долгому росту."

    if direction == "up":
        if gap_note:
            plain = (
                f"Нефть растёт сейчас (≈${px:.2f}, за час +{abs(m1):.2f}%). "
                "Ищем свежую причину в X/wire — старые новости сюда не подставляем."
            )
        elif up_news >= down_news and has_geo:
            plain = (
                f"Нефть растёт (≈${px:.2f}, за час +{abs(m1):.2f}%). "
                "Рынок снова нервничает из‑за Ирана/Ормуза: боятся перебоев поставок."
            )
        else:
            plain = (
                f"Нефть растёт (≈${px:.2f}, +{abs(m1):.2f}% за час), "
                "но по новостям картина смешанная — часто короткий отскок."
            )
        do_now = (
            "Не шортить против сильного страха по Ормузу. "
            "Лонг — только от поддержки, без погони."
        )
    elif direction == "down":
        if gap_note:
            plain = (
                f"Нефть падает сейчас (≈${px:.2f}, за час −{abs(m1):.2f}%, за 4ч {m4:+.2f}%). "
                "Ищем свежую причину в X/wire — фон 2+ часов назад этот ход не объясняет."
            )
        else:
            plain = (
                f"Нефть падает (≈${px:.2f}, за час −{abs(m1):.2f}%, за 4ч {m4:+.2f}%). "
                "Чаще всего так, когда рынок ждёт: танкеры снова пойдут / напряжение слабее — "
                "страх поставок снимают и цену отпускают вниз."
            )
        do_now = (
            "Не ловить нож лонгом. Покупать только от сильной поддержки "
            "или когда снова новости про блок/удары."
        )
    else:
        plain = (
            f"Нефть около ${px:.2f} почти стоит. "
            "Ясного драйвера нет — лучше уровень или свежий сильный заголовок."
        )
        do_now = "Не выдумывать причину. Ждать касания уровня или громкую новость."

    if conflict:
        do_now = f"{conflict} {do_now}"
    # gap_note показывается отдельно в format — не дублируем в «Делать»

    return OilWhyReport(
        direction=direction,
        move_1h_pct=round(m1, 2),
        move_4h_pct=round(m4, 2),
        price=px,
        confidence=conf,
        plain_ru=plain,
        drivers_ru=tuple(drivers[:4]),
        do_now_ru=do_now,
        ai_now_ru=(ai_now_ru or "").strip(),
        gap_note_ru=gap_note,
        freshest_age_h=freshest,
    )


def format_oil_why_report(rep: OilWhyReport) -> str:
    mark = {"up": "📈", "down": "📉", "flat": "➡️"}.get(rep.direction, "➡️")
    dir_ru = {"up": "растёт", "down": "падает", "flat": "почти стоит"}.get(
        rep.direction, ""
    )
    lines = [
        f"{mark} <b>Почему нефть {dir_ru}</b> · {rep.confidence}/10",
        f"<i>${rep.price:.2f} · 1ч {rep.move_1h_pct:+.2f}% · 4ч {rep.move_4h_pct:+.2f}%</i>",
        "",
        _esc(rep.plain_ru),
    ]
    if rep.gap_note_ru:
        lines.append("")
        lines.append(f"⚠️ {_esc(rep.gap_note_ru)}")
    if rep.ai_now_ru:
        lines.append("")
        lines.append("<b>Сейчас</b>")
        lines.append(_esc(rep.ai_now_ru))
    lines.append("")
    lines.append("<b>Драйверы</b>")
    for d in rep.drivers_ru:
        lines.append(f"• {_soft_html(d)}")
    lines.append("")
    lines.append(f"<b>Делать:</b> {_esc(rep.do_now_ru)}")
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _soft_html(text: str) -> str:
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
    strong_move: bool = False,
    freshest_age_h: float | None = None,
) -> str:
    """Короткий ИИ-разбор только по-русски."""
    if not api_key:
        return ""
    try:
        from .ai_analyst import ask_gemini, sanitize_ai_reply_for_telegram

        dir_ru = {"up": "растёт", "down": "падает", "flat": "почти стоит"}.get(
            direction, direction
        )
        heads = "\n".join(f"- {h}" for h in headlines[:8]) or "- (свежих заголовков нет)"
        age_hint = (
            f"Самый свежий заголовок ≈{freshest_age_h:.1f}ч назад."
            if freshest_age_h is not None
            else "Свежих заголовков в ленте нет."
        )
        strong_hint = (
            "Ход цены СИЛЬНЫЙ прямо сейчас — объясняй только новостями ≤2ч. "
            "Старый фон (переговоры утром и т.п.) НЕ считай причиной текущего хода. "
            "Если свежих нет — так и скажи."
            if strong_move
            else "Предпочитай самые свежие сюжеты."
        )
        ctx = (
            "Ты аналитик нефти Brent (UKOUSD). Пиши ТОЛЬКО по-русски. "
            "Не копируй английские заголовки — перескажи смысл.\n"
            f"Цена ≈${price:.2f}, 1ч {move_1h:+.2f}%, 4ч {move_4h:+.2f}% "
            f"(нефть {dir_ru}).\n"
            f"{strong_hint}\n{age_hint}\n"
            f"Заголовки (свежие сверху):\n{heads}\n"
            f"Запасы/фон: {extra_context or 'нет'}\n"
            "Без entry/stop/TP. Не финсовет."
        )
        user = (
            "Ровно 3 коротких пункта по-русски:\n"
            "1) Главный драйвер СЕЙЧАС (только если свежий; иначе «свежей причины в ленте нет»)\n"
            "2) Что это значит для цены (1 фраза)\n"
            "3) На что смотреть дальше (1 фраза)\n"
            "Без английских цитат и без воды."
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
        if len(text) > 450:
            text = text[:447] + "…"
        return text
    except Exception:
        return ""
