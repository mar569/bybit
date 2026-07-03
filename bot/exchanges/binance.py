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

BINANCE_REST_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_WS_STREAM = "wss://fstream.binance.com/stream?streams=!ticker@arr"
BINANCE_WS_RAW = "wss://fstream.binance.com/ws"

WS_SUBSCRIBE_BATCH = 200
SYMBOL_REFRESH_SECONDS = 3600


class BinanceScanner(ExchangeScanner):
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
        async with aiohttp.ClientSession() as session:
            async with session.get(BINANCE_REST_INFO, timeout=30) as response:
                data = await response.json()

        symbols = [
            s["symbol"]
            for s in data.get("symbols", [])
            if s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ]
        self.symbols = sorted(set(symbols))
        self._last_symbol_refresh = time.time()
        logger.info("Loaded %d Binance futures symbols", len(self.symbols))
        return self.symbols

    async def run(self) -> None:
        self._running = True
        if not self.symbols:
            await self.load_symbols()

        await asyncio.gather(
            self._ticker_stream_loop(),
            self._open_interest_stream_loop(),
        )

    async def _maybe_refresh_symbols(self) -> None:
        if time.time() - self._last_symbol_refresh < SYMBOL_REFRESH_SECONDS:
            return
        try:
            await self.load_symbols()
        except Exception as exc:
            logger.warning("Binance symbol refresh failed: %s", exc)

    async def _ticker_stream_loop(self) -> None:
        while self._running:
            if not self._enabled():
                await asyncio.sleep(2)
                continue

            try:
                await self._maybe_refresh_symbols()
                async with connect(
                    BINANCE_WS_STREAM,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**24,
                ) as websocket:
                    logger.info("Binance ticker websocket connected")
                    async for message in websocket:
                        if not self._running or not self._enabled():
                            break
                        await self._handle_ticker_message(message)
            except Exception as exc:
                logger.warning("Binance ticker websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _open_interest_stream_loop(self) -> None:
        while self._running:
            if not self._enabled() or not self.symbols:
                await asyncio.sleep(2)
                continue

            try:
                async with connect(
                    BINANCE_WS_RAW,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**24,
                ) as websocket:
                    logger.info("Binance open-interest websocket connected")
                    streams = [f"{symbol.lower()}@openInterest" for symbol in self.symbols]
                    for index in range(0, len(streams), WS_SUBSCRIBE_BATCH):
                        batch = streams[index : index + WS_SUBSCRIBE_BATCH]
                        await websocket.send(json.dumps({
                            "method": "SUBSCRIBE",
                            "params": batch,
                            "id": index + 1,
                        }))
                        await asyncio.sleep(0.1)

                    async for message in websocket:
                        if not self._running or not self._enabled():
                            break
                        await self._handle_open_interest_message(message)
            except Exception as exc:
                logger.warning("Binance OI websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _handle_ticker_message(self, raw_message: str) -> None:
        data = json.loads(raw_message)
        payload = data.get("data", [])
        if not isinstance(payload, list):
            return

        for item in payload:
            await self._process_ticker(item)

    async def _process_ticker(self, payload: dict[str, Any]) -> None:
        symbol = payload.get("s")
        if not symbol or symbol not in self.symbols:
            return

        snapshot = self.snapshots.get(symbol) or MarketSnapshot(exchange="Binance", symbol=symbol)
        snapshot.price = float(payload.get("c", snapshot.price or 0.0))
        snapshot.price_change_percent = float(payload.get("P", snapshot.price_change_percent or 0.0))
        snapshot.volume_24h = float(payload.get("v", snapshot.volume_24h or 0.0))
        snapshot.bid_price = float(payload.get("b", snapshot.bid_price or 0.0))
        snapshot.ask_price = float(payload.get("a", snapshot.ask_price or 0.0))
        snapshot.timestamp = time.time()
        snapshot.additional.update({
            "trade_count": int(payload.get("n", 0)),
            "high_price": payload.get("h"),
            "low_price": payload.get("l"),
            "quote_volume_24h": payload.get("q"),
        })
        self.snapshots[symbol] = snapshot
        await self._dispatch_update(snapshot)

    async def _handle_open_interest_message(self, raw_message: str) -> None:
        data = json.loads(raw_message)
        if data.get("e") != "openInterest":
            return

        symbol = data.get("s")
        if not symbol or symbol not in self.symbols:
            return

        snapshot = self.snapshots.get(symbol) or MarketSnapshot(exchange="Binance", symbol=symbol)
        snapshot.open_interest = float(data.get("o", snapshot.open_interest or 0.0))
        snapshot.timestamp = time.time()
        if snapshot.price and snapshot.open_interest:
            snapshot.additional["open_interest_value"] = snapshot.open_interest * snapshot.price

        self.snapshots[symbol] = snapshot
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
