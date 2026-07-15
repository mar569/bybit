"""CVD по taker-сделкам Bybit: WebSocket publicTrade (live) + REST recent-trade."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

import aiohttp
from websockets import connect

logger = logging.getLogger(__name__)

BYBIT_RECENT_TRADE_URL = "https://api.bybit.com/v5/market/recent-trade"
BYBIT_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"
WS_SUBSCRIBE_BATCH = 50
SYMBOL_REFRESH_SECONDS = 120
MAX_TRADES_PER_SYMBOL = 12_000


@dataclass(frozen=True)
class TakerCvdSnapshot:
    buy_volume: float
    sell_volume: float
    delta: float
    ratio: float
    trade_count: int
    window_minutes: float
    detail: str = ""
    source: str = "taker"

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "cvd_source": self.source,
            "cvd_buy_volume": round(self.buy_volume, 4),
            "cvd_sell_volume": round(self.sell_volume, 4),
            "cvd_delta": round(self.delta, 4),
            "cvd_ratio": round(self.ratio, 4),
            "cvd_trade_count": self.trade_count,
            "cvd_window_minutes": self.window_minutes,
        }


def _format_delta(delta: float) -> str:
    ad = abs(delta)
    if ad >= 1_000_000:
        return f"{delta / 1_000_000:.2f}M"
    if ad >= 1_000:
        return f"{delta / 1_000:.1f}K"
    if ad >= 1:
        return f"{delta:.2f}"
    return f"{delta:.4f}"


def summarize_taker_cvd(
    buy_volume: float,
    sell_volume: float,
    *,
    trade_count: int,
    window_minutes: float,
    source: str = "taker",
) -> TakerCvdSnapshot:
    total = buy_volume + sell_volume
    delta = buy_volume - sell_volume
    ratio = buy_volume / total if total > 0 else 0.5
    d_str = _format_delta(delta)
    tag = "live" if source == "live" else "taker"
    if ratio >= 0.62:
        detail = (
            f"CVD↑ {tag}-buy {ratio:.0%} · Δ+{d_str} "
            f"({trade_count} сд., ~{window_minutes:.0f}м)"
        )
    elif ratio <= 0.38:
        detail = (
            f"CVD↓ {tag}-sell {(1 - ratio):.0%} · Δ{d_str} "
            f"({trade_count} сд., ~{window_minutes:.0f}м)"
        )
    else:
        detail = (
            f"CVD {tag} {ratio:.0%} buy · Δ{d_str} "
            f"({trade_count} сд., ~{window_minutes:.0f}м)"
        )
    return TakerCvdSnapshot(
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        delta=delta,
        ratio=ratio,
        trade_count=trade_count,
        window_minutes=window_minutes,
        detail=detail,
        source=source,
    )


def aggregate_taker_trades(
    trades: list[dict],
    *,
    cutoff_ms: int,
) -> tuple[float, float, int, float]:
    """Сумма taker buy/sell с cutoff по времени. window_minutes — фактический охват."""
    buy = 0.0
    sell = 0.0
    count = 0
    oldest_ms = 0
    now_ms = int(time.time() * 1000)
    for row in trades:
        try:
            ts = int(row.get("time") or row.get("T") or 0)
            size = float(row.get("size") or row.get("v") or 0)
            side = str(row.get("side") or row.get("S") or "").lower()
        except (TypeError, ValueError):
            continue
        if ts < cutoff_ms or size <= 0:
            continue
        count += 1
        if oldest_ms == 0 or ts < oldest_ms:
            oldest_ms = ts
        if side == "buy":
            buy += size
        elif side == "sell":
            sell += size
    if count == 0:
        return 0.0, 0.0, 0, 0.0
    span_min = max(1.0, (now_ms - oldest_ms) / 60_000.0)
    return buy, sell, count, span_min


def build_taker_cvd_snapshot(
    trades: list[dict],
    *,
    lookback_minutes: float,
    min_trades: int = 25,
    source: str = "taker",
) -> TakerCvdSnapshot | None:
    if not trades:
        return None
    cutoff_ms = int(time.time() * 1000) - int(lookback_minutes * 60 * 1000)
    buy, sell, count, span_min = aggregate_taker_trades(trades, cutoff_ms=cutoff_ms)
    if count < min_trades or (buy + sell) <= 0:
        return None
    return summarize_taker_cvd(
        buy,
        sell,
        trade_count=count,
        window_minutes=min(lookback_minutes, span_min),
        source=source,
    )


class BybitTakerCvdLiveTracker:
    """WebSocket publicTrade.{symbol} — накопление taker CVD в реальном времени."""

    def __init__(
        self,
        get_symbols: Callable[[], list[str]],
        *,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._get_symbols = get_symbols
        self._enabled = enabled or (lambda: True)
        self._running = False
        self._trades: dict[str, deque[tuple[float, str, float]]] = defaultdict(
            lambda: deque(maxlen=MAX_TRADES_PER_SYMBOL)
        )
        self._subscribed: set[str] = set()
        self._last_symbol_refresh = 0.0
        self._lock = asyncio.Lock()

    def record_trade(self, symbol: str, *, ts_ms: int, side: str, size: float) -> None:
        symbol = symbol.upper()
        side_norm = str(side).lower()
        if side_norm not in {"buy", "sell"} or size <= 0:
            return
        self._trades[symbol].append((ts_ms / 1000.0, side_norm, size))

    def ingest_rest_trades(self, symbol: str, trades: list[dict]) -> None:
        for row in trades:
            try:
                ts = int(row.get("time") or row.get("T") or 0)
                size = float(row.get("size") or row.get("v") or 0)
                side = str(row.get("side") or row.get("S") or "")
            except (TypeError, ValueError):
                continue
            if ts > 0 and size > 0:
                self.record_trade(symbol, ts_ms=ts, side=side, size=size)

    def build_snapshot(
        self,
        symbol: str,
        *,
        lookback_minutes: float,
        min_trades: int = 25,
    ) -> TakerCvdSnapshot | None:
        symbol = symbol.upper()
        cutoff = time.time() - lookback_minutes * 60.0
        buy = 0.0
        sell = 0.0
        count = 0
        oldest_ts = 0.0
        for ts, side, size in self._trades.get(symbol, ()):
            if ts < cutoff:
                continue
            count += 1
            if oldest_ts == 0.0 or ts < oldest_ts:
                oldest_ts = ts
            if side == "buy":
                buy += size
            else:
                sell += size
        if count < min_trades or (buy + sell) <= 0:
            return None
        span_min = max(1.0, (time.time() - oldest_ts) / 60.0) if oldest_ts else lookback_minutes
        return summarize_taker_cvd(
            buy,
            sell,
            trade_count=count,
            window_minutes=min(lookback_minutes, span_min),
            source="live",
        )

    async def run(self) -> None:
        self._running = True
        while self._running:
            if not self._enabled():
                await asyncio.sleep(2)
                continue
            if not self._get_symbols():
                await asyncio.sleep(3)
                continue
            try:
                async with connect(
                    BYBIT_WS_LINEAR,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**22,
                ) as websocket:
                    logger.info("Bybit taker CVD websocket connected")
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
                logger.warning("Bybit taker CVD websocket disconnected: %s", exc)
                await asyncio.sleep(5)

    async def _subscribe_symbols(self, websocket: object) -> None:
        symbols = [s.upper() for s in self._get_symbols() if s]
        if not symbols:
            return
        desired = set(symbols)
        if desired == self._subscribed:
            return
        topics = [f"publicTrade.{symbol}" for symbol in sorted(desired)]
        for index in range(0, len(topics), WS_SUBSCRIBE_BATCH):
            batch = topics[index : index + WS_SUBSCRIBE_BATCH]
            await websocket.send(json.dumps({"op": "subscribe", "args": batch}))
            await asyncio.sleep(0.05)
        self._subscribed = desired
        logger.info("Bybit taker CVD subscribed to %d symbols", len(self._subscribed))

    async def _handle_message(self, raw_message: str) -> None:
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return
        topic = data.get("topic", "")
        if not topic.startswith("publicTrade."):
            return
        payload = data.get("data")
        rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
        async with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("s") or topic.split(".", 1)[-1]).upper()
                try:
                    ts_ms = int(row.get("T") or row.get("time") or 0)
                    size = float(row.get("v") or row.get("size") or 0)
                    side = str(row.get("S") or row.get("side") or "")
                except (TypeError, ValueError):
                    continue
                if ts_ms > 0 and size > 0:
                    self.record_trade(symbol, ts_ms=ts_ms, side=side, size=size)

    def stop(self) -> None:
        self._running = False


class BybitTakerCvdCache:
    """Live WS → CVD; fallback REST recent-trade (до 1000 сделок)."""

    def __init__(self, ttl_seconds: float = 8.0) -> None:
        self._ttl = ttl_seconds
        self._trade_cache: dict[str, tuple[float, list[dict]]] = {}
        self._cvd_cache: dict[str, tuple[float, TakerCvdSnapshot | None]] = {}
        self._lock = asyncio.Lock()
        self._live: BybitTakerCvdLiveTracker | None = None

    def attach_live(self, tracker: BybitTakerCvdLiveTracker) -> None:
        self._live = tracker

    def peek_live_cvd(
        self,
        symbol: str,
        *,
        lookback_minutes: float = 10.0,
        min_trades: int = 25,
    ) -> TakerCvdSnapshot | None:
        """Синхронный peek live CVD (без REST) — для ранних детекторов сканера."""
        if self._live is None:
            return None
        return self._live.build_snapshot(
            symbol, lookback_minutes=lookback_minutes, min_trades=min_trades,
        )

    async def get_cvd(
        self,
        symbol: str,
        *,
        lookback_minutes: float = 60.0,
        category: str = "linear",
    ) -> TakerCvdSnapshot | None:
        symbol = symbol.upper()
        cache_key = f"{symbol}:{int(lookback_minutes)}"
        now = time.time()
        cached = self._cvd_cache.get(cache_key)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        async with self._lock:
            cached = self._cvd_cache.get(cache_key)
            if cached and now - cached[0] < self._ttl:
                return cached[1]

            snap: TakerCvdSnapshot | None = None
            if self._live is not None:
                snap = self._live.build_snapshot(symbol, lookback_minutes=lookback_minutes)

            if snap is None:
                trades = await self._get_trades(symbol, category=category)
                if self._live is not None and trades:
                    self._live.ingest_rest_trades(symbol, trades)
                    snap = self._live.build_snapshot(symbol, lookback_minutes=lookback_minutes)
                if snap is None:
                    snap = build_taker_cvd_snapshot(trades, lookback_minutes=lookback_minutes)

            self._cvd_cache[cache_key] = (time.time(), snap)
            return snap

    async def _get_trades(self, symbol: str, *, category: str) -> list[dict]:
        symbol = symbol.upper()
        now = time.time()
        cached = self._trade_cache.get(symbol)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        params = {"category": category, "symbol": symbol, "limit": 1000}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(BYBIT_RECENT_TRADE_URL, params=params, timeout=15) as resp:
                    data = await resp.json()
        except Exception:
            logger.warning("Bybit recent-trade failed for %s", symbol, exc_info=True)
            return cached[1] if cached else []

        if data.get("retCode") != 0:
            logger.debug("Bybit recent-trade %s: %s", symbol, data.get("retMsg"))
            return cached[1] if cached else []

        trades = list(data.get("result", {}).get("list", []))
        self._trade_cache[symbol] = (time.time(), trades)
        return trades


_taker_cvd_cache = BybitTakerCvdCache(ttl_seconds=8.0)


def get_taker_cvd_cache() -> BybitTakerCvdCache:
    return _taker_cvd_cache
