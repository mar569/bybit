from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

BYBIT_ACCOUNT_RATIO = "https://api.bybit.com/v5/market/account-ratio"


@dataclass(frozen=True)
class AccountRatioSnapshot:
    symbol: str
    buy_ratio: float
    sell_ratio: float
    long_short_ratio: float
    period: str
    timestamp_ms: int


class BybitAccountRatioCache:
    """Long/Short ratio аккаунтов — GET /v5/market/account-ratio (реальные данные Bybit)."""

    def __init__(self, ttl_seconds: float = 120.0, period: str = "5min") -> None:
        self._ttl = ttl_seconds
        self._period = period
        self._cache: dict[str, tuple[float, AccountRatioSnapshot]] = {}
        self._lock = asyncio.Lock()

    async def get_ratio(self, symbol: str) -> AccountRatioSnapshot | None:
        symbol = symbol.upper()
        now = time.time()
        cached = self._cache.get(symbol)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        async with self._lock:
            cached = self._cache.get(symbol)
            if cached and now - cached[0] < self._ttl:
                return cached[1]

            snapshot = await self._fetch(symbol)
            if snapshot is not None:
                self._cache[symbol] = (time.time(), snapshot)
            return snapshot

    async def _fetch(self, symbol: str) -> AccountRatioSnapshot | None:
        params = {
            "category": "linear",
            "symbol": symbol,
            "period": self._period,
            "limit": 1,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(BYBIT_ACCOUNT_RATIO, params=params, timeout=15) as response:
                    data = await response.json()
        except Exception:
            logger.warning("Bybit account-ratio fetch failed for %s", symbol, exc_info=True)
            return None

        if data.get("retCode") != 0:
            logger.debug("Bybit account-ratio %s: %s", symbol, data.get("retMsg"))
            return None

        items = data.get("result", {}).get("list", [])
        if not items:
            return None

        row = items[0]
        try:
            buy = float(row["buyRatio"])
            sell = float(row["sellRatio"])
        except (KeyError, TypeError, ValueError):
            return None

        if sell <= 0:
            ls_ratio = buy / max(sell, 1e-9)
        else:
            ls_ratio = buy / sell

        return AccountRatioSnapshot(
            symbol=symbol,
            buy_ratio=buy,
            sell_ratio=sell,
            long_short_ratio=round(ls_ratio, 4),
            period=self._period,
            timestamp_ms=int(row.get("timestamp", 0)),
        )


def format_bybit_real_data_block(details: dict[str, object] | None) -> str:
    if not details:
        return ""

    lines: list[str] = []
    ar = details.get("account_ratio")
    if isinstance(ar, dict) and ar.get("long_short_ratio") is not None:
        period = ar.get("period", "5min")
        lines.append(
            f"📊 <b>Bybit L/S</b> ({period}): "
            f"ratio <b>{float(ar['long_short_ratio']):.2f}</b> | "
            f"лонг <b>{ar.get('long_pct', '?')}%</b> / "
            f"шорт <b>{ar.get('short_pct', '?')}%</b> аккаунтов"
        )

    liq = details.get("liquidations")
    if isinstance(liq, dict):
        window = int(liq.get("window_minutes", 15))
        long_usd = float(liq.get("long_liq_usd", 0) or 0)
        short_usd = float(liq.get("short_liq_usd", 0) or 0)
        events = int(liq.get("event_count", 0) or 0)
        lines.append(
            f"💥 <b>Ликвидации {window}м</b>: "
            f"лонги <b>{long_usd:,.0f}$</b> | шорты <b>{short_usd:,.0f}$</b> "
            f"({events} событ.)"
            .replace(",", " ")
        )

    if not lines:
        return ""
    return "\n".join(lines)


def format_bybit_real_data_compact(details: dict[str, object] | None) -> str:
    if not details:
        return ""
    parts: list[str] = []
    ar = details.get("account_ratio")
    if isinstance(ar, dict) and ar.get("long_short_ratio") is not None:
        ratio = float(ar["long_short_ratio"])
        short_pct = ar.get("short_pct", "?")
        long_pct = ar.get("long_pct", "?")
        bias = "шортов больше" if ratio < 0.95 else ("лонгов больше" if ratio > 1.05 else "баланс")
        parts.append(f"L/S <b>{ratio:.2f}</b> ({long_pct}/{short_pct}%) {bias}")

    liq = details.get("liquidations")
    if isinstance(liq, dict):
        events = int(liq.get("event_count", 0) or 0)
        if events > 0:
            long_usd = float(liq.get("long_liq_usd", 0) or 0)
            short_usd = float(liq.get("short_liq_usd", 0) or 0)
            parts.append(f"💥 ликв. L {long_usd:,.0f}$ S {short_usd:,.0f}$".replace(",", " "))

    if not parts:
        return ""
    return "📊 " + " · ".join(parts)
