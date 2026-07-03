from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

from websockets import connect

logger = logging.getLogger(__name__)

BYBIT_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"
WS_SUBSCRIBE_BATCH = 50
SYMBOL_REFRESH_SECONDS = 120
LIQUIDATION_WINDOW_SECONDS = 900  # 15 минут
MAX_EVENTS_PER_SYMBOL = 500


@dataclass(frozen=True)
class LiquidationEvent:
    timestamp: float
    symbol: str
    side: str
    size: float
    price: float
    usd_value: float

    @property
    def is_long_liquidated(self) -> bool:
        return self.side == "Buy"

    @property
    def is_short_liquidated(self) -> bool:
        return self.side == "Sell"


@dataclass(frozen=True)
class LiquidationStats:
    symbol: str
    window_minutes: int
    long_liq_usd: float
    short_liq_usd: float
    event_count: int

    @property
    def total_usd(self) -> float:
        return self.long_liq_usd + self.short_liq_usd

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "symbol": self.symbol,
            "window_minutes": self.window_minutes,
            "long_liq_usd": round(self.long_liq_usd, 2),
            "short_liq_usd": round(self.short_liq_usd, 2),
            "total_usd": round(self.total_usd, 2),
            "event_count": self.event_count,
        }


class BybitLiquidationTracker:
    """WebSocket allLiquidation.{symbol} — реальные ликвидации Bybit (топ монеты)."""

    def __init__(
        self,
        get_symbols: Callable[[], list[str]],
        *,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._get_symbols = get_symbols
        self._enabled = enabled or (lambda: True)
        self._running = False
        self._events: dict[str, deque[LiquidationEvent]] = defaultdict(
            lambda: deque(maxlen=MAX_EVENTS_PER_SYMBOL)
        )
        self._subscribed: set[str] = set()
        self._last_symbol_refresh = 0.0
        self._lock = asyncio.Lock()

    def get_stats(self, symbol: str, window_minutes: int = 15) -> LiquidationStats:
        symbol = symbol.upper()
        cutoff = time.time() - window_minutes * 60
        long_usd = 0.0
        short_usd = 0.0
        count = 0
        for event in self._events.get(symbol, ()):
            if event.timestamp < cutoff:
                continue
            count += 1
            if event.is_long_liquidated:
                long_usd += event.usd_value
            elif event.is_short_liquidated:
                short_usd += event.usd_value
        return LiquidationStats(
            symbol=symbol,
            window_minutes=window_minutes,
            long_liq_usd=long_usd,
            short_liq_usd=short_usd,
            event_count=count,
        )

    async def run(self) -> None:
        self._running = True
        while self._running:
            if not self._enabled():
                await asyncio.sleep(2)
                continue
            try:
                async with connect(
                    BYBIT_WS_LINEAR,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**20,
                ) as websocket:
                    logger.info("Bybit liquidation websocket connected")
                    await self._subscribe_symbols(websocket)
                    self._last_symbol_refresh = time.time()
                    async for message in websocket:
                        if not self._running or not self._enabled():
                            break
                        if time.time() - self._last_symbol_refresh > SYMBOL_REFRESH_SECONDS:
                            await self._subscribe_symbols(websocket)
                            self._last_symbol_refresh = time.time()
                        await self._handle_message(message)
            except Exception as exc:
                logger.warning("Bybit liquidation websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _subscribe_symbols(self, websocket: object) -> None:
        symbols = [s.upper() for s in self._get_symbols() if s]
        if not symbols:
            return
        desired = set(symbols)
        new_symbols = desired - self._subscribed
        if not new_symbols and desired == self._subscribed:
            return

        topics = [f"allLiquidation.{symbol}" for symbol in sorted(desired)]
        for index in range(0, len(topics), WS_SUBSCRIBE_BATCH):
            batch = topics[index : index + WS_SUBSCRIBE_BATCH]
            await websocket.send(json.dumps({"op": "subscribe", "args": batch}))
            await asyncio.sleep(0.05)
        self._subscribed = desired
        logger.info("Bybit liquidation subscribed to %d symbols", len(self._subscribed))

    async def _handle_message(self, raw_message: str) -> None:
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        topic = data.get("topic", "")
        if not topic.startswith("allLiquidation."):
            return

        payload = data.get("data")
        rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
        now = time.time()

        async with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("s", "")).upper()
                if not symbol:
                    continue
                try:
                    size = float(row.get("v", 0))
                    price = float(row.get("p", 0))
                except (TypeError, ValueError):
                    continue
                if size <= 0 or price <= 0:
                    continue
                side = str(row.get("S", ""))
                ts_ms = row.get("T")
                ts = float(ts_ms) / 1000.0 if ts_ms else now
                self._events[symbol].append(LiquidationEvent(
                    timestamp=ts,
                    symbol=symbol,
                    side=side,
                    size=size,
                    price=price,
                    usd_value=size * price,
                ))

    def stop(self) -> None:
        self._running = False
