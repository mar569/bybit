"""Быстрый wire (FinancialJuice / ForexLive) + crash-алерты по цене UKOUSD.

Google News опаздывает на 5–20+ мин после terminal/FJ. Этот слой:
1) тянет прямые RSS агрегаторов трейдеров;
2) орёт по самой цене, даже если новости ещё нет.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import timezone
from typing import Sequence

from .bybit_klines import KlineBar

logger = logging.getLogger(__name__)

# Прямые RSS — раньше Google News на market-moving headlines
OIL_WIRE_FEEDS: tuple[tuple[str, str], ...] = (
    ("FinancialJuice", "https://www.financialjuice.com/feed.ashx?xy=rss"),
    ("ForexLive", "https://www.forexlive.com/feed/"),
    ("InvestingLive", "https://www.investinglive.com/feed/"),
)

_WIRE_OIL_KEYS: tuple[str, ...] = (
    "oil", "crude", "brent", "wti", "нефт", "opec", "опек", "eia",
    "hormuz", "ормуз", "iran", "иран", "tehran", "strait",
    "tanker", "танкер", "petroleum", "gasoline", "diesel",
    "energy", "commodity", "saudi", "сауд",
)
_WIRE_TRUMP_GEO: tuple[str, ...] = (
    "trump", "трамп", "white house", "pentagon", "strike", "attack",
    "удар", "атак", "truth social", "negotiation", "переговор",
    "ceasefire", "reopen", "deal", "сделк", "sanction", "санкц",
    "tweet", "posts",
)
_WIRE_BESSENT: tuple[str, ...] = (
    "bessent", "scott bessent", "treasury secretary", "бессент",
    "министр финансов",
)


@dataclass(frozen=True)
class OilWireItem:
    title: str
    url: str
    source: str
    published_ts: float


@dataclass(frozen=True)
class OilCrashAlert:
    """Сильный ход цены без ожидания новости."""
    direction: str  # down | up
    move_15m_pct: float
    move_30m_pct: float
    move_60m_pct: float
    range_60m_pct: float
    price: float
    high_60m: float
    low_60m: float
    severity: str  # warn | crash | mega
    trigger: str  # какой порог сработал


def _parse_pub(pub: str | None) -> float | None:
    if not pub or not str(pub).strip():
        return None
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
        now = time.time()
        if ts > now + 3600 or ts < now - 86400 * 14:
            return None
        return ts
    except Exception:
        return None


def wire_headline_oil_relevant(title: str) -> bool:
    """FJ/FXLive: только рыночные нефтяные катализаторы (анти-спам)."""
    low = (title or "").lower()
    if not low.strip():
        return False
    # Шум статистики / FX-обёрток
    if any(
        p in low
        for p in (
            "oil import price",
            "oil export price",
            "biofuel",
            "fx news wrap",
            "morning kickstart",
            "markets open with",
        )
    ):
        return False
    try:
        from .oil_monitor import is_oil_market_moving_headline

        return is_oil_market_moving_headline(title)
    except Exception:
        if any(k in low for k in _WIRE_OIL_KEYS):
            return True
        if any(k in low for k in ("trump", "трамп", "truth social")) and any(
            k in low for k in _WIRE_TRUMP_GEO[1:]
        ):
            return True
        if any(k in low for k in _WIRE_BESSENT) and any(
            k in low
            for k in (
                "iran", "иран", "hormuz", "ормуз", "oil", "crude", "energy", "энерг",
                "deal", "сделк", "reopen", "negotiat", "переговор", "cnbc",
            )
        ):
            return True
        if any(k in low for k in ("iran", "иран", "hormuz", "ормуз")):
            return True
        return False


def _fetch_one_wire_rss(
    source_name: str,
    feed_url: str,
    *,
    timeout: float = 12.0,
    max_age_hours: float = 6.0,
) -> list[OilWireItem]:
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; BybitBot/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        logger.debug("Oil wire RSS failed: %s", feed_url, exc_info=True)
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    cutoff = time.time() - max(1.0, max_age_hours) * 3600.0
    out: list[OilWireItem] = []
    for item in channel.findall("item"):
        title = re.sub(r"\s+", " ", (item.findtext("title") or "").strip())
        if not title or not wire_headline_oil_relevant(title):
            continue
        # FJ префикс "FinancialJuice: …"
        title = re.sub(r"^FinancialJuice:\s*", "", title, flags=re.I).strip()
        link = (item.findtext("link") or "").strip()
        pub_ts = _parse_pub(item.findtext("pubDate"))
        if pub_ts is None or pub_ts < cutoff:
            continue
        out.append(
            OilWireItem(
                title=title[:240],
                url=link or feed_url,
                source=source_name,
                published_ts=pub_ts,
            )
        )
    return out


def fetch_oil_wire_headlines(
    *,
    max_age_hours: float = 6.0,
    max_items: int = 20,
) -> list[OilWireItem]:
    """Синхронно: все wire-фиды (вызывать через asyncio.to_thread / gather)."""
    bag: list[OilWireItem] = []
    seen: set[str] = set()
    for name, url in OIL_WIRE_FEEDS:
        for it in _fetch_one_wire_rss(name, url, max_age_hours=max_age_hours):
            key = it.title.lower()[:100]
            if key in seen:
                continue
            seen.add(key)
            bag.append(it)
    bag.sort(key=lambda x: x.published_ts, reverse=True)
    return bag[:max_items]


def _pct(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return (b - a) / a * 100.0


def detect_oil_price_crash(
    bars: Sequence[KlineBar] | None,
    *,
    interval_minutes: int = 5,
    pct_15m: float = 1.5,
    pct_30m: float = 3.0,
    pct_60m: float = 4.0,
) -> OilCrashAlert | None:
    """Обвал/памп по цене — независимо от новостей.

    Смотрим close-to-close и high→low диапазон за час (водопад как 87→82).
    """
    if not bars or len(bars) < 8:
        return None
    im = max(5, int(interval_minutes))
    n15 = max(1, int(round(15 / im)))
    n30 = max(n15 + 1, int(round(30 / im)))
    n60 = max(n30 + 1, int(round(60 / im)))
    px = float(bars[-1].close)
    if px <= 0:
        return None

    def _close_n(n: int) -> float:
        idx = -1 - min(n, len(bars) - 1)
        return float(bars[idx].close)

    m15 = _pct(_close_n(n15), px)
    m30 = _pct(_close_n(n30), px)
    m60 = _pct(_close_n(n60), px)

    window = bars[-min(len(bars), n60 + 1) :]
    hi = max(float(b.high) for b in window)
    lo = min(float(b.low) for b in window)
    range_pct = ((hi - lo) / hi * 100.0) if hi > 0 else 0.0

    # Водопад: от хая окна к текущей цене
    drop_from_high = ((px - hi) / hi * 100.0) if hi > 0 else 0.0
    rally_from_low = ((px - lo) / lo * 100.0) if lo > 0 else 0.0

    triggers: list[tuple[str, float, str]] = []
    # down
    if m15 <= -abs(pct_15m):
        triggers.append(("15m", m15, "down"))
    if m30 <= -abs(pct_30m):
        triggers.append(("30m", m30, "down"))
    if m60 <= -abs(pct_60m):
        triggers.append(("60m", m60, "down"))
    if drop_from_high <= -abs(pct_30m) and range_pct >= abs(pct_30m):
        triggers.append(("range_high", drop_from_high, "down"))
    # up
    if m15 >= abs(pct_15m):
        triggers.append(("15m", m15, "up"))
    if m30 >= abs(pct_30m):
        triggers.append(("30m", m30, "up"))
    if m60 >= abs(pct_60m):
        triggers.append(("60m", m60, "up"))
    if rally_from_low >= abs(pct_30m) and range_pct >= abs(pct_30m):
        triggers.append(("range_low", rally_from_low, "up"))

    if not triggers:
        return None

    # Берём самый сильный по |move|
    best = max(triggers, key=lambda t: abs(t[1]))
    trigger_name, move, direction = best
    abs_m = abs(move)
    if abs_m >= 5.0 or range_pct >= 5.5:
        severity = "mega"
    elif abs_m >= abs(pct_30m) or range_pct >= abs(pct_30m) + 0.5:
        severity = "crash"
    else:
        severity = "warn"

    return OilCrashAlert(
        direction=direction,
        move_15m_pct=m15,
        move_30m_pct=m30,
        move_60m_pct=m60,
        range_60m_pct=range_pct,
        price=px,
        high_60m=hi,
        low_60m=lo,
        severity=severity,
        trigger=trigger_name,
    )


def format_oil_crash_alert(
    alert: OilCrashAlert,
    *,
    recent_headlines: Sequence[str] | None = None,
) -> str:
    """Короткий приоритетный пуш по цене — без простыни."""
    if alert.direction == "down":
        head = "🚨‼️ 📉 ОБВАЛ UKOUSD"
        tip = "Не ловить нож. Ждать базу."
    else:
        head = "🚨‼️ 📈 СКАЧОК UKOUSD"
        tip = "Не догонять хай. Ждать откат."
    if alert.severity == "mega":
        head = head.replace("‼️", "‼️‼️")

    lines = [
        head,
        (
            f"<b>${alert.price:.3f}</b> · "
            f"{alert.move_15m_pct:+.1f}%/15м · {alert.move_30m_pct:+.1f}%/30м · "
            f"{alert.move_60m_pct:+.1f}%/1ч"
        ),
        f"<i>${alert.low_60m:.2f}–${alert.high_60m:.2f} · {tip}</i>",
    ]
    if recent_headlines:
        lines.append("Фон:")
        for h in list(recent_headlines)[:2]:
            lines.append(f"• {h[:110]}")
    return "\n".join(lines)


def wire_item_to_news_fields(item: OilWireItem) -> dict:
    """Поля для OilNewsItem (импорт цикла избегаем)."""
    return {
        "title": item.title,
        "url": item.url,
        "source": item.source,
        "published_ts": item.published_ts,
    }
