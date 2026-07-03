from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import aiohttp
from websockets import connect

from .base import ExchangeScanner, MarketSnapshot

logger = logging.getLogger(__name__)

BINANCE_REST_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_WS_BASE = "wss://fstream.binance.com/stream?streams="

class BinanceScanner(ExchangeScanner):
    def __init__(self, on_update: Callable[..., Awaitable[None]]) -> None:
        self.symbols: list[str] = []
        self.snapshots: dict[str, MarketSnapshot] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self.on_update = on_update

    async def load_symbols(self) -> list[str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(BINANCE_REST_INFO, timeout=30) as response:
                data = await response.json()

        symbols = [s["symbol"] for s in data.get("symbols", [])
                   if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"]
        self.symbols = sorted(set(symbols))
        logger.info("Loaded %d Binance futures symbols", len(self.symbols))
        return self.symbols

    async def run(self) -> None:
        self._running = True
        if not self.symbols:
            await self.load_symbols()

        ticker_stream = "!ticker@arr"
        oi_stream = "!openInterest@arr"
        url = f"{BINANCE_WS_BASE}{ticker_stream}/{oi_stream}"

        while self._running:
            try:
                async with connect(url, ping_interval=20, ping_timeout=10, max_size=2**24) as websocket:
                    logger.info("Binance websocket connected")
                    async for message in websocket:
                        if not self._running:
                            break
                        await self._handle_message(message)
            except Exception as exc:
                logger.warning("Binance websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _handle_message(self, raw_message: str) -> None:
        data = json.loads(raw_message)
        stream = data.get("stream", "")
        payload = data.get("data", {})
        if stream.endswith("@arr") and isinstance(payload, list):
            # combined stream returns list of updates per symbol
            for item in payload:
                await self._process_payload(item)
        elif isinstance(payload, dict):
            await self._process_payload(payload)

    async def _process_payload(self, payload: dict[str, Any]) -> None:
        symbol = payload.get("s") or payload.get("symbol")
        if not symbol:
            return
        symbol = symbol.upper()
        snapshot = self.snapshots.get(symbol) or MarketSnapshot(exchange="Binance", symbol=symbol)
        snapshot.timestamp = payload.get("E") or payload.get("E") or snapshot.timestamp
        if payload.get("e") == "24hrTicker" or payload.get("e") == "ticker":
            snapshot.price = float(payload.get("c", snapshot.price or 0.0))
            snapshot.price_change_percent = float(payload.get("P", snapshot.price_change_percent or 0.0))
            snapshot.volume_24h = float(payload.get("v", snapshot.volume_24h or 0.0))
            snapshot.bid_price = float(payload.get("b", snapshot.bid_price or 0.0))
            snapshot.ask_price = float(payload.get("a", snapshot.ask_price or 0.0))
            snapshot.additional.update({
                "trade_count": int(payload.get("n", 0)),
                "high_price": payload.get("h"),
                "low_price": payload.get("l"),
            })
        elif payload.get("e") == "openInterest":
            snapshot.open_interest = float(payload.get("o", snapshot.open_interest or 0.0))
            snapshot.timestamp = payload.get("E") or snapshot.timestamp
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
