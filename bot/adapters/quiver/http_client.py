"""Quiver Quant REST client (congress trading intel)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

from .client import QuiverSnippet

logger = logging.getLogger(__name__)

BASE_URL = "https://api.quiverquant.com"
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quiver")


def _tx_label_ru(tx: str) -> str:
    low = (tx or "").strip().lower()
    if "purchase" in low or "buy" in low:
        return "покупка"
    if "sale" in low or "sell" in low:
        return "продажа"
    return tx.strip()[:40] if tx else "сделка"


def snippet_from_congress_rows(ticker: str, rows: list[dict[str, Any]]) -> QuiverSnippet | None:
    if not rows:
        return None
    best: dict[str, Any] | None = None
    best_dt: datetime | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        tick = str(row.get("Ticker") or row.get("ticker") or "").upper()
        if tick and tick != ticker.upper():
            continue
        raw_date = (
            row.get("TransactionDate")
            or row.get("Date")
            or row.get("transaction_date")
            or row.get("ReportDate")
        )
        dt: datetime | None = None
        if isinstance(raw_date, str) and raw_date:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(raw_date[:19], fmt)
                    break
                except ValueError:
                    continue
        rep = str(row.get("Representative") or row.get("representative") or "конгрессмен").strip()
        tx = _tx_label_ru(str(row.get("Transaction") or row.get("transaction") or ""))
        party = str(row.get("Party") or row.get("party") or "").strip()
        party_bit = f", {party}" if party else ""
        headline = f"Конгресс ({ticker}): {rep} — {tx}{party_bit}"
        if dt is None:
            if best is None:
                best = row
                best_dt = None
            continue
        if best_dt is None or dt > best_dt:
            best = row
            best_dt = dt
    if best is None:
        best = rows[0] if isinstance(rows[0], dict) else None
    if best is None:
        return None
    rep = str(best.get("Representative") or best.get("representative") or "конгрессмен").strip()
    tx = _tx_label_ru(str(best.get("Transaction") or best.get("transaction") or ""))
    party = str(best.get("Party") or best.get("party") or "").strip()
    party_bit = f", {party}" if party else ""
    headline = f"Конгресс: {rep} — {tx}{party_bit}"
    return QuiverSnippet(ticker=ticker.upper(), headline_ru=headline[:200])


class HttpQuiverClient:
    def __init__(self, api_key: str, *, ttl_seconds: float = 3600.0) -> None:
        self._api_key = api_key.strip()
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, QuiverSnippet | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def from_env(cls) -> HttpQuiverClient | None:
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")
        key = os.getenv("QUIVER_API_KEY", "").strip()
        if not key:
            return None
        return cls(api_key=key)

    async def _fetch_congress(self, ticker: str) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/beta/historical/congresstrading/{ticker.upper()}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    return []
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"Quiver API {resp.status}: {text[:200]}")
                data = await resp.json()
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            inner = data.get("data") or data.get("results")
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
        return []

    async def congress_activity_async(self, ticker: str) -> QuiverSnippet | None:
        base = ticker.strip().upper().removesuffix("USDT")
        if not base or not base.isalpha() or len(base) > 6:
            return None
        cached = self._cache.get(base)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]

        lock = self._locks.setdefault(base, asyncio.Lock())
        async with lock:
            cached = self._cache.get(base)
            if cached and time.monotonic() - cached[0] < self._ttl:
                return cached[1]
            try:
                rows = await self._fetch_congress(base)
                snip = snippet_from_congress_rows(base, rows)
            except Exception as exc:
                logger.warning("Quiver congress %s failed: %s", base, exc)
                snip = None
            self._cache[base] = (time.monotonic(), snip)
            return snip

    def congress_activity(self, ticker: str) -> QuiverSnippet | None:
        return _run_async(self.congress_activity_async(ticker))


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    future = _executor.submit(asyncio.run, coro)
    return future.result(timeout=25)
