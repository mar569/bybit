"""Нефть: отдельный чат — свежие новости (ссылки) + техразбор + прогноз.



Данные: Google News RSS (EN + RU), Yahoo Finance BZ=F (Brent), CL=F (WTI).

UKOUSD Bybit TradFi ≈ Brent; USOIL ≈ WTI.

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



from .bybit_klines import KlineBar

from .oil_level_watcher import OilLevelWatcher

from .ta_analysis import TAAnalysisResult, fmt_price, run_ta_analysis



logger = logging.getLogger(__name__)



_OIL_KEYWORDS = frozenset({

    "oil", "crude", "brent", "wti", "petroleum", "gasoline", "diesel", "hormuz",

    "opec", "barrel", "нефт", "баррель", "spr", "inventory", "eia", "запас",

})

_GEO_KEYWORDS = frozenset({

    "iran", "usa", "u.s.", "united states", "hormuz", "sanction", "trump",

    "tehran", "persian gulf", "middle east", "иран", "санкц", "ормуз",

})

_BULL_NEWS = frozenset({

    "surge", "rise", "rally", "jump", "spike", "attack", "strike", "block",

    "close strait", "escalat", "sanction", "cut produc", "draw", "tighten",

    "рост", "подскоч", "атак",

})

_BEAR_NEWS = frozenset({

    "fall", "drop", "decline", "slide", "plunge", "deal", "reopen", "de-escal",

    "ceasefire", "forecast cut", "build", "oversupply", "release spr", "accord",

    "паден", "снижен", "сделк",

})

_CRITICAL_TERMS: dict[str, int] = {

    "hormuz": 4,

    "ормуз": 4,

    "strait": 3,

    "iran": 2,

    "иран": 2,

    "opec": 3,

    "опек": 3,

    "eia": 3,

    "inventory": 2,

    "запас": 2,

    "sanction": 2,

    "санкц": 2,

    "strike": 3,

    "attack": 3,

    "атак": 3,

    "blockade": 3,

    "ceasefire": 2,

    "перемир": 2,

    "spr": 2,

    "production cut": 3,

    "сокращен": 2,

    "houthi": 2,

    "red sea": 2,

    "красн": 2,

    "brent": 1,

    "wti": 1,

    "нефт": 1,

    "crude": 1,

}



NEWS_QUERIES_EN: tuple[str, ...] = (

    "Iran oil Strait of Hormuz when:1d",

    "US Iran oil sanctions when:1d",

    "Brent crude oil when:1d",

    "OPEC oil production when:2d",

    "EIA oil inventory when:2d",

)

NEWS_QUERIES_RU: tuple[str, ...] = (

    "нефть Иран Ормуз when:1d",

    "нефть Brent цена when:1d",

    "ОПЕК нефть when:2d",

    "санкции Иран нефть when:2d",

    "EIA запасы нефть when:2d",

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





def yahoo_params_for_interval(minutes: int) -> tuple[str, str]:

    m = max(5, min(60, int(minutes)))

    if m <= 5:

        return "5m", "5d"

    if m <= 15:

        return "15m", "10d"

    if m <= 30:

        return "30m", "15d"

    return "60m", "45d"





def news_critical_score(title: str) -> int:

    low = title.lower()

    score = 0

    for term, weight in _CRITICAL_TERMS.items():

        if term in low:

            score += weight

    return score





def is_critical_oil_news(item: OilNewsItem, min_score: int = 3) -> bool:

    return news_critical_score(item.title) >= min_score





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





def _is_relevant(title: str) -> bool:

    low = title.lower()

    if any(k in low for k in _OIL_KEYWORDS):

        return True

    if any(k in low for k in _GEO_KEYWORDS) and any(

        k in low for k in ("iran", "hormuz", "sanction", "gulf", "tehran", "opec", "иран", "ормуз")

    ):

        return True

    return False





def classify_news_impact(title: str) -> str:

    low = title.lower()

    bull = sum(1 for k in _BULL_NEWS if k in low)

    bear = sum(1 for k in _BEAR_NEWS if k in low)

    if bull > bear and bull > 0:

        return "bullish"

    if bear > bull and bear > 0:

        return "bearish"

    return "neutral"





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

        if not _is_relevant(title):

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

            )

        )

    return out





async def fetch_oil_news(

    max_items: int = 15,

    *,

    include_russian: bool = True,

    critical_only: bool = True,

    critical_min_score: int = 3,

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

    merged.sort(key=lambda x: x.published_ts, reverse=True)

    return merged[:max_items]





def _fetch_yahoo_bars(

    yahoo_symbol: str,

    *,

    interval: str = "60m",

    range_: str = "30d",

) -> list[KlineBar]:

    url = (

        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol)}"

        f"?interval={interval}&range={range_}"

    )

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urllib.request.urlopen(req, timeout=25) as resp:

        import json



        j = json.loads(resp.read())

    res = j["chart"]["result"][0]

    ts = res["timestamp"]

    q = res["indicators"]["quote"][0]

    bars: list[KlineBar] = []

    for i, t in enumerate(ts):

        c = q["close"][i]

        if c is None:

            continue

        bars.append(

            KlineBar(

                open_time=float(t),

                open=float(q["open"][i] or c),

                high=float(q["high"][i] or c),

                low=float(q["low"][i] or c),

                close=float(c),

                volume=float(q.get("volume", [0])[i] or 0),

            )

        )

    return bars





async def fetch_oil_last_prices() -> dict[str, float]:

    """Быстрый тик для level-alerts — 5m последнее закрытие."""

    out: dict[str, float] = {}

    for label, sym in (("BRENT", "BZ=F"), ("WTI", "CL=F")):

        try:

            bars = await asyncio.to_thread(

                _fetch_yahoo_bars, sym, interval="5m", range_="1d",

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

    """Brent (primary) + WTI — TA на выбранном TF (5–60m)."""

    yahoo_interval, yahoo_range = yahoo_params_for_interval(interval_minutes)

    im = max(5, min(60, int(interval_minutes)))



    brent_bars: list[KlineBar] = []

    brent_ta: TAAnalysisResult | None = None

    brent_snap: OilMarketSnapshot | None = None



    if include_brent:

        try:

            brent_bars = await asyncio.to_thread(

                _fetch_yahoo_bars, "BZ=F", interval=yahoo_interval, range_=yahoo_range,

            )

        except Exception:

            logger.warning("Brent fetch failed", exc_info=True)

            return None

        if len(brent_bars) < 24:

            return None

        hours = min(int(len(brent_bars) * im / 60), 1080)

        brent_ta = run_ta_analysis(

            brent_bars,

            is_long=True,

            symbol="Brent",

            hours=hours,

            interval_minutes=im,

            pattern_detection_enabled=True,

            pattern_min_confidence=0.50,

        )

        brent_snap = _snapshot_from_ta("Brent", "BZ=F", brent_bars, brent_ta)

    else:

        return None



    wti_bars: list[KlineBar] | None = None

    wti_ta: TAAnalysisResult | None = None

    wti_snap: OilMarketSnapshot | None = None

    if include_wti:

        try:

            wti_bars = await asyncio.to_thread(

                _fetch_yahoo_bars, "CL=F", interval=yahoo_interval, range_=yahoo_range,

            )

            if len(wti_bars) >= 24:

                hours = min(int(len(wti_bars) * im / 60), 1080)

                wti_ta = run_ta_analysis(

                    wti_bars,

                    is_long=True,

                    symbol="WTI",

                    hours=hours,

                    interval_minutes=im,

                    pattern_detection_enabled=False,

                )

                wti_snap = _snapshot_from_ta("WTI", "CL=F", wti_bars, wti_ta)

        except Exception:

            logger.debug("WTI fetch failed", exc_info=True)



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

    lines = [

        "🛢 <b>Нефть · новость</b>",

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

        return "🛢 <b>Нефть</b>\n<i>Нет важных заголовков (Hormuz / EIA / OPEC).</i>"

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

    if market_mood:

        lines.append(f"• <b>Настроение рынка:</b> {market_mood}")

    lines.append(f"• Итог TA ({tf}): <b>{snap.verdict}</b> · {snap.confidence}/10")

    if snap.reason:

        lines.append(f"• {snap.reason}")



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

) -> str:

    primary = snaps[0] if snaps else None

    lines = [

        "📊 <b>Нефть · разбор</b>",

        f"<i>TF {interval_minutes}m · UKOUSD ≈ Brent · USOIL ≈ WTI</i>",

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

            )

        )

    elif primary:

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

        max_per_poll = int(getattr(settings, "oil_news_max_per_poll", 2))

        separate = bool(getattr(settings, "oil_news_separate_messages", True))

        critical_only = bool(getattr(settings, "oil_news_critical_only", True))

        critical_min = int(getattr(settings, "oil_news_critical_min_score", 3))

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

                    digest = format_oil_market_digest(

                        snaps,

                        ta=bundle.brent_ta,

                        interval_minutes=bundle.interval_minutes,

                        market_mood=bundle.market_mood,

                    )

                    png: bytes | None = None

                    if chart_enabled:

                        from .chart_renderer import render_oil_chart



                        display_h = int(getattr(settings, "oil_chart_display_hours", 168))

                        height_scale = float(

                            getattr(settings, "signal_chart_height_scale", 1.0) or 1.0

                        )

                        png = render_oil_chart(

                            bundle.brent_bars,

                            bundle.brent_ta,

                            symbol_label="Brent · UKOUSD",

                            interval_minutes=bundle.interval_minutes,

                            display_hours=display_h,

                            height_scale=height_scale,

                        )

                    ok = await self._on_digest(digest, png)

                    if ok:

                        self._last_digest_ts = now

                        sent += 1

                        if (

                            chart_enabled

                            and bundle.wti_bars

                            and bundle.wti_ta

                            and self._on_extra_chart is not None

                        ):

                            wti_png = render_oil_chart(

                                bundle.wti_bars,

                                bundle.wti_ta,

                                symbol_label="WTI · USOIL",

                                interval_minutes=bundle.interval_minutes,

                                display_hours=display_h,

                                height_scale=height_scale,

                            )

                            if wti_png:

                                wti_caption = (

                                    f"📊 <b>WTI · USOIL</b> · {bundle.interval_minutes}m · "

                                    f"${bundle.wti.price:.2f}"

                                )

                                try:

                                    await self._on_extra_chart(wti_caption, wti_png)

                                except Exception:

                                    logger.exception("Oil WTI chart dispatch failed")

            except Exception:

                logger.exception("Oil digest failed")



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


