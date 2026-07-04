from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Нормализованная сторона: ликвидированы лонги или шорты
SIDE_LONG_LIQ = "long_liq"
SIDE_SHORT_LIQ = "short_liq"


@dataclass(frozen=True)
class LiquidationAlertEvent:
    exchange: str
    timestamp: float
    symbol: str
    side: str
    usd_value: float
    price: float


@dataclass
class _PendingBurst:
    exchange: str
    symbol: str
    side: str
    total_usd: float = 0.0
    event_count: int = 0
    last_price: float = 0.0
    first_ts: float = 0.0
    flush_task: asyncio.Task | None = field(default=None, compare=False, repr=False)


class LiquidationAlertService:
    """Агрегирует всплески ликвидаций и шлёт алерты в TELEGRAM_ALERT_CHAT_ID."""

    def __init__(
        self,
        settings_getter: Callable[[], object],
        on_alert: Callable[[LiquidationAlertEvent, int, float], Awaitable[None]],
    ) -> None:
        self._get_settings = settings_getter
        self._on_alert = on_alert
        self._pending: dict[tuple[str, str, str], _PendingBurst] = {}
        self._cooldown_until: dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    def enabled(self) -> bool:
        settings = self._get_settings()
        return bool(getattr(settings, "liquidation_alerts_enabled", True))

    async def on_liquidation(self, event: LiquidationAlertEvent) -> None:
        if not self.enabled():
            return

        settings = self._get_settings()
        min_usd = float(getattr(settings, "liquidation_min_usd", 40_000.0))
        if event.usd_value <= 0:
            return

        key = (event.exchange.lower(), event.symbol.upper(), event.side)
        cooldown_key = (event.exchange.lower(), event.symbol.upper())
        now = time.time()
        cooldown_sec = int(getattr(settings, "liquidation_cooldown_seconds", 60))

        async with self._lock:
            if now < self._cooldown_until.get(cooldown_key, 0.0):
                return

            burst = self._pending.get(key)
            if burst is None:
                burst = _PendingBurst(
                    exchange=event.exchange,
                    symbol=event.symbol.upper(),
                    side=event.side,
                    first_ts=now,
                )
                self._pending[key] = burst

            burst.total_usd += event.usd_value
            burst.event_count += 1
            burst.last_price = event.price

            if burst.flush_task is not None:
                burst.flush_task.cancel()

            window = float(getattr(settings, "liquidation_burst_window_seconds", 2.0))
            burst.flush_task = asyncio.create_task(
                self._flush_after(key, window, min_usd, cooldown_sec, cooldown_key),
            )

    async def _flush_after(
        self,
        key: tuple[str, str, str],
        window: float,
        min_usd: float,
        cooldown_sec: int,
        cooldown_key: tuple[str, str],
    ) -> None:
        try:
            await asyncio.sleep(window)
        except asyncio.CancelledError:
            return

        async with self._lock:
            burst = self._pending.pop(key, None)
            if burst is None or burst.total_usd < min_usd:
                return
            self._cooldown_until[cooldown_key] = time.time() + cooldown_sec

        alert = LiquidationAlertEvent(
            exchange=burst.exchange,
            timestamp=time.time(),
            symbol=burst.symbol,
            side=burst.side,
            usd_value=burst.total_usd,
            price=burst.last_price,
        )
        try:
            await self._on_alert(alert, burst.event_count, burst.total_usd)
        except Exception:
            logger.exception(
                "Liquidation alert failed %s %s",
                burst.exchange,
                burst.symbol,
            )


def normalize_bybit_side(side: str) -> str | None:
    if side == "Buy":
        return SIDE_LONG_LIQ
    if side == "Sell":
        return SIDE_SHORT_LIQ
    return None


def normalize_binance_side(side: str) -> str | None:
    # Binance forceOrder: SELL = ликвидация лонга, BUY = ликвидация шорта
    if side == "SELL":
        return SIDE_LONG_LIQ
    if side == "BUY":
        return SIDE_SHORT_LIQ
    return None


def coinglass_url(symbol: str, exchange: str) -> str:
    normalized = symbol.upper().replace("/", "")
    exchange_key = exchange.lower()
    if "bybit" in exchange_key:
        return f"https://www.coinglass.com/tv/Bybit_{normalized}"
    if "binance" in exchange_key:
        return f"https://www.coinglass.com/tv/Binance_{normalized}"
    return f"https://www.coinglass.com/Futures/{normalized}"


def exchange_trade_url(symbol: str, exchange: str) -> str:
    sym = symbol.upper()
    if "binance" in exchange.lower():
        return f"https://www.binance.com/en/futures/{sym}"
    return f"https://www.bybit.com/trade/usdt/{sym}"


def base_ticker(symbol: str) -> str:
    sym = symbol.upper()
    if sym.endswith("USDT"):
        return sym[:-4]
    if sym.endswith("USDC"):
        return sym[:-4]
    return sym


def format_liquidation_alert(
    event: LiquidationAlertEvent,
    event_count: int,
    total_usd: float,
    *,
    show_reversal_hint: bool = True,
) -> str:
    exchange_key = "bybit" if "bybit" in event.exchange.lower() else "binance"
    if exchange_key == "bybit":
        exchange_emoji, exchange_name = "⚫", "ByBit"
    else:
        exchange_emoji, exchange_name = "🟡", "Binance"

    is_long_liq = event.side == SIDE_LONG_LIQ
    side_emoji = "🔴" if is_long_liq else "🟢"

    ticker = base_ticker(event.symbol)
    cg_url = coinglass_url(event.symbol, event.exchange)
    ex_url = exchange_trade_url(event.symbol, event.exchange)
    usd_text = f"${int(round(total_usd)):,}".replace(",", "")
    ts = datetime.fromtimestamp(event.timestamp, tz=timezone.utc).strftime("%H:%M")

    lines = [
        (
            f'{exchange_emoji} <a href="{ex_url}">{exchange_name}</a> '
            f'{side_emoji} <a href="{cg_url}">#{ticker}</a> '
            f"{usd_text} (🔊 {event_count}) {ts}"
        ),
    ]
    if show_reversal_hint:
        if is_long_liq:
            lines.append("↗️ <i>Лонги в ликвидации — возможен отскок</i>")
        else:
            lines.append("↘️ <i>Шорты в ликвидации — возможен откат</i>")
    return "\n".join(lines)
