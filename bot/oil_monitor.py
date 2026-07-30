"""Нефть: отдельный чат — свежие новости (ссылки) + техразбор + прогноз.

Данные: Google News RSS (EN + RU), Bybit TradFi linear commodity:
  BZUSDT = Brent (UI: UKOUSD / Brent), CLUSDT = WTI (UI: USOIL).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable

from .bybit_klines import BYBIT_KLINE_URL, KlineBar
from .oil_level_watcher import OilLevelWatcher
from .ta_analysis import TAAnalysisResult, TradeScenario, fmt_price, run_ta_analysis

logger = logging.getLogger(__name__)

# Bybit TradFi commodity perps (category=linear, symbolType=commodity)
OIL_BRENT_SYMBOL = "BZUSDT"  # Brent — в UI часто как UKOUSD
OIL_WTI_SYMBOL = "CLUSDT"    # WTI — в UI часто как USOIL

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
_BULL_NEWS = frozenset({
    "surge", "rise", "rally", "jump", "spike", "attack", "strike", "block",
    "close strait", "escalat", "sanction", "cut produc", "draw", "tighten",
    "shortage", "deficit", "buy", "purchase", "рост", "подскоч", "атак",
    "сокращен", "дефицит", "покуп",
})
_BEAR_NEWS = frozenset({
    "fall", "drop", "decline", "slide", "plunge", "deal", "reopen", "de-escal",
    "ceasefire", "forecast cut", "build", "oversupply", "release spr", "accord",
    "surplus", "sell", "dump", "паден", "снижен", "сделк", "избыт", "продаж",
    "перемир",
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
}

# Только эти темы имеют право уйти в чат (не «любая нефть»)
_PRIORITY_THEMES = frozenset({
    "iran_geo",
    "trump_us",
    "inventory",
    "opec",
    "flow_deal",
})

NEWS_QUERIES_EN: tuple[str, ...] = (
    "Iran oil Trump sanctions Hormuz when:1d",
    "Trump Iran oil when:1d",
    "US Iran crude oil sanctions when:1d",
    "EIA crude oil inventory stocks when:1d",
    "OPEC oil production quota cut when:2d",
    "SPR oil release OR strategic petroleum reserve when:2d",
    "crude oil tanker export import volume when:2d",
)
NEWS_QUERIES_RU: tuple[str, ...] = (
    "нефть Иран Трамп санкции Ормуз when:1d",
    "Трамп Иран нефть when:1d",
    "EIA запасы нефти США when:1d",
    "ОПЕК квота добыча нефть when:2d",
    "СПР запасы нефть США when:2d",
    "экспорт импорт нефти танкер when:2d",
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
    theme: str = ""  # iran_geo | trump_us | inventory | opec | flow_deal


def detect_oil_news_theme(title: str) -> str:
    """Главная тема заголовка — без темы приоритета новость не шлём."""
    low = title.lower()
    has_oil = any(k in low for k in _OIL_KEYWORDS)
    if not has_oil:
        # Иран/Ормуз без слова oil всё равно нефтяная геополитика
        if any(k in low for k in ("hormuz", "ормуз", "iran", "иран")) and any(
            k in low for k in ("strait", "sanction", "санкц", "tanker", "танкер", "crude", "нефт")
        ):
            has_oil = True
    if not has_oil:
        return ""

    if any(k in low for k in ("hormuz", "ормуз", "iran", "иран", "tehran", "тегеран", "houthi", "хусит")):
        return "iran_geo"
    if any(k in low for k in ("trump", "трамп")) or (
        any(k in low for k in _US_KEYWORDS)
        and any(k in low for k in ("sanction", "санкц", "iran", "иран", "spr", "eia"))
    ):
        return "trump_us"
    if any(k in low for k in ("eia", "inventory", "inventories", "запас", "spr", "stockpile", "api ")):
        return "inventory"
    if any(k in low for k in _OPEC_KEYWORDS):
        return "opec"
    if any(k in low for k in _FLOW_KEYWORDS):
        return "flow_deal"
    return ""


def _is_relevant(title: str) -> bool:
    """Только нефть + приоритетная тема (не любой заголовок про Brent)."""
    return detect_oil_news_theme(title) in _PRIORITY_THEMES


def news_critical_score(title: str) -> int:
    low = title.lower()
    score = 0
    for term, weight in _CRITICAL_TERMS.items():
        if term in low:
            score += weight
    theme = detect_oil_news_theme(title)
    # Бонус за приоритетную тему
    if theme == "iran_geo":
        score += 2
    elif theme in {"trump_us", "inventory", "opec"}:
        score += 1
    return score


def is_critical_oil_news(item: OilNewsItem, min_score: int = 4) -> bool:
    theme = item.theme or detect_oil_news_theme(item.title)
    if theme not in _PRIORITY_THEMES:
        return False
    return news_critical_score(item.title) >= min_score


def theme_label_ru(theme: str) -> str:
    return {
        "iran_geo": "🇮🇷 Иран / Ормуз",
        "trump_us": "🇺🇸 Трамп / США",
        "inventory": "📦 Запасы EIA/SPR",
        "opec": "🛢️ ОПЕК / добыча",
        "flow_deal": "🚢 Покупки / объёмы / поставки",
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

def _parse_rss_pub(pub: str) -> float:
    if not pub:
        return time.time()
    try:
        return parsedate_to_datetime(pub).timestamp()
    except Exception:
        return time.time()

def _clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    if " - " in t:
        parts = t.rsplit(" - ", 1)
        if len(parts[1]) < 40:
            return parts[0].strip()
    return t

def classify_news_impact(title: str) -> str:
    low = title.lower()
    bull = sum(1 for k in _BULL_NEWS if k in low)
    bear = sum(1 for k in _BEAR_NEWS if k in low)
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
    weighted_score: float = 0.0  # >0 вверх, <0 вниз
    bias: str = "neutral"  # bullish | bearish | mixed | neutral
    summary_ru: str = ""
    how_to_use_ru: str = ""


def news_impact_weight(item: OilNewsItem) -> float:
    """Вес новости: критичность (Hormuz/EIA/OPEC) сильнее обычного заголовка."""
    score = float(news_critical_score(item.title))
    return max(1.0, min(6.0, score / 2.0))


def summarize_oil_news_bias(
    items: list[OilNewsItem],
    *,
    ta_verdict: str | None = None,
) -> OilNewsBias:
    """Считает, сколько важных новостей давят вверх/вниз и как это стыковать с TA."""
    if not items:
        return OilNewsBias(
            summary_ru="Новостной фон: нет важных заголовков",
            how_to_use_ru="Торговать только от уровней TA, без новостного подтверждения.",
        )

    bull = bear = neut = 0
    weighted = 0.0
    for it in items:
        w = news_impact_weight(it)
        if it.impact == "bullish":
            bull += 1
            weighted += w
        elif it.impact == "bearish":
            bear += 1
            weighted -= w
        else:
            neut += 1

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

    if bias == "bullish":
        arrow = f"🟢↑ давление вверх ({bull} вверх / {bear} вниз"
        if neut:
            arrow += f" / {neut} нейтр."
        arrow += ")"
    elif bias == "bearish":
        arrow = f"🔴↓ давление вниз ({bear} вниз / {bull} вверх"
        if neut:
            arrow += f" / {neut} нейтр."
        arrow += ")"
    elif bias == "mixed":
        arrow = f"🟡 смешанно ({bull} вверх / {bear} вниз)"
    else:
        arrow = f"⚪ нейтрально ({neut} контекст)"

    score_txt = f"score {weighted:+.1f}"
    summary = f"Новостной фон: {arrow} · {score_txt}"

    tv = (ta_verdict or "WAIT").upper()
    if bias == "bullish" and tv == "LONG":
        howto = "Новости = TA → приоритет LONG от уровней / на пробое вверх."
    elif bias == "bearish" and tv == "SHORT":
        howto = "Новости = TA → приоритет SHORT от уровней / на пробое вниз."
    elif bias == "bullish" and tv == "SHORT":
        howto = "Конфликт: новости вверх, TA вниз → не гнаться; ждать пробоя или уменьшить размер."
    elif bias == "bearish" and tv == "LONG":
        howto = "Конфликт: новости вниз, TA вверх → не гнаться; ждать пробоя или уменьшить размер."
    elif bias == "bullish":
        howto = "Новости вверх, TA нейтрален → ловить LONG от support / на пробое R."
    elif bias == "bearish":
        howto = "Новости вниз, TA нейтрален → ловить SHORT от resistance / на пробое S."
    elif bias == "mixed":
        howto = "Новости спорят → только чистый пробой уровня, без агрессивного входа."
    else:
        howto = "Новостей мало → торговать чисто от TA / уровней."

    return OilNewsBias(
        bullish=bull,
        bearish=bear,
        neutral=neut,
        weighted_score=round(weighted, 1),
        bias=bias,
        summary_ru=summary,
        how_to_use_ru=howto,
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


def _pick_news_catalyst(items: list[OilNewsItem], bias: str) -> str:
    want = "bullish" if bias == "bullish" else "bearish"
    scored: list[tuple[float, OilNewsItem]] = []
    for it in items:
        if it.impact != want:
            continue
        scored.append((news_impact_weight(it) + news_critical_score(it.title) * 0.1, it))
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
            f"Новости↑ → ловить LONG-отскок от {fmt_price(bounce)}"
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
        f"Новости↓ → ловить SHORT-отскок от {fmt_price(bounce)}"
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


def apply_oil_bounce_to_ta(ta: TAAnalysisResult, plan: OilBouncePlan) -> None:
    """Переписывает entry/stop/TP на графике под новостной отскок."""
    ta.verdict = "LONG" if plan.side == "long" else "SHORT"
    ta.verdict_confidence = max(int(ta.verdict_confidence or 0), 7 if plan.strong else 6)
    ta.verdict_reason = plan.reason_ru[:220]
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
            f"стоп {fmt_price(plan.stop)}",
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
        theme = detect_oil_news_theme(title)
        if theme not in _PRIORITY_THEMES:
            continue
        link = (link_el.text or "").strip()
        source = (src_el.text or "news").strip() if src_el is not None else "news"
        pub_ts = _parse_rss_pub(pub_el.text if pub_el is not None else "")
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

async def fetch_oil_news(
    max_items: int = 10,
    *,
    include_russian: bool = True,
    critical_only: bool = True,
    critical_min_score: int = 4,
) -> list[OilNewsItem]:
    seen: set[str] = set()
    merged: list[OilNewsItem] = []
    queries: list[tuple[str, str]] = [(q, "en") for q in NEWS_QUERIES_EN]
    if include_russian:
        queries.extend((q, "ru") for q in NEWS_QUERIES_RU)

    for query, lang in queries:
        items = await asyncio.to_thread(_fetch_google_news_rss, query, lang=lang)
        for it in items:
            if critical_only and not is_critical_oil_news(it, critical_min_score):
                continue
            key = it.title.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    # Сначала самые критичные, потом свежесть
    merged.sort(
        key=lambda x: (news_critical_score(x.title), x.published_ts),
        reverse=True,
    )
    return merged[:max_items]

def _fetch_bybit_oil_bars(
    symbol: str,
    *,
    interval_minutes: int = 15,
    limit: int | None = None,
) -> list[KlineBar]:
    """Свечи Bybit TradFi commodity (linear): BZUSDT / CLUSDT."""
    import json

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


async def fetch_oil_last_prices() -> dict[str, float]:
    """Быстрый тик для level-alerts — 5m последнее закрытие Bybit."""
    out: dict[str, float] = {}
    for label, sym in (("BRENT", OIL_BRENT_SYMBOL), ("WTI", OIL_WTI_SYMBOL)):
        try:
            bars = await asyncio.to_thread(
                _fetch_bybit_oil_bars, sym, interval_minutes=5, limit=5,
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
    """Brent (BZUSDT) + WTI (CLUSDT) с Bybit TradFi linear."""
    im = max(5, min(60, int(interval_minutes)))

    brent_bars: list[KlineBar] = []
    brent_ta: TAAnalysisResult | None = None
    brent_snap: OilMarketSnapshot | None = None

    if include_brent:
        try:
            brent_bars = await asyncio.to_thread(
                _fetch_bybit_oil_bars, OIL_BRENT_SYMBOL, interval_minutes=im,
            )
        except Exception:
            logger.warning("Brent BZUSDT fetch failed", exc_info=True)
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
            "Brent · BZUSDT", OIL_BRENT_SYMBOL, brent_bars, brent_ta,
        )
    else:
        return None

    wti_bars: list[KlineBar] | None = None
    wti_ta: TAAnalysisResult | None = None
    wti_snap: OilMarketSnapshot | None = None
    if include_wti:
        try:
            wti_bars = await asyncio.to_thread(
                _fetch_bybit_oil_bars, OIL_WTI_SYMBOL, interval_minutes=im,
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
                    "WTI · CLUSDT", OIL_WTI_SYMBOL, wti_bars, wti_ta,
                )
        except Exception:
            logger.debug("WTI CLUSDT fetch failed", exc_info=True)

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
    if age_h < 48:
        return f"{age_h:.1f}ч назад"
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
    theme = item.theme or detect_oil_news_theme(item.title)
    theme_ru = theme_label_ru(theme)
    score = news_critical_score(item.title)
    lines = [
        "🛢 <b>Нефть · важное</b>",
        f"<i>{theme_ru} · вес {score}</i>",
        "",
    ]
    if item.url:
        lines.append(f"<a href=\"{item.url}\"><b>{title}</b></a>")
    else:
        lines.append(f"<b>{title}</b>")
    lines.append("")
    lines.append(f"{impact_ru}")
    lines.append(f"<i>{lang_mark} {item.source} · {_age_label(item.published_ts)}</i>")
    if item.url:
        lines.append(f"🔗 <a href=\"{item.url}\">Открыть источник</a>")
    return "\n".join(lines)

def format_oil_news_message(items: list[OilNewsItem], *, max_show: int = 5) -> str:
    if not items:
        return (
            "🛢 <b>Нефть</b>\n"
            "<i>Нет важных: Иран/Трамп/США, EIA запасы, ОПЕК, покупки/объёмы.</i>"
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
) -> list[str]:
    """Сценарии LONG / SHORT / база — intraday 5m–1h."""
    lines: list[str] = []
    px = snap.price
    s = snap.support
    r = snap.resistance
    bd = snap.breakdown
    bo = snap.breakout
    tf = f"{interval_minutes}m"

    lines.append("<b>Прогноз / план</b>")
    if news_bias is not None:
        lines.append(f"• {news_bias.summary_ru}")
        lines.append(f"• <i>{news_bias.how_to_use_ru}</i>")
    if bounce_plan is not None:
        side = "LONG" if bounce_plan.side == "long" else "SHORT"
        tps = " / ".join(fmt_price(t) for t in bounce_plan.targets[:3])
        lines.append(
            f"• <b>Отскок {side}:</b> от {fmt_price(bounce_plan.bounce_level)} · "
            f"вход {fmt_price(bounce_plan.entry_lo)}–{fmt_price(bounce_plan.entry_hi)} · "
            f"стоп {fmt_price(bounce_plan.stop)} · TP {tps}"
        )
        if bounce_plan.catalyst:
            cat = bounce_plan.catalyst.replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"  катализатор: <i>{cat}</i>")
    if market_mood:
        lines.append(f"• <b>Настроение рынка:</b> {market_mood}")
    lines.append(f"• Итог TA ({tf}): <b>{snap.verdict}</b> · {snap.confidence}/10")
    if snap.reason:
        lines.append(f"• {snap.reason}")

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
        "<i>Драйверы: Hormuz, US–Iran, EIA Wed, OPEC+, спред Brent/WTI, SPR.</i>"
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
) -> str:
    primary = snaps[0] if snaps else None
    lines = [
        "📊 <b>Нефть · разбор</b>",
        f"<i>Bybit TradFi · TF {interval_minutes}m · BZUSDT (Brent) · CLUSDT (WTI)</i>",
        "",
    ]
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
    ) -> None:
        self.settings_manager = settings_manager
        self._on_news = on_news
        self._on_digest = on_digest
        self._on_level_alert = on_level_alert
        self._on_extra_chart = on_extra_chart
        self._seen_titles: set[str] = set()
        self._last_digest_ts = 0.0
        self._level_watcher = OilLevelWatcher()
        self._recent_news: list[OilNewsItem] = []
        self._last_bounce_alert_ts: dict[str, float] = {}
        self._active_bounce: OilBouncePlan | None = None

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
            "BRENT",
            price=bundle.brent.price,
            breakout=bundle.brent.breakout,
            breakdown=bundle.brent.breakdown,
        )
        if bundle.wti:
            self._level_watcher.update_levels(
                "WTI",
                price=bundle.wti.price,
                breakout=bundle.wti.breakout,
                breakdown=bundle.wti.breakdown,
            )

    async def _tick_level_alerts(self, settings: Any) -> int:
        if not getattr(settings, "oil_level_alerts_enabled", True):
            return 0
        if self._on_level_alert is None:
            return 0
        prices = await fetch_oil_last_prices()
        if not prices:
            return 0
        alerts = self._level_watcher.check_prices(prices, settings)
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

    async def poll_once(self) -> int:
        settings = self.settings_manager.settings
        if not getattr(settings, "oil_news_enabled", False):
            return 0
        if getattr(settings, "bot_paused", False):
            return 0

        sent = 0
        sent += await self._tick_level_alerts(settings)

        max_age_h = float(getattr(settings, "oil_news_max_age_hours", 12.0))
        cutoff = time.time() - max_age_h * 3600.0
        max_per_poll = int(getattr(settings, "oil_news_max_per_poll", 1))
        separate = bool(getattr(settings, "oil_news_separate_messages", True))
        critical_only = bool(getattr(settings, "oil_news_critical_only", True))
        critical_min = int(getattr(settings, "oil_news_critical_min_score", 4))
        include_ru = bool(getattr(settings, "oil_russian_news", True))

        try:
            items = await fetch_oil_news(
                max_items=20,
                include_russian=include_ru,
                critical_only=critical_only,
                critical_min_score=critical_min,
            )
        except Exception:
            logger.exception("Oil news fetch failed")
            items = []

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
        chart_enabled = bool(getattr(settings, "oil_chart_enabled", True))
        interval_min = int(getattr(settings, "oil_interval_minutes", 15))
        include_brent = bool(getattr(settings, "oil_include_brent", True))
        include_wti = bool(getattr(settings, "oil_include_wti", True))
        now = time.time()
        if (
            digest_enabled
            and self._on_digest is not None
            and now - self._last_digest_ts >= digest_h * 3600.0
        ):
            try:
                bundle = await build_oil_analysis_bundle(
                    interval_minutes=interval_min,
                    include_brent=include_brent,
                    include_wti=include_wti,
                )
                if bundle:
                    self._sync_levels_from_bundle(bundle)
                    snaps = [bundle.brent]
                    if bundle.wti:
                        snaps.append(bundle.wti)
                    min_score = float(
                        getattr(settings, "oil_bounce_min_news_score", 3.0)
                    )
                    news_bias = summarize_oil_news_bias(
                        self._recent_news,
                        ta_verdict=bundle.brent.verdict,
                    )
                    bounce = build_oil_bounce_plan(
                        bundle.brent,
                        news_bias,
                        news_items=self._recent_news,
                        min_score=min_score,
                    )
                    self._active_bounce = bounce
                    if bounce is not None:
                        apply_oil_bounce_to_ta(bundle.brent_ta, bounce)
                        # snap mirrors chart levels for digest consistency
                        bundle.brent.verdict = (
                            "LONG" if bounce.side == "long" else "SHORT"
                        )
                        bundle.brent.entry_zone = (
                            bounce.entry_lo,
                            bounce.entry_hi,
                        )
                        bundle.brent.stop = bounce.stop
                        bundle.brent.targets = bounce.targets
                        bundle.brent.reason = bounce.reason_ru[:200]
                    digest = format_oil_market_digest(
                        snaps,
                        ta=bundle.brent_ta,
                        interval_minutes=bundle.interval_minutes,
                        market_mood=bundle.market_mood,
                        news_bias=news_bias,
                        bounce_plan=bounce,
                    )
                    png: bytes | None = None
                    if chart_enabled:
                        from .chart_renderer import render_oil_chart

                        display_h = int(getattr(settings, "oil_chart_display_hours", 6) or 6)
                        height_scale = float(
                            getattr(settings, "oil_chart_height_scale", 1.45)
                            or getattr(settings, "signal_chart_height_scale", 1.0)
                            or 1.45
                        )
                        png = render_oil_chart(
                            bundle.brent_bars,
                            bundle.brent_ta,
                            symbol_label="Brent · BZUSDT",
                            interval_minutes=bundle.interval_minutes,
                            display_hours=display_h,
                            height_scale=max(1.35, height_scale),
                        )
                    ok = await self._on_digest(digest, png)
                    if ok:
                        self._last_digest_ts = now
                        sent += 1
                        if bounce is not None:
                            sent += await self._maybe_send_bounce_alert(
                                bounce,
                                label="Brent · BZUSDT",
                                settings=settings,
                                png=None,  # график уже ушёл с дайджестом
                            )
                        if (
                            chart_enabled
                            and bundle.wti_bars
                            and bundle.wti_ta
                            and self._on_extra_chart is not None
                        ):
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
                                        bundle.wti_ta, wti_bounce,
                                    )
                            wti_png = render_oil_chart(
                                bundle.wti_bars,
                                bundle.wti_ta,
                                symbol_label="WTI · CLUSDT",
                                interval_minutes=bundle.interval_minutes,
                                display_hours=display_h,
                                height_scale=max(1.35, height_scale),
                            )
                            if wti_png:
                                wti_caption = (
                                    f"📊 <b>WTI · CLUSDT</b> · {bundle.interval_minutes}m · "
                                    f"${bundle.wti.price:.2f}"
                                )
                                if wti_bounce is not None:
                                    wti_caption += (
                                        f"\n{wti_bounce.reason_ru}"
                                    )
                                try:
                                    await self._on_extra_chart(wti_caption, wti_png)
                                except Exception:
                                    logger.exception("Oil WTI chart dispatch failed")
            except Exception:
                logger.exception("Oil digest failed")

        # Между дайджестами: редкий bounce-alert если цена подошла к уровню
        if self._active_bounce is not None:
            try:
                prices = await fetch_oil_last_prices()
                brent_px = prices.get("BRENT")
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
                        label="Brent · BZUSDT",
                        settings=settings,
                        png=None,
                    )
            except Exception:
                logger.debug("Oil bounce tick failed", exc_info=True)

        return sent

    async def run_loop(self, interval: float | None = None) -> None:
        while True:
            settings = self.settings_manager.settings
            sleep_s = float(
                interval
                if interval is not None
                else getattr(settings, "oil_news_interval_seconds", 300.0)
            )
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

