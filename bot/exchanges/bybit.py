from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

import aiohttp

from .base import ExchangeScanner, MarketSnapshot

logger = logging.getLogger(__name__)

BYBIT_REST_BASE = "https://api.bybit.com"
BYBIT_INSTRUMENTS = f"{BYBIT_REST_BASE}/v5/market/instruments-info"
BYBIT_TICKERS = f"{BYBIT_REST_BASE}/v5/market/tickers"

SYMBOL_REFRESH_SECONDS = 3600
REST_TIMEOUT_SECONDS = 90
REST_MIN_INTERVAL_SECONDS = 2.0
REST_MAX_RETRIES = 3
REST_DISPATCH_CONCURRENCY = 12


class BybitScanner(ExchangeScanner):
    """
    Bybit linear USDT: один REST-запрос на все тикеры.
    WebSocket tickers.* на все символы перегружает event loop → ping timeout и REST TimeoutError.
    """

    def __init__(
        self,
        on_update: Callable[..., Awaitable[None]],
        scan_interval: Callable[[], float] | float = 1.0,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self.symbols: list[str] = []
        self.snapshots: dict[str, MarketSnapshot] = {}
        self._running = False
        self.on_update = on_update
        if callable(scan_interval):
            self._get_scan_interval = scan_interval
        else:
            self._get_scan_interval = lambda: float(scan_interval)
        self._enabled = enabled or (lambda: True)
        self._last_symbol_refresh = 0.0
        self._last_dispatch_sig: dict[str, tuple[float, float]] = {}
        self._session: aiohttp.ClientSession | None = None

    async def load_symbols(self) -> list[str]:
        params = {"category": "linear"}
        timeout = aiohttp.ClientTimeout(total=60)
        session = self._session or aiohttp.ClientSession(timeout=timeout)
        async with session.get(BYBIT_INSTRUMENTS, params=params) as response:
            data = await response.json()

        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit instruments-info failed: {data.get('retMsg')}")

        symbols: list[str] = []
        for item in data.get("result", {}).get("list", []):
            if item.get("quoteCoin") == "USDT" and item.get("status") == "Trading":
                symbols.append(item["symbol"])

        self.symbols = sorted(set(symbols))
        self._last_symbol_refresh = time.time()
        logger.info("Loaded %d Bybit linear USDT symbols", len(self.symbols))
        return self.symbols

    async def run(self) -> None:
        self._running = True
        timeout = aiohttp.ClientTimeout(total=REST_TIMEOUT_SECONDS, connect=20)
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        try:
            if not self.symbols:
                await self.load_symbols()
            await self._rest_poll_loop()
        finally:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def _maybe_refresh_symbols(self) -> None:
        if time.time() - self._last_symbol_refresh < SYMBOL_REFRESH_SECONDS:
            return
        try:
            await self.load_symbols()
        except Exception as exc:
            logger.warning(
                "Bybit symbol refresh failed: %s: %s",
                type(exc).__name__,
                exc or "no details",
            )

    async def _rest_poll_loop(self) -> None:
        while self._running:
            if not self._enabled():
                await asyncio.sleep(self._get_scan_interval())
                continue

            try:
                await self._maybe_refresh_symbols()
                await self._fetch_all_tickers()
            except asyncio.TimeoutError:
                logger.warning(
                    "Bybit REST poll timed out (>%ss) — повтор на следующем цикле",
                    REST_TIMEOUT_SECONDS,
                )
            except aiohttp.ClientError as exc:
                logger.warning("Bybit REST network error: %s: %s", type(exc).__name__, exc)
            except Exception as exc:
                logger.warning(
                    "Bybit REST poll failed: %s: %s",
                    type(exc).__name__,
                    exc or "no details",
                )

            interval = max(self._get_scan_interval(), REST_MIN_INTERVAL_SECONDS)
            await asyncio.sleep(interval)

    async def _fetch_ticker_json(self) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("Bybit HTTP session is not initialized")

        last_exc: Exception | None = None
        for attempt in range(1, REST_MAX_RETRIES + 1):
            try:
                async with self._session.get(
                    BYBIT_TICKERS,
                    params={"category": "linear"},
                ) as response:
                    data = await response.json()
                if data.get("retCode") != 0:
                    raise RuntimeError(f"Bybit tickers failed: {data.get('retMsg')}")
                return data
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < REST_MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
        assert last_exc is not None
        raise last_exc

    async def _fetch_all_tickers(self) -> None:
        data = await self._fetch_ticker_json()
        known = set(self.symbols)
        items = [
            item
            for item in data.get("result", {}).get("list", [])
            if item.get("symbol") in known
        ]

        to_dispatch: list[MarketSnapshot] = []
        for item in items:
            snapshot = self._merge_ticker_payload(item, source="rest")
            if snapshot is not None:
                to_dispatch.append(snapshot)

        if not to_dispatch:
            return

        semaphore = asyncio.Semaphore(REST_DISPATCH_CONCURRENCY)

        async def dispatch(snapshot: MarketSnapshot) -> None:
            async with semaphore:
                await self._dispatch_update(snapshot)

        await asyncio.gather(*(dispatch(snapshot) for snapshot in to_dispatch))

    def _merge_ticker_payload(
        self,
        payload: dict[str, Any],
        source: str,
    ) -> MarketSnapshot | None:
        symbol = payload.get("symbol")
        if not symbol or symbol not in self.symbols:
            return None

        snapshot = self.snapshots.get(symbol) or MarketSnapshot(exchange="Bybit", symbol=symbol)

        if payload.get("lastPrice") is not None:
            snapshot.price = float(payload["lastPrice"])
        if payload.get("openInterest") is not None:
            snapshot.open_interest = float(payload["openInterest"])
        if payload.get("volume24h") is not None:
            snapshot.volume_24h = float(payload["volume24h"])
        if payload.get("bid1Price") is not None:
            snapshot.bid_price = float(payload["bid1Price"])
        if payload.get("ask1Price") is not None:
            snapshot.ask_price = float(payload["ask1Price"])
        if payload.get("price24hPcnt") is not None:
            snapshot.price_change_percent = float(payload["price24hPcnt"]) * 100.0

        snapshot.timestamp = time.time()
        snapshot.additional.update({
            "funding_rate": payload.get("fundingRate"),
            "turnover_24h": payload.get("turnover24h"),
            "open_interest_value": payload.get("openInterestValue"),
            "mark_price": payload.get("markPrice"),
            "source": source,
        })
        self.snapshots[symbol] = snapshot

        if not snapshot.price or not snapshot.open_interest:
            return None

        sig = (round(snapshot.price, 8), round(snapshot.open_interest, 6))
        if self._last_dispatch_sig.get(symbol) == sig:
            return None
        self._last_dispatch_sig[symbol] = sig
        return snapshot

    async def _dispatch_update(self, snapshot: MarketSnapshot) -> None:
        await self.on_update(
            snapshot.exchange,
            snapshot.symbol,
            snapshot.price,
            snapshot.open_interest,
            snapshot.volume_24h,
            snapshot.bid_price,
            snapshot.ask_price,
            snapshot.timestamp,
            snapshot.additional,
        )

    def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        return self.snapshots.get(symbol.upper())

    def get_symbols(self) -> list[str]:
        return list(self.symbols)

    def stop(self) -> None:
        self._running = False
