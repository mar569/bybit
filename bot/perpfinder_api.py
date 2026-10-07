from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://perpfinder.com/api/data"
_VENUE_ALIASES = {
    "bybit": "Bybit",
    "binance": "Binance",
    "okx": "OKX",
}


class PerpFinderApiError(RuntimeError):
    pass


class PerpFinderClient:
    """Public PerpFinder market-data API (no API key). Funding + venue OI snapshot."""

    def __init__(self, *, ttl_seconds: float = 45.0) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def venue_snapshot(
        self,
        asset: str,
        *,
        venue: str = "Bybit",
    ) -> dict[str, Any]:
        coin = asset.upper().replace("/", "").removesuffix("USDT").removesuffix("USDC")
        venue_name = _VENUE_ALIASES.get(venue.lower(), venue.title())
        key = (coin, venue_name)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return dict(cached[1])

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < self._ttl:
                return dict(cached[1])
            result = await self._fetch_funding(coin, venue_name)
            self._cache[key] = (time.monotonic(), result)
            return dict(result)

    async def _fetch_funding(self, coin: str, venue: str) -> dict[str, Any]:
        params = {"asset": coin, "venue": venue, "limit": 1}
        timeout = aiohttp.ClientTimeout(total=12)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{BASE_URL}/funding-rates", params=params) as response:
                    if response.status >= 400:
                        body = (await response.text())[:200]
                        raise PerpFinderApiError(f"HTTP {response.status}: {body}")
                    payload = await response.json(content_type=None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("PerpFinder funding unavailable for %s/%s: %s", coin, venue, exc)
            return {"available": False, "venue": venue, "coin": coin}

        return self._parse_funding_payload(payload, coin=coin, venue=venue)

    @staticmethod
    def _parse_funding_payload(payload: Any, *, coin: str, venue: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"available": False, "venue": venue, "coin": coin}
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            return {"available": False, "venue": venue, "coin": coin}
        row = rows[0]
        if not isinstance(row, dict):
            return {"available": False, "venue": venue, "coin": coin}
        exchanges = row.get("exchanges")
        if not isinstance(exchanges, dict):
            return {"available": False, "venue": venue, "coin": coin}
        venue_row = exchanges.get(venue)
        if not isinstance(venue_row, dict):
            return {"available": False, "venue": venue, "coin": coin}
        try:
            funding_rate = float(venue_row.get("rawRate"))
        except (TypeError, ValueError):
            funding_rate = None
        try:
            oi_usd = float(venue_row.get("oi"))
        except (TypeError, ValueError):
            oi_usd = None
        try:
            price = float(venue_row.get("price"))
        except (TypeError, ValueError):
            price = None
        return {
            "available": funding_rate is not None,
            "venue": venue,
            "coin": coin,
            "funding_rate": funding_rate,
            "oi_usd": oi_usd,
            "price": price,
            "funding_interval_hours": venue_row.get("intervalHours"),
            "updated_at": payload.get("updatedAt"),
        }


_client = PerpFinderClient()


def get_perpfinder_client() -> PerpFinderClient:
    return _client
