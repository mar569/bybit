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

# Сначала deal/Trump/Bessent/Иран·Ормуз (то, что валит цену за минуты)
FAST_LANE_QUERIES_EN: tuple[str, ...] = (
    # Трамп: Truth Social / X / TACO
    "Trump Truth Social Iran OR Hormuz OR deal OR reopen OR strike when:12h",
    "Trump posts OR tweet OR Truth Social Iran OR Hormuz OR oil when:12h",
    "site:truthsocial.com Trump Iran OR Hormuz OR oil OR deal when:12h",
    "Trump called off OR cancels OR delays Iran strike OR attack when:12h",
    "Trump Iran negotiations OR last chance OR Hormuz reopen when:12h",
    # Бессент / Treasury — сильно двигает premium
    "Bessent Iran OR Hormuz OR oil OR energy OR deal when:12h",
    "Scott Bessent Treasury Hormuz OR Iran OR oil OR reopen when:12h",
    "Treasury Secretary Bessent oil OR energy OR Iran OR Hormuz when:12h",
    # Иран ↔ Ормуз: открывает / не открывает
    "Iran reopen OR open OR refuse OR deny OR reject Strait of Hormuz when:12h",
    "Iran will not open OR won't reopen Hormuz OR denies talks when:12h",
    "Tehran Hormuz reopen OR blockade OR tanker OR shipping when:12h",
    "site:reuters.com OR site:bloomberg.com Trump cancels OR pauses OR TACO Iran strike when:12h",
    "site:reuters.com OR site:bloomberg.com Strait of Hormuz tanker OR blockade OR reopen when:12h",
    # Трейдерские агрегаторы (зеркало прямого RSS)
    "site:financialjuice.com oil OR crude OR Brent OR Iran OR Hormuz OR Trump OR Bessent when:12h",
    "site:forexlive.com OR site:investinglive.com oil OR Brent OR Iran OR Hormuz OR Trump OR Bessent when:12h",
    "site:wsj.com Iran OR Hormuz OR oil OR Trump when:12h",
    "site:reuters.com Iran OR Hormuz OR oil OR Trump when:12h",
    "site:bloomberg.com Iran OR Hormuz OR oil OR energy OR Trump OR Bessent when:12h",
    "site:apnews.com Iran OR Hormuz OR Trump oil OR strike OR cancel when:12h",
    "site:investing.com oil OR Brent OR WTI OR Hormuz OR Iran OR crude when:12h",
    "Javier Blas oil OR Hormuz OR Iran OR Brent when:12h",
    "site:ft.com oil OR Iran OR Hormuz OR Brent when:1d",
    "site:nytimes.com Iran OR Hormuz OR oil OR Trump when:1d",
    "site:whitehouse.gov Iran OR Hormuz OR oil when:1d",
    "EIA crude oil inventory OR STEO when:1d",
    "site:farsnews.ir Hormuz OR Strait OR Iran OR tanker OR oil when:12h",
    "Fars News Agency Hormuz OR Iran Strait OR refuse OR reject OR reopen when:12h",
    "site:tasnimnews.com Hormuz OR Iran oil OR Strait when:12h",
    "site:en.irna.ir Hormuz OR Strait OR tanker OR oil OR reopen when:12h",
)

FAST_LANE_QUERIES_RU: tuple[str, ...] = (
    "Трамп Truth Social OR твит Иран OR Ормуз when:12h",
    "Трамп отменил удар Иран OR отложил атаку нефть when:12h",
    "Трамп Ормуз сделка OR переговоры Иран нефть when:12h",
    "Бессент OR министр финансов США Ормуз OR Иран OR нефть when:12h",
    "Иран откроет OR не откроет Ормуз OR отрицает переговоры when:12h",
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
    # Трейдерский wire — раньше массовых СМИ
    ("financialjuice.com", "FinancialJuice", 1),
    ("financialjuice", "FinancialJuice", 1),
    ("forexlive.com", "ForexLive", 1),
    ("forexlive", "ForexLive", 1),
    ("investinglive.com", "InvestingLive", 1),
    ("investinglive", "InvestingLive", 1),
    ("truthsocial.com", "Truth Social", 1),
    ("truth social", "Truth Social", 1),
    ("x.com", "X", 1),
    ("twitter.com", "X", 1),
    ("x @", "X", 1),
    ("deltaone", "X · DeItaone", 1),
    ("financialjuice", "FinancialJuice", 1),
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
    "india today",
    "ndtv.com",
    "timesofindia",
    "hindustantimes.com",
    "thehindu.com",
    "news18.com",
    "ynetnews.com",
    "ynetnews",
    "ynet ",
    "msn.com",
    "yahoo.com",
    # news.google.com НЕ здесь: это наш RSS-агрегатор; outlet берём из <source>
    # (Bloomberg/WSJ). Иначе ВСЕ Tier‑1 с Google News молчат.
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
    "called off": 5,
    "calls off": 5,
    "delays": 3,
    "interview": 3,
    "truth social": 5,
    "tweet": 3,
    "posts on": 3,
    "last chance": 4,
    "negotiations": 3,
    "talks with iran": 4,
    "bessent": 5,
    "treasury secretary": 4,
    "scott bessent": 5,
    "open hormuz": 5,
    "reopen hormuz": 5,
    "open the strait": 4,
    "denies talks": 4,
    "denies negotiations": 4,
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
    "truth social", "taco",
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
    # Trump deal/TACO tape без слова oil — всё равно двигает Brent
    if any(k in low for k in ("trump", "трамп", "truth social")) and any(
        k in low
        for k in (
            "iran", "иран", "hormuz", "ормуз", "strike", "attack", "удар", "атак",
            "taco", "deal", "сделк", "reopen", "negotiation", "переговор",
            "called off", "cancels", "pauses",
        )
    ):
        return True
    # Бессент / Treasury про энергию/Иран/Ормуз
    if any(
        k in low for k in ("bessent", "treasury secretary", "scott bessent", "бессент")
    ) and any(
        k in low
        for k in (
            "iran", "иран", "hormuz", "ормуз", "oil", "crude", "energy", "энерг",
            "deal", "сделк", "reopen", "negotiat", "переговор",
        )
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
    try:
        from .oil_monitor import is_oil_market_moving_headline

        if not is_oil_market_moving_headline(title):
            return False
    except Exception:
        pass
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
    """Короткий ИИ-разбор только для важных flash (не простыня)."""
    if not api_key:
        return "", None
    try:
        from .ai_analyst import ask_gemini, gemini_in_cooldown, sanitize_ai_reply_for_telegram

        if gemini_in_cooldown():
            return "", None
        ctx = (
            "Трейдер Brent/UKOUSD. Ответ ТОЛЬКО по-русски, коротко.\n"
            f"Источник: {outlet or source}\n"
            f"Заголовок: {title}\n"
            f"Черновик бота: {impact} (может ошибаться).\n"
            f"Цена: {move_note or 'без сильного хода'}\n"
            "Правила: отмена удара/сделка/открытие Ормуза → вниз; "
            "отказ Ирана открыть / новые удары → вверх; "
            "Трамп, Бессент, Ормуз — главные драйверы.\n"
            "Запрет: вход/стоп/TP/цифры уровней/ВЕРДИКТ LONG.\n"
        )
        user = (
            "Строка1: OIL_RELEVANT: YES|NO\n"
            "Строка2 (если YES): OIL_BIAS: UP|DOWN|MIXED\n"
            "Если NO — одна фраза почему шум.\n"
            "Если YES — РОВНО 3 коротких пункта (без воды):\n"
            "• Суть (1 предложение)\n"
            "• Для нефти: вверх/вниз и почему\n"
            "• Что делать: не догонять / ждать / осторожно\n"
            "Максимум 500 символов после мета-строк."
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
        if len(text) > 700:
            text = text[:697] + "…"
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


def detect_oil_primary_actors(title: str) -> list[str]:
    """Кто двигает нефть в заголовке: Трамп / Бессент / Иран·Ормуз."""
    low = (title or "").lower()
    actors: list[str] = []
    if any(
        k in low
        for k in (
            "trump", "трамп", "truth social", "taco",
        )
    ):
        actors.append("Трамп")
    if any(
        k in low
        for k in (
            "bessent", "treasury secretary", "scott bessent",
            "министр финансов", "бессент",
        )
    ):
        actors.append("Бессент")
    if any(
        k in low
        for k in (
            "hormuz", "ормуз", "iran", "иран", "tehran", "тегеран",
            "fars", "tasnim", "irna",
        )
    ):
        actors.append("Иран·Ормуз")
    return actors


def _bounce_hint(impact: str, move_note: str) -> str:
    """Одна короткая строка — без простыни."""
    strong = "опережает" in (move_note or "")
    if impact == "bullish":
        return "Не шортить против импульса; лонг только от уровня." if not strong else "Уже вверх — не догонять хай."
    if impact == "bearish":
        return "Не ловить нож; шорт от сопротивления." if not strong else "Уже вниз — ждать базу, не лонговать в воздух."
    return "Ждать подтверждения (Reuters/AP) или уровень."


def should_ai_analyze_flash(
    title: str,
    meta: FastLaneMeta,
    *,
    min_score: int = 11,
) -> bool:
    """ИИ только на громких драйверах — не на каждый заголовок."""
    actors = detect_oil_primary_actors(title)
    # Трамп / Бессент / Ормуз — главный контур
    if actors and meta.flash_score >= max(9, min_score - 1):
        return True
    # Очень громкий wire без явного actor-тега
    if meta.tier == 1 and meta.flash_score >= min_score + 2:
        return True
    return False


def _headline_ru(title: str) -> tuple[str, str]:
    """Русский пересказ заголовка + влияние на цену."""
    try:
        from .oil_why import _explain_headline

        what, means, _ = _explain_headline(title or "")
        return what, means
    except Exception:
        return "Новость по нефти", "Смотри Ормуз и уровни"


def format_fastlane_flash(
    item: Any,
    *,
    meta: FastLaneMeta,
    ai_ru: str = "",
    move_note: str = "",
    age_label: str = "",
    compact: bool = True,
) -> str:
    """Короткий ‼️ flash — по-русски, без сырого EN-заголовка."""
    raw_title = getattr(item, "title", "") or ""
    url = getattr(item, "url", "") or ""
    impact = getattr(item, "impact", "neutral") or "neutral"
    impact_ru = {
        "bullish": "🟢 ↑",
        "bearish": "🔴 ↓",
        "neutral": "⚪ ~",
    }.get(impact, "⚪")
    bang = "‼️‼️" if meta.tier == 1 and meta.flash_score >= 12 else "‼️"
    actors = detect_oil_primary_actors(raw_title)
    actor_line = (" · ".join(actors)) if actors else ""
    head = f"{bang} <b>{meta.outlet}</b> {impact_ru}"
    if actor_line:
        head += f" · {actor_line}"

    what, means = _headline_ru(raw_title)
    lines = [head, f"<b>{_esc(what)}</b>", f"→ {_esc(means)}"]
    if url:
        lines.append(f'<a href="{url}">источник</a>')

    if ai_ru:
        short = " ".join(ai_ru.strip().split())
        if len(short) > 200:
            short = short[:197] + "…"
        lines.append(_esc(short))
    else:
        hint = _bounce_hint(impact, move_note)
        if hint:
            lines.append(hint)

    src = (getattr(item, "source", "") or meta.publisher or meta.outlet).strip()
    age = age_label or ""
    foot = f"<i>{src}"
    if age:
        foot += f" · {age}"
    foot += " · не входить 5–15м</i>"
    lines.append(foot)
    return "\n".join(lines)


def _rule_brief(impact: str, title: str) -> str:
    dir_ru = {
        "bullish": "скорее вверх (поставки/гео)",
        "bearish": "скорее вниз (сделка/деэскалация)",
        "neutral": "смешанно",
    }.get(impact, "смешанно")
    return f"<i>{dir_ru}. Не догонять импульс.</i>"


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
    raw_title = getattr(item, "title", "") or ""
    url = getattr(item, "url", "") or ""
    impact = getattr(item, "impact", "neutral") or "neutral"
    dir_ru = {
        "bullish": "🟢 вверх",
        "bearish": "🔴 вниз",
    }.get(impact, "⚪ смешанно")
    what, means = _headline_ru(raw_title)
    lines = [
        "🛢 <b>Новость влияет на сделку</b>",
        f"Направление: <b>{dir_ru}</b> · {meta.outlet}",
        "",
        f"<b>{_esc(what)}</b>",
        f"→ {_esc(means)}",
    ]
    if url:
        lines.append(f'<a href="{url}">источник</a>')
    lines.append("")
    lines.append(_bounce_hint(impact, move_note))
    if move_note:
        lines.append(_esc(move_note))
    if ai_ru:
        lines.append("")
        lines.append("🤖 <b>Главное</b>")
        lines.append(_esc(ai_ru))
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
