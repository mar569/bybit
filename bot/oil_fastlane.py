"""Fast-lane нефтяных новостей: WSJ / Reuters / Bloomberg / Blas / FT / NYT / official."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlparse

from .bybit_klines import KlineBar

logger = logging.getLogger(__name__)

# Узкий набор — только топ wire / эксклюзивы / official (site: — не тянем tertiary)
FAST_LANE_QUERIES_EN: tuple[str, ...] = (
    "site:wsj.com Iran OR Hormuz OR oil OR Trump when:12h",
    "site:reuters.com Iran OR Hormuz OR oil OR Trump when:12h",
    "site:bloomberg.com Iran OR Hormuz OR oil OR energy OR Trump when:12h",
    "site:apnews.com Iran OR Hormuz OR Trump oil OR strike OR cancel when:12h",
    "site:investing.com oil OR Brent OR WTI OR Hormuz OR Iran OR crude when:12h",
    "Javier Blas oil OR Hormuz OR Iran OR Brent when:12h",
    "site:ft.com oil OR Iran OR Hormuz OR Brent when:1d",
    "site:nytimes.com Iran OR Hormuz OR oil OR Trump when:1d",
    "site:whitehouse.gov Iran OR Hormuz OR oil when:1d",
    "site:defense.gov Iran OR Hormuz OR oil when:1d",
    "EIA crude oil inventory OR STEO when:1d",
    "site:reuters.com OR site:bloomberg.com OR site:wsj.com Trump cancels OR pauses OR TACO Iran strike when:12h",
    "site:reuters.com OR site:bloomberg.com Strait of Hormuz tanker OR blockade OR reopen when:12h",
    # Иранская линия (Fars / Tasnim) — контр-нарратив к «deal tape»
    "site:farsnews.ir Hormuz OR Strait OR Iran OR tanker OR oil when:12h",
    "Fars News Agency Hormuz OR Iran Strait OR refuse OR reject when:12h",
    "site:tasnimnews.com Hormuz OR Iran oil OR Strait when:12h",
    "site:en.irna.ir Hormuz OR Strait OR tanker OR oil when:12h",
)

FAST_LANE_QUERIES_RU: tuple[str, ...] = (
    "WSJ Трамп Иран удар OR атака нефть when:12h",
    "Reuters Ормуз нефть Иран when:12h",
    "Bloomberg Ормуз OR Иран нефть when:12h",
    "investing.com нефть OR Brent OR Ормуз OR Иран when:12h",
    "Reuters OR Bloomberg Трамп отменил удар Иран нефть when:12h",
    "Fars Ормуз OR Иран пролив нефть when:12h",
    "site:farsnews.ir Ормуз OR Иран when:12h",
)

# Needle только в source/url (НЕ в title) — иначе EdexLive «White House signals…» = fake Tier-1
_TIER1_SOURCES: tuple[tuple[str, str, int], ...] = (
    ("wall street journal", "WSJ", 1),
    ("wsj.com", "WSJ", 1),
    ("reuters.com", "Reuters", 1),
    ("reuters", "Reuters", 1),
    ("bloomberg.com", "Bloomberg", 1),
    ("bloomberg", "Bloomberg", 1),
    ("apnews.com", "AP", 1),
    ("associated press", "AP", 1),
    ("javier blas", "Javier Blas", 1),
    ("investing.com", "Investing.com", 1),
    ("ft.com", "FT", 2),
    ("financial times", "FT", 2),
    ("nytimes.com", "NYT", 2),
    ("new york times", "NYT", 2),
    ("whitehouse.gov", "White House", 1),
    ("defense.gov", "DoD", 1),
    ("eia.gov", "EIA", 1),
    ("energy information administration", "EIA", 1),
    # Иранская гос. линия — важна для Ормуза (не WSJ, но primary для Тегерана)
    ("farsnews.ir", "Fars", 2),
    ("fars news", "Fars", 2),
    ("farsnews", "Fars", 2),
    ("tasnimnews.com", "Tasnim", 2),
    ("tasnim", "Tasnim", 2),
    ("irna.ir", "IRNA", 2),
)

# Домены-синдикаты / образовательные зеркала — не пускать в ‼️
_SYNDICATE_HOST_DENY: tuple[str, ...] = (
    "edexlive.com",
    "edexlive",
    "indiatoday.in",
    "ndtv.com",
    "timesofindia",
    "hindustantimes.com",
    "thehindu.com",
    "news18.com",
    "msn.com",
    "yahoo.com",
    "news.google.com",
)

# Баллы только по ЗАГОЛОВКУ (не по source — иначе любой WSJ даёт flash)
_FLASH_TERMS: dict[str, int] = {
    "attack": 5,
    "strike": 5,
    "удар": 5,
    "атак": 5,
    "orders attack": 6,
    "ordered a": 4,
    "taco": 5,
    "cancels": 4,
    "cancel": 3,
    "pauses": 4,
    "holds off": 4,
    "hold off": 4,
    "tumble": 4,
    "tumbles": 4,
    "slump": 3,
    "plunge": 3,
    "hormuz": 5,
    "ормуз": 5,
    "blockade": 4,
    "close strait": 5,
    "reopen": 3,
    "ceasefire": 3,
    "sanction": 3,
    "trump": 3,
    "трамп": 3,
    "iran": 3,
    "иран": 3,
    "oil": 3,
    "crude": 3,
    "brent": 3,
    "нефт": 3,
    "opec": 2,
    "eia": 2,
    "tanker": 3,
    "танкер": 3,
    "javier blas": 4,
    "pentagon": 3,
    "white house": 3,
    "fars": 3,
    "tasnim": 2,
    "refuse": 3,
    "reject": 3,
    "will not open": 4,
    "won't open": 4,
    "won't negotiate": 4,
    "refuse to reopen": 4,
    "refuses to reopen": 4,
    "will not reopen": 4,
    "won't reopen": 4,
    "no agreement to reopen": 4,
}

# Без этого в title — не шлём, даже если outlet = WSJ
# Короткие токены (spr, eia, wti) — только целыми словами (иначе spr⊂Spreading).
_TOPIC_OIL: tuple[str, ...] = (
    "oil", "crude", "brent", "petroleum", "gasoline", "diesel",
    "нефт", "баррель", "barrel", "opec", "опек",
    "energy", "энерг", "commodity", "commodit", "fuel oil",
)
_TOPIC_OIL_WORDS: tuple[str, ...] = (
    "wti", "eia", "spr", "fuel",
)
_TOPIC_GEO: tuple[str, ...] = (
    "iran", "иран", "tehran", "тегеран", "hormuz", "ормуз", "strait",
    "houthi", "хусит", "persian gulf", "middle east", "ближн",
    "israel", "израил", "hezbollah", "red sea",
)


def _has_whole_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-zа-я0-9]){re.escape(word)}(?![a-zа-я0-9])", text) is not None


def _url_host(url: str) -> str:
    try:
        host = (urlparse(url or "").netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def is_syndicate_host(url: str = "", source: str = "") -> bool:
    """Tertiary зеркала PTI/AP (EdexLive и т.п.) — не primary wire."""
    host = _url_host(url)
    blob = f"{host} {(source or '').lower()}"
    return any(d in blob for d in _SYNDICATE_HOST_DENY)


@dataclass(frozen=True)
class FastLaneMeta:
    outlet: str
    tier: int  # 1 or 2
    flash_score: int
    is_flash: bool
    publisher: str = ""  # реальный RSS source / host


def fastlane_title_on_topic(title: str) -> bool:
    """Заголовок про нефть / Ормуз / Иран / энерго-гео — иначе шум (мода, спорт…)."""
    low = (title or "").lower()
    if not low.strip():
        return False
    if any(k in low for k in _TOPIC_OIL):
        return True
    if any(_has_whole_word(low, w) for w in _TOPIC_OIL_WORDS):
        return True
    if any(k in low for k in _TOPIC_GEO):
        return True
    # Санкции/атака США — только вместе с нефтью или Ираном (не любой Trump-заголовок)
    if any(k in low for k in ("sanction", "санкц", "attack", "strike", "удар", "атак")) and any(
        k in low for k in ("oil", "crude", "нефт", "iran", "иран", "hormuz", "ормуз", "energy")
    ):
        return True
    return False


def detect_fastlane_outlet(title: str, source: str = "", url: str = "") -> FastLaneMeta | None:
    """Outlet = реальный publisher (source/url), НЕ слова из заголовка.

    «White House signals…» на edexlive.com → None (не Tier-1 White House).
    """
    if is_syndicate_host(url, source):
        return None

    # Только source + host — title не участвует в определении outlet
    # (исключение: именные аналитики Javier Blas — часто в title, не в domain)
    host = _url_host(url)
    provenance = f"{source or ''} {host} {url or ''}".lower()
    title_l = (title or "").lower()
    analyst_blob = f"{provenance} {title_l}"

    best: tuple[str, int] | None = None
    for needle, display, tier in _TIER1_SOURCES:
        blob = analyst_blob if display == "Javier Blas" else provenance
        if needle not in blob:
            continue
        if best is None or tier < best[1]:
            best = (display, tier)
        elif tier == best[1] and display in {"WSJ", "Javier Blas", "Bloomberg", "Reuters", "AP"}:
            # Именной аналитик важнее общего Bloomberg Opinion
            if display == "Javier Blas":
                best = (display, tier)
            elif best[0] != "Javier Blas" and display in {"WSJ", "Bloomberg", "Reuters", "AP"}:
                best = (display, tier)

    if best is None:
        return None

    score = 0
    for term, w in _FLASH_TERMS.items():
        if term in title_l:
            score += w
    score += 4 if best[1] == 1 else 2
    is_flash = score >= 8
    publisher = (source or "").strip() or host or best[0]
    return FastLaneMeta(
        outlet=best[0],
        tier=best[1],
        flash_score=score,
        is_flash=is_flash,
        publisher=publisher,
    )


def is_fastlane_item(item: Any, *, min_flash_score: int = 7) -> bool:
    title = getattr(item, "title", "") or ""
    if not fastlane_title_on_topic(title):
        return False
    meta = detect_fastlane_outlet(
        title,
        getattr(item, "source", "") or "",
        getattr(item, "url", "") or "",
    )
    if meta is None:
        return False
    # Tier-1 geo/oil always; tier-2 needs higher score
    if meta.tier == 1 and meta.flash_score >= min_flash_score:
        return True
    # Иранские агентства (Fars/Tasnim/IRNA) — тот же порог, что Tier-1 по Ормузу
    if meta.outlet in {"Fars", "Tasnim", "IRNA"} and meta.flash_score >= min_flash_score:
        return True
    if meta.tier == 2 and meta.flash_score >= min_flash_score + 2:
        return True
    return meta.is_flash


def ai_says_off_topic(ai_text: str) -> bool:
    """Gemini пометил новость как не про нефть → не слать в чат."""
    raw = (ai_text or "").strip()
    if not raw:
        return False
    head = raw[:220].upper().replace(" ", "")
    if "OIL_RELEVANT:NO" in head or "OIL_RELEVANT：NO" in head:
        return True
    if "OIL_RELEVANT:YES" in head or "OIL_RELEVANT：YES" in head:
        return False
    low = raw.lower()
    strong_no = (
        "никак не относится к нефт",
        "абсолютно никак не относится",
        "не относится к нефт",
        "влияние на котировки ukousd нулев",
        "влияние на нефть: нет",
        "no_trade",
        "информационным шумом",
        "полностью игнорировать эту новость",
    )
    return any(p in low for p in strong_no)


def _price_move_note(bars: Sequence[KlineBar] | None, *, interval_minutes: int = 5) -> str:
    """Если цена уже скакнула — рынок мог опередить заголовок."""
    if not bars or len(bars) < 6:
        return ""
    b30 = max(1, int(round(30 / max(5, interval_minutes))))
    b60 = max(b30 + 1, int(round(60 / max(5, interval_minutes))))
    px = float(bars[-1].close)
    p30 = float(bars[-1 - min(b30, len(bars) - 1)].close)
    p60 = float(bars[-1 - min(b60, len(bars) - 1)].close)
    if p30 <= 0 or p60 <= 0:
        return ""
    m30 = (px - p30) / p30 * 100.0
    m60 = (px - p60) / p60 * 100.0
    if abs(m30) < 0.35 and abs(m60) < 0.5:
        return ""
    if abs(m30) >= 0.8 or abs(m60) >= 1.2:
        return (
            f"⚠️ Движение опережает новость: прокси-цена уже "
            f"{m30:+.2f}% / 30м и {m60:+.2f}% / 1ч (≈${px:.2f}). "
            "Часто так при слухах до полного текста WSJ/Reuters."
        )
    return (
        f"Цена уже сдвинулась: {m30:+.2f}% / 30м, {m60:+.2f}% / 1ч (≈${px:.2f})."
    )


def _bounce_hint(impact: str, move_note: str) -> str:
    """После сильного импульса часто бывает отскок — простыми словами."""
    strong = "опережает" in move_note or "уже" in move_note
    if impact == "bullish":
        if strong:
            return (
                "Цена уже сильно скакнула вверх — часто потом чуть откатывает. "
                "Не догонять хай; лонг только от уровня."
            )
        return (
            "Страх поставок тянет цену вверх. Против тренда шортить опасно; "
            "лонг — от поддержки, не в середине импульса."
        )
    if impact == "bearish":
        if strong:
            return (
                "Цена уже сильно упала — часто бывает короткий отскок вверх. "
                "Не ловить нож лонгом; шорт от сопротивления."
            )
        return (
            "Страх войны/блока слабеет — давление вниз. "
            "Лонг только от сильной поддержки."
        )
    return "Картина смешанная — ждать уровень и следующий сильный заголовок."


async def enrich_fastlane_with_gemini(
    title: str,
    *,
    source: str,
    outlet: str,
    impact: str,
    move_note: str,
    api_key: str | None,
    model: str = "gemini-3.6-flash",
) -> tuple[str, str | None]:
    """Короткий ИИ-разбор для UKOUSD.

    Returns (text, bias_override) where bias_override is bullish|bearish|neutral|None.
    Первые строки: OIL_RELEVANT + OIL_BIAS.
    """
    if not api_key:
        return "", None
    try:
        from .ai_analyst import ask_gemini, sanitize_ai_reply_for_telegram

        ctx = (
            "Ты профессиональный трейдер нефти (Brent / UKOUSD). "
            "Пиши ТОЛЬКО по-русски, простыми словами, без англ. жаргона "
            "(не пиши gap, bias, deal-tape — говори «скорее подешевеет», «страх войны»).\n"
            f"Издатель (реальный): {source}\n"
            f"Канал (если primary wire): {outlet}\n"
            f"Заголовок: {title}\n"
            f"Черновой тон бота (может ОШИБАТЬСЯ): {impact} — НЕ копируй слепо.\n"
            f"Цена: {move_note or 'без сильного сдвига до новости'}\n"
            "Правила направления:\n"
            "- tumbles/falls/обвал → вниз.\n"
            "- отмена/пауза ударов, TACO, сделка, открытие Ормуза → вниз.\n"
            "- новые удары/блок пролива без отмены → вверх.\n"
            "- attack само по себе НЕ вверх, если рядом cancel/pause/TACO.\n"
            "- если заголовок про «weighing / considering strikes», а рынок уже "
            "знает отмену/сделку — это СТАРЫЙ нарратив, не разгоняй LONG.\n"
            "ЗАПРЕЩЕНО: цены входа, стоп, TP, уровни вроде $74.50, «ВЕРДИКТ: LONG», "
            "«открываю покупку», RR. Только направление и осторожность.\n"
        )
        user = (
            "Строка 1 СТРОГО: OIL_RELEVANT: YES или OIL_RELEVANT: NO\n"
            "Строка 2 (если YES): OIL_BIAS: UP|DOWN|MIXED\n\n"
            "YES — только нефть/Ормуз/санкции/запасы/ОПЕК. NO — одной фразой почему шум.\n\n"
            "Если YES — ровно такой каркас (простые слова, 7–10 строк):\n"
            "📌 Что случилось — 1–2 предложения, факт\n"
            "💡 Что это значит для Brent/UKOUSD — почему цена может дёрнуться\n"
            "👀 Что ждать дальше — 1–2 clarifier (Reuters/AP, Ормуз, EIA)\n"
            "⚡ Риск сюрприза — может ли дать резкие ±2–5% в ближайшие часы/сессию "
            "(низкий/средний/высокий) и от чего\n"
            "🧭 Как аккуратно — без цифр входа/стопа: не догонять / ждать / осторожно\n"
            "Не выдумывай факты и уровни. Если похоже на репост вчерашнего — скажи прямо."
        )
        result = await ask_gemini(
            api_key=api_key,
            model=model,
            context_text=ctx,
            user_text=user,
        )
        text = sanitize_ai_reply_for_telegram(result.text or "").strip()
        if result.error or not text:
            return "", None
        bias_override = parse_gemini_oil_bias(text)
        text = strip_invented_trade_levels(text)
        if len(text) > 1400:
            text = text[:1397] + "…"
        return text, bias_override
    except Exception:
        logger.exception("Fast-lane Gemini failed")
        return "", None


def parse_gemini_oil_bias(ai_text: str) -> str | None:
    """Извлекает OIL_BIAS: UP|DOWN|MIXED → bullish|bearish|neutral."""
    for line in (ai_text or "").splitlines()[:6]:
        up = line.upper().replace(" ", "")
        if "OIL_BIAS:UP" in up or "OIL_BIAS：UP" in up:
            return "bullish"
        if "OIL_BIAS:DOWN" in up or "OIL_BIAS：DOWN" in up:
            return "bearish"
        if "OIL_BIAS:MIXED" in up or "OIL_BIAS:NEUTRAL" in up:
            return "neutral"
    return None


def strip_gemini_oil_meta(ai_text: str) -> str:
    """Убирает служебные OIL_RELEVANT / OIL_BIAS из текста для чата."""
    lines = (ai_text or "").splitlines()
    kept: list[str] = []
    for line in lines:
        u = line.upper().replace(" ", "")
        if u.startswith("OIL_RELEVANT:") or u.startswith("OIL_BIAS:"):
            continue
        if "OIL_RELEVANT" in u and len(u) < 24:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def strip_invented_trade_levels(ai_text: str) -> str:
    """Убирает выдуманные entry/stop/TP / «ВЕРДИКТ: LONG» из flash-ИИ."""
    if not ai_text:
        return ""
    lines: list[str] = []
    skip_re = re.compile(
        r"(вход|стоп|take.?profit|тейк|tp\s*[12]?|entry|stop.?loss|"
        r"вердикт\s*:\s*long|вердикт\s*:\s*short|открываю\s+(покуп|прод)|"
        r"\$\s*\d{2,3}(?:[.,]\d+)?|"
        r"\d{2,3}(?:[.,]\d+)?\s*\$?\s*/\s*барр|"
        r"rr\s*\d|риск/?награда)",
        re.IGNORECASE,
    )
    for line in ai_text.splitlines():
        if skip_re.search(line) and (
            "вход" in line.lower()
            or "стоп" in line.lower()
            or "tp" in line.lower()
            or "тейк" in line.lower()
            or "вердикт" in line.lower()
            or "открываю" in line.lower()
            or "$" in line
            or "барр" in line.lower()
        ):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def format_fastlane_flash(
    item: Any,
    *,
    meta: FastLaneMeta,
    ai_ru: str = "",
    move_note: str = "",
    age_label: str = "",
) -> str:
    """Сообщение ‼️ КРИТИЧНО в Новостник — суть сверху, детали без простыни."""
    title = (getattr(item, "title", "") or "").replace("<", "&lt;").replace(">", "&gt;")
    url = getattr(item, "url", "") or ""
    impact = getattr(item, "impact", "neutral") or "neutral"
    impact_ru = {
        "bullish": "🟢 нефть скорее ВВЕРХ",
        "bearish": "🔴 нефть скорее ВНИЗ",
        "neutral": "⚪ влияние смешанное",
    }.get(impact, "⚪ контекст")
    bang = "‼️‼️" if meta.tier == 1 and meta.flash_score >= 12 else "‼️"
    # В шапке — реальный wire (Reuters), не слово из title
    lines = [
        f"{bang} <b>ВАЖНО ДЛЯ НЕФТИ</b> · {meta.outlet}",
        f"{impact_ru}",
        "",
    ]
    if url:
        lines.append(f"<a href=\"{url}\"><b>{title}</b></a>")
    else:
        lines.append(f"<b>{title}</b>")
    lines.append("")
    lines.append(_bounce_hint(impact, move_note))
    if move_note:
        lines.append("")
        lines.append(_esc(move_note))
    if ai_ru:
        lines.append("")
        lines.append("🤖 <b>Разбор</b>")
        lines.append(_esc(ai_ru))
    else:
        # Без Gemini — минимальный проф-каркас по правилам
        lines.append("")
        lines.append("🧭 <b>Кратко</b>")
        lines.append(_rule_brief(impact, title))
    lines.append("")
    src = (getattr(item, "source", "") or meta.publisher or meta.outlet).strip()
    age = age_label or ""
    lines.append(
        f"<i>{src}"
        + (f" · {age}" if age else "")
        + f" · вес {meta.flash_score}</i>"
    )
    if url:
        lines.append(f"🔗 <a href=\"{url}\">Источник</a>")
    lines.append("<i>Не финансовый совет. Без выдуманных уровней входа.</i>")
    return "\n".join(lines)


def _rule_brief(impact: str, title: str) -> str:
    low = (title or "").lower()
    surprise = "средний"
    if any(k in low for k in ("strike", "attack", "blockade", "closed", "удар", "атак")):
        surprise = "высокий"
    if any(k in low for k in ("cancel", "taco", "pause", "holds off", "отмен")):
        surprise = "высокий"
    if any(k in low for k in ("eia", "inventory", "spr", "запас")):
        surprise = "средний"
    dir_ru = {
        "bullish": "давление вверх (страх поставок / тугой склад)",
        "bearish": "давление вниз (деэскалация / избыток)",
        "neutral": "смешанно — нужен clarifier",
    }.get(impact, "смешанно")
    return (
        f"💡 Значит: {dir_ru}.\n"
        f"👀 Ждать: подтверждение от Reuters/AP/Bloomberg, не tertiary-репост.\n"
        f"⚡ Сюрприз ±2–5%: <b>{surprise}</b> на гео/официальных заявлениях.\n"
        f"🧭 Аккуратно: не догонять импульс; торговать только на открытой сессии Bybit."
    )


def is_trade_critical_flash(
    item: Any,
    meta: FastLaneMeta,
    *,
    min_score: int = 10,
) -> bool:
    """Достаточно громко, чтобы дублировать в ручной анализ (сделка)."""
    impact = getattr(item, "impact", "neutral") or "neutral"
    if impact not in {"bullish", "bearish"}:
        return False
    if meta.flash_score < min_score and meta.tier > 1:
        return False
    if meta.tier == 1 and meta.flash_score >= min_score:
        return True
    # Tier-2 Investing и т.п. — только очень громкий score
    return meta.flash_score >= min_score + 3


def format_trade_impact_for_manual_ta(
    item: Any,
    *,
    meta: FastLaneMeta,
    ai_ru: str = "",
    move_note: str = "",
) -> str:
    """Краткий разбор для чата ручного TA — влияет на сделку."""
    title = (getattr(item, "title", "") or "").replace("<", "&lt;").replace(">", "&gt;")
    url = getattr(item, "url", "") or ""
    impact = getattr(item, "impact", "neutral") or "neutral"
    dir_ru = {
        "bullish": "🟢 вверх",
        "bearish": "🔴 вниз",
    }.get(impact, "⚪ смешанно")
    lines = [
        "🛢 <b>Новость влияет на сделку</b>",
        f"Направление: <b>{dir_ru}</b> · {meta.outlet}",
        "",
    ]
    if url:
        lines.append(f"<a href=\"{url}\"><b>{title}</b></a>")
    else:
        lines.append(f"<b>{title}</b>")
    lines.append("")
    lines.append(_bounce_hint(impact, move_note))
    if move_note:
        lines.append(_esc(move_note))
    if ai_ru:
        lines.append("")
        lines.append("🤖 <b>Главное</b>")
        lines.append(_esc(ai_ru))
    lines.append("")
    lines.append(
        "<i>Сверь с графиком UKOUSD. Не финсовет. "
        "Кнопки ниже — почему / открытие / Ормуз / ИИ.</i>"
    )
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def filter_fastlane_items(
    items: Sequence[Any],
    *,
    min_flash_score: int = 7,
    max_age_hours: float = 4.0,
) -> list[Any]:
    now = time.time()
    out: list[Any] = []
    for it in items:
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts and (now - ts) > max_age_hours * 3600:
            continue
        if is_fastlane_item(it, min_flash_score=min_flash_score):
            out.append(it)
    out.sort(
        key=lambda x: (
            float(getattr(x, "published_ts", 0) or 0),
            (
                detect_fastlane_outlet(
                    getattr(x, "title", "") or "",
                    getattr(x, "source", "") or "",
                    getattr(x, "url", "") or "",
                )
                or FastLaneMeta(outlet="", tier=9, flash_score=0, is_flash=False)
            ).flash_score,
        ),
        reverse=True,
    )
    return out
