"""Unified derivatives context: CoinGlass when keyed, else PerpFinder + Bybit (default)."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

from .bybit_market_data import BybitAccountRatioCache, account_ratio_to_dict
from .coinglass_api import get_coinglass_client
from .perpfinder_api import get_perpfinder_client

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"
_BYBIT_OI = "https://api.bybit.com/v5/market/open-interest"

_account_ratio_cache = BybitAccountRatioCache()


def market_flow_provider() -> str:
    """auto | coinglass | perpfinder — auto prefers CoinGlass when COINGLASS_API_KEY is set."""
    raw = os.getenv("MARKET_FLOW_PROVIDER", "auto").strip().lower()
    if raw in {"coinglass", "perpfinder"}:
        return raw
    return "auto"


async def market_context(
    symbol: str,
    *,
    interval_minutes: int,
    exchange: str = "Bybit",
) -> dict[str, Any]:
    symbol = symbol.upper().replace("/", "")
    provider = market_flow_provider()
    cg = get_coinglass_client()
    use_coinglass = provider == "coinglass" or (provider == "auto" and cg.configured)
    if use_coinglass and cg.configured:
        data = await cg.market_context(
            symbol,
            interval_minutes=interval_minutes,
            exchange=exchange,
        )
        data.setdefault("flow_provider", "coinglass")
        return data
    if provider == "coinglass" and not cg.configured:
        logger.warning("MARKET_FLOW_PROVIDER=coinglass but COINGLASS_API_KEY missing; using PerpFinder+Bybit")
    return await _perpfinder_bybit_context(
        symbol,
        interval_minutes=interval_minutes,
        exchange=exchange,
    )


async def _perpfinder_bybit_context(
    symbol: str,
    *,
    interval_minutes: int,
    exchange: str,
) -> dict[str, Any]:
    coin = symbol.removesuffix("USDT").removesuffix("USDC").removesuffix("USD")
    venue = exchange.title()
    pf_task = asyncio.create_task(
        get_perpfinder_client().venue_snapshot(coin, venue=venue)
    )
    bybit_task = asyncio.create_task(
        _bybit_price_oi_change(symbol, interval_minutes=interval_minutes)
    )
    ratio_task = asyncio.create_task(_account_ratio_cache.get_ratio(symbol))

    pf_snap, bybit_flow, ratio_snap = await asyncio.gather(
        pf_task, bybit_task, ratio_task, return_exceptions=True
    )

    metrics: dict[str, Any] = {}
    available: list[str] = []
    missing: list[str] = []

    if isinstance(bybit_flow, dict):
        for key in ("price_change_pct", "oi_change_pct", "oi_period_minutes", "oi_usd"):
            if bybit_flow.get(key) is not None:
                metrics[key] = bybit_flow[key]
        if metrics.get("price_change_pct") is not None:
            available.append("price")
        else:
            missing.append("price")
        if metrics.get("oi_change_pct") is not None:
            available.append("oi")
        else:
            missing.append("oi")
    else:
        missing.extend(["price", "oi"])
        if isinstance(bybit_flow, Exception):
            logger.debug("Bybit price/OI flow failed for %s: %s", symbol, bybit_flow)

    if isinstance(pf_snap, dict) and pf_snap.get("available"):
        if pf_snap.get("funding_rate") is not None:
            metrics["funding_rate"] = pf_snap["funding_rate"]
            available.append("funding")
        else:
            missing.append("funding")
        if metrics.get("oi_usd") is None and pf_snap.get("oi_usd") is not None:
            metrics["oi_usd"] = pf_snap["oi_usd"]
    else:
        missing.append("funding")
        if isinstance(pf_snap, Exception):
            logger.debug("PerpFinder snapshot failed for %s: %s", symbol, pf_snap)

    if ratio_snap is not None and not isinstance(ratio_snap, Exception):
        metrics["account_ratio"] = account_ratio_to_dict(ratio_snap)
        available.append("account_ratio")
    else:
        missing.append("account_ratio")

    for optional in (
        "liquidations",
        "taker",
        "futures_taker_history",
        "spot_taker_history",
    ):
        missing.append(optional)

    status = "данные получены" if available else "данных нет"
    if available and missing:
        status = "данные частичные"

    metrics.update({
        "source": "PerpFinder + Bybit",
        "flow_provider": "perpfinder",
        "coinglass_status": status,
        "coinglass_available_metrics": available,
        "coinglass_missing_metrics": [m for m in missing if m not in available],
    })
    return metrics


async def _bybit_price_oi_change(symbol: str, *, interval_minutes: int) -> dict[str, Any]:
    bars = max(int(interval_minutes) // 5, 1)
    period_minutes = bars * 5
    kline_params = {
        "category": "linear",
        "symbol": symbol,
        "interval": "5",
        "limit": min(30, bars + 5),
    }
    oi_params = {
        "category": "linear",
        "symbol": symbol,
        "intervalTime": "5min",
        "limit": min(30, bars + 5),
    }
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        k_resp, oi_resp = await asyncio.gather(
            session.get(_BYBIT_KLINE, params=kline_params),
            session.get(_BYBIT_OI, params=oi_params),
        )
        kdata = await k_resp.json(content_type=None)
        oidata = await oi_resp.json(content_type=None)

    if str(kdata.get("retCode")) != "0" or str(oidata.get("retCode")) != "0":
        return {}

    klines = list(reversed(kdata.get("result", {}).get("list", [])))
    oilist = list(reversed(oidata.get("result", {}).get("list", [])))
    if len(klines) < bars + 1 or len(oilist) < bars + 1:
        return {}

    try:
        now_close = float(klines[-1][4])
        ago_close = float(klines[-1 - bars][4])
        oi_now = float(oilist[-1].get("openInterest", 0))
        oi_ago = float(oilist[-1 - bars].get("openInterest", 0))
    except (IndexError, TypeError, ValueError):
        return {}

    if ago_close <= 0 or oi_ago <= 0:
        return {}

    price_change_pct = (now_close - ago_close) / ago_close * 100.0
    oi_val_now = oi_now * now_close
    oi_val_ago = oi_ago * ago_close
    oi_change_pct = (oi_val_now - oi_val_ago) / oi_val_ago * 100.0
    return {
        "price_change_pct": price_change_pct,
        "oi_change_pct": oi_change_pct,
        "oi_period_minutes": period_minutes,
        "oi_usd": oi_val_now,
    }
