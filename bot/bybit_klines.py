from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

# api.bybit.com иногда не резолвится в Docker/VPS → зеркало bytick
BYBIT_REST_HOSTS = (
    "https://api.bybit.com",
    "https://api.bytick.com",
)
BYBIT_KLINE_URL = f"{BYBIT_REST_HOSTS[0]}/v5/market/kline"


@dataclass(frozen=True)
class KlineBar:
    open_time: float
    open: float
    high: float
    low: float
    close: float
    volume: float


def fetch_bybit_klines_sync(
    symbol: str,
    *,
    interval: str,
    limit: int = 200,
    category: str = "linear",
    timeout: float = 25.0,
) -> list[KlineBar]:
    """Свечи Bybit REST с перебором хостов (bybit → bytick)."""
    lim = min(max(int(limit), 1), 1000)
    params = urllib.parse.urlencode(
        {
            "category": category,
            "symbol": symbol.upper(),
            "interval": str(interval),
            "limit": lim,
        }
    )
    last_err: Exception | None = None
    for host in BYBIT_REST_HOSTS:
        url = f"{host}/v5/market/kline?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BybitBot/1.0)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                j = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            logger.warning("Bybit kline host fail %s %s: %s", host, symbol, exc)
            continue
        if j.get("retCode") != 0:
            last_err = RuntimeError(f"Bybit kline {symbol}: {j.get('retMsg')}")
            logger.warning(
                "Bybit kline retCode %s %s: %s",
                host,
                symbol,
                j.get("retMsg"),
            )
            continue
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
        if bars:
            if host != BYBIT_REST_HOSTS[0]:
                logger.info("Bybit kline via mirror %s · %s", host, symbol)
            return bars
        last_err = RuntimeError(f"Bybit kline empty {symbol}")
    if last_err is not None:
        raise last_err
    return []


class BybitKlineCache:
    """Кэш 5m свечей Bybit linear — для мульти-часового контекста без лишних запросов."""

    def __init__(self, ttl_seconds: float = 90.0) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, list[KlineBar]]] = {}
        self._lock = asyncio.Lock()

    async def get_klines(
        self,
        symbol: str,
        *,
        limit: int = 72,
        interval_minutes: int = 5,
    ) -> list[KlineBar]:
        symbol = symbol.upper()
        interval = str(interval_minutes)
        cache_key = f"{symbol}:{interval}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self._ttl:
                return cached[1]

            bars = await self._fetch(
                symbol, limit=limit, interval_minutes=interval_minutes
            )
            self._cache[cache_key] = (time.time(), bars)
            return bars

    async def _fetch(
        self, symbol: str, limit: int, *, interval_minutes: int = 5
    ) -> list[KlineBar]:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": str(interval_minutes),
            "limit": min(max(limit, 12), 200),
        }
        last_err: Exception | None = None
        try:
            async with aiohttp.ClientSession() as session:
                for host in BYBIT_REST_HOSTS:
                    url = f"{host}/v5/market/kline"
                    try:
                        async with session.get(
                            url, params=params, timeout=15
                        ) as response:
                            data = await response.json()
                    except Exception as exc:
                        last_err = exc
                        logger.warning(
                            "Bybit kline host fail %s %s: %s", host, symbol, exc
                        )
                        continue
                    if data.get("retCode") != 0:
                        logger.warning(
                            "Bybit kline error %s %s: %s",
                            host,
                            symbol,
                            data.get("retMsg"),
                        )
                        continue
                    bars: list[KlineBar] = []
                    for row in data.get("result", {}).get("list", []):
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
                    if bars:
                        if host != BYBIT_REST_HOSTS[0]:
                            logger.info(
                                "Bybit kline via mirror %s · %s", host, symbol
                            )
                        return bars
        except Exception:
            logger.warning(
                "Bybit kline fetch failed for %s", symbol, exc_info=True
            )
            return []

        if last_err:
            logger.warning(
                "Bybit kline all hosts failed for %s: %s", symbol, last_err
            )
        return []
