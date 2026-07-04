from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from websockets import connect

from .bybit_liquidations import LiquidationStats
from .liquidation_alerts import (
    SIDE_LONG_LIQ,
    SIDE_SHORT_LIQ,
    LiquidationAlertEvent,
    normalize_binance_side,
)

MAX_EVENTS_PER_SYMBOL = 500

logger = logging.getLogger(__name__)

BINANCE_FORCE_ORDER_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"


@dataclass(frozen=True)
class _BinanceLiqEvent:
    timestamp: float
    symbol: str
    side: str
    usd_value: float


class BinanceLiquidationTracker:
    """WebSocket !forceOrder@arr — все ликвидации Binance Futures в одном потоке."""

    def __init__(
        self,
        *,
        enabled: Callable[[], bool] | None = None,
        on_event: Callable[[LiquidationAlertEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._enabled = enabled or (lambda: True)
        self._on_event = on_event
        self._running = False
        self._events: dict[str, deque[_BinanceLiqEvent]] = defaultdict(
            lambda: deque(maxlen=MAX_EVENTS_PER_SYMBOL)
        )
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
            if event.side == SIDE_LONG_LIQ:
                long_usd += event.usd_value
            elif event.side == SIDE_SHORT_LIQ:
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
                    BINANCE_FORCE_ORDER_WS,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**20,
                ) as websocket:
                    logger.info("Binance liquidation websocket connected")
                    async for message in websocket:
                        if not self._running or not self._enabled():
                            break
                        await self._handle_message(message)
            except Exception as exc:
                logger.warning("Binance liquidation websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _handle_message(self, raw_message: str) -> None:
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        row = data.get("o") if isinstance(data.get("o"), dict) else data
        if not isinstance(row, dict):
            return

        symbol = str(row.get("s", "")).upper()
        if not symbol:
            return

        side = normalize_binance_side(str(row.get("S", "")))
        if side is None:
            return

        try:
            price = float(row.get("p", 0))
            qty = float(row.get("q", 0))
        except (TypeError, ValueError):
            return
        if price <= 0 or qty <= 0:
            return

        ts_ms = row.get("T") or data.get("E")
        ts = float(ts_ms) / 1000.0 if ts_ms else time.time()
        usd_value = price * qty

        alert_event = LiquidationAlertEvent(
            exchange="Binance",
            timestamp=ts,
            symbol=symbol,
            side=side,
            usd_value=usd_value,
            price=price,
        )

        async with self._lock:
            self._events[symbol].append(
                _BinanceLiqEvent(timestamp=ts, symbol=symbol, side=side, usd_value=usd_value)
            )

        if self._on_event is not None:
            await self._on_event(alert_event)

    def stop(self) -> None:
        self._running = False
