from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

import aiohttp
from websockets import connect

from .base import ExchangeScanner, MarketSnapshot

logger = logging.getLogger(__name__)

BYBIT_REST_BASE = "https://api.bybit.com"
BYBIT_INSTRUMENTS = f"{BYBIT_REST_BASE}/v5/market/instruments-info"
BYBIT_TICKERS = f"{BYBIT_REST_BASE}/v5/market/tickers"
BYBIT_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"

# Bybit allows many topics per connection; batch size for subscribe messages.
WS_SUBSCRIBE_BATCH = 50
SYMBOL_REFRESH_SECONDS = 3600


class BybitScanner(ExchangeScanner):
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

    async def load_symbols(self) -> list[str]:
        params = {"category": "linear"}
        async with aiohttp.ClientSession() as session:
            async with session.get(BYBIT_INSTRUMENTS, params=params, timeout=30) as response:
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
        if not self.symbols:
            await self.load_symbols()

        await asyncio.gather(
            self._rest_poll_loop(),
            self._websocket_loop(),
        )

    async def _maybe_refresh_symbols(self) -> None:
        if time.time() - self._last_symbol_refresh < SYMBOL_REFRESH_SECONDS:
            return
        try:
            await self.load_symbols()
        except Exception as exc:
            logger.warning("Bybit symbol refresh failed: %s", exc)

    async def _rest_poll_loop(self) -> None:
        while self._running:
            if not self._enabled():
                await asyncio.sleep(self._get_scan_interval())
                continue

            try:
                await self._maybe_refresh_symbols()
                await self._fetch_all_tickers()
            except Exception as exc:
                logger.warning("Bybit REST poll failed: %s", exc)

            await asyncio.sleep(self._get_scan_interval())

    async def _fetch_all_tickers(self) -> None:
        params = {"category": "linear"}
        async with aiohttp.ClientSession() as session:
            async with session.get(BYBIT_TICKERS, params=params, timeout=30) as response:
                data = await response.json()

        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit tickers failed: {data.get('retMsg')}")

        known = set(self.symbols)
        for item in data.get("result", {}).get("list", []):
            symbol = item.get("symbol")
            if not symbol or symbol not in known:
                continue
            await self._apply_ticker_payload(item, source="rest")

    async def _websocket_loop(self) -> None:
        while self._running:
            if not self._enabled() or not self.symbols:
                await asyncio.sleep(2)
                continue

            try:
                async with connect(
                    BYBIT_WS_LINEAR,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**24,
                ) as websocket:
                    logger.info("Bybit v5 websocket connected")
                    await self._subscribe_all(websocket)
                    async for message in websocket:
                        if not self._running or not self._enabled():
                            break
                        await self._handle_ws_message(message)
            except Exception as exc:
                logger.warning("Bybit websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _subscribe_all(self, websocket: Any) -> None:
        topics = [f"tickers.{symbol}" for symbol in self.symbols]
        for index in range(0, len(topics), WS_SUBSCRIBE_BATCH):
            batch = topics[index : index + WS_SUBSCRIBE_BATCH]
            await websocket.send(json.dumps({"op": "subscribe", "args": batch}))
            await asyncio.sleep(0.05)

    async def _handle_ws_message(self, raw_message: str) -> None:
        data = json.loads(raw_message)
        topic = data.get("topic", "")
        if not topic.startswith("tickers."):
            return

        payload = data.get("data")
        if isinstance(payload, list):
            for item in payload:
                await self._apply_ticker_payload(item, source="ws")
        elif isinstance(payload, dict):
            await self._apply_ticker_payload(payload, source="ws")

    async def _apply_ticker_payload(self, payload: dict[str, Any], source: str) -> None:
        symbol = payload.get("symbol")
        if not symbol or symbol not in self.symbols:
            return

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
        if snapshot.price and snapshot.open_interest:
            await self._dispatch_update(snapshot)

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
