"""Нефть: отдельный чат — свежие новости (ссылки) + техразбор + прогноз.

Торговый UI Bybit TradFi (MT5): UKOUSD.s (Brent cash) / USOIL — публичных kline нет.
Свечи: форма Bybit BZUSDT/CLUSDT (ровный ряд), цена сдвинута к Yahoo BZ=F/CL=F
(≈ уровень UKOUSD). Fallback — sanitized Yahoo без дыр сессии.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from .bybit_klines import BYBIT_KLINE_URL, KlineBar
from .oil_level_watcher import OilLevelWatcher
from .ta_analysis import TAAnalysisResult, TradeScenario, fmt_price, run_ta_analysis

logger = logging.getLogger(__name__)

_OIL_RUNTIME_FILE = Path(__file__).resolve().parent / "oil_runtime.json"

# UI-символы (как на Bybit TradFi) + источники свечей
OIL_BRENT_SYMBOL = "UKOUSD"
OIL_WTI_SYMBOL = "USOIL"
OIL_BRENT_LABEL = "Brent · UKOUSD"
OIL_WTI_LABEL = "WTI · USOIL"
OIL_BRENT_YAHOO = "BZ=F"
OIL_WTI_YAHOO = "CL=F"
OIL_BRENT_BYBIT = "BZUSDT"  # fallback, другой basis
OIL_WTI_BYBIT = "CLUSDT"

_OIL_KEYWORDS = frozenset({
    "oil", "crude", "brent", "wti", "petroleum", "gasoline", "diesel", "hormuz",
    "opec", "barrel", "нефт", "баррель", "spr", "inventory", "eia", "запас",
    "crude oil", "black gold",
})
# Геополитика / политика США — только в связке с нефтью
_GEO_PRIORITY = frozenset({
    "iran", "иран", "tehran", "тегеран", "hormuz", "ормуз", "strait",
    "trump", "трамп", "white house", "белый дом",
    "sanction", "санкц", "pentagon", "пентагон",
    "houthi", "хусит", "israel", "израил", "hezbollah",
    "persian gulf", "persian", "middle east", "ближн",
})
_US_KEYWORDS = frozenset({
    "usa", "u.s.", "u.s ", "united states", "america", "washington",
    "сша", "америк", "вашингтон", "байден", "biden",
})
# Запасы / объёмы / покупка-продажа / предложение
_FLOW_KEYWORDS = frozenset({
    "inventory", "inventories", "stockpile", "stocks", "запас", "запасы",
    "eia", "api ", " api", "spr", "strategic petroleum",
    "draw", "build", "surplus", "deficit", "oversupply", "shortage",
    "volume", "объем", "объём", "barrel", "баррель",
    "buy", "buying", "purchase", "import", "export",
    "покуп", "продаж", "экспорт", "импорт", "сделк", "deal",
    "tanker", "танкер", "shipment", "поставк", "supply", "demand",
    "production", "добыч", "output", "quota", "квот",
})
_OPEC_KEYWORDS = frozenset({
    "opec", "опек", "opec+", "saudi", "саудов", "russia oil", "росси",
})
# Именованные про-аналитики: ловим в Google News + boost в Новостник
PRO_OIL_ANALYSTS: tuple[tuple[str, str, int], ...] = (
    # (match substring, display name, score boost)
    ("javier blas", "Javier Blas", 5),
    ("john kemp", "John Kemp", 4),
    ("helima croft", "Helima Croft", 3),
    ("amrita sen", "Amrita Sen", 3),
)

_ANALYST_KEYWORDS = frozenset({
    "forecast", "price outlook", "oil outlook", "market outlook", "brent outlook",
    "steo", "price target", "price view", "price to",
    "sees brent", "sees oil", "sees upside", "sees downside",
    "expects brent", "expects oil", "predicts", "projection",
    "oil market report", "short-term energy", "iea ", " iea",
    "barclays", "goldman", "jpmorgan", "jp morgan", "morgan stanley",
    "john kemp", "javier blas", "helima croft", "amrita sen",
    "analyst", "аналитик",
    "прогноз", "прогноз цен", "upside risk", "downside risk",
    "war premium", "cuts forecast", "raises forecast", "slashes forecast",
    "to average", "will average",
})
# Движение ЦЕНЫ в заголовке — главный приоритет (иначе attack/strike ломают «tumbles»).
_PRICE_UP = frozenset({
    "surge", "surges", "rise", "rises", "rally", "rallies", "jump", "jumps",
    "spike", "spikes", "soar", "soars", "climb", "climbs", "gain", "gains",
    "рекорд", "рост", "подскоч", "взлет", "взлёт", "дорожает", "подорож",
    "prices rise", "oil rises", "brent rises", "crude rises",
})
_PRICE_DOWN = frozenset({
    "tumble", "tumbles", "tumbled", "slump", "slumps", "slumped", "crash",
    "crashes", "crashed", "plunge", "plunges", "plunged", "fall", "falls",
    "fell", "drop", "drops", "dropped", "decline", "declines", "slide",
    "slides", "sink", "sinks", "sank", "retreat", "retreats", "weaken",
    "паден", "обвал", "обруш", "рухн", "дешев", "снижен цен", "цены пад",
    "price tumbles", "oil tumbles", "brent falls", "crude falls",
    "oil drops", "prices fall", "prices drop",
})
# Катализатор вверх (эскалация / дефицит) — только если нет явного price-down.
_BULL_NEWS = frozenset({
    "attack", "strike", "block", "close strait", "escalat", "sanction",
    "cut produc", "draw", "tighten", "shortage", "deficit", "buy", "purchase",
    "атак", "удар", "сокращен", "дефицит", "покуп", "upside", "raises forecast",
    "new strikes", "fresh strikes", "orders attack", "bomb",
})
# Катализатор вниз (деэскалация / сделка / отмена ударов).
_BEAR_NEWS = frozenset({
    "deal", "reopen", "de-escal", "ceasefire", "forecast cut", "cuts forecast",
    "slashes forecast", "build", "oversupply", "release spr", "accord",
    "surplus", "sell", "dump", "сделк", "избыт", "продаж", "перемир",
    "downside", "war premium unwind", "unwind", "cooling",
    "taco", "chickens out", "backs off", "calls off", "called off",
    "cancels attack", "cancel attack", "cancels strike", "cancel strike",
    "pauses strike", "pause strike", "suspends strike", "suspend attack",
    "abandons attack", "scraps strike", "no strike", "won't strike",
    "отмен", "отказ от удар", "струсил", "приостан", "деэскал",
})
# Веса: чем выше — тем важнее для отправки в чат
_CRITICAL_TERMS: dict[str, int] = {
    "hormuz": 5,
    "ормуз": 5,
    "strait of hormuz": 5,
    "iran": 3,
    "иран": 3,
    "tehran": 3,
    "тегеран": 3,
    "trump": 3,
    "трамп": 3,
    "sanction": 3,
    "санкц": 3,
    "opec": 4,
    "опек": 4,
    "eia": 4,
    "inventory": 3,
    "inventories": 3,
    "запас": 3,
    "запасы": 3,
    "spr": 4,
    "strategic petroleum": 4,
    "strike": 4,
    "attack": 4,
    "атак": 4,
    "blockade": 4,
    "houthi": 3,
    "хусит": 3,
    "production cut": 4,
    "сокращен": 3,
    "quota": 3,
    "квот": 3,
    "tanker": 3,
    "танкер": 3,
    "export ban": 4,
    "import ban": 4,
    "crude buy": 3,
    "oil purchase": 3,
    "покупк": 3,
    "продаж нефт": 3,
    "drawdown": 3,
    "stock build": 3,
    "api inventory": 3,
    "forecast": 3,
    "price outlook": 3,
    "steo": 4,
    "barclays": 3,
    "goldman": 3,
    "iea": 4,
    "прогноз": 3,
    "john kemp": 4,
    "javier blas": 5,
    "helima croft": 3,
    "amrita sen": 3,
    "war premium": 3,
}

# Только эти темы имеют право уйти в чат (не «любая нефть»)
_PRIORITY_THEMES = frozenset({
    "iran_geo",
    "trump_us",
    "inventory",
    "opec",
    "flow_deal",
    "analyst",
})

NEWS_QUERIES_EN: tuple[str, ...] = (
    "Iran oil Trump sanctions Hormuz when:12h",
    "Trump Iran oil when:12h",
    "US Iran crude oil sanctions when:12h",
    "EIA crude oil inventory stocks when:12h",
    "OPEC oil production quota when:1d",
    "SPR oil release OR strategic petroleum reserve when:1d",
    "crude oil tanker Hormuz when:12h",
    "Strait of Hormuz tanker traffic OR shipping OR transit when:12h",
    # Investing.com + wire (рано выходят)
    "site:investing.com oil OR Brent OR WTI OR Hormuz OR Iran when:12h",
    "site:investing.com crude oil price news when:12h",
    # Про-аналитика / прогнозы (Reuters, OilPrice, банки)
    "site:reuters.com Brent crude oil OR Hormuz when:1d",
    "site:oilprice.com Brent OR WTI OR OPEC when:1d",
    "Brent crude price forecast OR outlook Barclays OR Goldman OR EIA STEO when:1d",
    # Топ-аналитики (Blas / Kemp) — приоритет для Новостника
    "Javier Blas oil OR crude OR Hormuz OR Brent when:1d",
    "site:bloomberg.com Javier Blas oil OR energy OR Hormuz when:1d",
    "John Kemp oil OR crude OR inventories OR Brent when:1d",
    "John Kemp Reuters oil when:1d",
    "war premium oil unwind OR Hormuz deal oil prices when:12h",
    "Trump TACO OR cancels Iran strike OR pauses attack oil when:12h",
)
NEWS_QUERIES_RU: tuple[str, ...] = (
    "нефть Иран Трамп санкции Ормуз when:12h",
    "Трамп Иран нефть when:12h",
    "EIA запасы нефти США when:12h",
    "ОПЕК квота добыча нефть when:1d",
    "СПР запасы нефть США when:1d",
    "Ормуз танкер нефть when:12h",
    "Ормуз судоходство танкеры when:12h",
    "site:ru.investing.com нефть OR Brent OR Ормуз OR Иран when:12h",
    "прогноз цена нефти Brent EIA OR ОПЕК when:1d",
    "нефть Ормуз сделка цена when:12h",
    "Трамп отменил удар Иран нефть when:12h",
)

# Прямые RSS профи (отрасль / EIA) → новостник
PRO_OIL_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("OilPrice", "https://oilprice.com/rss/main"),
    ("EIA Today in Energy", "https://www.eia.gov/rss/todayinenergy.xml"),
    ("EIA Press", "https://www.eia.gov/rss/press_rss.xml"),
)

@dataclass(frozen=True)
class OilNewsItem:
    title: str
    url: str
    source: str
    published_ts: float
    impact: str = "neutral"  # bullish | bearish | neutral
    query: str = ""
    lang: str = "en"
    theme: str = ""  # iran_geo | trump_us | inventory | opec | flow_deal | analyst


def match_pro_oil_analyst(title: str, source: str = "") -> tuple[str, int] | None:
    """Если в заголовке/источнике топ-аналитик → (display_name, boost)."""
    blob = f"{title} {source}".lower()
    best: tuple[str, int] | None = None
    for needle, display, boost in PRO_OIL_ANALYSTS:
        if needle in blob:
            if best is None or boost > best[1]:
                best = (display, boost)
    return best


def detect_oil_news_theme(title: str, *, source: str = "") -> str:
    """Главная тема заголовка — без темы приоритета новость не шлём."""
    low = title.lower()
    src = (source or "").lower()
    pro = match_pro_oil_analyst(title, source)
    has_oil = any(k in low for k in _OIL_KEYWORDS)
    if not has_oil:
        # Иран/Ормуз без слова oil всё равно нефтяная геополитика
        if any(k in low for k in ("hormuz", "ормуз", "iran", "иран")) and any(
            k in low for k in ("strait", "sanction", "санкц", "tanker", "танкер", "crude", "нефт")
        ):
            has_oil = True
        # Прогнозы банков без «oil» в title, но с Brent/IEA/STEO
        if any(k in low for k in ("brent", "wti", "steo", "iea", "опек", "opec")):
            has_oil = True
        # Колонка Blas/Kemp: даже без слова oil в title
        if pro is not None and any(
            k in low or k in src
            for k in (
                "oil", "crude", "brent", "energy", "hormuz", "опек", "opec",
                "bloomberg", "reuters", "commodity", "commodit",
            )
        ):
            has_oil = True
    if not has_oil:
        return ""

    if any(k in low for k in ("hormuz", "ормуз", "iran", "иран", "tehran", "тегеран", "houthi", "хусит")):
        # Прогноз по Ормузу остаётся геополитикой (главный драйвер)
        return "iran_geo"
    if any(k in low for k in ("trump", "трамп")) or (
        any(k in low for k in _US_KEYWORDS)
        and any(k in low for k in ("sanction", "санкц", "iran", "иран", "spr", "eia"))
    ):
        return "trump_us"
    # Именованный аналитик / STEO / bank outlook
    if pro is not None or any(k in low for k in _ANALYST_KEYWORDS):
        return "analyst"
    if any(k in low for k in ("eia", "inventory", "inventories", "запас", "spr", "stockpile", "api ")):
        return "inventory"
    if any(k in low for k in _OPEC_KEYWORDS):
        return "opec"
    if any(k in low for k in _FLOW_KEYWORDS):
        return "flow_deal"
    return ""


def _pro_feed_theme(title: str) -> str:
    """Для OilPrice/EIA RSS: приоритетная тема или отраслевой oil-заголовок → analyst."""
    theme = detect_oil_news_theme(title)
    if theme:
        return theme
    low = title.lower()
    if not any(k in low for k in _OIL_KEYWORDS):
        return ""
    if any(
        k in low
        for k in (
            "brent", "wti", "crude", "oil price", "oil prices", "нефт",
            "petroleum", "opec", "eia", "gasoline", "diesel",
        )
    ):
        return "analyst"
    return ""


def _is_relevant(title: str) -> bool:
    """Только нефть + приоритетная тема (не любой заголовок про Brent)."""
    return detect_oil_news_theme(title) in _PRIORITY_THEMES


def news_critical_score(title: str, *, source: str = "") -> int:
    low = title.lower()
    score = 0
    for term, weight in _CRITICAL_TERMS.items():
        if term in low:
            score += weight
    theme = detect_oil_news_theme(title, source=source)
    # Бонус за приоритетную тему
    if theme == "iran_geo":
        score += 2
    elif theme in {"trump_us", "inventory", "opec", "analyst"}:
        score += 1
    pro = match_pro_oil_analyst(title, source)
    if pro is not None:
        # Именованный топ-аналитик — почти всегда пушим в чат
        score += pro[1]
    return score


def is_critical_oil_news(item: OilNewsItem, min_score: int = 5) -> bool:
    theme = item.theme or detect_oil_news_theme(item.title, source=item.source)
    if theme not in _PRIORITY_THEMES:
        return False
    # Старше 2 суток — не «критично» для пуша (рынок уже отыграл)
    if not oil_news_is_fresh(getattr(item, "published_ts", None), max_age_hours=48.0):
        return False
    score = news_critical_score(item.title, source=item.source)
    # Аналитика без гео/запасов — выше порог (иначе шум прогнозов)
    need = min_score
    if theme == "analyst":
        need = min_score + 1
    # Blas / Kemp — чуть мягче (их колонки важны)
    if match_pro_oil_analyst(item.title, item.source) is not None:
        need = max(4, min_score - 1)
    return score >= need


def theme_label_ru(theme: str) -> str:
    return {
        "iran_geo": "🇮🇷 Иран / Ормуз",
        "trump_us": "🇺🇸 Трамп / США",
        "inventory": "📦 Запасы США",
        "opec": "🛢️ ОПЕК / добыча",
        "flow_deal": "🚢 Покупки / объёмы / поставки",
        "analyst": "📊 Аналитика / прогноз",
    }.get(theme, "нефть")

@dataclass
class OilMarketSnapshot:
    label: str
    symbol: str
    price: float
    high_7d: float
    low_7d: float
    verdict: str
    confidence: int
    support: float | None
    resistance: float | None
    breakdown: float | None
    breakout: float | None
    phase: str
    elliott: str
    reason: str
    entry_zone: tuple[float, float] | None = None
    stop: float | None = None
    targets: tuple[float, ...] = ()

@dataclass
class OilAnalysisBundle:
    """Полный пакет для дайджеста: Brent primary + WTI контекст."""
    brent_bars: list[KlineBar]
    brent_ta: TAAnalysisResult
    brent: OilMarketSnapshot
    interval_minutes: int = 15
    market_mood: str = ""
    wti_bars: list[KlineBar] | None = None
    wti_ta: TAAnalysisResult | None = None
    wti: OilMarketSnapshot | None = None

def bybit_interval_for_minutes(minutes: int) -> str:
    """Bybit kline interval string for oil TF."""
    m = max(5, min(60, int(minutes)))
    if m <= 5:
        return "5"
    if m <= 15:
        return "15"
    if m <= 30:
        return "30"
    return "60"


def _bars_limit_for_interval(minutes: int) -> int:
    """Сколько свечей тянуть: ~5–7 торговых дней на выбранном TF."""
    m = max(5, min(60, int(minutes)))
    if m <= 5:
        return 1000
    if m <= 15:
        return 700
    if m <= 30:
        return 500
    return 400


def detect_oil_market_mood(
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    interval_minutes: int,
) -> str:
    """Режим intraday: тренд / база / волатильность — для адаптивного стиля 5m–1h."""
    if not bars:
        return "неопределён"
    bars_per_day = max(1, int(24 * 60 / max(5, interval_minutes)))
    window = bars[-min(len(bars), bars_per_day):]
    if len(window) < 8:
        return "нейтрально — от уровней"
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    mid = float(window[-1].close)
    if mid <= 0:
        return "нейтрально"
    rng_pct = (hi - lo) / mid * 100.0
    verdict = (ta.verdict or "WAIT").upper()
    if rng_pct >= 4.5:
        return "высокая волатильность — короткие тейки, быстрые стопы"
    s = ta.nearest_support
    r = ta.nearest_resistance
    if s and r and rng_pct < 2.0:
        in_range = abs(mid - s) / mid < 0.025 and abs(mid - r) / mid < 0.025
        if in_range:
            return "база / флэт — ждать пробой с закрытием"
    if verdict == "LONG":
        return "intraday бычий bias — откаты к support"
    if verdict == "SHORT":
        return "intraday медвежий bias — откаты к resistance"
    return "нейтрально — торговать от уровней 5–15m"

def _parse_rss_pub(pub: str) -> float | None:
    """Дата из RSS. None если нет/битая — не подставляем «сейчас» (это пускало старьё)."""
    if not pub or not str(pub).strip():
        return None
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
        # явный мусор / далёкое будущее
        now = time.time()
        if ts > now + 3600 or ts < now - 86400 * 400:
            return None
        return ts
    except Exception:
        return None


_URL_DATE_PATTERNS = (
    re.compile(r"/(20\d{2})/([01]?\d)/([0-3]?\d)(?:/|$)"),
    re.compile(r"/(20\d{2})-([01]?\d)-([0-3]?\d)(?:/|$)"),
    re.compile(r"[?&]date=(20\d{2})-([01]?\d)-([0-3]?\d)"),
)


def _unwrap_news_url(url: str) -> str:
    """Достаёт исходный URL из редиректа Google News, если есть."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ("url", "q", "u"):
            vals = qs.get(key) or []
            if vals and vals[0].startswith("http"):
                return vals[0]
        # path иногда содержит encoded url
        if "http" in raw:
            m = re.search(r"https?%3A%2F%2F[^\s&]+", raw, re.I)
            if m:
                return urllib.parse.unquote(m.group(0))
    except Exception:
        pass
    return raw


def _extract_url_published_ts(url: str) -> float | None:
    """Дата из пути статьи (/2026/04/06/…) — ловит перепубликации в Google News."""
    target = _unwrap_news_url(url)
    if not target:
        return None
    try:
        decoded = urllib.parse.unquote(target)
    except Exception:
        decoded = target
    for pat in _URL_DATE_PATTERNS:
        m = pat.search(decoded)
        if not m:
            continue
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if mo < 1 or mo > 12 or d < 1 or d > 31:
                continue
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            continue
    return None


def resolve_oil_news_published_ts(
    *,
    rss_pub: str | None,
    url: str,
) -> float | None:
    """Эффективная дата новости: min(RSS, URL) — если URL старый, статья старая."""
    rss_ts = _parse_rss_pub(rss_pub or "")
    url_ts = _extract_url_published_ts(url)
    if rss_ts is None and url_ts is None:
        return None
    if rss_ts is None:
        return url_ts
    if url_ts is None:
        return rss_ts
    # URL-дата часто «день публикации»; если она старше RSS — верим URL (не свежий репост)
    return min(rss_ts, url_ts)


def oil_news_is_fresh(published_ts: float | None, *, max_age_hours: float) -> bool:
    """Свежесть для чата. Жёсткий потолок — 48ч (2 суток): старше уже не двигает нефть."""
    if published_ts is None or published_ts <= 0:
        return False
    # Никогда не пускать старше 2 суток, даже если в settings ошибочно 72+
    hard_cap_h = 48.0
    max_age_h = max(1.0, min(hard_cap_h, float(max_age_hours)))
    age_h = (time.time() - published_ts) / 3600.0
    return 0.0 <= age_h <= max_age_h


def oil_news_freshness_weight(published_ts: float | None, *, now: float | None = None) -> float:
    """Вес сюжета для bias/прогноза: свежие сильнее, >24ч почти не влияют."""
    if published_ts is None or published_ts <= 0:
        return 0.0
    age_h = ((now if now is not None else time.time()) - published_ts) / 3600.0
    if age_h < 0:
        return 0.0
    if age_h <= 2.0:
        return 1.0
    if age_h <= 6.0:
        return 0.85
    if age_h <= 12.0:
        return 0.6
    if age_h <= 24.0:
        return 0.35
    if age_h <= 48.0:
        return 0.15
    return 0.0


def _clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    if " - " in t:
        parts = t.rsplit(" - ", 1)
        if len(parts[1]) < 40:
            return parts[0].strip()
    return t

def classify_news_impact(title: str) -> str:
    """Направление давления на нефть по заголовку.

    Приоритет: явное движение цены (tumbles/surges) > деэскалация/TACO >
    эскалация (attack) > прочие токены. Иначе «tumbles … attacks» → bullish.
    """
    low = title.lower()
    price_up = sum(1 for k in _PRICE_UP if k in low)
    price_down = sum(1 for k in _PRICE_DOWN if k in low)

    # Цена в заголовке важнее geo-слов (атака как фон, а не как bias).
    if price_down > price_up and price_down > 0:
        return "bearish"
    if price_up > price_down and price_up > 0:
        return "bullish"

    bull = sum(1 for k in _BULL_NEWS if k in low)
    bear = sum(1 for k in _BEAR_NEWS if k in low)

    # «cancels/pauses planned attacks» — attack есть, но смысл bearish.
    if any(
        k in low
        for k in (
            "cancel", "cancels", "cancelled", "canceled", "pause", "pauses",
            "paused", "suspend", "suspends", "abandoned", "scraps", "calls off",
            "called off", "backs off", "chickens out", "taco",
            "отмен", "приостан", "струсил", "отказался",
        )
    ) and any(k in low for k in ("attack", "strike", "удар", "атак", "bomb")):
        bear += 3
        bull = max(0, bull - 2)

    # «cuts Brent forecast» / «raises WTI outlook»
    if "forecast" in low or "outlook" in low or "прогноз" in low:
        if any(k in low for k in ("cut", "slash", "lower", "reduce", "trim", "сниж", "пониж")):
            bear += 1
        if any(k in low for k in ("raise", "hike", "lift", "boost", "повыш", "увеличен")):
            bull += 1

    if bull > bear and bull > 0:
        return "bullish"
    if bear > bull and bear > 0:
        return "bearish"
    return "neutral"

@dataclass(frozen=True)
class OilNewsBias:
    """Сводка новостного давления на нефть за окно."""
    bullish: int = 0
    bearish: int = 0
    neutral: int = 0
    weighted_score: float = 0.0  # >0 вверх, <0 вниз (−10…+10)
    bias: str = "neutral"  # bullish | bearish | mixed | neutral
    summary_ru: str = ""
    how_to_use_ru: str = ""
    basis_ru: str = ""  # понятное «на основе чего»
    unique_stories: int = 0
    top_catalyst: str = ""


def _news_story_key(title: str) -> str:
    """Схлопывает дубли одной темы; эскалация ≠ отмена ударов."""
    low = re.sub(r"[^a-zа-я0-9\s]", " ", title.lower())
    low = re.sub(r"\s+", " ", low).strip()
    tags: list[str] = []
    for t in (
        "hormuz", "ормуз", "iran", "иран", "sanction", "санкц", "trump", "трамп",
        "eia", "opec", "опек", "spr", "inventory", "запас", "tanker", "танкер",
        "quota", "квот", "forecast", "прогноз", "steo", "barclays",
        "blas", "kemp",
    ):
        if t in low:
            tags.append(t)
    # Направление сюжета: иначе «удары» и «отмена ударов» = один ключ
    deesc_kw = (
        "taco", "cancel", "cancels", "cancelling", "pause", "pauses",
        "holds off", "hold off", "reopen", "opening of the hormuz",
        "отмен", "пауз", "сделк", "deal perimeter", "parameters of a deal",
    )
    esc_kw = (
        "strike", "attack", "blockade", "closed", "weighing", "considering",
        "удар", "атак", "блок", "закрыт", "collapses", "broke it",
    )
    if any(k in low for k in deesc_kw):
        tags.append("deesc")
    elif any(k in low for k in esc_kw):
        tags.append("esc")
    elif "truce" in low or "ceasefire" in low:
        # «truce collapses» ≠ деэскалация
        if any(k in low for k in ("collapse", "broke", "break", "fail", "наруш")):
            tags.append("esc")
        else:
            tags.append("deesc")
    if tags:
        return "|".join(sorted(set(tags)))
    return low[:48]


def news_impact_weight(item: OilNewsItem) -> float:
    """Вес: критичность × свежесть (старое >24ч почти не давит на bias)."""
    score = float(news_critical_score(item.title, source=item.source))
    base = max(1.0, min(5.0, score / 3.0))
    return base * oil_news_freshness_weight(getattr(item, "published_ts", None))


def summarize_oil_news_bias(
    items: list[OilNewsItem],
    *,
    ta_verdict: str | None = None,
    ta_confidence: int | None = None,
) -> OilNewsBias:
    """Считает давление по уникальным сюжетам (без раздувания score дублями)."""
    if not items:
        return OilNewsBias(
            summary_ru="Новостной фон: нет важных заголовков",
            how_to_use_ru="Торговать только от уровней TA, без новостного подтверждения.",
            basis_ru="Нет приоритетных новостей (Иран/Трамп/EIA/ОПЕК/объёмы) → только график.",
        )

    # Одна тема = один голос (не 16 одинаковых Hormuz)
    best_by_story: dict[str, OilNewsItem] = {}
    for it in items:
        key = _news_story_key(it.title)
        prev = best_by_story.get(key)
        if prev is None or news_critical_score(
            it.title, source=it.source
        ) > news_critical_score(prev.title, source=prev.source):
            best_by_story[key] = it
    unique = list(best_by_story.values())

    bull = bear = neut = 0
    weighted = 0.0
    for it in unique:
        w = news_impact_weight(it)
        if it.impact == "bullish":
            bull += 1
            weighted += w
        elif it.impact == "bearish":
            bear += 1
            weighted -= w
        else:
            neut += 1

    # Нормализация в понятный диапазон −10…+10
    weighted = max(-10.0, min(10.0, weighted))

    total_dir = bull + bear
    if total_dir == 0:
        bias = "neutral"
    elif abs(weighted) < 1.5 and bull > 0 and bear > 0:
        bias = "mixed"
    elif weighted >= 1.5:
        bias = "bullish"
    elif weighted <= -1.5:
        bias = "bearish"
    elif bull > bear:
        bias = "bullish"
    elif bear > bull:
        bias = "bearish"
    else:
        bias = "mixed"

    catalyst = ""
    want = "bullish" if bias == "bullish" else "bearish" if bias == "bearish" else ""
    if want:
        ranked = sorted(
            (it for it in unique if it.impact == want),
            key=lambda x: news_critical_score(x.title, source=x.source),
            reverse=True,
        )
        if ranked:
            catalyst = ranked[0].title[:140]

    if bias == "bullish":
        arrow = f"🟢↑ вверх ({bull} сюжетов↑ / {bear}↓"
        if neut:
            arrow += f" / {neut} нейтр."
        arrow += ")"
    elif bias == "bearish":
        arrow = f"🔴↓ вниз ({bear} сюжетов↓ / {bull}↑"
        if neut:
            arrow += f" / {neut} нейтр."
        arrow += ")"
    elif bias == "mixed":
        arrow = f"🟡 смешанно ({bull}↑ / {bear}↓)"
    else:
        arrow = f"⚪ нейтрально ({neut} контекст)"

    summary = (
        f"Новостной фон: {arrow} · давление {weighted:+.1f}/10 "
        f"(уник. сюжетов: {len(unique)})"
    )

    tv = (ta_verdict or "WAIT").upper()
    tc = int(ta_confidence) if ta_confidence is not None else None
    ta_part = f"TA {tv}" + (f" {tc}/10" if tc is not None else "")

    if bias == "bullish" and tv == "LONG":
        howto = f"Совпадение: новости↑ + {ta_part} → приоритет LONG (отскок от S / пробой R)."
    elif bias == "bearish" and tv == "SHORT":
        howto = f"Совпадение: новости↓ + {ta_part} → приоритет SHORT (отскок от R / пробой S)."
    elif bias == "bullish" and tv == "SHORT":
        howto = f"Конфликт: новости↑, но {ta_part} → не гнаться; ждать пробоя или уменьшить размер."
    elif bias == "bearish" and tv == "LONG":
        howto = f"Конфликт: новости↓, но {ta_part} → не гнаться; ждать пробоя или уменьшить размер."
    elif bias == "bullish":
        howto = f"Новости↑, {ta_part} слаб/нейтрален → только LONG от support, без chase."
    elif bias == "bearish":
        howto = f"Новости↓, {ta_part} слаб/нейтрален → только SHORT от resistance, без chase."
    elif bias == "mixed":
        howto = f"Сюжеты спорят, {ta_part} → только чистый пробой уровня."
    else:
        howto = f"Новостей мало → торговать чисто от {ta_part}."

    basis_parts = [
        f"Считаем уникальные сюжеты (дубли одной темы схлопнуты), не каждый RSS-заголовок.",
        f"Давление {weighted:+.1f}/10 из весов тем: Иран/пролив, Трамп/США, запасы США, ОПЕК, объёмы.",
    ]
    if catalyst:
        basis_parts.append(f"Главный катализатор: «{catalyst[:100]}».")
    basis_parts.append(howto)

    return OilNewsBias(
        bullish=bull,
        bearish=bear,
        neutral=neut,
        weighted_score=round(weighted, 1),
        bias=bias,
        summary_ru=summary,
        how_to_use_ru=howto,
        basis_ru=" ".join(basis_parts),
        unique_stories=len(unique),
        top_catalyst=catalyst,
    )


@dataclass(frozen=True)
class OilBouncePlan:
    """План отскока под новостной bias — entry/stop/TP для графика и алерта."""
    side: str  # long | short
    bounce_level: float
    entry_lo: float
    entry_hi: float
    stop: float
    targets: tuple[float, ...]
    catalyst: str
    reason_ru: str
    strong: bool
    dist_pct: float = 0.0

    @property
    def entry_mid(self) -> float:
        return (self.entry_lo + self.entry_hi) / 2.0


@dataclass(frozen=True)
class OilScalpCall:
    """Краткосрочный вызов 10–100 мин: что открывать / не открывать."""
    action: str  # open_long | open_short | wait
    hold_min: int
    hold_max: int
    entry_lo: float | None
    entry_hi: float | None
    stop: float | None
    target: float | None
    score: int  # 1–10 согласованность факторов
    headline_ru: str
    factors_ru: tuple[str, ...]
    trigger_ru: str = ""


def _scalp_hold_window(market_mood: str, *, interval_minutes: int) -> tuple[int, int]:
    """Окно удержания 10–100 мин под режим рынка."""
    mood = (market_mood or "").lower()
    if "волатил" in mood:
        return 10, 35
    if "база" in mood or "флэт" in mood or "флет" in mood:
        return 25, 70
    if "бычий" in mood or "медвеж" in mood:
        return 20, 90
    # TF влияет: 5m → короче, 15m+ → длиннее
    if interval_minutes <= 5:
        return 15, 50
    if interval_minutes <= 15:
        return 20, 75
    return 30, 100


def _dist_pct(px: float, level: float | None) -> float | None:
    if not level or px <= 0:
        return None
    return abs(px - float(level)) / px * 100.0


def build_oil_scalp_call(
    snap: OilMarketSnapshot,
    ta: TAAnalysisResult,
    *,
    news_bias: OilNewsBias | None = None,
    bounce_plan: OilBouncePlan | None = None,
    market_mood: str = "",
    interval_minutes: int = 15,
    ta_confidence_raw: int | None = None,
    ta_verdict_raw: str | None = None,
) -> OilScalpCall:
    """Сводка всех факторов → одна команда на 10–100 мин."""
    px = float(snap.price or 0.0)
    hold_lo, hold_hi = _scalp_hold_window(market_mood, interval_minutes=interval_minutes)
    ta_v = (ta_verdict_raw or snap.verdict or getattr(ta, "verdict", None) or "WAIT").upper()
    ta_c = int(
        ta_confidence_raw
        if ta_confidence_raw is not None
        else (snap.confidence or getattr(ta, "verdict_confidence", 0) or 0)
    )
    news = news_bias.bias if news_bias else "neutral"
    news_w = float(news_bias.weighted_score) if news_bias else 0.0
    ap = (getattr(ta, "action_priority", "") or "").lower()
    s = snap.support
    r = snap.resistance
    bd = snap.breakdown
    bo = snap.breakout

    near_s = (_dist_pct(px, s) or 99) <= 0.35
    near_r = (_dist_pct(px, r) or 99) <= 0.35
    near_bd = (_dist_pct(px, bd) or 99) <= 0.25
    near_bo = (_dist_pct(px, bo) or 99) <= 0.25
    mid_range = False
    if s and r and px > 0:
        mid = (float(s) + float(r)) / 2.0
        mid_range = abs(px - mid) / px < 0.012 and not (near_s or near_r)

    factors: list[str] = []
    factors.append(f"TA {ta_v} {ta_c}/10")
    if news_bias:
        factors.append(f"новости {news} {news_w:+.1f}/10")
    if market_mood:
        factors.append(f"режим: {market_mood.split('—')[0].strip()[:40]}")
    if bounce_plan is not None:
        factors.append(
            f"отскок {bounce_plan.side.upper()} у {fmt_price(bounce_plan.bounce_level)}"
        )
    if ap in {"long", "short"}:
        factors.append(f"lean графика → {ap}")

    # ---- scoring sides ----
    long_pts = 0
    short_pts = 0
    if ta_v == "LONG":
        long_pts += 3 if ta_c >= 6 else 2 if ta_c >= 4 else 1
    elif ta_v == "SHORT":
        short_pts += 3 if ta_c >= 6 else 2 if ta_c >= 4 else 1
    if news == "bullish":
        long_pts += 2 if abs(news_w) >= 3 else 1
    elif news == "bearish":
        short_pts += 2 if abs(news_w) >= 3 else 1
    if bounce_plan is not None:
        if bounce_plan.side == "long":
            long_pts += 3 if bounce_plan.strong else 2
        else:
            short_pts += 3 if bounce_plan.strong else 2
    if ap == "long":
        long_pts += 1
    elif ap == "short":
        short_pts += 1
    if near_s or near_bd:
        long_pts += 1  # у поддержки чаще ловят long / не шортят
        short_pts -= 1
    if near_r or near_bo:
        short_pts += 1
        long_pts -= 1
    if mid_range:
        long_pts -= 2
        short_pts -= 2
        factors.append("середина range → без market")

    # конфликт новости vs TA
    if news == "bullish" and ta_v == "SHORT":
        short_pts -= 2
        factors.append("конфликт: новости↑ vs TA SHORT")
    if news == "bearish" and ta_v == "LONG":
        long_pts -= 2
        factors.append("конфликт: новости↓ vs TA LONG")

    long_pts = max(0, long_pts)
    short_pts = max(0, short_pts)
    score = max(long_pts, short_pts)
    score = max(1, min(10, score + (1 if abs(long_pts - short_pts) >= 2 else 0)))

    # ---- decide ----
    open_threshold = 5
    action = "wait"
    entry_lo = entry_hi = stop = target = None
    trigger = ""
    headline = "✋ НЕ ОТКРЫВАТЬ сейчас"

    def _long_levels() -> None:
        nonlocal entry_lo, entry_hi, stop, target, trigger
        if bounce_plan is not None and bounce_plan.side == "long":
            entry_lo, entry_hi = bounce_plan.entry_lo, bounce_plan.entry_hi
            stop = bounce_plan.stop
            target = bounce_plan.targets[0] if bounce_plan.targets else (r or bo)
            return
        if near_s and s:
            entry_lo, entry_hi = float(s) * 0.998, min(px, float(s) * 1.003)
            stop = float(bd or s) * 0.994
            target = float(r or bo or px * 1.006)
            return
        if near_bo and bo:
            entry_lo = entry_hi = float(bo)
            stop = float(s or bd or px * 0.993)
            target = float(bo) * 1.004
            trigger = f"вход после закрытия {interval_minutes}m выше {fmt_price(bo)}"
            return
        entry_lo = entry_hi = px
        stop = float(bd or s or px * 0.994)
        target = float(bo or r or px * 1.005)
        trigger = f"LONG-триггер: закрытие {interval_minutes}m ≥ {fmt_price(bo)}" if bo else (
            "ждать уровень поддержки / пробоя"
        )

    def _short_levels() -> None:
        nonlocal entry_lo, entry_hi, stop, target, trigger
        if bounce_plan is not None and bounce_plan.side == "short":
            entry_lo, entry_hi = bounce_plan.entry_lo, bounce_plan.entry_hi
            stop = bounce_plan.stop
            target = bounce_plan.targets[0] if bounce_plan.targets else (s or bd)
            return
        if near_r and r:
            entry_lo, entry_hi = max(px, float(r) * 0.997), float(r) * 1.002
            stop = float(bo or r) * 1.006
            target = float(s or bd or px * 0.994)
            return
        if near_bd and bd:
            entry_lo = entry_hi = float(bd)
            stop = float(r or bo or px * 1.007)
            target = float(bd) * 0.996
            trigger = f"вход после закрытия {interval_minutes}m ниже {fmt_price(bd)}"
            return
        entry_lo = entry_hi = px
        stop = float(bo or r or px * 1.006)
        target = float(bd or s or px * 0.995)
        trigger = f"SHORT-триггер: закрытие {interval_minutes}m ≤ {fmt_price(bd)}" if bd else (
            "ждать сопротивление / пробой вниз"
        )

    # Готовый вход: сильный score + цена у уровня / bounce / подтверждённый пробой
    ready_long = (
        long_pts >= open_threshold
        and long_pts > short_pts + 1
        and (
            (bounce_plan is not None and bounce_plan.side == "long")
            or near_s
            or near_bo
            or (ta_v == "LONG" and ta_c >= 6 and (near_s or near_bo or not mid_range))
        )
        and not mid_range
    )
    ready_short = (
        short_pts >= open_threshold
        and short_pts > long_pts + 1
        and (
            (bounce_plan is not None and bounce_plan.side == "short")
            or near_r
            or near_bd
            or (ta_v == "SHORT" and ta_c >= 6 and (near_r or near_bd or not mid_range))
        )
        and not mid_range
    )

    # Без касания уровня — не market-chase, даже при сильном score
    if ready_long and not (
        near_s or near_bo or (bounce_plan is not None and bounce_plan.side == "long")
    ):
        ready_long = False
    if ready_short and not (
        near_r or near_bd or (bounce_plan is not None and bounce_plan.side == "short")
    ):
        ready_short = False

    if ready_long:
        action = "open_long"
        _long_levels()
        headline = f"🟢 ОТКРЫВАТЬ LONG · {hold_lo}–{hold_hi} мин"
        factors.append(f"score long {long_pts} > short {short_pts}")
    elif ready_short:
        action = "open_short"
        _short_levels()
        headline = f"🔴 ОТКРЫВАТЬ SHORT · {hold_lo}–{hold_hi} мин"
        factors.append(f"score short {short_pts} > long {long_pts}")
    else:
        if long_pts > short_pts:
            _long_levels()
            headline = "✋ НЕ ОТКРЫВАТЬ · ждать LONG-триггер"
            if bo and not trigger:
                trigger = f"LONG: закрытие {interval_minutes}m ≥ {fmt_price(bo)}"
        elif short_pts > long_pts:
            _short_levels()
            headline = "✋ НЕ ОТКРЫВАТЬ · ждать SHORT-триггер"
            if bd and not trigger:
                trigger = f"SHORT: закрытие {interval_minutes}m ≤ {fmt_price(bd)}"
        elif mid_range:
            headline = "✋ НЕ ОТКРЫВАТЬ · середина диапазона"
            trigger = "ждать край range или новостной импульс"
        else:
            headline = "✋ НЕ ОТКРЫВАТЬ · нет сходимости факторов"
            trigger = "ждать уровень + подтверждение свечой"
        factors.append(f"score L{long_pts}/S{short_pts} — вход не готов")
        # для wait уровни — ориентир, не «вход сейчас»
        entry_lo = entry_hi = None

    return OilScalpCall(
        action=action,
        hold_min=hold_lo,
        hold_max=hold_hi,
        entry_lo=float(entry_lo) if entry_lo else None,
        entry_hi=float(entry_hi) if entry_hi else None,
        stop=float(stop) if stop else None,
        target=float(target) if target else None,
        score=int(score),
        headline_ru=headline,
        factors_ru=tuple(factors[:7]),
        trigger_ru=(trigger or "")[:180],
    )


def format_oil_scalp_block(call: OilScalpCall) -> str:
    """Блок в шапке дайджеста: что делать на 10–100 мин."""
    lines = [
        "⚡ <b>СЕЙЧАС · 10–100 мин</b>",
        f"<b>{call.headline_ru}</b> · сходимость <b>{call.score}/10</b>",
    ]
    if call.action in {"open_long", "open_short"}:
        if call.entry_lo and call.entry_hi:
            if abs(call.entry_lo - call.entry_hi) / max(call.entry_lo, 1e-9) < 0.0003:
                lines.append(f"Вход: <b>{fmt_price(call.entry_lo)}</b>")
            else:
                lines.append(
                    f"Вход: <b>{fmt_price(call.entry_lo)} – {fmt_price(call.entry_hi)}</b>"
                )
        if call.stop:
            lines.append(f"Стоп: <b>{fmt_price(call.stop)}</b>")
        if call.target:
            lines.append(f"TP1: <b>{fmt_price(call.target)}</b>")
        lines.append(
            f"Time-stop: <b>{call.hold_min}–{call.hold_max} мин</b> "
            "(если цели нет — закрыть / не держать)"
        )
    if call.trigger_ru:
        lines.append(f"Триггер: <i>{call.trigger_ru}</i>")
    if call.factors_ru:
        lines.append("Факторы: " + " · ".join(call.factors_ru))
    lines.append(
        "<i>Не финсовет. Размер маленький; без подтверждения уровня — не входить.</i>"
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class OilMicroSignal:
    """Микро-сигнал UKOUSD: цель ~0.2–0.3%, удержание десятки минут."""
    side: str  # long | short
    entry: float
    stop: float
    target: float
    tp_pct: float
    sl_pct: float
    impulse_pct: float
    hold_min: int
    hold_max: int
    quality: int  # 1–10
    reason_ru: str
    label: str = OIL_BRENT_LABEL


def detect_oil_micro_signal(
    bars: list[KlineBar],
    *,
    news_bias: OilNewsBias | None = None,
    tp_pct: float = 0.25,
    sl_pct: float = 0.18,
    min_impulse_pct: float = 0.12,
    max_impulse_pct: float = 0.55,
    lookback_bars: int = 4,
) -> OilMicroSignal | None:
    """Импульс 5m UKOUSD → SHORT/LONG на микротейк 0.2–0.3%.

    Фильтры качества:
    - импульс уже есть, но не «догоняем» большой ход (>max)
    - 2+ закрытия в сторону / тело не doji
    - новости не против сильного импульса
    """
    if len(bars) < max(12, lookback_bars + 2):
        return None
    tp = max(0.15, min(0.45, float(tp_pct)))
    sl = max(0.10, min(0.40, float(sl_pct)))
    lb = max(2, min(8, int(lookback_bars)))
    window = bars[-(lb + 1) :]
    px0 = float(window[0].close)
    px = float(window[-1].close)
    if px0 <= 0 or px <= 0:
        return None
    impulse = (px - px0) / px0 * 100.0
    abs_imp = abs(impulse)
    if abs_imp < min_impulse_pct or abs_imp > max_impulse_pct:
        return None

    # Последовательность закрытий в сторону импульса
    closes = [float(b.close) for b in window]
    if impulse < 0:
        descending = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
        if descending < max(1, lb // 2):
            return None
        side = "short"
    else:
        ascending = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
        if ascending < max(1, lb // 2):
            return None
        side = "long"

    last = window[-1]
    body = abs(float(last.close) - float(last.open))
    rng = max(float(last.high) - float(last.low), 1e-9)
    if body / rng < 0.22:
        return None  # doji / шум

    # Не входить против сильного новостного давления
    if news_bias is not None and abs(news_bias.weighted_score) >= 3.0:
        if side == "short" and news_bias.bias == "bullish":
            return None
        if side == "long" and news_bias.bias == "bearish":
            return None

    # Качество: сила импульса в «сладкой» зоне + тело + новости с нами
    quality = 5
    if 0.15 <= abs_imp <= 0.35:
        quality += 2
    elif abs_imp <= 0.45:
        quality += 1
    if body / rng >= 0.45:
        quality += 1
    if news_bias is not None:
        if side == "short" and news_bias.bias == "bearish":
            quality += 1
        elif side == "long" and news_bias.bias == "bullish":
            quality += 1
    quality = max(1, min(10, quality))
    if quality < 6:
        return None

    if side == "short":
        entry = px
        target = px * (1.0 - tp / 100.0)
        stop = px * (1.0 + sl / 100.0)
        reason = (
            f"UKOUSD падает {impulse:.2f}% за ~{lb * 5}м → SHORT микротейк {tp:.2f}%"
        )
    else:
        entry = px
        target = px * (1.0 + tp / 100.0)
        stop = px * (1.0 - sl / 100.0)
        reason = (
            f"UKOUSD растёт {impulse:+.2f}% за ~{lb * 5}м → LONG микротейк {tp:.2f}%"
        )

    # hold: быстрее на сильном импульсе
    if abs_imp >= 0.30:
        hold_min, hold_max = 10, 40
    else:
        hold_min, hold_max = 15, 60

    return OilMicroSignal(
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        tp_pct=tp,
        sl_pct=sl,
        impulse_pct=impulse,
        hold_min=hold_min,
        hold_max=hold_max,
        quality=quality,
        reason_ru=reason,
    )


def format_oil_micro_signal(sig: OilMicroSignal) -> str:
    side = "LONG" if sig.side == "long" else "SHORT"
    emoji = "🟢" if sig.side == "long" else "🔴"
    rr = abs(sig.target - sig.entry) / max(abs(sig.stop - sig.entry), 1e-9)
    # Абсолютные уровни — только ориентир с прокси-цены (Yahoo BZ / BZUSDT),
    # не тик Bybit TradFi UKOUSD.s (часто +0.5…2$).
    if sig.side == "long":
        how = (
            f"На Bybit жми <b>Buy</b> по <b>текущей</b> цене UKOUSD.s "
            f"(не копируй {fmt_price(sig.entry)} слепо).\n"
            f"TP: <b>+{sig.tp_pct:.2f}%</b> от твоей цены входа · "
            f"стоп: <b>−{sig.sl_pct:.2f}%</b> от входа."
        )
    else:
        how = (
            f"На Bybit жми <b>Sell</b> по <b>текущей</b> цене UKOUSD.s "
            f"(не копируй {fmt_price(sig.entry)} слепо).\n"
            f"TP: <b>−{sig.tp_pct:.2f}%</b> от твоей цены входа · "
            f"стоп: <b>+{sig.sl_pct:.2f}%</b> от входа."
        )
    return "\n".join([
        f"🛢 <b>Bybit UKOUSD.s · сигнал {side}</b> {emoji}",
        f"<b>Микро-сделка</b> · качество <b>{sig.quality}/10</b>",
        "",
        how,
        "",
        f"<i>Ориентир уровней (на Bybit цена может отличаться на $0.5–2): "
        f"вход {fmt_price(sig.entry)} · TP {fmt_price(sig.target)} · "
        f"стоп {fmt_price(sig.stop)}</i>",
        f"R:R ≈ <b>{rr:.1f}</b> · держать <b>{sig.hold_min}–{sig.hold_max} мин</b>",
        f"Импульс: <b>{sig.impulse_pct:+.2f}%</b>",
        "",
        f"<i>{sig.reason_ru}</i>",
        "<i>Торгуй только UKOUSD.s на Bybit TradFi. Считай TP/стоп в % "
        "от своей цены. Малый размер. Не финсовет.</i>",
    ])


def _pick_news_catalyst(items: list[OilNewsItem], bias: str) -> str:
    want = "bullish" if bias == "bullish" else "bearish"
    scored: list[tuple[float, OilNewsItem]] = []
    for it in items:
        if it.impact != want:
            continue
        scored.append(
            (news_impact_weight(it) + news_critical_score(it.title, source=it.source) * 0.1, it)
        )
    if not scored:
        return ""
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1].title[:140]


def build_oil_bounce_plan(
    snap: OilMarketSnapshot,
    news_bias: OilNewsBias,
    *,
    news_items: list[OilNewsItem] | None = None,
    min_score: float = 3.0,
) -> OilBouncePlan | None:
    """Сильный news-bias → конкретный отскок: уровень, entry, stop, TP."""
    if news_bias.bias not in {"bullish", "bearish"}:
        return None
    if abs(news_bias.weighted_score) < min_score:
        return None

    px = float(snap.price or 0.0)
    if px <= 0:
        return None
    s = snap.support
    r = snap.resistance
    bd = snap.breakdown
    bo = snap.breakout
    hi = snap.high_7d
    lo = snap.low_7d
    catalyst = _pick_news_catalyst(news_items or [], news_bias.bias)
    strong = abs(news_bias.weighted_score) >= min_score + 1.0

    if news_bias.bias == "bullish":
        bounce = float(s or bd or px * 0.992)
        if bounce <= 0 or bounce > px * 1.01:
            bounce = px * 0.995
        entry_lo = bounce * 0.998
        entry_hi = min(px, bounce * 1.004) if px >= bounce else bounce * 1.003
        stop = float(bd or bounce * 0.988)
        if stop >= entry_lo:
            stop = entry_lo * 0.992
        tp1 = float(r or bo or bounce * 1.008)
        if tp1 <= entry_hi:
            tp1 = entry_hi * 1.006
        tp2 = float(bo or hi or tp1 * 1.008)
        if tp2 <= tp1:
            tp2 = tp1 * 1.006
        tp3 = float(hi or tp2 * 1.01)
        if tp3 <= tp2:
            tp3 = tp2 * 1.008
        reason = (
            f"Новости↑ → LONG-отскок от S={fmt_price(bounce)} "
            f"(вход у support, стоп под breakdown, TP к R/breakout)"
            + (f" · {catalyst}" if catalyst else "")
        )
        return OilBouncePlan(
            side="long",
            bounce_level=bounce,
            entry_lo=entry_lo,
            entry_hi=entry_hi,
            stop=stop,
            targets=(tp1, tp2, tp3),
            catalyst=catalyst,
            reason_ru=reason,
            strong=strong,
            dist_pct=abs(px - bounce) / px * 100.0,
        )

    # bearish
    bounce = float(r or bo or px * 1.008)
    if bounce <= 0 or bounce < px * 0.99:
        bounce = px * 1.005
    entry_hi = bounce * 1.002
    entry_lo = max(px, bounce * 0.996) if px <= bounce else bounce * 0.997
    stop = float(bo or bounce * 1.012)
    if stop <= entry_hi:
        stop = entry_hi * 1.008
    tp1 = float(s or bd or bounce * 0.992)
    if tp1 >= entry_lo:
        tp1 = entry_lo * 0.994
    tp2 = float(bd or lo or tp1 * 0.992)
    if tp2 >= tp1:
        tp2 = tp1 * 0.994
    tp3 = float(lo or tp2 * 0.99)
    if tp3 >= tp2:
        tp3 = tp2 * 0.992
    reason = (
        f"Новости↓ → SHORT-отскок от R={fmt_price(bounce)} "
        f"(вход у resistance, стоп выше breakout, TP к S/breakdown)"
        + (f" · {catalyst}" if catalyst else "")
    )
    return OilBouncePlan(
        side="short",
        bounce_level=bounce,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        targets=(tp1, tp2, tp3),
        catalyst=catalyst,
        reason_ru=reason,
        strong=strong,
        dist_pct=abs(px - bounce) / px * 100.0,
    )


def apply_oil_bounce_to_ta(
    ta: TAAnalysisResult,
    plan: OilBouncePlan,
    *,
    ta_confidence_raw: int | None = None,
) -> None:
    """Переписывает entry/stop/TP на графике под новостной отскок."""
    raw = int(
        ta_confidence_raw
        if ta_confidence_raw is not None
        else (ta.verdict_confidence or 5)
    )
    # Не раздуваем 4/10 → 7/10: новости дают сторону, уверенность = TA + умеренный бонус
    news_boost = 1 if plan.strong else 0
    plan_conf = min(8, max(raw, min(raw + news_boost, 6 if plan.strong else raw)))

    ta.verdict = "LONG" if plan.side == "long" else "SHORT"
    ta.verdict_confidence = plan_conf
    why = (
        f"{plan.reason_ru} | база: TA {raw}/10 + новости "
        f"{'↑' if plan.side == 'long' else '↓'} → план {plan_conf}/10"
    )
    ta.verdict_reason = why[:220]
    ta.entry_zone = (float(plan.entry_lo), float(plan.entry_hi))
    ta.target_prices = [float(t) for t in plan.targets[:3]]
    ta.invalidation_price = float(plan.stop)
    ta.elliott_stop_price = float(plan.stop)
    ta.action_priority = "high" if plan.strong else "elevated"
    label = "новостной отскок LONG" if plan.side == "long" else "новостной отскок SHORT"
    scenario = TradeScenario(
        direction=plan.side,
        trigger_price=float(plan.bounce_level),
        trigger_label=label,
        stop_price=float(plan.stop),
        target_prices=[float(t) for t in plan.targets[:3]],
        conditions=[
            plan.reason_ru,
            f"зона входа {fmt_price(plan.entry_lo)}–{fmt_price(plan.entry_hi)}",
            f"стоп {fmt_price(plan.stop)} (под/над уровнем отмены)",
            f"TA было {raw}/10 · план {plan_conf}/10",
        ],
    )
    if plan.side == "long":
        ta.bullish_scenario = scenario
        ta.primary_scenario = "bullish"
    else:
        ta.bearish_scenario = scenario
        ta.primary_scenario = "bearish"


def format_oil_bounce_alert(plan: OilBouncePlan, *, label: str = "Brent") -> str:
    side = "LONG" if plan.side == "long" else "SHORT"
    emoji = "🟢" if plan.side == "long" else "🔴"
    tps = " / ".join(fmt_price(t) for t in plan.targets[:3])
    lines = [
        f"🛢 <b>{label} · отскок {side}</b> {emoji}",
        "",
        f"<b>Ловить отскок от {fmt_price(plan.bounce_level)}</b>",
        f"Вход: <b>{fmt_price(plan.entry_lo)} – {fmt_price(plan.entry_hi)}</b>",
        f"Стоп: <b>{fmt_price(plan.stop)}</b>",
        f"TP: <b>{tps}</b>",
        f"До уровня: <b>{plan.dist_pct:.2f}%</b>",
    ]
    if plan.catalyst:
        cat = plan.catalyst.replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"Катализатор: <i>{cat}</i>")
    lines.append("")
    lines.append("<i>Без шума: только сильный news-bias + цена у уровня.</i>")
    return "\n".join(lines)


def bounce_plan_near_level(plan: OilBouncePlan, *, near_pct: float = 0.4) -> bool:
    """Цена достаточно близко к уровню отскока — можно алертить."""
    return plan.dist_pct <= max(0.15, float(near_pct))

def _fetch_google_news_rss(
    query: str,
    *,
    timeout: float = 20.0,
    lang: str = "en",
) -> list[OilNewsItem]:
    q = urllib.parse.quote(query)
    if lang == "ru":
        url = f"https://news.google.com/rss/search?q={q}&hl=ru&gl=RU&ceid=RU:ru"
    else:
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BybitBot/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        logger.debug("Oil news RSS failed for %s (%s)", query, lang, exc_info=True)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        return []
    out: list[OilNewsItem] = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        src_el = item.find("source")
        if title_el is None or not title_el.text:
            continue
        title = _clean_title(title_el.text)
        link = (link_el.text or "").strip()
        source = (src_el.text or "news").strip() if src_el is not None else "news"
        theme = detect_oil_news_theme(title, source=source)
        if theme not in _PRIORITY_THEMES:
            continue
        pub_ts = resolve_oil_news_published_ts(
            rss_pub=pub_el.text if pub_el is not None else "",
            url=link,
        )
        if pub_ts is None:
            continue
        out.append(
            OilNewsItem(
                title=title[:240],
                url=link,
                source=source[:60],
                published_ts=pub_ts,
                impact=classify_news_impact(title),
                query=query,
                lang=lang,
                theme=theme,
            )
        )
    return out


def _fetch_pro_oil_rss(
    source_name: str,
    feed_url: str,
    *,
    timeout: float = 20.0,
) -> list[OilNewsItem]:
    """Прямой RSS OilPrice / EIA — без Google News."""
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; BybitBot/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        logger.debug("Pro oil RSS failed for %s", feed_url, exc_info=True)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        # Atom fallback
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns) or root.findall("entry")
        out_atom: list[OilNewsItem] = []
        for entry in entries:
            title_el = entry.find("a:title", ns) if entry.find("a:title", ns) is not None else entry.find("title")
            if title_el is None or not (title_el.text or "").strip():
                continue
            title = _clean_title(title_el.text or "")
            theme = _pro_feed_theme(title)
            if theme not in _PRIORITY_THEMES:
                continue
            link = ""
            link_el = entry.find("a:link", ns) if entry.find("a:link", ns) is not None else entry.find("link")
            if link_el is not None:
                link = (link_el.get("href") or link_el.text or "").strip()
            updated = entry.find("a:updated", ns) if entry.find("a:updated", ns) is not None else entry.find("updated")
            pub_raw = (updated.text or "") if updated is not None else ""
            pub_ts = resolve_oil_news_published_ts(rss_pub=pub_raw, url=link)
            if pub_ts is None:
                continue
            out_atom.append(
                OilNewsItem(
                    title=title[:240],
                    url=link,
                    source=source_name[:60],
                    published_ts=pub_ts,
                    impact=classify_news_impact(title),
                    query=feed_url,
                    lang="en",
                    theme=theme,
                )
            )
        return out_atom

    out: list[OilNewsItem] = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        if title_el is None or not title_el.text:
            continue
        title = _clean_title(title_el.text)
        theme = _pro_feed_theme(title)
        if theme not in _PRIORITY_THEMES:
            continue
        link = (link_el.text or "").strip() if link_el is not None else ""
        pub_ts = resolve_oil_news_published_ts(
            rss_pub=pub_el.text if pub_el is not None else "",
            url=link,
        )
        if pub_ts is None:
            continue
        out.append(
            OilNewsItem(
                title=title[:240],
                url=link,
                source=source_name[:60],
                published_ts=pub_ts,
                impact=classify_news_impact(title),
                query=feed_url,
                lang="en",
                theme=theme,
            )
        )
    return out


async def fetch_oil_fastlane_news(
    *,
    max_items: int = 8,
    max_age_hours: float = 4.0,
    min_flash_score: int = 7,
    include_russian: bool = True,
) -> list[OilNewsItem]:
    """Узкий быстрый пул: WSJ/Reuters/Bloomberg/Blas/FT/NYT/official."""
    from .oil_fastlane import FAST_LANE_QUERIES_EN, FAST_LANE_QUERIES_RU, is_fastlane_item

    seen: set[str] = set()
    merged: list[OilNewsItem] = []
    queries: list[tuple[str, str]] = [(q, "en") for q in FAST_LANE_QUERIES_EN]
    if include_russian:
        queries.extend((q, "ru") for q in FAST_LANE_QUERIES_RU)

    for query, lang in queries:
        items = await asyncio.to_thread(_fetch_google_news_rss, query, lang=lang)
        for it in items:
            if not oil_news_is_fresh(it.published_ts, max_age_hours=max_age_hours):
                continue
            # Fast-lane: не требуем общий critical_only — свой score по outlet
            if not is_fastlane_item(it, min_flash_score=min_flash_score):
                # Всё же пропустим geo tier1 с чуть более мягким порогом
                if not is_fastlane_item(it, min_flash_score=max(5, min_flash_score - 2)):
                    continue
                theme = it.theme or detect_oil_news_theme(it.title, source=it.source)
                if theme not in {"iran_geo", "trump_us"}:
                    continue
            key = it.title.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)

    merged.sort(
        key=lambda x: (x.published_ts, news_critical_score(x.title, source=x.source)),
        reverse=True,
    )
    return merged[:max_items]


async def fetch_oil_news(
    max_items: int = 10,
    *,
    include_russian: bool = True,
    critical_only: bool = True,
    critical_min_score: int = 5,
    max_age_hours: float = 24.0,
    include_pro_feeds: bool = True,
) -> list[OilNewsItem]:
    seen: set[str] = set()
    merged: list[OilNewsItem] = []
    queries: list[tuple[str, str]] = [(q, "en") for q in NEWS_QUERIES_EN]
    if include_russian:
        queries.extend((q, "ru") for q in NEWS_QUERIES_RU)

    for query, lang in queries:
        items = await asyncio.to_thread(_fetch_google_news_rss, query, lang=lang)
        for it in items:
            if not oil_news_is_fresh(it.published_ts, max_age_hours=max_age_hours):
                continue
            if critical_only and not is_critical_oil_news(it, critical_min_score):
                continue
            key = it.title.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)

    if include_pro_feeds:
        for source_name, feed_url in PRO_OIL_RSS_FEEDS:
            items = await asyncio.to_thread(_fetch_pro_oil_rss, source_name, feed_url)
            for it in items:
                if not oil_news_is_fresh(it.published_ts, max_age_hours=max_age_hours):
                    continue
                if critical_only and not is_critical_oil_news(it, critical_min_score):
                    continue
                key = it.title.lower()[:120]
                if key in seen:
                    continue
                seen.add(key)
                merged.append(it)

    # Сначала свежесть, потом критичность
    merged.sort(
        key=lambda x: (x.published_ts, news_critical_score(x.title, source=x.source)),
        reverse=True,
    )
    return merged[:max_items]

def _yahoo_interval_and_range(interval_minutes: int) -> tuple[str, str, int]:
    """(yahoo_interval, range, aggregate_factor). 10m → 5m×2."""
    im = max(5, min(60, int(interval_minutes)))
    if im <= 5:
        return "5m", "5d", 1
    if im <= 10:
        return "5m", "5d", 2
    if im <= 15:
        return "15m", "1mo", 1
    if im <= 30:
        return "30m", "1mo", 1
    return "60m", "3mo", 1


def _aggregate_oil_bars(bars: list[KlineBar], factor: int) -> list[KlineBar]:
    if factor <= 1 or len(bars) < factor:
        return bars
    out: list[KlineBar] = []
    for i in range(0, len(bars) - (len(bars) % factor), factor):
        chunk = bars[i : i + factor]
        out.append(
            KlineBar(
                open_time=chunk[0].open_time,
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low for b in chunk),
                close=chunk[-1].close,
                volume=sum(b.volume for b in chunk),
            )
        )
    return out


def _is_dead_oil_bar(bar: KlineBar) -> bool:
    """Yahoo часто отдаёт точки o=h=l=c в паузах сессии — на графике «точки/палки»."""
    rng = float(bar.high) - float(bar.low)
    body = abs(float(bar.close) - float(bar.open))
    return rng <= 1e-6 and body <= 1e-6


def sanitize_oil_session_bars(
    bars: list[KlineBar],
    *,
    interval_minutes: int = 5,
) -> list[KlineBar]:
    """Убрать мёртвые свечи и схлопнуть дыры сессии → ровный ряд как на терминале.

    Yahoo BZ=F/CL=F: weekend/overnight gaps + нулевые бары → datetime-ось рвёт
    ширину свечей. Переиндексируем живые бары с равномерным шагом TF.
    """
    if not bars:
        return bars
    alive = [b for b in bars if not _is_dead_oil_bar(b)]
    if len(alive) < 24:
        alive = list(bars)
    if len(alive) < 2:
        return alive
    step = max(60, int(interval_minutes) * 60)
    end_ts = float(alive[-1].open_time)
    start_ts = end_ts - (len(alive) - 1) * step
    out: list[KlineBar] = []
    for i, b in enumerate(alive):
        out.append(
            KlineBar(
                open_time=start_ts + i * step,
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
                volume=float(b.volume),
            )
        )
    return out


def _shift_bars_to_price(bars: list[KlineBar], ref_price: float) -> list[KlineBar]:
    """Сдвиг всей серии к ref (Bybit-форма → уровень UKOUSD)."""
    if not bars or ref_price <= 0:
        return bars
    basis = float(ref_price) - float(bars[-1].close)
    if abs(basis) < 1e-9:
        return bars
    return [
        KlineBar(
            open_time=b.open_time,
            open=float(b.open) + basis,
            high=float(b.high) + basis,
            low=float(b.low) + basis,
            close=float(b.close) + basis,
            volume=float(b.volume),
        )
        for b in bars
    ]


def _fetch_yahoo_oil_bars(
    yahoo_symbol: str,
    *,
    interval_minutes: int = 15,
    limit: int | None = None,
) -> list[KlineBar]:
    """Свечи Yahoo futures (BZ=F / CL=F) — proxy для UKOUSD / USOIL."""
    y_int, y_range, agg = _yahoo_interval_and_range(interval_minutes)
    lim = limit if limit is not None else _bars_limit_for_interval(interval_minutes)
    lim = min(max(int(lim), 24), 1000)
    params = urllib.parse.urlencode({
        "interval": y_int,
        "range": y_range,
        "includePrePost": "false",
    })
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol)}?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; BybitBot/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read())
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = ((payload.get("chart") or {}).get("error") or {}).get("description") or "empty"
        raise RuntimeError(f"Yahoo oil kline {yahoo_symbol}: {err}")
    r0 = result[0]
    timestamps = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    bars: list[KlineBar] = []
    for i, ts in enumerate(timestamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            if o is None or h is None or l is None or c is None:
                continue
            vol = float(volumes[i] or 0.0)
            bars.append(
                KlineBar(
                    open_time=float(ts),
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=vol,
                )
            )
        except (IndexError, TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b.open_time)
    bars = _aggregate_oil_bars(bars, agg)
    bars = sanitize_oil_session_bars(bars, interval_minutes=interval_minutes)
    if lim and len(bars) > lim:
        bars = bars[-lim:]
    return bars


def _fetch_bybit_oil_bars(
    symbol: str,
    *,
    interval_minutes: int = 15,
    limit: int | None = None,
) -> list[KlineBar]:
    """Свечи Bybit native commodity perps (linear): BZUSDT / CLUSDT."""
    interval = bybit_interval_for_minutes(interval_minutes)
    lim = limit if limit is not None else _bars_limit_for_interval(interval_minutes)
    lim = min(max(int(lim), 24), 1000)
    params = urllib.parse.urlencode({
        "category": "linear",
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": lim,
    })
    url = f"{BYBIT_KLINE_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BybitBot/1.0)"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        j = json.loads(resp.read())
    if j.get("retCode") != 0:
        raise RuntimeError(f"Bybit oil kline {symbol}: {j.get('retMsg')}")
    bars: list[KlineBar] = []
    for row in j.get("result", {}).get("list", []) or []:
        try:
            bars.append(
                KlineBar(
                    open_time=float(row[0]) / 1000.0,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        except (IndexError, TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b.open_time)
    return bars


def _fetch_oil_bars(
    *,
    yahoo_symbol: str,
    bybit_symbol: str,
    interval_minutes: int = 15,
    limit: int | None = None,
) -> list[KlineBar]:
    """Непрерывные свечи как на терминале + цена ≈ UKOUSD/USOIL.

    Yahoo BZ=F даёт дыры сессии и нулевые бары → «точки» на графике.
    Берём форму Bybit (ровный 5m), сдвигаем к последней цене Yahoo.
    Если Bybit недоступен — sanitized Yahoo.
    """
    yahoo_last: float | None = None
    yahoo_bars: list[KlineBar] = []
    try:
        yahoo_bars = _fetch_yahoo_oil_bars(
            yahoo_symbol, interval_minutes=interval_minutes, limit=limit,
        )
        if yahoo_bars:
            yahoo_last = float(yahoo_bars[-1].close)
    except Exception:
        logger.warning("Yahoo oil %s failed", yahoo_symbol, exc_info=True)

    try:
        bybit_bars = _fetch_bybit_oil_bars(
            bybit_symbol, interval_minutes=interval_minutes, limit=limit,
        )
        if len(bybit_bars) >= 24:
            if yahoo_last and yahoo_last > 0:
                return _shift_bars_to_price(bybit_bars, yahoo_last)
            return bybit_bars
    except Exception:
        logger.warning(
            "Bybit oil %s failed, fallback Yahoo",
            bybit_symbol,
            exc_info=True,
        )

    if len(yahoo_bars) >= 24:
        return yahoo_bars
    raise RuntimeError(f"Oil bars unavailable ({yahoo_symbol}/{bybit_symbol})")


async def fetch_oil_last_prices() -> dict[str, float]:
    """Быстрый тик для level-alerts — ключи UKOUSD / USOIL."""
    out: dict[str, float] = {}
    pairs = (
        ("UKOUSD", OIL_BRENT_YAHOO, OIL_BRENT_BYBIT),
        ("USOIL", OIL_WTI_YAHOO, OIL_WTI_BYBIT),
    )
    for label, ysym, bsym in pairs:
        try:
            bars = await asyncio.to_thread(
                _fetch_oil_bars,
                yahoo_symbol=ysym,
                bybit_symbol=bsym,
                interval_minutes=5,
                limit=5,
            )
            if bars:
                out[label] = float(bars[-1].close)
        except Exception:
            logger.debug("Oil price tick failed %s", label, exc_info=True)
    return out

def _snapshot_from_ta(
    label: str,
    sym: str,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
) -> OilMarketSnapshot:
    window = bars[-168:] if len(bars) >= 168 else bars
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    targets: list[float] = []
    if ta.target_prices:
        targets = [float(t) for t in ta.target_prices[:3] if t]
    elif ta.elliott_tp_prices:
        targets = [float(t) for t in ta.elliott_tp_prices[:3] if t]
    entry_zone = None
    if ta.entry_zone and len(ta.entry_zone) >= 2:
        entry_zone = (float(ta.entry_zone[0]), float(ta.entry_zone[1]))
    stop = ta.elliott_stop_price or ta.invalidation_price
    return OilMarketSnapshot(
        label=label,
        symbol=sym,
        price=float(bars[-1].close),
        high_7d=float(hi),
        low_7d=float(lo),
        verdict=(ta.verdict or "WAIT"),
        confidence=int(ta.verdict_confidence or 0),
        support=ta.nearest_support,
        resistance=ta.nearest_resistance,
        breakdown=ta.breakdown_level,
        breakout=ta.breakout_level,
        phase=(ta.phase_label or ""),
        elliott=(ta.elliott_label or "")[:120],
        reason=(ta.verdict_reason or ta.professional_summary or "")[:200],
        entry_zone=entry_zone,
        stop=float(stop) if stop else None,
        targets=tuple(targets),
    )

async def build_oil_analysis_bundle(
    *,
    interval_minutes: int = 15,
    include_brent: bool = True,
    include_wti: bool = True,
) -> OilAnalysisBundle | None:
    """Brent (UKOUSD ≈ BZ=F) + WTI (USOIL ≈ CL=F)."""
    im = max(5, min(60, int(interval_minutes)))

    brent_bars: list[KlineBar] = []
    brent_ta: TAAnalysisResult | None = None
    brent_snap: OilMarketSnapshot | None = None

    if include_brent:
        try:
            brent_bars = await asyncio.to_thread(
                _fetch_oil_bars,
                yahoo_symbol=OIL_BRENT_YAHOO,
                bybit_symbol=OIL_BRENT_BYBIT,
                interval_minutes=im,
            )
        except Exception:
            logger.warning("Brent UKOUSD/BZ=F fetch failed", exc_info=True)
            return None
        if len(brent_bars) < 24:
            return None
        hours = min(int(len(brent_bars) * im / 60), 1080)
        brent_ta = run_ta_analysis(
            brent_bars,
            is_long=True,
            symbol=OIL_BRENT_SYMBOL,
            hours=hours,
            interval_minutes=im,
            pattern_detection_enabled=True,
            pattern_min_confidence=0.50,
        )
        brent_snap = _snapshot_from_ta(
            OIL_BRENT_LABEL, OIL_BRENT_SYMBOL, brent_bars, brent_ta,
        )
    else:
        return None

    wti_bars: list[KlineBar] | None = None
    wti_ta: TAAnalysisResult | None = None
    wti_snap: OilMarketSnapshot | None = None
    if include_wti:
        try:
            wti_bars = await asyncio.to_thread(
                _fetch_oil_bars,
                yahoo_symbol=OIL_WTI_YAHOO,
                bybit_symbol=OIL_WTI_BYBIT,
                interval_minutes=im,
            )
            if len(wti_bars) >= 24:
                hours = min(int(len(wti_bars) * im / 60), 1080)
                wti_ta = run_ta_analysis(
                    wti_bars,
                    is_long=True,
                    symbol=OIL_WTI_SYMBOL,
                    hours=hours,
                    interval_minutes=im,
                    pattern_detection_enabled=False,
                )
                wti_snap = _snapshot_from_ta(
                    OIL_WTI_LABEL, OIL_WTI_SYMBOL, wti_bars, wti_ta,
                )
        except Exception:
            logger.debug("WTI USOIL/CL=F fetch failed", exc_info=True)

    mood = detect_oil_market_mood(brent_bars, brent_ta, im)

    return OilAnalysisBundle(
        brent_bars=brent_bars,
        brent_ta=brent_ta,
        brent=brent_snap,
        interval_minutes=im,
        market_mood=mood,
        wti_bars=wti_bars,
        wti_ta=wti_ta,
        wti=wti_snap,
    )

async def build_oil_market_snapshots(
    *,
    interval_minutes: int = 15,
) -> list[OilMarketSnapshot]:
    bundle = await build_oil_analysis_bundle(interval_minutes=interval_minutes)
    if bundle is None:
        return []
    out = [bundle.brent]
    if bundle.wti:
        out.append(bundle.wti)
    return out

def _age_label(published_ts: float) -> str:
    age_h = max(0.0, (time.time() - published_ts) / 3600.0)
    if age_h < 1.0:
        return f"{int(age_h * 60)} мин назад"
    if age_h <= 3.0:
        return f"{age_h:.1f}ч назад"
    if age_h <= 12.0:
        return f"{age_h:.1f}ч назад · рынок мог уже отыграть"
    if age_h < 48:
        return f"{age_h:.1f}ч назад · скорее фон, не импульс"
    return datetime.fromtimestamp(published_ts, tz=timezone.utc).strftime("%d.%m %H:%M UTC")

def format_single_oil_news(item: OilNewsItem) -> str:
    """Одна новость = одно сообщение со ссылкой."""
    title = item.title.replace("<", "&lt;").replace(">", "&gt;")
    impact_ru = {
        "bullish": "🟢 давление вверх на нефть",
        "bearish": "🔴 давление вниз на нефть",
        "neutral": "⚪ контекст / следить",
    }.get(item.impact, "⚪ контекст")
    lang_mark = "🇷🇺" if item.lang == "ru" else "🇬🇧"
    theme = item.theme or detect_oil_news_theme(item.title, source=item.source)
    theme_ru = theme_label_ru(theme)
    score = news_critical_score(item.title, source=item.source)
    pro = match_pro_oil_analyst(item.title, item.source)
    is_analyst = theme == "analyst" or pro is not None
    if pro is not None:
        header = f"🛢 <b>Нефть · ⭐ {pro[0]}</b>"
    elif is_analyst:
        header = "🛢 <b>Нефть · аналитика</b>"
    else:
        header = "🛢 <b>Нефть · важное</b>"
    lines = [
        header,
        f"<i>{theme_ru} · вес {score}</i>",
        "",
    ]
    if item.url:
        lines.append(f"<a href=\"{item.url}\"><b>{title}</b></a>")
    else:
        lines.append(f"<b>{title}</b>")
    lines.append("")
    lines.append(f"{impact_ru}")
    if pro is not None:
        lines.append(
            f"<i>Топ-аналитик ({pro[0]}) — приоритет для bias; "
            "не сигнал входа, сверяй с графиком UKOUSD.</i>"
        )
    elif is_analyst:
        lines.append("<i>Прогноз/мнение — не сигнал входа; сверяй с графиком UKOUSD.</i>")
    lines.append(f"<i>{lang_mark} {item.source} · {_age_label(item.published_ts)}</i>")
    if item.url:
        lines.append(f"🔗 <a href=\"{item.url}\">Открыть источник</a>")
    return "\n".join(lines)

def format_oil_news_message(items: list[OilNewsItem], *, max_show: int = 5) -> str:
    if not items:
        return (
            "🛢 <b>Нефть</b>\n"
            "<i>Нет важных: Иран/Трамп/США, EIA запасы, ОПЕК, прогнозы банков, покупки/объёмы.</i>"
        )
    if len(items) == 1:
        return format_single_oil_news(items[0])
    lines = ["🛢 <b>Нефть · важные новости</b>", ""]
    for it in items[:max_show]:
        lines.append(format_single_oil_news(it))
        lines.append("")
    return "\n".join(lines).strip()

def _oil_trading_plan(
    snap: OilMarketSnapshot,
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
    market_mood: str = "",
    news_bias: OilNewsBias | None = None,
    bounce_plan: OilBouncePlan | None = None,
    ta_confidence_raw: int | None = None,
    ta_verdict_raw: str | None = None,
) -> list[str]:
    """Сценарии LONG / SHORT / база — intraday 5m–1h."""
    lines: list[str] = []
    px = snap.price
    s = snap.support
    r = snap.resistance
    bd = snap.breakdown
    bo = snap.breakout
    tf = f"{interval_minutes}m"
    ta_raw = int(
        ta_confidence_raw
        if ta_confidence_raw is not None
        else (snap.confidence or ta.verdict_confidence or 0)
    )
    ta_v_raw = (ta_verdict_raw or snap.verdict or "WAIT").upper()

    lines.append("<b>Почему такое суждение</b>")
    if news_bias is not None:
        lines.append(f"• {news_bias.summary_ru}")
        if news_bias.basis_ru:
            lines.append(f"• <i>{news_bias.basis_ru}</i>")
    else:
        lines.append("• Новостной фон не учтён")

    lines.append(
        f"• Уровни TA ({tf}): цена <b>${px:.2f}</b>"
        + (f" · S {fmt_price(s)}" if s else "")
        + (f" · R {fmt_price(r)}" if r else "")
        + (f" ·↓{fmt_price(bd)}" if bd else "")
        + (f" ·↑{fmt_price(bo)}" if bo else "")
    )
    lines.append(
        f"• Чистый TA (до новостей): <b>{ta_v_raw}</b> {ta_raw}/10"
        + (f" · {snap.reason}" if snap.reason and bounce_plan is None else "")
    )

    lines.append("")
    lines.append("<b>Рабочий план</b>")
    if bounce_plan is not None:
        side = "LONG" if bounce_plan.side == "long" else "SHORT"
        tps = " / ".join(fmt_price(t) for t in bounce_plan.targets[:3])
        lines.append(
            f"• <b>Отскок {side}</b> от <b>{fmt_price(bounce_plan.bounce_level)}</b>"
        )
        lines.append(
            f"  вход {fmt_price(bounce_plan.entry_lo)}–{fmt_price(bounce_plan.entry_hi)} · "
            f"стоп {fmt_price(bounce_plan.stop)} · TP {tps}"
        )
        if bounce_plan.side == "long":
            lines.append(
                "  <i>Почему уровни: вход у support (покупатели), стоп под breakdown "
                "(отмена структуры), TP1=R, TP2=breakout, TP3=7д high.</i>"
            )
        else:
            lines.append(
                "  <i>Почему уровни: вход у resistance (продавцы), стоп выше breakout, "
                "TP1=S, TP2=breakdown, TP3=7д low.</i>"
            )
        if bounce_plan.catalyst:
            cat = bounce_plan.catalyst.replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"  катализатор: <i>{cat}</i>")
        if news_bias and news_bias.how_to_use_ru:
            lines.append(f"• {news_bias.how_to_use_ru}")
    else:
        if news_bias is not None:
            lines.append(f"• <i>{news_bias.how_to_use_ru}</i>")
        lines.append(f"• Итог на графике: <b>{snap.verdict}</b> · {snap.confidence}/10")

    if market_mood:
        lines.append(f"• Режим рынка: {market_mood}")

    # Если есть bounce-план — не дублируем оба сценария, только подтверждающий
    if bounce_plan is None:
        if s and r and px > 0:
            mid = (s + r) / 2.0
            if abs(px - mid) / px < 0.015:
                lines.append(
                    f"• <b>База:</b> range {fmt_price(s)} – {fmt_price(r)} · "
                    f"ждать пробой с закрытием {tf}"
                )

        if bo and bo > 0:
            lines.append(
                f"• <b>LONG:</b> закрытие {tf} выше {fmt_price(bo)} → "
                f"цели {fmt_price(bo + (bo - (bd or s or px) * 0.5))} / {fmt_price(snap.high_7d)}"
            )
            inv = (s or bd or px * 0.97)
            lines.append(f"  стоп под {fmt_price(inv)} · отмена если ниже")

        if bd and bd > 0:
            lines.append(
                f"• <b>SHORT:</b> закрытие {tf} ниже {fmt_price(bd)} → "
                f"цели {fmt_price(bd - (bo or r or px - bd) * 0.5)} / {fmt_price(snap.low_7d)}"
            )
            inv = (r or bo or px * 1.03)
            lines.append(f"  стоп выше {fmt_price(inv)} · отмена если выше")
    else:
        if bounce_plan.side == "long" and bo and bo > 0:
            lines.append(
                f"• Альтернатива: пробой↑ {fmt_price(bo)} (если отскок не дали)"
            )
        if bounce_plan.side == "short" and bd and bd > 0:
            lines.append(
                f"• Альтернатива: пробой↓ {fmt_price(bd)} (если отскок не дали)"
            )

    if snap.elliott:
        lines.append(f"• EW: {snap.elliott}")

    lines.append("")
    lines.append(
        "<i>Читай так: новости = направление bias; уровни TA = где входить/стоп; "
        "без пробоя/касания уровня — не входить.</i>"
    )
    return lines

def format_oil_market_digest(
    snaps: list[OilMarketSnapshot],
    *,
    ta: TAAnalysisResult | None = None,
    interval_minutes: int = 15,
    market_mood: str = "",
    news_bias: OilNewsBias | None = None,
    bounce_plan: OilBouncePlan | None = None,
    ta_confidence_raw: int | None = None,
    ta_verdict_raw: str | None = None,
    scalp_call: OilScalpCall | None = None,
    forecast: Any | None = None,
    flow: Any | None = None,
    bars: list[KlineBar] | None = None,
) -> str:
    primary = snaps[0] if snaps else None
    lines = [
        "📊 <b>Нефть · разбор</b>",
        f"<i>Bybit TradFi · <b>UKOUSD.s</b> · TF {interval_minutes}m</i>",
        "<i>Смотри только цену на Bybit. Другие графики (TV/Hyperliquid) — не для входа.</i>",
        "",
    ]
    if forecast is not None:
        from .oil_forecast import format_oil_forecast_block

        lines.append(format_oil_forecast_block(forecast))
        lines.append("")

    if flow is None and bars:
        from .oil_flow import compute_oil_flow_proxy

        # ~2–3ч окна: 5m→24, 15m→12, 60m→6
        lb = 24 if interval_minutes <= 5 else 12 if interval_minutes <= 15 else 8
        flow = compute_oil_flow_proxy(bars, lookback=lb)

    if flow is not None:
        from .oil_flow import format_oil_flow_block

        lines.append(format_oil_flow_block(flow))
        lines.append("")

    if scalp_call is None and primary is not None and ta is not None:
        scalp_call = build_oil_scalp_call(
            primary,
            ta,
            news_bias=news_bias,
            bounce_plan=bounce_plan,
            market_mood=market_mood,
            interval_minutes=interval_minutes,
            ta_confidence_raw=ta_confidence_raw,
            ta_verdict_raw=ta_verdict_raw,
        )
    if scalp_call is not None:
        lines.append(format_oil_scalp_block(scalp_call))
        lines.append("")

    for s in snaps:
        lines.append(f"<b>{s.label}</b> · <b>${s.price:.2f}</b>")
        lines.append(f"  7д: {fmt_price(s.low_7d)} – {fmt_price(s.high_7d)}")
        if s.support and s.resistance:
            lines.append(f"  S {fmt_price(s.support)} · R {fmt_price(s.resistance)}")
        if s.breakdown or s.breakout:
            lines.append(
                f"  пробой↓ {fmt_price(s.breakdown or 0)} · пробой↑ {fmt_price(s.breakout or 0)}"
            )
        lines.append("")

    if primary and ta is not None:
        lines.extend(
            _oil_trading_plan(
                primary,
                ta,
                interval_minutes=interval_minutes,
                market_mood=market_mood,
                news_bias=news_bias,
                bounce_plan=bounce_plan,
                ta_confidence_raw=ta_confidence_raw,
                ta_verdict_raw=ta_verdict_raw,
            )
        )
    elif primary:
        if news_bias is not None:
            lines.append(news_bias.summary_ru)
            lines.append(f"<i>{news_bias.how_to_use_ru}</i>")
        if bounce_plan is not None:
            lines.append(format_oil_bounce_alert(bounce_plan, label=primary.label))
        lines.append(f"Фаза: {primary.phase}")
        if primary.elliott:
            lines.append(f"EW: {primary.elliott}")

    if len(snaps) >= 2:
        spread = snaps[0].price - snaps[1].price
        lines.append(f"Спред Brent−WTI: <b>${spread:.2f}</b> (геополитика → Brent чувствительнее)")

    try:
        from .urals_price import fetch_urals_snapshot

        brent_px = snaps[0].price if snaps else None
        urals = fetch_urals_snapshot(brent_ref=brent_px)
        if urals is not None:
            disc = urals.discount_vs_brent
            disc_txt = f" · скидка к Brent <b>{disc:+.2f}</b>$" if disc is not None else ""
            chg = ""
            if urals.change_pct is not None:
                sign = "+" if urals.change_pct >= 0 else ""
                chg = f" ({sign}{urals.change_pct:.1f}% дн.)"
            lines.append(
                f"🇷🇺 <b>Urals</b> · <b>${urals.price:.2f}</b>{chg}{disc_txt}"
            )
    except Exception:
        pass

    return "\n".join(lines)

class OilMonitorEngine:
    """Отдельный oil-чат: важные новости + разбор + алерты уровней Brent/WTI."""

    def __init__(
        self,
        settings_manager: Any,
        on_news: Callable[[str], Awaitable[bool]],
        on_digest: Callable[[str, bytes | None], Awaitable[bool]] | None = None,
        on_level_alert: Callable[[str], Awaitable[bool]] | None = None,
        on_extra_chart: Callable[[str, bytes], Awaitable[bool]] | None = None,
        on_signal: Callable[[str], Awaitable[bool]] | None = None,
        on_setup: Callable[[str, bytes | None], Awaitable[bool]] | None = None,
        gemini_api_key: Callable[[], str | None] | str | None = None,
        gemini_model: Callable[[], str] | str | None = None,
    ) -> None:
        self.settings_manager = settings_manager
        self._on_news = on_news
        self._on_digest = on_digest
        self._on_level_alert = on_level_alert
        self._on_extra_chart = on_extra_chart
        self._on_signal = on_signal or on_level_alert
        self._on_setup = on_setup
        self._gemini_api_key = gemini_api_key
        self._gemini_model = gemini_model
        self._seen_titles: set[str] = set()
        self._last_digest_ts = 0.0
        self._level_watcher = OilLevelWatcher()
        self._recent_news: list[OilNewsItem] = []
        self._last_bounce_alert_ts: dict[str, float] = {}
        self._active_bounce: OilBouncePlan | None = None
        self._last_micro_signal_ts: float = 0.0
        self._micro_signal_hour: list[float] = []
        self._last_setup_ts: float = 0.0
        self._last_setup_side: str = ""
        self._last_trade_brief_ts: float = 0.0
        self._last_preopen_alert_ts: float = 0.0
        self._last_fastlane_ts: float = 0.0
        self._seen_fastlane: set[str] = set()
        self._seen_fastlane_stories: set[str] = set()
        self._last_regular_news_ts: float = 0.0
        self._last_hormuz_check_ts: float = 0.0
        self._last_hormuz_alert_ts: float = 0.0
        self._hormuz_prev: Any = None
        from .oil_journal import OilSetupJournal

        self._setup_journal = OilSetupJournal()
        self._last_ta_push_ts: float = 0.0
        self._load_oil_runtime_timing()

    def _load_oil_runtime_timing(self) -> None:
        """Паузы дайджест/setup переживают рестарт — не спамить сразу после reboot."""
        try:
            if not _OIL_RUNTIME_FILE.exists():
                return
            raw = json.loads(_OIL_RUNTIME_FILE.read_text(encoding="utf-8"))
            self._last_digest_ts = float(raw.get("last_digest_ts") or 0)
            self._last_setup_ts = float(raw.get("last_setup_ts") or 0)
            self._last_ta_push_ts = float(raw.get("last_ta_push_ts") or 0)
            self._last_setup_side = str(raw.get("last_setup_side") or "")
        except Exception:
            logger.debug("Oil runtime timing load failed", exc_info=True)

    def _save_oil_runtime_timing(self) -> None:
        try:
            payload = {
                "last_digest_ts": self._last_digest_ts,
                "last_setup_ts": self._last_setup_ts,
                "last_ta_push_ts": self._last_ta_push_ts,
                "last_setup_side": self._last_setup_side,
                "updated_ts": time.time(),
            }
            tmp = _OIL_RUNTIME_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(_OIL_RUNTIME_FILE)
        except Exception:
            logger.debug("Oil runtime timing save failed", exc_info=True)

    def _oil_ta_gap_ok(self, settings: Any) -> bool:
        gap = float(getattr(settings, "oil_ta_signal_gap_seconds", 10800))
        if gap <= 0:
            return True
        return (time.time() - self._last_ta_push_ts) >= gap

    def _mark_oil_ta_push(self) -> None:
        self._last_ta_push_ts = time.time()
        self._save_oil_runtime_timing()

    def _bybit_tradfi_open(self) -> bool:
        """True если UKOUSD.s сейчас торгуется на Bybit TradFi."""
        from .oil_session import is_ukousd_session_open

        return is_ukousd_session_open()

    def _oil_entry_signals_allowed(self, settings: Any) -> bool:
        """Мастер-тумблер сигналов входа (панель Нефть). Не зависит от сессии."""
        return bool(getattr(settings, "oil_entry_signals_enabled", True))

    def _resolve_gemini_key(self) -> str | None:
        key = self._gemini_api_key
        if callable(key):
            key = key()
        return (key or None) if key else None

    def _resolve_gemini_model(self) -> str:
        model = self._gemini_model
        if callable(model):
            model = model()
        return str(model or "gemini-3.6-flash")

    def _remember_news(self, items: list[OilNewsItem], *, cutoff: float) -> None:
        """Хранит важные новости за окно для сводки в дайджесте."""
        seen = {it.title.lower()[:120] for it in self._recent_news}
        for it in items:
            if it.published_ts < cutoff:
                continue
            key = it.title.lower()[:120]
            if key in seen:
                continue
            self._recent_news.append(it)
            seen.add(key)
        # truncate by age + count
        self._recent_news = [
            it for it in self._recent_news if it.published_ts >= cutoff
        ][-40:]

    async def _maybe_send_bounce_alert(
        self,
        plan: OilBouncePlan,
        *,
        label: str,
        settings: Any,
        png: bytes | None = None,
    ) -> int:
        """Редкий алерт: сильный bias + цена у уровня. Без спама."""
        if not getattr(settings, "oil_bounce_alerts_enabled", True):
            return 0
        if self._on_level_alert is None:
            return 0
        if not self._oil_entry_signals_allowed(settings):
            return 0
        if not plan.strong:
            return 0
        near_pct = float(getattr(settings, "oil_bounce_near_pct", 0.4))
        if not bounce_plan_near_level(plan, near_pct=near_pct):
            return 0
        cooldown = int(getattr(settings, "oil_bounce_alert_cooldown_seconds", 7200))
        key = f"{label.upper()}:{plan.side}"
        now = time.time()
        if now - self._last_bounce_alert_ts.get(key, 0.0) < cooldown:
            return 0
        msg = format_oil_bounce_alert(plan, label=label)
        try:
            ok = await self._on_level_alert(msg)
        except Exception:
            logger.exception("Oil bounce alert failed")
            ok = False
        if not ok:
            return 0
        self._last_bounce_alert_ts[key] = now
        # Журнал: авто «сбылось/нет» по TP/SL
        tps = plan.targets or ()
        tp1 = float(tps[0]) if tps else 0.0
        tp2 = float(tps[1]) if len(tps) > 1 else None
        if tp1 > 0 and plan.stop:
            self._setup_journal.register(
                side=plan.side,
                entry=float(plan.entry_mid),
                stop=float(plan.stop),
                tp1=tp1,
                tp2=tp2,
                price=float(plan.entry_mid),
                catalyst=plan.catalyst or plan.reason_ru or "",
                quality=8 if plan.strong else 6,
                source="bounce",
            )
        sent = 1
        # График с уже переписанными entry/stop/TP — в manual TA, без дубля текста в oil-чат
        if png and self._on_extra_chart is not None:
            try:
                await self._on_extra_chart(msg, png)
            except Exception:
                logger.exception("Oil bounce chart failed")
        return sent

    def _sync_levels_from_bundle(self, bundle: OilAnalysisBundle) -> None:
        self._level_watcher.update_levels(
            OIL_BRENT_LABEL,
            price=bundle.brent.price,
            breakout=bundle.brent.breakout,
            breakdown=bundle.brent.breakdown,
            symbol=OIL_BRENT_SYMBOL,
        )
        if bundle.wti and getattr(
            self.settings_manager.settings, "oil_include_wti", False
        ):
            self._level_watcher.update_levels(
                OIL_WTI_LABEL,
                price=bundle.wti.price,
                breakout=bundle.wti.breakout,
                breakdown=bundle.wti.breakdown,
                symbol=OIL_WTI_SYMBOL,
            )

    async def _tick_level_alerts(self, settings: Any) -> int:
        if not getattr(settings, "oil_level_alerts_enabled", True):
            return 0
        if self._on_level_alert is None:
            return 0
        if not self._oil_entry_signals_allowed(settings):
            return 0
        prices = await fetch_oil_last_prices()
        if not prices:
            return 0
        # только включённые инструменты
        filtered: dict[str, float] = {}
        if getattr(settings, "oil_include_brent", True) and "UKOUSD" in prices:
            filtered["UKOUSD"] = prices["UKOUSD"]
        if getattr(settings, "oil_include_wti", False) and "USOIL" in prices:
            filtered["USOIL"] = prices["USOIL"]
        if not filtered:
            return 0
        alerts = self._level_watcher.check_prices(filtered, settings)
        sent = 0
        for alert in alerts:
            try:
                ok = await self._on_level_alert(alert.message)
            except Exception:
                logger.exception("Oil level alert dispatch failed")
                ok = False
            if ok:
                sent += 1
        return sent

    async def _tick_micro_signals(self, settings: Any) -> int:
        """Микро-сигналы UKOUSD: TP ~0.2–0.3%, hold десятки минут."""
        if not getattr(settings, "oil_micro_signals_enabled", True):
            return 0
        if not self._oil_entry_signals_allowed(settings):
            return 0
        dispatch = self._on_signal or self._on_level_alert
        if dispatch is None:
            return 0
        now = time.time()
        cooldown = int(getattr(settings, "oil_micro_cooldown_seconds", 1200))
        if now - self._last_micro_signal_ts < cooldown:
            return 0
        max_h = int(getattr(settings, "oil_micro_max_per_hour", 3))
        self._micro_signal_hour = [t for t in self._micro_signal_hour if now - t < 3600]
        if len(self._micro_signal_hour) >= max_h:
            return 0

        try:
            bars = await asyncio.to_thread(
                _fetch_oil_bars,
                yahoo_symbol=OIL_BRENT_YAHOO,
                bybit_symbol=OIL_BRENT_BYBIT,
                interval_minutes=5,
                limit=80,
            )
        except Exception:
            logger.debug("Oil micro bars failed", exc_info=True)
            return 0
        news_bias = summarize_oil_news_bias(self._recent_news)
        sig = detect_oil_micro_signal(
            bars,
            news_bias=news_bias,
            tp_pct=float(getattr(settings, "oil_micro_tp_pct", 0.25)),
            sl_pct=float(getattr(settings, "oil_micro_sl_pct", 0.18)),
            min_impulse_pct=float(getattr(settings, "oil_micro_min_impulse_pct", 0.12)),
            max_impulse_pct=float(getattr(settings, "oil_micro_max_impulse_pct", 0.55)),
            lookback_bars=int(getattr(settings, "oil_micro_lookback_bars", 4)),
        )
        if sig is None:
            return 0
        msg = format_oil_micro_signal(sig)
        try:
            ok = await dispatch(msg)
        except Exception:
            logger.exception("Oil micro signal dispatch failed")
            return 0
        if not ok:
            return 0
        self._last_micro_signal_ts = now
        self._micro_signal_hour.append(now)
        self._setup_journal.register(
            side=sig.side,
            entry=float(sig.entry),
            stop=float(sig.stop),
            tp1=float(sig.target),
            tp2=None,
            price=float(sig.entry),
            catalyst=sig.reason_ru or "micro",
            quality=int(sig.quality),
            source="micro",
        )
        logger.info(
            "Oil micro %s @ %.2f TP=%.2f%% Q=%d",
            sig.side, sig.entry, sig.tp_pct, sig.quality,
        )
        return 1

    async def _tick_fastlane(self, settings: Any) -> int:
        """‼️ КРИТИЧНО: WSJ/Reuters/Bloomberg/Blas/FT/NYT/official → сразу в Новостник."""
        if not getattr(settings, "oil_fastlane_enabled", True):
            return 0
        now = time.time()
        interval = float(getattr(settings, "oil_fastlane_interval_seconds", 60.0))
        interval = max(45.0, interval)
        if now - self._last_fastlane_ts < interval:
            return 0

        from .oil_fastlane import (
            ai_says_off_topic,
            detect_fastlane_outlet,
            enrich_fastlane_with_gemini,
            fastlane_title_on_topic,
            format_fastlane_flash,
            _price_move_note,
        )

        max_age_h = float(getattr(settings, "oil_fastlane_max_age_hours", 4.0))
        min_score = int(getattr(settings, "oil_fastlane_min_score", 8))
        max_per = int(getattr(settings, "oil_fastlane_max_per_poll", 2))
        include_ru = bool(getattr(settings, "oil_russian_news", True))
        use_gemini = bool(getattr(settings, "oil_fastlane_gemini", True))

        try:
            items = await fetch_oil_fastlane_news(
                max_items=12,
                max_age_hours=max_age_h,
                min_flash_score=min_score,
                include_russian=include_ru,
            )
        except Exception:
            logger.exception("Oil fast-lane fetch failed")
            self._last_fastlane_ts = now
            return 0

        self._last_fastlane_ts = now
        cutoff = now - max_age_h * 3600.0
        self._remember_news(items, cutoff=cutoff)

        bars = None
        try:
            bars = await asyncio.to_thread(
                _fetch_oil_bars,
                yahoo_symbol=OIL_BRENT_YAHOO,
                bybit_symbol=OIL_BRENT_BYBIT,
                interval_minutes=5,
                limit=80,
            )
        except Exception:
            logger.debug("Oil fast-lane bars failed", exc_info=True)

        move_note = _price_move_note(bars, interval_minutes=5)
        sent = 0
        for it in items:
            key = it.title.lower()[:120]
            story = _news_story_key(it.title)
            if key in self._seen_titles or key in self._seen_fastlane:
                continue
            if story and story in self._seen_fastlane_stories:
                self._seen_fastlane.add(key)
                logger.info(
                    "Oil fast-lane skip story-dupe (%s): %s",
                    story[:60],
                    it.title[:80],
                )
                continue
            if not fastlane_title_on_topic(it.title):
                self._seen_fastlane.add(key)
                logger.info("Oil fast-lane skip off-topic title: %s", it.title[:90])
                continue
            meta = detect_fastlane_outlet(it.title, it.source, it.url or "")
            if meta is None:
                continue
            ai_ru = ""
            if use_gemini:
                ai_ru, bias_ov = await enrich_fastlane_with_gemini(
                    it.title,
                    source=it.source,
                    outlet=meta.outlet,
                    impact=it.impact,
                    move_note=move_note,
                    api_key=self._resolve_gemini_key(),
                    model=self._resolve_gemini_model(),
                )
                if ai_ru and ai_says_off_topic(ai_ru):
                    self._seen_fastlane.add(key)
                    self._seen_titles.add(key)
                    if story:
                        self._seen_fastlane_stories.add(story)
                    logger.info(
                        "Oil fast-lane drop (Gemini off-topic): %s",
                        it.title[:90],
                    )
                    continue
                if bias_ov in {"bullish", "bearish", "neutral"} and bias_ov != it.impact:
                    logger.info(
                        "Oil fast-lane bias override %s→%s: %s",
                        it.impact,
                        bias_ov,
                        it.title[:80],
                    )
                    it = replace(it, impact=bias_ov)
                    for i, old in enumerate(self._recent_news):
                        if (old.title or "").lower()[:120] == key:
                            self._recent_news[i] = it
                            break
            if ai_ru:
                from .oil_fastlane import strip_gemini_oil_meta, strip_invented_trade_levels

                ai_ru = strip_invented_trade_levels(strip_gemini_oil_meta(ai_ru))
            msg = format_fastlane_flash(
                it,
                meta=meta,
                ai_ru=ai_ru,
                move_note=move_note,
                age_label=_age_label(it.published_ts),
            )
            try:
                ok = await self._on_news(msg)
            except Exception:
                logger.exception("Oil fast-lane dispatch failed")
                ok = False
            if ok:
                self._seen_titles.add(key)
                self._seen_fastlane.add(key)
                if story:
                    self._seen_fastlane_stories.add(story)
                sent += 1
                logger.info(
                    "Oil fast-lane %s score=%d: %s",
                    meta.outlet,
                    meta.flash_score,
                    it.title[:80],
                )
                # Громкая новость, влияющая на сделку → ручной анализ
                await self._maybe_forward_trade_brief(
                    it,
                    meta=meta,
                    ai_ru=ai_ru,
                    move_note=move_note,
                    settings=settings,
                )
                if sent >= max_per:
                    break

        if len(self._seen_fastlane) > 300:
            self._seen_fastlane = set(list(self._seen_fastlane)[-150:])
        if len(self._seen_fastlane_stories) > 120:
            self._seen_fastlane_stories = set(list(self._seen_fastlane_stories)[-60:])
        return sent

    async def _maybe_forward_trade_brief(
        self,
        item: Any,
        *,
        meta: Any,
        ai_ru: str,
        move_note: str,
        settings: Any,
    ) -> None:
        """Дублирует важное для сделки в чат ручного TA (новости 24/7)."""
        if self._on_setup is None:
            return
        if not bool(getattr(settings, "oil_setup_enabled", True)):
            return
        from .oil_fastlane import (
            format_trade_impact_for_manual_ta,
            is_trade_critical_flash,
        )

        if not is_trade_critical_flash(item, meta, min_score=10):
            return
        now = time.time()
        # Не спамить ручной TA чаще чем раз в 15 мин
        if now - self._last_trade_brief_ts < 900:
            return
        brief = format_trade_impact_for_manual_ta(
            item, meta=meta, ai_ru=ai_ru, move_note=move_note,
        )
        try:
            ok = await self._on_setup(brief, None)
        except Exception:
            logger.exception("Oil trade-brief to manual TA failed")
            return
        if ok:
            self._last_trade_brief_ts = now
            logger.info(
                "Oil trade-brief → manual TA (%s score=%d)",
                meta.outlet,
                meta.flash_score,
            )

    async def poll_once(self) -> int:
        settings = self.settings_manager.settings
        if not getattr(settings, "oil_news_enabled", False):
            return 0
        if getattr(settings, "bot_paused", False):
            return 0

        sent = 0
        sent += await self._tick_level_alerts(settings)
        sent += await self._tick_micro_signals(settings)
        sent += await self._tick_fastlane(settings)
        sent += await self._tick_hormuz_alert(settings)
        sent += await self._tick_preopen_alert(settings)
        sent += await self._tick_setup_outcomes(settings)

        now = time.time()
        news_interval = float(getattr(settings, "oil_news_interval_seconds", 300.0))
        run_regular = (now - self._last_regular_news_ts) >= max(120.0, news_interval)

        max_age_h = float(getattr(settings, "oil_news_max_age_hours", 24.0))
        cutoff = now - max_age_h * 3600.0
        max_per_poll = int(getattr(settings, "oil_news_max_per_poll", 1))
        separate = bool(getattr(settings, "oil_news_separate_messages", True))
        critical_only = bool(getattr(settings, "oil_news_critical_only", True))
        critical_min = int(getattr(settings, "oil_news_critical_min_score", 5))
        include_ru = bool(getattr(settings, "oil_russian_news", True))
        include_pro = bool(getattr(settings, "oil_pro_feeds_enabled", True))

        items: list[OilNewsItem] = []
        if run_regular:
            try:
                items = await fetch_oil_news(
                    max_items=20,
                    include_russian=include_ru,
                    critical_only=critical_only,
                    critical_min_score=critical_min,
                    max_age_hours=max_age_h,
                    include_pro_feeds=include_pro,
                )
            except Exception:
                logger.exception("Oil news fetch failed")
                items = []
            self._last_regular_news_ts = now

            fresh: list[OilNewsItem] = []
            for it in items:
                if it.published_ts < cutoff:
                    continue
                key = it.title.lower()[:120]
                if key in self._seen_titles:
                    continue
                fresh.append(it)

            # Все свежие важные (не только отправленные) — в фон для дайджеста.
            self._remember_news(items, cutoff=cutoff)

            if fresh:
                batch = fresh[:max_per_poll]
                if separate:
                    for it in batch:
                        msg = format_single_oil_news(it)
                        try:
                            ok = await self._on_news(msg)
                        except Exception:
                            logger.exception("Oil news dispatch failed")
                            ok = False
                        if ok:
                            self._seen_titles.add(it.title.lower()[:120])
                            sent += 1
                else:
                    msg = format_oil_news_message(batch, max_show=len(batch))
                    try:
                        ok = await self._on_news(msg)
                    except Exception:
                        logger.exception("Oil news dispatch failed")
                        ok = False
                    if ok:
                        for it in batch:
                            self._seen_titles.add(it.title.lower()[:120])
                        sent += len(batch)

                if len(self._seen_titles) > 500:
                    self._seen_titles = set(list(self._seen_titles)[-200:])

        digest_enabled = bool(getattr(settings, "oil_digest_enabled", True))
        digest_h = float(getattr(settings, "oil_digest_interval_hours", 4.0))
        if (
            digest_enabled
            and self._on_digest is not None
            and now - self._last_digest_ts >= digest_h * 3600.0
        ):
            sent += await self._send_digest_once(settings, update_last_ts=True)

        # Между дайджестами: редкий bounce-alert если цена подошла к уровню
        if self._active_bounce is not None:
            try:
                prices = await fetch_oil_last_prices()
                brent_px = prices.get("UKOUSD") or prices.get("BRENT")
                if brent_px and brent_px > 0:
                    plan = self._active_bounce
                    dist = abs(brent_px - plan.bounce_level) / brent_px * 100.0
                    refreshed = OilBouncePlan(
                        side=plan.side,
                        bounce_level=plan.bounce_level,
                        entry_lo=plan.entry_lo,
                        entry_hi=plan.entry_hi,
                        stop=plan.stop,
                        targets=plan.targets,
                        catalyst=plan.catalyst,
                        reason_ru=plan.reason_ru,
                        strong=plan.strong,
                        dist_pct=dist,
                    )
                    sent += await self._maybe_send_bounce_alert(
                        refreshed,
                        label=OIL_BRENT_LABEL,
                        settings=settings,
                        png=None,
                    )
            except Exception:
                logger.debug("Oil bounce tick failed", exc_info=True)

        return sent

    async def send_digest_now(self) -> tuple[bool, str]:
        """Ручной вызов: свежий разбор + графики, без ожидания интервала."""
        settings = self.settings_manager.settings
        if self._on_digest is None:
            return False, "oil digest callback не подключен"
        try:
            n = await self._send_digest_once(settings, update_last_ts=True)
        except Exception as exc:
            logger.exception("Oil manual digest failed")
            return False, f"ошибка: {exc}"
        if n <= 0:
            return False, "не удалось собрать/отправить (проверьте Bybit/чат)"
        return True, f"отправлено ({n})"

    async def explain_why_now(self) -> tuple[bool, str]:
        """Честный разбор: почему цена растёт/падает — свежий сбор фактов «прямо сейчас»."""
        settings = self.settings_manager.settings
        interval_min = int(getattr(settings, "oil_interval_minutes", 15))
        try:
            bundle = await build_oil_analysis_bundle(
                interval_minutes=interval_min,
                include_brent=True,
                include_wti=False,
            )
            if not bundle:
                return False, "не удалось собрать свечи UKOUSD"

            # Жёсткий свежий сбор: fast-lane ≤4ч + обычные ≤8ч (не старый кэш)
            fresh_h = 8.0
            flash_h = min(
                4.0, float(getattr(settings, "oil_fastlane_max_age_hours", 4.0))
            )
            cutoff = time.time() - fresh_h * 3600.0
            try:
                regular, flash = await asyncio.gather(
                    fetch_oil_news(
                        max_items=20,
                        include_russian=bool(getattr(settings, "oil_russian_news", True)),
                        critical_only=False,
                        critical_min_score=2,
                        max_age_hours=fresh_h,
                        include_pro_feeds=bool(
                            getattr(settings, "oil_pro_feeds_enabled", True)
                        ),
                    ),
                    fetch_oil_fastlane_news(
                        max_items=12,
                        include_russian=bool(getattr(settings, "oil_russian_news", True)),
                        max_age_hours=flash_h,
                        min_flash_score=max(
                            6, int(getattr(settings, "oil_fastlane_min_score", 8)) - 2
                        ),
                    ),
                )
                merged: list[OilNewsItem] = []
                seen: set[str] = set()
                for it in list(flash) + list(regular):
                    key = (it.title or "").lower()[:120]
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(it)
                self._remember_news(merged, cutoff=cutoff)
                # Для why — приоритет свежему merged, не раздутый старый кэш
                why_items = [
                    it
                    for it in merged
                    if oil_news_is_fresh(it.published_ts, max_age_hours=fresh_h)
                ] or merged
            except Exception:
                logger.debug("Oil why news refresh failed", exc_info=True)
                why_items = list(self._recent_news)

            news_bias = summarize_oil_news_bias(
                why_items,
                ta_verdict=bundle.brent.verdict,
                ta_confidence=int(bundle.brent.confidence or 0),
            )
            forecast = None
            if bool(getattr(settings, "oil_forecast_enabled", True)):
                from .oil_forecast import build_oil_forecast

                scalp = build_oil_scalp_call(
                    bundle.brent,
                    bundle.brent_ta,
                    news_bias=news_bias,
                    market_mood=bundle.market_mood,
                    interval_minutes=bundle.interval_minutes,
                )
                forecast = build_oil_forecast(
                    bundle.brent,
                    bundle.brent_ta,
                    news_bias=news_bias,
                    news_items=why_items,
                    scalp_call=scalp,
                    market_mood=bundle.market_mood,
                    interval_minutes=bundle.interval_minutes,
                )

            from .oil_flow import compute_oil_flow_proxy
            from .oil_why import (
                build_oil_why_report,
                enrich_why_with_gemini,
                format_oil_why_report,
            )

            flow = compute_oil_flow_proxy(
                bundle.brent_bars,
                lookback=12 if interval_min <= 15 else 8,
            )
            # Черновик без ИИ — чтобы знать direction/move
            draft = build_oil_why_report(
                bundle.brent_bars,
                news_items=why_items,
                news_bias=news_bias,
                forecast=forecast,
                flow=flow,
                interval_minutes=bundle.interval_minutes,
            )
            if draft is None:
                return False, "мало данных для объяснения"

            extra_bits: list[str] = []
            try:
                from .oil_inventory import fetch_us_oil_inventory, format_inventory_short

                inv = await fetch_us_oil_inventory()
                short = format_inventory_short(inv)
                if short:
                    extra_bits.append(short)
            except Exception:
                logger.debug("Why+inventory failed", exc_info=True)

            headlines = [
                f"{getattr(it, 'source', '')}: {getattr(it, 'title', '')}"
                for it in why_items[:10]
                if getattr(it, "title", "")
            ]
            ai_now = await enrich_why_with_gemini(
                direction=draft.direction,
                move_1h=draft.move_1h_pct,
                move_4h=draft.move_4h_pct,
                price=draft.price,
                headlines=headlines,
                extra_context="; ".join(extra_bits),
                api_key=self._resolve_gemini_key(),
                model=self._resolve_gemini_model(),
            )
            report = build_oil_why_report(
                bundle.brent_bars,
                news_items=why_items,
                news_bias=news_bias,
                forecast=forecast,
                flow=flow,
                interval_minutes=bundle.interval_minutes,
                ai_now_ru=ai_now,
            )
            if report is None:
                return False, "мало данных для объяснения"
            text = format_oil_why_report(report)
            if extra_bits:
                text += "\n\n" + "\n".join(extra_bits)
            return True, text
        except Exception as exc:
            logger.exception("Oil why explain failed")
            return False, f"ошибка: {exc}"

    async def weekend_open_brief_now(self) -> tuple[bool, str]:
        """Что ждать на открытии пн ~01:00 MSK по свежим новостям."""
        settings = self.settings_manager.settings
        try:
            from .oil_session import (
                build_weekend_open_brief,
                format_weekend_open_brief,
                oil_session_status,
            )

            session = oil_session_status()
            interval_min = int(getattr(settings, "oil_interval_minutes", 15))
            bundle = await build_oil_analysis_bundle(
                interval_minutes=interval_min,
                include_brent=True,
                include_wti=False,
            )
            price = float(bundle.brent.price) if bundle else 0.0
            sat_hi = sun_lo = None
            if bundle and bundle.brent_bars:
                recent = bundle.brent_bars[-min(len(bundle.brent_bars), 400) :]
                if recent:
                    sat_hi = max(float(b.high) for b in recent)
                    sun_lo = min(float(b.low) for b in recent)

            max_age_h = float(getattr(settings, "oil_news_max_age_hours", 24.0))
            cutoff = time.time() - max_age_h * 3600.0
            try:
                fresh = await fetch_oil_news(
                    max_items=20,
                    include_russian=bool(getattr(settings, "oil_russian_news", True)),
                    critical_only=False,
                    critical_min_score=2,
                    max_age_hours=max_age_h,
                    include_pro_feeds=bool(getattr(settings, "oil_pro_feeds_enabled", True)),
                )
                self._remember_news(fresh, cutoff=cutoff)
            except Exception:
                logger.debug("Open-brief news refresh failed", exc_info=True)

            news_bias = summarize_oil_news_bias(self._recent_news)
            forecast = None
            if bool(getattr(settings, "oil_forecast_enabled", True)) and bundle:
                from .oil_forecast import build_oil_forecast

                forecast = build_oil_forecast(
                    bundle.brent,
                    bundle.brent_ta,
                    news_bias=news_bias,
                    news_items=self._recent_news,
                    market_mood=bundle.market_mood,
                    interval_minutes=bundle.interval_minutes,
                )

            brief = build_weekend_open_brief(
                price=price or float(getattr(bundle.brent, "price", 0) if bundle else 0),
                news_items=self._recent_news,
                news_bias=news_bias,
                forecast=forecast,
                session=session,
                sat_high_hint=sat_hi,
                sun_low_hint=sun_lo,
            )
            text = format_weekend_open_brief(brief, session=session)
            # Без длинного ИИ-блока — короткий бриф сам по делу
            return True, text
        except Exception as exc:
            logger.exception("Weekend open brief failed")
            return False, f"ошибка: {exc}"

    async def hormuz_status_now(self) -> tuple[bool, str]:
        """Открыт ли Ормуз / ходят ли танкеры (новости ± API)."""
        settings = self.settings_manager.settings
        try:
            from .oil_hormuz import build_hormuz_status, format_hormuz_status

            max_age_h = min(24.0, float(getattr(settings, "oil_news_max_age_hours", 24.0)))
            cutoff = time.time() - max_age_h * 3600.0
            try:
                fresh = await fetch_oil_news(
                    max_items=16,
                    include_russian=True,
                    critical_only=False,
                    critical_min_score=2,
                    max_age_hours=max_age_h,
                    include_pro_feeds=True,
                )
                self._remember_news(fresh, cutoff=cutoff)
            except Exception:
                logger.debug("Hormuz news refresh failed", exc_info=True)

            import os

            st = await build_hormuz_status(
                self._recent_news,
                api_key=os.getenv("HORMUZ_API_KEY"),
            )
            text = format_hormuz_status(st)
            try:
                from .oil_inventory import fetch_us_oil_inventory, format_inventory_short

                inv = await fetch_us_oil_inventory()
                short = format_inventory_short(inv)
                if short:
                    text += (
                        "\n\n———\n"
                        + short
                        + "\n<i>Подробнее: /spr или кнопка «📦 Запасы»</i>"
                    )
            except Exception:
                logger.debug("Hormuz+inventory attach failed", exc_info=True)
            return True, text
        except Exception as exc:
            logger.exception("Hormuz status failed")
            return False, f"ошибка: {exc}"

    async def inventory_status_now(self) -> tuple[bool, str]:
        """SPR + коммерческие запасы США (EIA) с оценкой мало/много."""
        try:
            from .oil_inventory import fetch_us_oil_inventory, format_inventory_status

            st = await fetch_us_oil_inventory()
            return True, format_inventory_status(st)
        except Exception as exc:
            logger.exception("Inventory status failed")
            return False, f"ошибка: {exc}"

    async def ask_oil_ai(self, question: str) -> tuple[bool, str]:
        """Чат с ИИ по нефти: новости + цена + сессия."""
        q = (question or "").strip()
        if not q:
            return False, "пустой вопрос"
        key = self._resolve_gemini_key()
        if not key:
            return False, "нет GEMINI_API_KEY"
        settings = self.settings_manager.settings
        try:
            from .ai_analyst import ask_gemini, sanitize_ai_reply_for_telegram
            from .oil_session import oil_session_status

            session = oil_session_status()
            interval_min = int(getattr(settings, "oil_interval_minutes", 15))
            bundle = await build_oil_analysis_bundle(
                interval_minutes=interval_min,
                include_brent=True,
                include_wti=False,
            )
            price = float(bundle.brent.price) if bundle else 0.0
            news_bias = summarize_oil_news_bias(self._recent_news)
            tops = "\n".join(
                f"- [{getattr(it, 'impact', '')}] {getattr(it, 'title', '')}"
                for it in self._recent_news[:10]
            ) or "(нет свежих в памяти — сначала дождись poll новостей)"
            ctx = (
                "Ты аналитик нефти для обычного человека, не для терминала. "
                "Отвечай ТОЛЬКО по-русски, простыми словами, без англ. жаргона "
                "(никаких gap-down, deal-tape, bias, premium — говори «откроется ниже», "
                "«страх войны ослаб», «скорее подешевеет»).\n"
                f"Сейчас: {session.market_open_hint_ru}. {session.next_open_label_ru}\n"
                f"Ориентир цены: {price}\n"
                f"Тон новостей бота: {news_bias.bias} (число {news_bias.weighted_score})\n"
                f"Заголовки:\n{tops}\n"
            )
            result = await ask_gemini(
                api_key=key,
                model=self._resolve_gemini_model(),
                context_text=ctx,
                user_text=(
                    q
                    + "\n\nОтвет по-русски: 6–10 строк, выжми важное, без простыни. "
                    "Структура: что случилось → почему важно → куда цена → что делать → на что смотреть. "
                    "Не финансовый совет. Если выходные — отдельно про открытие биржи."
                ),
            )
            text = sanitize_ai_reply_for_telegram(result.text or "").strip()
            if result.error or not text:
                return False, result.error or "пустой ответ ИИ"
            safe = (
                text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            return True, f"🤖 <b>Нефть · ИИ</b>\n\n{safe}"
        except Exception as exc:
            logger.exception("Oil AI chat failed")
            return False, f"ошибка: {exc}"

    async def _send_digest_once(
        self,
        settings: Any,
        *,
        update_last_ts: bool = True,
    ) -> int:
        """Собрать дайджест + PNG Brent/WTI и отправить."""
        if self._on_digest is None:
            return 0
        chart_enabled = bool(getattr(settings, "oil_chart_enabled", True))
        interval_min = int(getattr(settings, "oil_interval_minutes", 15))
        include_brent = bool(getattr(settings, "oil_include_brent", True))
        include_wti = bool(getattr(settings, "oil_include_wti", False))
        sent = 0
        try:
            bundle = await build_oil_analysis_bundle(
                interval_minutes=interval_min,
                include_brent=include_brent,
                include_wti=include_wti,
            )
            if not bundle:
                return 0
            self._sync_levels_from_bundle(bundle)
            snaps = [bundle.brent]
            if bundle.wti:
                snaps.append(bundle.wti)
            min_score = float(getattr(settings, "oil_bounce_min_news_score", 3.0))
            ta_verdict_raw = bundle.brent.verdict
            ta_conf_raw = int(bundle.brent.confidence or 0)
            news_bias = summarize_oil_news_bias(
                self._recent_news,
                ta_verdict=ta_verdict_raw,
                ta_confidence=ta_conf_raw,
            )
            bounce = build_oil_bounce_plan(
                bundle.brent,
                news_bias,
                news_items=self._recent_news,
                min_score=min_score,
            )
            self._active_bounce = bounce
            if bounce is not None:
                apply_oil_bounce_to_ta(
                    bundle.brent_ta,
                    bounce,
                    ta_confidence_raw=ta_conf_raw,
                )
                bundle.brent.verdict = (
                    "LONG" if bounce.side == "long" else "SHORT"
                )
                bundle.brent.confidence = int(
                    bundle.brent_ta.verdict_confidence or ta_conf_raw
                )
                bundle.brent.entry_zone = (bounce.entry_lo, bounce.entry_hi)
                bundle.brent.stop = bounce.stop
                bundle.brent.targets = bounce.targets
                bundle.brent.reason = bounce.reason_ru[:200]

            scalp = build_oil_scalp_call(
                bundle.brent,
                bundle.brent_ta,
                news_bias=news_bias,
                bounce_plan=bounce,
                market_mood=bundle.market_mood,
                interval_minutes=bundle.interval_minutes,
                ta_confidence_raw=ta_conf_raw,
                ta_verdict_raw=ta_verdict_raw,
            )
            forecast = None
            if bool(getattr(settings, "oil_forecast_enabled", True)):
                from .oil_forecast import (
                    build_oil_forecast,
                    enrich_oil_forecast_with_gemini,
                )

                forecast = build_oil_forecast(
                    bundle.brent,
                    bundle.brent_ta,
                    news_bias=news_bias,
                    news_items=self._recent_news,
                    bounce_plan=bounce,
                    scalp_call=scalp,
                    market_mood=bundle.market_mood,
                    interval_minutes=bundle.interval_minutes,
                    ta_verdict_raw=ta_verdict_raw,
                    ta_confidence_raw=ta_conf_raw,
                )
                if bool(getattr(settings, "oil_forecast_gemini", True)):
                    forecast = await enrich_oil_forecast_with_gemini(
                        forecast,
                        bundle.brent,
                        news_items=self._recent_news,
                        api_key=self._resolve_gemini_key(),
                        model=self._resolve_gemini_model(),
                    )

            digest = format_oil_market_digest(
                snaps,
                ta=bundle.brent_ta,
                interval_minutes=bundle.interval_minutes,
                market_mood=bundle.market_mood,
                news_bias=news_bias,
                bounce_plan=bounce,
                ta_confidence_raw=ta_conf_raw,
                ta_verdict_raw=ta_verdict_raw,
                scalp_call=scalp,
                forecast=forecast,
                bars=bundle.brent_bars,
            )
            # пометка ручного/планового вызова в шапке уже есть в digest
            png: bytes | None = None
            display_h = int(getattr(settings, "oil_chart_display_hours", 18) or 18)
            height_scale = float(
                getattr(settings, "oil_chart_height_scale", 1.45)
                or getattr(settings, "signal_chart_height_scale", 1.0)
                or 1.45
            )
            if chart_enabled:
                from .chart_renderer import render_oil_chart

                png = render_oil_chart(
                    bundle.brent_bars,
                    bundle.brent_ta,
                    symbol_label=OIL_BRENT_LABEL,
                    interval_minutes=bundle.interval_minutes,
                    display_hours=display_h,
                    height_scale=max(1.35, height_scale),
                )
            ok = await self._on_digest(digest, png)
            if not ok:
                return 0
            if update_last_ts:
                self._last_digest_ts = time.time()
                self._mark_oil_ta_push()
            sent = 1
            # По умолчанию НЕ шлём «Вход» следом — в дайджесте уже план/прогноз
            if bool(getattr(settings, "oil_setup_with_digest", False)):
                sent += await self._maybe_dispatch_confluence_setup(
                    settings,
                    bundle=bundle,
                    forecast=forecast,
                    news_bias=news_bias,
                    scalp=scalp,
                    bounce=bounce,
                    ta_verdict_raw=ta_verdict_raw,
                    ta_conf_raw=ta_conf_raw,
                    png=png,
                )
            if (
                chart_enabled
                and bundle.wti_bars
                and bundle.wti_ta
                and self._on_extra_chart is not None
            ):
                from .chart_renderer import render_oil_chart

                wti_bounce = None
                if bounce is not None and bundle.wti:
                    wti_bias = summarize_oil_news_bias(
                        self._recent_news,
                        ta_verdict=bundle.wti.verdict,
                    )
                    wti_bounce = build_oil_bounce_plan(
                        bundle.wti,
                        wti_bias,
                        news_items=self._recent_news,
                        min_score=min_score,
                    )
                    if wti_bounce is not None:
                        apply_oil_bounce_to_ta(
                            bundle.wti_ta,
                            wti_bounce,
                            ta_confidence_raw=int(bundle.wti.confidence or 0),
                        )
                wti_png = render_oil_chart(
                    bundle.wti_bars,
                    bundle.wti_ta,
                    symbol_label=OIL_WTI_LABEL,
                    interval_minutes=bundle.interval_minutes,
                    display_hours=display_h,
                    height_scale=max(1.35, height_scale),
                )
                if wti_png:
                    wti_caption = (
                        f"📊 <b>{OIL_WTI_LABEL}</b> · {bundle.interval_minutes}m · "
                        f"${bundle.wti.price:.2f}"
                    )
                    if wti_bounce is not None:
                        wti_caption += f"\n{wti_bounce.reason_ru}"
                    try:
                        await self._on_extra_chart(wti_caption, wti_png)
                        sent += 1
                    except Exception:
                        logger.exception("Oil WTI chart dispatch failed")
            return sent
        except Exception:
            logger.exception("Oil digest compose failed")
            return 0

    async def _maybe_dispatch_confluence_setup(
        self,
        settings: Any,
        *,
        bundle: OilAnalysisBundle,
        forecast: Any | None,
        news_bias: OilNewsBias,
        scalp: OilScalpCall,
        bounce: OilBouncePlan | None,
        ta_verdict_raw: str,
        ta_conf_raw: int,
        png: bytes | None,
    ) -> int:
        """Сильный confluence → отдельное сообщение в ручной TA."""
        if self._on_setup is None:
            return 0
        if not bool(getattr(settings, "oil_setup_enabled", True)):
            return 0
        if not self._oil_entry_signals_allowed(settings):
            logger.debug("Oil confluence skipped: entry signals toggled OFF")
            return 0
        # Антиспам: не дублировать торговые пуши в ручной TA
        if not self._oil_ta_gap_ok(settings):
            logger.debug("Oil confluence skipped: TA signal gap")
            return 0
        base_q = int(getattr(settings, "oil_setup_min_quality", 7))
        from .oil_journal import adaptive_min_quality, gemini_memory_block

        stats = self._setup_journal.stats()
        min_q = (
            adaptive_min_quality(base_q, stats)
            if bool(getattr(settings, "oil_outcome_learning_enabled", True))
            else base_q
        )
        near_pct = float(getattr(settings, "oil_setup_near_pct", 0.35))
        cooldown = float(getattr(settings, "oil_setup_cooldown_seconds", 14400))
        now = time.time()
        if now - self._last_setup_ts < cooldown:
            return 0

        from .oil_confluence import (
            build_oil_confluence_setup,
            enrich_setup_with_gemini,
            format_oil_confluence_setup,
            setup_passes_gate,
        )

        setup = build_oil_confluence_setup(
            bundle.brent,
            bundle.brent_ta,
            forecast=forecast,
            news_bias=news_bias,
            scalp_call=scalp,
            bounce_plan=bounce,
            news_items=self._recent_news,
            market_mood=bundle.market_mood,
            interval_minutes=bundle.interval_minutes,
            ta_verdict_raw=ta_verdict_raw,
            ta_confidence_raw=ta_conf_raw,
            min_quality=min_q,
            near_pct=near_pct,
        )
        if not setup_passes_gate(setup, min_quality=min_q):
            return 0
        assert setup is not None
        # Не спамить ту же сторону подряд в пределах cooldown (уже проверен) —
        # доп. защита: тот же side < 15 мин даже если cooldown снижен
        if setup.side == self._last_setup_side and now - self._last_setup_ts < 900:
            return 0

        memory = ""
        if bool(getattr(settings, "oil_outcome_learning_enabled", True)):
            memory = gemini_memory_block(stats)
        if bool(getattr(settings, "oil_forecast_gemini", True)):
            setup = await enrich_setup_with_gemini(
                setup,
                bundle.brent,
                news_items=self._recent_news,
                api_key=self._resolve_gemini_key(),
                model=self._resolve_gemini_model(),
                memory_ru=memory,
            )

        msg = format_oil_confluence_setup(setup)
        chart_png = png
        if chart_png is None and bool(getattr(settings, "oil_chart_enabled", True)):
            try:
                from .chart_renderer import render_oil_chart

                display_h = int(getattr(settings, "oil_chart_display_hours", 18) or 18)
                height_scale = float(
                    getattr(settings, "oil_chart_height_scale", 1.45) or 1.45
                )
                chart_png = render_oil_chart(
                    bundle.brent_bars,
                    bundle.brent_ta,
                    symbol_label=OIL_BRENT_LABEL,
                    interval_minutes=bundle.interval_minutes,
                    display_hours=display_h,
                    height_scale=max(1.35, height_scale),
                )
            except Exception:
                logger.exception("Oil setup chart render failed")
                chart_png = None
        try:
            ok = await self._on_setup(msg, chart_png)
        except Exception:
            logger.exception("Oil confluence setup dispatch failed")
            return 0
        if not ok:
            return 0
        self._last_setup_ts = now
        self._last_setup_side = setup.side
        self._mark_oil_ta_push()
        # Журнал исхода
        entry = None
        if setup.entry_lo is not None and setup.entry_hi is not None:
            entry = (float(setup.entry_lo) + float(setup.entry_hi)) / 2.0
        elif setup.price:
            entry = float(setup.price)
        if entry and setup.stop and setup.tp1:
            self._setup_journal.register(
                side=setup.side,
                entry=entry,
                stop=float(setup.stop),
                tp1=float(setup.tp1),
                tp2=float(setup.tp2) if setup.tp2 else None,
                price=float(setup.price),
                catalyst=setup.catalyst or "",
                quality=int(setup.quality),
                source="confluence",
            )
        logger.info(
            "Oil confluence setup %s quality=%d @ %.2f",
            setup.side,
            setup.quality,
            setup.price,
        )
        return 1

    async def _tick_hormuz_alert(self, settings: Any) -> int:
        """Пуш в Новостник, когда Ормуз меняется так, что это важно для цены."""
        if not bool(getattr(settings, "oil_hormuz_alerts_enabled", True)):
            return 0
        now = time.time()
        interval = float(getattr(settings, "oil_hormuz_interval_seconds", 900))
        if now - self._last_hormuz_check_ts < max(300.0, interval):
            return 0
        self._last_hormuz_check_ts = now

        from .oil_hormuz import build_hormuz_status, detect_hormuz_alert

        try:
            import os

            st = await build_hormuz_status(
                self._recent_news,
                api_key=os.getenv("HORMUZ_API_KEY"),
            )
        except Exception:
            logger.debug("Hormuz poll for alert failed", exc_info=True)
            return 0

        alert = detect_hormuz_alert(self._hormuz_prev, st)
        self._hormuz_prev = st
        if alert is None:
            return 0

        cooldown = float(getattr(settings, "oil_hormuz_alert_cooldown_seconds", 1800))
        # Смена open↔closed / critical — короче кулдаун
        min_cd = 600.0 if alert.trade_critical else cooldown
        if now - self._last_hormuz_alert_ts < min_cd:
            return 0

        sent = 0
        try:
            if await self._on_news(alert.message_html):
                sent += 1
        except Exception:
            logger.exception("Hormuz alert → news chat failed")

        if (
            sent
            and alert.trade_critical
            and self._on_setup is not None
            and bool(getattr(settings, "oil_setup_enabled", True))
        ):
            brief = (
                "🚢 <b>Ормуз → сделка</b>\n"
                + alert.message_html
                + "\n\n<i>Учти до входа: трафик пролива = риск премии/дисконта по нефти.</i>"
            )
            try:
                if await self._on_setup(brief, None):
                    sent += 1
            except Exception:
                logger.exception("Hormuz alert → manual TA failed")

        if sent:
            self._last_hormuz_alert_ts = now
            logger.info(
                "Hormuz alert sent (%d) critical=%s: %s",
                sent,
                alert.trade_critical,
                "; ".join(alert.reasons_ru)[:160],
            )
        return sent

    async def _tick_preopen_alert(self, settings: Any) -> int:
        """За 30–70 мин до открытия пн — краткий план в Новостник + ручной TA."""
        from .oil_session import should_send_preopen_alert

        if not should_send_preopen_alert():
            return 0
        now = time.time()
        # Не чаще 1 раза в 5 дней
        if now - self._last_preopen_alert_ts < 5 * 24 * 3600:
            return 0
        ok, text = await self.weekend_open_brief_now()
        if not ok:
            return 0
        header = (
            "⏰ <b>Скоро открытие нефти</b> · осталось ~30–60 минут\n"
            "Прочитай главное до входа. Новости США/Иран сейчас важнее графика.\n\n"
        )
        full = header + text
        sent = 0
        try:
            if await self._on_news(full):
                sent += 1
        except Exception:
            logger.exception("Pre-open → news chat failed")
        if self._on_setup is not None and bool(getattr(settings, "oil_setup_enabled", True)):
            try:
                if await self._on_setup(full, None):
                    sent += 1
            except Exception:
                logger.exception("Pre-open → manual TA failed")
        if sent:
            self._last_preopen_alert_ts = now
            logger.info("Oil pre-open alert sent (%d)", sent)
        return sent

    async def _tick_setup_outcomes(self, settings: Any) -> int:
        """Авто-журнал: сбылось / стоп / время вышло (в фоне)."""
        if self._on_setup is None and self._on_news is None:
            return 0
        if not self._setup_journal.active():
            return 0

        from .oil_journal import format_outcome_message

        session_open = self._bybit_tradfi_open()
        price = 0.0
        if session_open:
            try:
                bundle = await build_oil_analysis_bundle(
                    interval_minutes=15,
                    include_brent=True,
                    include_wti=False,
                )
            except Exception:
                logger.debug("Outcome bars failed", exc_info=True)
                bundle = None
            if bundle:
                price = float(bundle.brent.price)
            else:
                # Нет цены — только expire по возрасту
                session_open = False

        # Закрытая сессия: только «время вышло», без TP/SL по Yahoo
        done = self._setup_journal.check_price(
            price,
            allow_price_resolve=bool(session_open and price > 0),
        )
        if not done:
            return 0

        stats = self._setup_journal.stats()
        sent = 0
        for w in done:
            px = price if price > 0 else float(w.resolve_price or w.price_at_signal)
            msg = format_outcome_message(w, price_now=px, stats=stats)
            try:
                if self._on_setup is not None and w.source in {
                    "confluence",
                    "bounce",
                }:
                    ok = await self._on_setup(msg, None)
                elif self._on_news is not None:
                    ok = await self._on_news(msg)
                elif self._on_setup is not None:
                    ok = await self._on_setup(msg, None)
                else:
                    ok = False
            except Exception:
                logger.exception("Oil outcome dispatch failed")
                ok = False
            if ok:
                sent += 1
            logger.info(
                "Oil outcome %s %s source=%s → %s",
                w.side,
                w.watch_id,
                w.source,
                w.outcome,
            )
        return sent

    async def run_loop(self, interval: float | None = None) -> None:
        while True:
            settings = self.settings_manager.settings
            sleep_s = float(
                interval
                if interval is not None
                else getattr(settings, "oil_news_interval_seconds", 300.0)
            )
            fastlane_on = bool(getattr(settings, "oil_fastlane_enabled", True))
            if fastlane_on:
                fl = float(getattr(settings, "oil_fastlane_interval_seconds", 60.0))
                sleep_s = min(sleep_s, max(45.0, fl))
                sleep_s = max(45.0, sleep_s)
            else:
                sleep_s = max(120.0, sleep_s)
            try:
                if getattr(settings, "oil_news_enabled", False):
                    n = await self.poll_once()
                    if n:
                        logger.info("Oil monitor sent %d message(s)", n)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Oil monitor loop error")
            await asyncio.sleep(sleep_s)

