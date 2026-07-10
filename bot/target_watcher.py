"""Уведомления при достижении TP / стопа по playbook."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from .ta_analysis import fmt_price
from .trade_playbook import TradePlaybook

logger = logging.getLogger(__name__)

PENDING_KEY = "playbook:pending"
CHECK_INTERVAL_SEC = 8.0
MAX_WATCH_HOURS = 6.0


class TargetWatcher:
    def __init__(
        self,
        redis: Any,
        scanner: Any,
        *,
        notify: Callable[[str], Awaitable[None]],
    ) -> None:
        self.redis = redis
        self.scanner = scanner
        self._notify = notify

    async def schedule(
        self,
        *,
        exchange: str,
        symbol: str,
        playbook: TradePlaybook,
        signal_type: str,
        entry_price: float | None,
    ) -> None:
        if self.redis is None or not playbook.target_prices:
            return
        record = {
            "id": str(uuid.uuid4()),
            "exchange": exchange,
            "symbol": symbol.upper(),
            "side": playbook.side,
            "signal_type": signal_type,
            "entry_price": entry_price or playbook.entry_price,
            "stop_price": playbook.stop_price,
            "targets": playbook.target_prices[:3],
            "hits": [],
            "stopped": False,
            "created_at": time.time(),
            "expires_at": time.time() + MAX_WATCH_HOURS * 3600,
            "next_check_at": time.time() + CHECK_INTERVAL_SEC,
        }
        try:
            await self.redis.hset(
                PENDING_KEY,
                record["id"],
                json.dumps(record, ensure_ascii=False),
            )
        except Exception:
            logger.exception("TargetWatcher schedule failed %s", symbol)

    async def run_loop(self, interval: float = CHECK_INTERVAL_SEC) -> None:
        while True:
            await self._process_due()
            await asyncio.sleep(interval)

    async def _process_due(self) -> None:
        if self.redis is None:
            return
        try:
            raw_map = await self.redis.hgetall(PENDING_KEY)
        except Exception:
            logger.exception("TargetWatcher read failed")
            return

        now = time.time()
        for record_id, raw in raw_map.items():
            try:
                record = json.loads(raw)
            except Exception:
                await self.redis.hdel(PENDING_KEY, record_id)
                continue
            if now >= float(record.get("expires_at", 0)):
                await self.redis.hdel(PENDING_KEY, record_id)
                continue
            if now < float(record.get("next_check_at", 0)):
                continue
            done = await self._evaluate(record_id, record)
            if done:
                await self.redis.hdel(PENDING_KEY, record_id)
            else:
                record["next_check_at"] = now + CHECK_INTERVAL_SEC
                await self.redis.hset(
                    PENDING_KEY,
                    record_id,
                    json.dumps(record, ensure_ascii=False),
                )

    async def _evaluate(self, record_id: str, record: dict[str, Any]) -> bool:
        exchange = record["exchange"]
        symbol = record["symbol"]
        snapshot = self.scanner.get_snapshot_for(exchange, symbol)
        if snapshot is None or snapshot.price is None:
            return False

        price = float(snapshot.price)
        side = record.get("side", "long")
        stop = record.get("stop_price")
        targets: list[float] = record.get("targets") or []
        hits: list[int] = list(record.get("hits") or [])

        if not record.get("stopped") and stop:
            stop_f = float(stop)
            if side == "long" and price <= stop_f:
                await self._notify_hit(symbol, side, "stop", price, stop_f, 0)
                record["stopped"] = True
                return True
            if side == "short" and price >= stop_f:
                await self._notify_hit(symbol, side, "stop", price, stop_f, 0)
                record["stopped"] = True
                return True

        for idx, tgt in enumerate(targets, start=1):
            if idx in hits:
                continue
            tgt_f = float(tgt)
            reached = (side == "long" and price >= tgt_f) or (side == "short" and price <= tgt_f)
            if not reached:
                continue
            hits.append(idx)
            record["hits"] = hits
            await self._notify_hit(symbol, side, "tp", price, tgt_f, idx)
            if idx >= len(targets):
                return True
        return False

    async def _notify_hit(
        self,
        symbol: str,
        side: str,
        kind: str,
        price: float,
        level: float,
        tp_index: int,
    ) -> None:
        label = "LONG" if side == "long" else "SHORT"
        if kind == "stop":
            text = (
                f"⚠️ <b>Стоп</b> · <b>{symbol}</b> · {label}\n"
                f"Цена <b>{fmt_price(price)}</b> · стоп <b>{fmt_price(level)}</b>\n"
                f"<i>Сценарий отменён</i>"
            )
        else:
            ordinals = {1: "Первая", 2: "Вторая", 3: "Третья"}
            ord_label = ordinals.get(tp_index, f"TP{tp_index}")
            text = (
                f"✅ <b>{ord_label} цель</b> · <b>{symbol}</b> · {label}\n"
                f"Цена <b>{fmt_price(price)}</b> · цель <b>{fmt_price(level)}</b>"
            )
        try:
            await self._notify(text)
        except Exception:
            logger.exception("TargetWatcher notify failed %s", symbol)
