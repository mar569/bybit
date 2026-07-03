from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import aiohttp
from websockets import connect

from .base import ExchangeScanner, MarketSnapshot

logger = logging.getLogger(__name__)

BYBIT_REST_SYMBOLS = "https://api.bybit.com/v2/public/symbols"
BYBIT_WS_URL = "wss://stream.bybit.com/realtime_public"

class BybitScanner(ExchangeScanner):
    def __init__(self, on_update: Callable[..., Awaitable[None]]) -> None:
        self.symbols: list[str] = []
        self.snapshots: dict[str, MarketSnapshot] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self.on_update = on_update

    async def load_symbols(self) -> list[str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(BYBIT_REST_SYMBOLS, timeout=30) as response:
                data = await response.json()

        symbols = []
        for item in data.get("result", []):
            if item.get("quote_currency") == "USDT" and item.get("status") == "Trading":
                symbols.append(item.get("name"))
        self.symbols = sorted(set(symbols))
        logger.info("Loaded %d Bybit futures symbols", len(self.symbols))
        return self.symbols

    async def run(self) -> None:
        self._running = True
        if not self.symbols:
            await self.load_symbols()

        while self._running:
            try:
                async with connect(BYBIT_WS_URL, ping_interval=20, ping_timeout=10, max_size=2**24) as websocket:
                    logger.info("Bybit websocket connected")
                    await websocket.send(json.dumps({"op": "subscribe", "args": ["instrument_info.100ms"]}))
                    async for message in websocket:
                        if not self._running:
                            break
                        await self._handle_message(message)
            except Exception as exc:
                logger.warning("Bybit websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _handle_message(self, raw_message: str) -> None:
        data = json.loads(raw_message)
        if data.get("topic") != "instrument_info.100ms":
            return
        for item in data.get("data", []):
            await self._process_payload(item)

    async def _process_payload(self, payload: dict[str, Any]) -> None:
        symbol = payload.get("symbol")
        if not symbol:
            return
        if symbol not in self.symbols:
            return

        snapshot = self.snapshots.get(symbol) or MarketSnapshot(exchange="Bybit", symbol=symbol)
        snapshot.price = float(payload.get("last_price", snapshot.price or 0.0))
        snapshot.open_interest = float(payload.get("open_interest", snapshot.open_interest or 0.0))
        snapshot.volume_24h = float(payload.get("volume_24h", snapshot.volume_24h or 0.0))
        snapshot.bid_price = float(payload.get("bid_price", snapshot.bid_price or 0.0))
        snapshot.ask_price = float(payload.get("ask_price", snapshot.ask_price or 0.0))
        snapshot.price_change_percent = float(payload.get("price_24h_pct", snapshot.price_change_percent or 0.0))
        snapshot.timestamp = payload.get("timestamp") or snapshot.timestamp
        snapshot.additional.update({
            "funding_rate": payload.get("funding_rate"),
            "turnover_24h": payload.get("turnover_24h"),
        })
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
        if self._task and not self._task.done():
            self._task.cancel()
