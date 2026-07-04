from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .liquidation_analysis import LiquidationAnalysisResult

logger = logging.getLogger(__name__)

OUTCOME_CHECKPOINTS_MIN = (15, 30, 60)
PENDING_KEY = "analysis:pending"
OUTCOMES_KEY = "analysis:outcomes"
WEIGHTS_KEY = "analysis:weights"
OUTCOMES_MAX = 2000
MIN_OUTCOMES_FOR_WEIGHTS = 20
SUCCESS_MOVE_PCT = 1.0


@dataclass(frozen=True)
class AnalysisOutcomeSummary:
    total_completed: int
    success_60m: int
    success_30m: int
    success_15m: int
    pending: int
    success_rate_60m: float | None
    success_rate_30m: float | None
    days: int


class AnalysisOutcomeTracker:
    """Проверка исходов post-liquidation разборов через 15/30/60 мин."""

    def __init__(self, redis: Any, scanner: Any) -> None:
        self.redis = redis
        self.scanner = scanner
        self._cached_weights: dict[str, float] | None = None

    async def schedule(self, result: LiquidationAnalysisResult) -> None:
        if self.redis is None:
            return
        record = {
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "exchange": result.exchange,
            "symbol": result.symbol,
            "direction": result.direction,
            "confidence": result.confidence,
            "entry_price": result.current_price,
            "invalidation_price": result.invalidation_price,
            "cluster_usd": result.cluster_usd,
            "factors": {f.key: round(f.score, 3) for f in result.factors},
            "checkpoints": {str(m): None for m in OUTCOME_CHECKPOINTS_MIN},
            "next_check_min": OUTCOME_CHECKPOINTS_MIN[0],
            "next_check_at": time.time() + OUTCOME_CHECKPOINTS_MIN[0] * 60,
        }
        try:
            await self.redis.hset(
                PENDING_KEY,
                record["id"],
                json.dumps(record, ensure_ascii=False),
            )
        except Exception:
            logger.exception("Failed to schedule analysis outcome tracking")

    async def run_loop(self, interval: float = 30.0) -> None:
        while True:
            await self._process_due()
            await asyncio.sleep(interval)

    async def get_summary(self, days: int = 7) -> AnalysisOutcomeSummary:
        if self.redis is None:
            return AnalysisOutcomeSummary(0, 0, 0, 0, 0, None, None, days)

        cutoff = time.time() - days * 86400
        pending = 0
        try:
            pending = await self.redis.hlen(PENDING_KEY)
            raw_list = await self.redis.lrange(OUTCOMES_KEY, -OUTCOMES_MAX, -1)
        except Exception:
            logger.exception("Failed to read analysis outcomes summary")
            return AnalysisOutcomeSummary(0, 0, 0, 0, pending, None, None, days)

        total = success_60 = success_30 = success_15 = 0
        for raw in raw_list:
            try:
                record = json.loads(raw)
            except Exception:
                continue
            if float(record.get("ts", 0)) < cutoff:
                continue
            total += 1
            flags = record.get("success_flags", {})
            if flags.get("60"):
                success_60 += 1
            if flags.get("30"):
                success_30 += 1
            if flags.get("15"):
                success_15 += 1

        rate_60 = round(success_60 / total * 100, 1) if total else None
        rate_30 = round(success_30 / total * 100, 1) if total else None
        return AnalysisOutcomeSummary(
            total_completed=total,
            success_60m=success_60,
            success_30m=success_30,
            success_15m=success_15,
            pending=pending,
            success_rate_60m=rate_60,
            success_rate_30m=rate_30,
            days=days,
        )

    async def get_adaptive_weights(self) -> dict[str, float] | None:
        if self._cached_weights is not None:
            return self._cached_weights
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(WEIGHTS_KEY)
            if raw:
                self._cached_weights = json.loads(raw)
                return self._cached_weights
        except Exception:
            logger.exception("Failed to load adaptive analysis weights")
        return None

    async def _process_due(self) -> None:
        if self.redis is None:
            return
        try:
            raw_map = await self.redis.hgetall(PENDING_KEY)
        except Exception:
            logger.exception("Failed to read pending analysis outcomes")
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

        direction = record.get("direction", "long")
        invalidation = float(record.get("invalidation_price") or 0)
        success = self._is_success(direction, change_pct, current_price, invalidation)
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
            logger.exception("Failed to persist analysis outcome")
        await self.redis.hdel(PENDING_KEY, record_id)
        await self._refresh_adaptive_weights()

        logger.info(
            "Analysis outcome %s %s %s | conf %.0f%% | 60m %+.2f%% | success=%s",
            exchange,
            symbol,
            direction,
            float(record.get("confidence") or 0),
            change_pct,
            record.get("success_flags", {}).get("60"),
        )

    @staticmethod
    def _is_success(
        direction: str,
        change_pct: float,
        current_price: float,
        invalidation: float,
    ) -> bool:
        if direction == "long":
            if invalidation > 0 and current_price < invalidation:
                return False
            return change_pct >= SUCCESS_MOVE_PCT
        if invalidation > 0 and current_price > invalidation:
            return False
        return change_pct <= -SUCCESS_MOVE_PCT

    async def _refresh_adaptive_weights(self) -> None:
        if self.redis is None:
            return
        try:
            raw_list = await self.redis.lrange(OUTCOMES_KEY, -OUTCOMES_MAX, -1)
        except Exception:
            return

        records: list[dict[str, Any]] = []
        for raw in raw_list:
            try:
                records.append(json.loads(raw))
            except Exception:
                continue

        if len(records) < MIN_OUTCOMES_FOR_WEIGHTS:
            return

        factor_keys: set[str] = set()
        for rec in records:
            factor_keys.update((rec.get("factors") or {}).keys())
        if not factor_keys:
            return

        base_weight = 1.0 / len(factor_keys)
        weights: dict[str, float] = {k: base_weight for k in factor_keys}
        success_scores: dict[str, list[float]] = {k: [] for k in factor_keys}
        fail_scores: dict[str, list[float]] = {k: [] for k in factor_keys}

        for rec in records:
            factors = rec.get("factors") or {}
            won = bool((rec.get("success_flags") or {}).get("60"))
            bucket = success_scores if won else fail_scores
            for key, score in factors.items():
                if key in bucket:
                    bucket[key].append(float(score))

        for key in factor_keys:
            wins = success_scores[key]
            fails = fail_scores[key]
            if not wins and not fails:
                continue
            avg_win = sum(wins) / len(wins) if wins else 0.5
            avg_fail = sum(fails) / len(fails) if fails else 0.5
            delta = avg_win - avg_fail
            weights[key] = max(0.03, base_weight * (1.0 + delta * 1.5))

        total = sum(weights.values())
        if total <= 0:
            return
        normalized = {k: round(v / total, 4) for k, v in weights.items()}
        try:
            await self.redis.set(WEIGHTS_KEY, json.dumps(normalized, ensure_ascii=False))
            self._cached_weights = normalized
            logger.info("Adaptive analysis weights updated from %d outcomes", len(records))
        except Exception:
            logger.exception("Failed to save adaptive analysis weights")
