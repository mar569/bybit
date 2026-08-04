"""X (Twitter) лента для нефти: Bearer API + бесплатный RSSHub fallback."""
from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Sequence

logger = logging.getLogger(__name__)

# Аккаунты «терминала» из совета (List)
DEFAULT_X_HANDLES: tuple[str, ...] = (
    "DeItaone",
    "financialjuice",
    "Reuters",
    "ReutersBiz",
    "unusual_whales",
    "realDonaldTrump",
    "WhiteHouse",
    "POTUS",
    "SecBessent",
)

_OIL_KW = (
    "oil", "brent", "wti", "crude", "opec", "hormuz", "iran", "israel",
    "trump", "bessent", "eia", "api ", "spr", "petroleum", "gasoline",
    "нефт", "ормуз", "иран", "опек", "трамп", "sanction", "tariff",
    "fomc", "powell", "inventory", "strait",
)


@dataclass(frozen=True)
class OilXItem:
    title: str
    url: str
    source: str
    published_ts: float
    handle: str


def _oil_relevant(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in _OIL_KW)


def _parse_rss_pub(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            from datetime import timezone

            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _fetch_url(url: str, *, timeout: float = 12.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BybitOilBot/1.0 (+local; oil watch)",
            "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_rsshub_user(handle: str, *, max_items: int = 8) -> list[OilXItem]:
    """Бесплатный fallback без ключа X (RSSHub)."""
    handle = handle.lstrip("@").strip()
    if not handle:
        return []
    urls = (
        f"https://rsshub.app/twitter/user/{handle}",
        f"https://rsshub.rssforever.com/twitter/user/{handle}",
    )
    out: list[OilXItem] = []
    for feed_url in urls:
        try:
            raw = _fetch_url(feed_url)
            root = ET.fromstring(raw)
        except Exception:
            logger.debug("RSSHub %s failed", handle, exc_info=True)
            continue
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for it in items[:max_items]:
            title_el = it.find("title")
            link_el = it.find("link")
            date_el = it.find("pubDate")
            title = (title_el.text or "").strip() if title_el is not None else ""
            if link_el is not None and link_el.text:
                link = link_el.text.strip()
            elif link_el is not None and link_el.get("href"):
                link = link_el.get("href", "").strip()
            else:
                link = f"https://x.com/{handle}"
            if not title:
                continue
            if not _oil_relevant(title):
                continue
            ts = _parse_rss_pub(date_el.text if date_el is not None else "") or time.time()
            out.append(
                OilXItem(
                    title=title[:240],
                    url=link,
                    source=f"X @{handle}",
                    published_ts=ts,
                    handle=handle,
                )
            )
        if out:
            break
    return out


def _x_api_user_id(bearer: str, handle: str) -> str | None:
    handle = handle.lstrip("@").strip()
    url = f"https://api.x.com/2/users/by/username/{urllib.parse.quote(handle)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {bearer}", "User-Agent": "BybitOilBot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return str((data.get("data") or {}).get("id") or "") or None
    except Exception:
        logger.debug("X user lookup @%s failed", handle, exc_info=True)
        return None


def _x_api_user_tweets(bearer: str, user_id: str, *, max_results: int = 5) -> list[dict]:
    q = urllib.parse.urlencode(
        {
            "max_results": max(5, min(20, max_results)),
            "tweet.fields": "created_at,text",
            "exclude": "replies,retweets",
        }
    )
    url = f"https://api.x.com/2/users/{user_id}/tweets?{q}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {bearer}", "User-Agent": "BybitOilBot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return list(data.get("data") or [])
    except Exception:
        logger.debug("X tweets %s failed", user_id, exc_info=True)
        return []


def _parse_iso_ts(raw: str) -> float:
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return time.time()


def fetch_x_oil_via_api(
    bearer: str,
    handles: Sequence[str],
    *,
    max_per_user: int = 5,
    max_age_hours: float = 2.0,
) -> list[OilXItem]:
    """Официальный X API v2 (нужен Bearer; free/pay — как даст портал)."""
    bearer = (bearer or "").strip()
    if not bearer:
        return []
    now = time.time()
    cutoff = now - max(0.5, float(max_age_hours)) * 3600.0
    out: list[OilXItem] = []
    for handle in handles:
        uid = _x_api_user_id(bearer, handle)
        if not uid:
            continue
        for tw in _x_api_user_tweets(bearer, uid, max_results=max_per_user):
            text = (tw.get("text") or "").strip()
            if not text or not _oil_relevant(text):
                continue
            tid = tw.get("id") or ""
            ts = _parse_iso_ts(tw.get("created_at") or "")
            if ts < cutoff:
                continue
            h = handle.lstrip("@")
            out.append(
                OilXItem(
                    title=text.replace("\n", " ")[:240],
                    url=f"https://x.com/{h}/status/{tid}" if tid else f"https://x.com/{h}",
                    source=f"X @{h}",
                    published_ts=ts,
                    handle=h,
                )
            )
        time.sleep(0.35)  # бережём лимиты
    out.sort(key=lambda x: x.published_ts, reverse=True)
    return out


def fetch_x_oil_via_rsshub(
    handles: Sequence[str],
    *,
    max_age_hours: float = 2.0,
) -> list[OilXItem]:
    now = time.time()
    cutoff = now - max(0.5, float(max_age_hours)) * 3600.0
    out: list[OilXItem] = []
    for handle in handles:
        for it in _fetch_rsshub_user(handle):
            if it.published_ts >= cutoff:
                out.append(it)
    out.sort(key=lambda x: x.published_ts, reverse=True)
    return out


async def fetch_oil_x_headlines(
    *,
    bearer_token: str | None,
    handles: Sequence[str] | None = None,
    max_age_hours: float = 2.0,
    prefer_api: bool = True,
) -> list[OilXItem]:
    """Сначала API (если ключ), иначе RSSHub. Оба пути — только oil-relevant."""
    hs = tuple(h.lstrip("@").strip() for h in (handles or DEFAULT_X_HANDLES) if h.strip())
    if not hs:
        return []

    def _run() -> list[OilXItem]:
        items: list[OilXItem] = []
        if prefer_api and (bearer_token or "").strip():
            items = fetch_x_oil_via_api(
                bearer_token or "",
                hs,
                max_age_hours=max_age_hours,
            )
        if not items:
            items = fetch_x_oil_via_rsshub(hs, max_age_hours=max_age_hours)
        return items

    return await asyncio.to_thread(_run)
