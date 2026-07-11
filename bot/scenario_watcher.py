from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from .models import Signal
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

ScenarioKind = Literal["correction", "continuation"]
UpdateKind = Literal[
    "correction_started",
    "continuation_confirmed",
    "entry_short",
    "entry_long",
]

IMPULSE_SIGNAL_TYPES = frozenset({
    "impulse_pump",
    "impulse_dump",
    "trend_pump",
    "trend_dump",
    "vertical_pump",
    "vertical_dump",
    "mega_pump",
    "mega_dump",
    "reversal_pump",
    "reversal_dump",
    "liq_cascade_pump",
    "liq_cascade_dump",
    "short_squeeze",
})

MIN_AGE_BEFORE_FIRE_SEC = 90.0
TRIGGER_WATCH_DEFAULT_MIN_AGE_SEC = 12.0


@dataclass
class ScenarioWatch:
    exchange: str
    symbol: str
    side: str
    signal_type: str
    primary: ScenarioKind
    enroll_price: float
    local_high: float
    local_low: float
    correction_target: float | None
    continuation_target: float | None
    breakdown_level: float | None
    breakout_level: float | None
    initial_verdict: str
    coinglass_url: str
    started_at: float
    expires_at: float
    correction_fired: bool = False
    continuation_fired: bool = False
    entry_fired: bool = False
    enroll_high: float = 0.0
    trigger_only: bool = False

    def __post_init__(self) -> None:
        if self.enroll_high <= 0:
            self.enroll_high = self.local_high


@dataclass
class ScenarioUpdate:
    watch: ScenarioWatch
    kind: UpdateKind
    price: float
    move_pct: float
    reference_price: float


def primary_forecast_kind(ta: TAAnalysisResult) -> ScenarioKind | None:
    corr = ta.correction_path
    cont = ta.continuation_path
    if corr is None and cont is None:
        return None
    if corr and cont:
        return "correction" if corr.confidence >= cont.confidence else "continuation"
    if corr:
        return "correction"
    if cont:
        return "continuation"
    return None


def _forecast_target(ta: TAAnalysisResult, kind: ScenarioKind) -> float | None:
    path = ta.correction_path if kind == "correction" else ta.continuation_path
    if path is None or not path.waypoints:
        return None
    if kind == "correction" and len(path.waypoints) >= 3:
        return path.waypoints[2]
    return path.waypoints[-1]


def should_enroll_scenario_watch(
    signal: Signal,
    ta: TAAnalysisResult,
    *,
    enabled: bool = True,
) -> ScenarioKind | None:
    if not enabled:
        return None
    primary = primary_forecast_kind(ta)
    if primary is None:
        return None
    if ta.verdict != "WAIT" and not ta.post_pump and signal.signal_type not in IMPULSE_SIGNAL_TYPES:
        return None
    return primary


class ScenarioWatcher:
    def __init__(self) -> None:
        self._watches: dict[tuple[str, str], ScenarioWatch] = {}
        self._last_enroll_at: dict[tuple[str, str], float] = {}

    @property
    def active_count(self) -> int:
        return len(self._watches)

    def clear_all(self) -> None:
        self._watches.clear()

    def try_enroll(
        self,
        signal: Signal,
        ta: TAAnalysisResult,
        settings: Any,
    ) -> bool:
        if not getattr(settings, "scenario_watch_enabled", True):
            return False
        primary = should_enroll_scenario_watch(signal, ta, enabled=True)
        if primary is None:
            return False

        key = (signal.exchange.lower(), signal.symbol.upper())
        now = time.time()
        enroll_cd = int(getattr(settings, "scenario_watch_enroll_cooldown_seconds", 600))
        last = self._last_enroll_at.get(key, 0.0)
        if now - last < enroll_cd and key not in self._watches:
            return False

        price = ta.current_price or signal.current_price
        if price <= 0:
            return False

        watch_minutes = int(getattr(settings, "scenario_watch_minutes", 45))
        watch = ScenarioWatch(
            exchange=signal.exchange,
            symbol=signal.symbol,
            side=signal.side,
            signal_type=signal.signal_type,
            primary=primary,
            enroll_price=price,
            local_high=price,
            local_low=price,
            correction_target=_forecast_target(ta, "correction"),
            continuation_target=_forecast_target(ta, "continuation"),
            breakdown_level=ta.breakdown_level,
            breakout_level=ta.breakout_level,
            initial_verdict=ta.verdict,
            coinglass_url=signal.link,
            started_at=now,
            expires_at=now + watch_minutes * 60,
            enroll_high=price,
        )
        self._watches[key] = watch
        self._last_enroll_at[key] = now
        logger.info(
            "Scenario watch %s %s primary=%s target_corr=%s target_cont=%s",
            signal.exchange,
            signal.symbol,
            primary,
            fmt_price(watch.correction_target) if watch.correction_target else "-",
            fmt_price(watch.continuation_target) if watch.continuation_target else "-",
        )
        return True

    def try_enroll_quality_watch(
        self,
        signal: Signal,
        ta: TAAnalysisResult,
        settings: Any,
        *,
        quality_tier: str,
    ) -> bool:
        """WATCH-сигнал: следим за пробоем уровня → TRIGGER ENTRY."""
        if quality_tier != "watch":
            return False
        if not getattr(settings, "scenario_watch_enabled", True):
            return False
        side = (signal.side or "").lower()
        if side == "short" and not ta.breakdown_level:
            return False
        if side == "long" and not ta.breakout_level:
            return False

        key = (signal.exchange.lower(), signal.symbol.upper())
        now = time.time()
        enroll_cd = int(getattr(settings, "scenario_watch_enroll_cooldown_seconds", 600))
        last = self._last_enroll_at.get(key, 0.0)
        if now - last < enroll_cd and key not in self._watches:
            return False

        price = ta.current_price or signal.current_price
        if price <= 0:
            return False

        watch_minutes = int(getattr(settings, "scenario_watch_minutes", 45))
        watch = ScenarioWatch(
            exchange=signal.exchange,
            symbol=signal.symbol,
            side=signal.side,
            signal_type=signal.signal_type,
            primary="correction" if side == "short" else "continuation",
            enroll_price=price,
            local_high=price,
            local_low=price,
            correction_target=ta.target_prices[0] if ta.target_prices else None,
            continuation_target=ta.target_prices[0] if ta.target_prices else None,
            breakdown_level=ta.breakdown_level,
            breakout_level=ta.breakout_level,
            initial_verdict=ta.verdict,
            coinglass_url=signal.link,
            started_at=now,
            expires_at=now + watch_minutes * 60,
            enroll_high=price,
            trigger_only=True,
            correction_fired=True,
        )
        self._watches[key] = watch
        self._last_enroll_at[key] = now
        lvl = ta.breakdown_level if side == "short" else ta.breakout_level
        logger.info(
            "Trigger watch %s %s %s @ %s",
            signal.exchange,
            signal.symbol,
            side.upper(),
            fmt_price(lvl) if lvl else "-",
        )
        return True

    def tick(self, scanner: Any, settings: Any) -> list[ScenarioUpdate]:
        if not getattr(settings, "scenario_watch_enabled", True):
            return []
        if not self._watches:
            return []

        now = time.time()
        updates: list[ScenarioUpdate] = []
        pullback_pct = float(getattr(settings, "scenario_watch_pullback_pct", 3.0))
        continuation_pct = float(getattr(settings, "scenario_watch_continuation_pct", 0.8))
        zone_pct = float(getattr(settings, "scenario_watch_zone_pct", 0.45))

        for key, watch in list(self._watches.items()):
            if now >= watch.expires_at:
                self._watches.pop(key, None)
                continue

            snapshot = scanner.get_snapshot_for(watch.exchange, watch.symbol)
            if snapshot is None or snapshot.price is None or snapshot.price <= 0:
                continue

            price = float(snapshot.price)
            watch.local_high = max(watch.local_high, price)
            watch.local_low = min(watch.local_low, price)
            age = now - watch.started_at

            if watch.trigger_only:
                min_age = float(
                    getattr(
                        settings,
                        "scenario_watch_trigger_min_age_seconds",
                        TRIGGER_WATCH_DEFAULT_MIN_AGE_SEC,
                    )
                )
                if age < min_age:
                    continue
                batch = self._check_entry_ready(watch, price)
                updates.extend(batch)
                for upd in batch:
                    if upd.kind in {"entry_short", "entry_long"}:
                        self._watches.pop(key, None)
                        break
                continue

            if watch.primary == "correction":
                batch = self._check_correction_primary(
                    watch, price, age, pullback_pct, continuation_pct, zone_pct,
                )
            else:
                batch = self._check_continuation_primary(
                    watch, price, age, pullback_pct, continuation_pct, zone_pct,
                )
            updates.extend(batch)
            for upd in batch:
                if upd.kind == "continuation_confirmed" and watch.primary == "correction":
                    self._watches.pop(key, None)
                    break
                if upd.kind in {"entry_short", "entry_long"}:
                    self._watches.pop(key, None)
                    break

        return updates

    def _check_correction_primary(
        self,
        watch: ScenarioWatch,
        price: float,
        age: float,
        pullback_pct: float,
        continuation_pct: float,
        zone_pct: float,
    ) -> list[ScenarioUpdate]:
        out: list[ScenarioUpdate] = []

        if not watch.continuation_fired and price >= watch.enroll_high * (1.0 + continuation_pct / 100.0):
            watch.continuation_fired = True
            move = (price - watch.enroll_price) / watch.enroll_price * 100.0
            out.append(ScenarioUpdate(
                watch=watch,
                kind="continuation_confirmed",
                price=price,
                move_pct=move,
                reference_price=watch.enroll_high,
            ))
            return out

        if watch.correction_fired or age < MIN_AGE_BEFORE_FIRE_SEC:
            if watch.correction_fired and not watch.entry_fired:
                out.extend(self._check_entry_ready(watch, price))
            return out

        drop_from_high = (watch.local_high - price) / watch.local_high * 100.0 if watch.local_high > 0 else 0.0
        in_zone = False
        if watch.correction_target and watch.correction_target > 0:
            tol = watch.correction_target * zone_pct / 100.0
            in_zone = price <= watch.correction_target + tol

        if drop_from_high >= pullback_pct or in_zone:
            watch.correction_fired = True
            out.append(ScenarioUpdate(
                watch=watch,
                kind="correction_started",
                price=price,
                move_pct=drop_from_high,
                reference_price=watch.local_high,
            ))
            out.extend(self._check_entry_ready(watch, price))

        return out

    def _check_continuation_primary(
        self,
        watch: ScenarioWatch,
        price: float,
        age: float,
        pullback_pct: float,
        continuation_pct: float,
        zone_pct: float,
    ) -> list[ScenarioUpdate]:
        out: list[ScenarioUpdate] = []

        if not watch.continuation_fired and age >= MIN_AGE_BEFORE_FIRE_SEC:
            rise = (price - watch.enroll_price) / watch.enroll_price * 100.0 if watch.enroll_price > 0 else 0.0
            near_target = False
            if watch.continuation_target and watch.continuation_target > 0:
                tol = watch.continuation_target * zone_pct / 100.0
                near_target = price >= watch.continuation_target - tol
            if rise >= continuation_pct * 1.5 or near_target:
                watch.continuation_fired = True
                out.append(ScenarioUpdate(
                    watch=watch,
                    kind="continuation_confirmed",
                    price=price,
                    move_pct=rise,
                    reference_price=watch.enroll_price,
                ))

        if watch.continuation_fired or age < MIN_AGE_BEFORE_FIRE_SEC:
            if watch.continuation_fired and not watch.entry_fired:
                out.extend(self._check_entry_ready(watch, price))
            return out

        drop_from_high = (watch.local_high - price) / watch.local_high * 100.0 if watch.local_high > 0 else 0.0
        if drop_from_high >= pullback_pct:
            watch.correction_fired = True
            out.append(ScenarioUpdate(
                watch=watch,
                kind="correction_started",
                price=price,
                move_pct=drop_from_high,
                reference_price=watch.local_high,
            ))

        return out

    def _check_entry_ready(self, watch: ScenarioWatch, price: float) -> list[ScenarioUpdate]:
        if watch.entry_fired:
            return []

        out: list[ScenarioUpdate] = []
        if watch.breakdown_level and price <= watch.breakdown_level:
            watch.entry_fired = True
            move = (price - watch.enroll_price) / watch.enroll_price * 100.0
            out.append(ScenarioUpdate(
                watch=watch,
                kind="entry_short",
                price=price,
                move_pct=move,
                reference_price=watch.breakdown_level,
            ))
        elif watch.breakout_level and price >= watch.breakout_level:
            watch.entry_fired = True
            move = (price - watch.enroll_price) / watch.enroll_price * 100.0
            out.append(ScenarioUpdate(
                watch=watch,
                kind="entry_long",
                price=price,
                move_pct=move,
                reference_price=watch.breakout_level,
            ))
        return out
