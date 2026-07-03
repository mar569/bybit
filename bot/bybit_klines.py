from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"


@dataclass(frozen=True)
class KlineBar:
    open_time: float
    open: float
    high: float
    low: float
    close: float
    volume: float


class BybitKlineCache:
    """Кэш 5m свечей Bybit linear — для мульти-часового контекста без лишних запросов."""

    def __init__(self, ttl_seconds: float = 90.0) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, list[KlineBar]]] = {}
        self._lock = asyncio.Lock()

    async def get_klines(self, symbol: str, *, limit: int = 72) -> list[KlineBar]:
        symbol = symbol.upper()
        now = time.time()
        cached = self._cache.get(symbol)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        async with self._lock:
            cached = self._cache.get(symbol)
            if cached and now - cached[0] < self._ttl:
                return cached[1]

            bars = await self._fetch(symbol, limit=limit)
            self._cache[symbol] = (time.time(), bars)
            return bars

    async def _fetch(self, symbol: str, limit: int) -> list[KlineBar]:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": "5",
            "limit": min(max(limit, 12), 200),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(BYBIT_KLINE_URL, params=params, timeout=15) as response:
                    data = await response.json()
        except Exception:
            logger.warning("Bybit kline fetch failed for %s", symbol, exc_info=True)
            return []

        if data.get("retCode") != 0:
            logger.warning("Bybit kline error %s: %s", symbol, data.get("retMsg"))
            return []

        bars: list[KlineBar] = []
        for row in data.get("result", {}).get("list", []):
            try:
                bars.append(KlineBar(
                    open_time=float(row[0]) / 1000.0,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                ))
            except (IndexError, TypeError, ValueError):
                continue

        bars.sort(key=lambda bar: bar.open_time)
        return bars
