from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict
from typing import Any

from .models import Signal

logger = logging.getLogger(__name__)

OUTCOME_CHECKPOINTS_MIN = (15, 30, 60)
PENDING_KEY = "prob:pending"
OUTCOMES_KEY = "prob:outcomes"
OUTCOMES_MAX = 2000


class OutcomeTracker:
    def __init__(self, redis: Any, scanner: Any) -> None:
        self.redis = redis
        self.scanner = scanner

    async def schedule(self, signal: Signal) -> None:
        if self.redis is None:
            return
        record = {
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "exchange": signal.exchange,
            "symbol": signal.symbol,
            "side": signal.side,
            "signal_type": signal.signal_type,
            "entry_price": signal.current_price,
            "probability_percent": signal.details.get("probability_percent"),
            "oi_change_percent": signal.oi_change_percent,
            "price_change_percent": signal.price_change_percent,
            "checkpoints": {str(m): None for m in OUTCOME_CHECKPOINTS_MIN},
            "next_check_min": OUTCOME_CHECKPOINTS_MIN[0],
            "next_check_at": time.time() + OUTCOME_CHECKPOINTS_MIN[0] * 60,
        }
        try:
            await self.redis.hset(PENDING_KEY, record["id"], json.dumps(record, ensure_ascii=False))
        except Exception:
            logger.exception("Failed to schedule outcome tracking")

    async def run_loop(self, interval: float = 30.0) -> None:
        while True:
            await self._process_due()
            await asyncio.sleep(interval)

    async def _process_due(self) -> None:
        if self.redis is None:
            return
        try:
            raw_map = await self.redis.hgetall(PENDING_KEY)
        except Exception:
            logger.exception("Failed to read pending outcomes")
            return

        now = time.time()
        for record_id, raw in raw_map.items():
            try:
                record = json.loads(raw)
            except Exception:
                await self.redis.hdel(PENDING_KEY, record_id)
                continue

            if now < float(record.get("next_check_at", 0)):
                continue

            await self._evaluate_checkpoint(record_id, record)

    async def _evaluate_checkpoint(self, record_id: str, record: dict[str, Any]) -> None:
        exchange = record["exchange"]
        symbol = record["symbol"]
        entry = record.get("entry_price")
        if not entry:
            await self.redis.hdel(PENDING_KEY, record_id)
            return

        snapshot = self.scanner.get_snapshot_for(exchange, symbol)
        if snapshot is None or snapshot.price is None:
            record["next_check_at"] = time.time() + 60
            await self.redis.hset(PENDING_KEY, record_id, json.dumps(record, ensure_ascii=False))
            return

        current_price = snapshot.price
        change_pct = (current_price - entry) / entry * 100.0
        min_key = str(int(record["next_check_min"]))
        record["checkpoints"][min_key] = round(change_pct, 3)

        side = record.get("side", "long")
        if side == "long":
            success = change_pct >= 2.0 and change_pct > -1.0
        else:
            success = change_pct <= -2.0 and change_pct < 1.0
        record.setdefault("success_flags", {})[min_key] = success

        idx = OUTCOME_CHECKPOINTS_MIN.index(int(record["next_check_min"]))
        if idx + 1 < len(OUTCOME_CHECKPOINTS_MIN):
            nxt = OUTCOME_CHECKPOINTS_MIN[idx + 1]
            record["next_check_min"] = nxt
            record["next_check_at"] = float(record["ts"]) + nxt * 60
            await self.redis.hset(PENDING_KEY, record_id, json.dumps(record, ensure_ascii=False))
            return

        record["completed_at"] = time.time()
        record["final_price"] = current_price
        try:
            await self.redis.rpush(OUTCOMES_KEY, json.dumps(record, ensure_ascii=False))
            await self.redis.ltrim(OUTCOMES_KEY, -OUTCOMES_MAX, -1)
        except Exception:
            logger.exception("Failed to persist outcome")
        await self.redis.hdel(PENDING_KEY, record_id)

        prob = record.get("probability_percent")
        logger.info(
            "Outcome %s %s %s | prob %s | 60m %+.2f%% | success=%s",
            exchange,
            symbol,
            record.get("signal_type"),
            prob,
            change_pct,
            record.get("success_flags", {}).get("60"),
        )

    async def fade_type_stats(self, signal_type: str, *, limit: int = 200) -> tuple[int, float] | None:
        """Winrate по типу сигнала (60m checkpoint) для feedback loop."""
        if self.redis is None or signal_type not in {
            "reversal_dump", "impulse_dump", "trend_dump", "vertical_dump",
            "reversal_pump", "impulse_pump", "trend_pump", "vertical_pump",
        }:
            return None
        try:
            raw_list = await self.redis.lrange(OUTCOMES_KEY, -limit, -1)
        except Exception:
            return None
        wins = 0
        total = 0
        for raw in raw_list:
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if rec.get("signal_type") != signal_type:
                continue
            flags = rec.get("success_flags") or {}
            if "60" not in flags:
                continue
            total += 1
            if flags.get("60"):
                wins += 1
        if total == 0:
            return None
        return total, wins / total * 100.0