"""Liquid Trading market data via signed REST API."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

from .auth import HEADER_API_KEY, sign_request
from .client import LiquidQuote, MarketSession

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = (
    "https://prometheus-prod--liquid-public-api-public-fastapi-app.modal.run"
)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="liquid-trade")


def bybit_symbol_to_liquid_perp(symbol: str) -> str | None:
    sym = (symbol or "").strip().upper().replace("/", "")
    if not sym:
        return None
    base = sym.removesuffix("USDT").removesuffix("USDC").removesuffix("USD")
    if not base or len(base) > 12:
        return None
    return f"{base}-PERP"


class HttpLiquidTradeClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        ttl_seconds: float = 45.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, LiquidQuote]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def from_env(cls) -> HttpLiquidTradeClient | None:
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")
        key = os.getenv("LIQUID_API_KEY", "").strip()
        secret = os.getenv("LIQUID_API_SECRET", "").strip()
        if not key or not secret:
            return None
        base = os.getenv("LIQUID_API_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        return cls(api_key=key, api_secret=secret, base_url=base)

    async def _get_json(self, path: str) -> Any:
        url = f"{self._base_url}{path}"
        auth = sign_request(self._api_secret, "GET", path, "", None)
        headers = {
            HEADER_API_KEY: self._api_key,
            **auth,
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"Liquid API {resp.status}: {text[:200]}")
                payload = await resp.json()
        if isinstance(payload, dict) and "success" in payload:
            if not payload.get("success"):
                err = payload.get("error") or {}
                raise RuntimeError(str(err.get("message") or "Liquid API error"))
            return payload.get("data")
        return payload

    async def fetch_ticker(self, liquid_symbol: str) -> LiquidQuote | None:
        liquid_symbol = liquid_symbol.strip().upper()
        if not liquid_symbol:
            return None
        cached = self._cache.get(liquid_symbol)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]

        lock = self._locks.setdefault(liquid_symbol, asyncio.Lock())
        async with lock:
            cached = self._cache.get(liquid_symbol)
            if cached and time.monotonic() - cached[0] < self._ttl:
                return cached[1]
            try:
                data = await self._get_json(f"/v1/markets/{liquid_symbol}/ticker")
            except Exception as exc:
                logger.warning("Liquid ticker %s failed: %s", liquid_symbol, exc)
                return None
            if not isinstance(data, dict):
                return None
            mark_raw = data.get("mark_price") or data.get("last") or data.get("price")
            try:
                last = float(mark_raw)
            except (TypeError, ValueError):
                return None
            asset = "crypto" if liquid_symbol.endswith("-PERP") else "unknown"
            quote = LiquidQuote(symbol=liquid_symbol, last=last, asset_class=asset)
            self._cache[liquid_symbol] = (time.monotonic(), quote)
            return quote

    def get_quote(self, symbol: str) -> LiquidQuote | None:
        liquid_sym = bybit_symbol_to_liquid_perp(symbol) or symbol.strip().upper()
        if not liquid_sym:
            return None
        return _run_async(self.fetch_ticker(liquid_sym))

    def market_session(self, symbol: str, *, at: datetime | None = None) -> MarketSession | None:
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        _ = at or datetime.now(timezone.utc)
        liquid_sym = bybit_symbol_to_liquid_perp(sym) or sym
        if liquid_sym.endswith("-PERP"):
            return MarketSession(
                symbol=liquid_sym,
                asset_class="crypto",
                is_open=True,
                session_label="24/7",
            )
        return MarketSession(
            symbol=liquid_sym,
            asset_class="unknown",
            is_open=True,
            session_label="—",
        )


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    future = _executor.submit(asyncio.run, coro)
    return future.result(timeout=20)


def liquid_intel_line(symbol: str, *, client: HttpLiquidTradeClient | None = None) -> str:
    """Cross-venue mark price hint for INTEL (requires LIQUID_API_KEY + secret)."""
    c = client or HttpLiquidTradeClient.from_env()
    if c is None:
        return ""
    liquid_sym = bybit_symbol_to_liquid_perp(symbol)
    if not liquid_sym:
        return ""
    quote = c.get_quote(symbol)
    if not quote or quote.last <= 0:
        return ""
    px = quote.last
    if px >= 1000:
        label = f"Liquid mark ~{px:,.0f}"
    elif px >= 1:
        label = f"Liquid mark ~{px:,.2f}"
    else:
        label = f"Liquid mark ~{px:.4f}"
    return label[:120]
