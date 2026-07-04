from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

from websockets import connect

from .liquidation_alerts import LiquidationAlertEvent, normalize_binance_side

logger = logging.getLogger(__name__)

BINANCE_FORCE_ORDER_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"


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

        event = LiquidationAlertEvent(
            exchange="Binance",
            timestamp=ts,
            symbol=symbol,
            side=side,
            usd_value=price * qty,
            price=price,
        )
        if self._on_event is not None:
            await self._on_event(event)

    def stop(self) -> None:
        self._running = False
