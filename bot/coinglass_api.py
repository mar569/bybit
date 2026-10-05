from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api-v4.coinglass.com"
_ALLOWED_INTERVALS = {5: "5m", 10: "15m", 15: "15m", 30: "30m", 60: "1h", 240: "4h"}
_INTERVAL_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
_FLOW_EXCHANGES = "Binance,OKX,Bybit"


class CoinGlassApiError(RuntimeError):
    pass


class CoinGlassClient:
    def __init__(self, *, ttl_seconds: float = 30.0) -> None:
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        self._api_key = os.getenv("COINGLASS_API_KEY", "").strip()
        self._ttl = ttl_seconds
        self._cache: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}
        self._locks: dict[tuple[str, int, str], asyncio.Lock] = {}
        self._warned_missing_key = False

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def market_context(
        self,
        symbol: str,
        *,
        interval_minutes: int,
        exchange: str = "Bybit",
    ) -> dict[str, Any]:
        symbol = symbol.upper().replace("/", "")
        coin = symbol.removesuffix("USDT").removesuffix("USDC").removesuffix("USD")
        exchange_name = exchange.title()
        key = (symbol, interval_minutes, exchange_name)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return dict(cached[1])
        if not self._api_key:
            if not self._warned_missing_key:
                logger.warning("CoinGlass metrics disabled: COINGLASS_API_KEY is not configured")
                self._warned_missing_key = True
            return {
                "coinglass_status": "COINGLASS_API_KEY не настроен",
                "coinglass_available_metrics": [],
                "coinglass_missing_metrics": [
                    "oi", "funding", "account_ratio", "liquidations",
                    "futures_taker_history", "spot_taker_history", "taker",
                ],
            }

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < self._ttl:
                return dict(cached[1])
            result = await self._fetch_context(
                symbol=symbol,
                coin=coin,
                interval_minutes=interval_minutes,
                exchange=exchange_name,
            )
            self._cache[key] = (time.monotonic(), result)
            return dict(result)

    async def _fetch_context(
        self,
        *,
        symbol: str,
        coin: str,
        interval_minutes: int,
        exchange: str,
    ) -> dict[str, Any]:
        interval = _ALLOWED_INTERVALS.get(interval_minutes, "15m")
        short_range = interval if interval in {"5m", "15m", "30m", "1h", "4h"} else "15m"
        requests = {
            "price": (
                "/api/futures/price/history",
                {"exchange": exchange, "symbol": symbol, "interval": interval, "limit": 20},
            ),
            "oi": (
                "/api/futures/open-interest/aggregated-history",
                {"symbol": coin, "interval": interval, "limit": 20, "unit": "usd"},
            ),
            "funding": (
                "/api/futures/funding-rate/history",
                {"exchange": exchange, "symbol": symbol, "interval": interval, "limit": 20},
            ),
            "account_ratio": (
                "/api/futures/global-long-short-account-ratio/history",
                {"exchange": exchange, "symbol": symbol, "interval": "4h", "limit": 2},
            ),
            "liquidations": (
                "/api/futures/liquidation/history",
                {"exchange": exchange, "symbol": symbol, "interval": short_range, "limit": 3},
            ),
            "taker": (
                "/api/futures/taker-buy-sell-volume/exchange-list",
                {"symbol": coin, "range": short_range},
            ),
            "futures_taker_history": (
                "/api/futures/aggregated-taker-buy-sell-volume/history",
                {
                    "exchange_list": _FLOW_EXCHANGES,
                    "symbol": coin,
                    "interval": interval,
                    "limit": 4,
                    "unit": "usd",
                },
            ),
            "spot_taker_history": (
                "/api/spot/aggregated-taker-buy-sell-volume/history",
                {
                    "exchange_list": _FLOW_EXCHANGES,
                    "symbol": coin,
                    "interval": interval,
                    "limit": 4,
                    "unit": "usd",
                },
            ),
        }
        timeout = aiohttp.ClientTimeout(total=12)
        headers = {
            "accept": "application/json",
            "CG-API-KEY": self._api_key,
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            responses = await asyncio.gather(
                *(
                    self._request(session, path, params)
                    for path, params in requests.values()
                ),
                return_exceptions=True,
            )

        raw: dict[str, Any] = {}
        failures: list[str] = []
        for (name, (_path, _params)), response in zip(requests.items(), responses):
            if isinstance(response, Exception):
                logger.warning("CoinGlass %s unavailable for %s: %s", name, symbol, response)
                failures.append(name)
            else:
                raw[name] = response
        metrics = self._normalize(
            raw,
            interval_minutes=_INTERVAL_MINUTES.get(interval, interval_minutes),
        )
        metric_keys = {
            "price": "price_change_pct",
            "oi": "oi_change_pct",
            "funding": "funding_rate",
            "account_ratio": "account_ratio",
            "liquidations": "liquidations",
            "taker": "taker_buy_ratio",
            "futures_taker_history": "futures_taker_buy_ratio",
            "spot_taker_history": "spot_taker_buy_ratio",
        }
        available = [name for name, metric in metric_keys.items() if metric in metrics]
        missing = [name for name in metric_keys if name not in available]
        for name in raw:
            if name not in available:
                logger.warning("CoinGlass %s returned no usable data for %s", name, symbol)
        metrics.update({
            "source": "CoinGlass V4",
            "coinglass_status": "данные частичные" if missing else "данные получены",
            "coinglass_available_metrics": available,
            "coinglass_missing_metrics": missing,
        })
        if not available:
            metrics["coinglass_status"] = "данных нет; проверьте ключ, тариф и символ"
        return metrics

    async def _request(
        self,
        session: aiohttp.ClientSession,
        path: str,
        params: Mapping[str, object],
    ) -> Any:
        async with session.get(f"{BASE_URL}{path}", params=params) as response:
            if response.status >= 400:
                body = (await response.text())[:240]
                raise CoinGlassApiError(f"HTTP {response.status}: {body}")
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise CoinGlassApiError(
                str(payload.get("msg", "invalid response"))[:240]
                if isinstance(payload, dict)
                else "invalid response"
            )
        return payload.get("data")

    @staticmethod
    def _rows(payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, Mapping)]
        if isinstance(payload, Mapping):
            data = payload.get("data")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, Mapping)]
            return [payload]
        return []

    @classmethod
    def _normalize(cls, raw: Mapping[str, Any], *, interval_minutes: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for component in (
            "price",
            "oi",
            "funding",
            "account_ratio",
            "liquidations",
            "futures_taker_history",
            "spot_taker_history",
        ):
            rows = cls._rows(raw.get(component))
            if not rows:
                continue
            rows.sort(key=lambda row: float(row.get("time", 0) or 0))
            if component in {"price", "oi"}:
                try:
                    first = float(rows[0].get("close", 0) or 0)
                    last = float(rows[-1].get("close", 0) or 0)
                    if first > 0:
                        result[f"{component}_change_pct"] = (last - first) / first * 100
                        if component == "oi":
                            result["oi_usd"] = last
                            result["oi_period_minutes"] = interval_minutes * (len(rows) - 1)
                except (TypeError, ValueError):
                    pass
            elif component in {"futures_taker_history", "spot_taker_history"}:
                try:
                    buy = sum(
                        float(row.get("aggregated_buy_volume_usd", 0) or 0)
                        for row in rows[-3:]
                    )
                    sell = sum(
                        float(row.get("aggregated_sell_volume_usd", 0) or 0)
                        for row in rows[-3:]
                    )
                    total = buy + sell
                    if total > 0:
                        result[f"{component.removesuffix('_history')}_buy_ratio"] = buy / total
                        result[f"{component.removesuffix('_history')}_buy_vol_usd"] = buy
                        result[f"{component.removesuffix('_history')}_sell_vol_usd"] = sell
                        result[f"{component.removesuffix('_history')}_window_minutes"] = (
                            interval_minutes * min(3, len(rows))
                        )
                except (TypeError, ValueError):
                    pass
            elif component == "funding":
                try:
                    result["funding_rate"] = float(rows[-1].get("close"))
                except (TypeError, ValueError):
                    pass
            elif component == "account_ratio":
                try:
                    result["account_ratio"] = {
                        "long_short_ratio": float(rows[-1]["global_account_long_short_ratio"]),
                        "long_pct": float(rows[-1]["global_account_long_percent"]),
                        "short_pct": float(rows[-1]["global_account_short_percent"]),
                        "period": "4h",
                    }
                except (KeyError, TypeError, ValueError):
                    pass
            else:
                try:
                    long_liq = sum(float(row.get("long_liquidation_usd", 0) or 0) for row in rows)
                    short_liq = sum(float(row.get("short_liquidation_usd", 0) or 0) for row in rows)
                    result["liquidations"] = {
                        "window_minutes": interval_minutes * len(rows),
                        "long_liq_usd": long_liq,
                        "short_liq_usd": short_liq,
                    }
                except (TypeError, ValueError):
                    pass

        taker = raw.get("taker")
        if isinstance(taker, Mapping):
            try:
                result["taker_buy_ratio"] = float(taker["buy_ratio"]) / 100
                result["taker_buy_vol_usd"] = float(taker["buy_vol_usd"])
                result["taker_sell_vol_usd"] = float(taker["sell_vol_usd"])
            except (KeyError, TypeError, ValueError):
                pass
        return result


_client = CoinGlassClient()


def get_coinglass_client() -> CoinGlassClient:
    return _client
