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
    "cancelled_late",
    "cancelled_opposite",
    "cancelled_user",
    "expired",
]

IMPULSE_SIGNAL_TYPES = frozenset({
    "impulse_pump",
    "impulse_dump",
    "trend_pump",
    "trend_dump",
    "trend_seed",
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
    # Ручная подписка: пользователь нажал «Ждать LONG/SHORT»
    user_intent: str = ""  # long | short | ""
    zone_low: float | None = None
    zone_high: float | None = None
    stop_hint: float | None = None
    target_hints: tuple[float, ...] = ()
    late_cancel_pct: float = 1.5
    opposite_cancel_pct: float = 1.2
    confirm_buffer_pct: float = 0.08  # закрепление за уровнем
    chat_id: int | None = None

    def __post_init__(self) -> None:
        if self.enroll_high <= 0:
            self.enroll_high = self.local_high
        self.user_intent = (self.user_intent or "").lower()
        if self.user_intent not in {"long", "short", ""}:
            self.user_intent = ""

    @property
    def is_user_watch(self) -> bool:
        return self.user_intent in {"long", "short"}


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

    def cancel_watch(self, exchange: str, symbol: str) -> ScenarioWatch | None:
        key = (exchange.lower(), symbol.upper())
        return self._watches.pop(key, None)

    def get_watch(self, exchange: str, symbol: str) -> ScenarioWatch | None:
        return self._watches.get((exchange.lower(), symbol.upper()))

    def try_enroll_user_intent(
        self,
        *,
        exchange: str,
        symbol: str,
        intent: str,
        price: float,
        breakout_level: float | None,
        breakdown_level: float | None,
        zone_low: float | None = None,
        zone_high: float | None = None,
        stop_hint: float | None = None,
        target_hints: list[float] | None = None,
        coinglass_url: str = "",
        signal_type: str = "user_watch",
        settings: Any = None,
        chat_id: int | None = None,
    ) -> tuple[bool, str]:
        """Подписка по кнопке: ждать LONG/SHORT с ранним подтверждением."""
        intent = (intent or "").lower()
        if intent not in {"long", "short"}:
            return False, "нужна сторона long/short"
        if settings is not None and not getattr(settings, "scenario_watch_enabled", True):
            return False, "сценарии выключены"
        if price <= 0:
            return False, "нет цены"
        if intent == "long" and not breakout_level and not zone_high and not zone_low:
            return False, "нет уровня для LONG (пробой/зона)"
        if intent == "short" and not breakdown_level and not zone_low and not zone_high:
            return False, "нет уровня для SHORT (пробой/зона)"

        key = (exchange.lower(), symbol.upper())
        now = time.time()
        watch_minutes = int(getattr(settings, "scenario_watch_minutes", 45) if settings else 45)
        late_pct = float(getattr(settings, "scenario_watch_late_cancel_pct", 1.5) if settings else 1.5)
        opp_pct = float(getattr(settings, "scenario_watch_opposite_cancel_pct", 1.2) if settings else 1.2)

        # Для long: зона около поддержки/Fib, триггер = breakout или верх зоны
        # Для short: зона около сопротивления/Fib, триггер = breakdown или низ зоны
        z_lo = zone_low
        z_hi = zone_high
        if intent == "long":
            if z_lo is None and breakout_level:
                z_lo = breakout_level * 0.992
            if z_hi is None and breakout_level:
                z_hi = breakout_level * 1.004
            if breakout_level is None and z_hi is not None:
                breakout_level = z_hi
        else:
            if z_hi is None and breakdown_level:
                z_hi = breakdown_level * 1.008
            if z_lo is None and breakdown_level:
                z_lo = breakdown_level * 0.996
            if breakdown_level is None and z_lo is not None:
                breakdown_level = z_lo

        watch = ScenarioWatch(
            exchange=exchange,
            symbol=symbol.upper(),
            side=intent,
            signal_type=signal_type or "user_watch",
            primary="continuation" if intent == "long" else "correction",
            enroll_price=price,
            local_high=price,
            local_low=price,
            correction_target=breakdown_level if intent == "short" else None,
            continuation_target=breakout_level if intent == "long" else None,
            breakdown_level=breakdown_level,
            breakout_level=breakout_level,
            initial_verdict="WAIT",
            coinglass_url=coinglass_url,
            started_at=now,
            expires_at=now + watch_minutes * 60,
            enroll_high=price,
            trigger_only=True,
            correction_fired=True,
            user_intent=intent,
            zone_low=z_lo,
            zone_high=z_hi,
            stop_hint=stop_hint,
            target_hints=tuple(t for t in (target_hints or []) if t and t > 0)[:3],
            late_cancel_pct=late_pct,
            opposite_cancel_pct=opp_pct,
            chat_id=chat_id,
        )
        self._watches[key] = watch
        self._last_enroll_at[key] = now
        lvl = breakout_level if intent == "long" else breakdown_level
        logger.info(
            "User intent watch %s %s %s @ %s (%.0f min)",
            exchange, symbol, intent.upper(),
            fmt_price(lvl) if lvl else "zone",
            watch_minutes,
        )
        return True, f"слежу {intent.upper()} ~{watch_minutes} мин"

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
                updates.append(ScenarioUpdate(
                    watch=watch,
                    kind="expired",
                    price=watch.enroll_price,
                    move_pct=0.0,
                    reference_price=watch.enroll_price,
                ))
                self._watches.pop(key, None)
                continue

            snapshot = scanner.get_snapshot_for(watch.exchange, watch.symbol)
            if snapshot is None or snapshot.price is None or snapshot.price <= 0:
                continue

            price = float(snapshot.price)
            watch.local_high = max(watch.local_high, price)
            watch.local_low = min(watch.local_low, price)
            age = now - watch.started_at

            if watch.is_user_watch or watch.trigger_only:
                min_age = float(
                    getattr(
                        settings,
                        "scenario_watch_trigger_min_age_seconds",
                        TRIGGER_WATCH_DEFAULT_MIN_AGE_SEC,
                    )
                )
                if age < min_age:
                    continue
                if watch.is_user_watch:
                    batch = self._check_user_intent(watch, price)
                else:
                    batch = self._check_entry_ready(watch, price)
                updates.extend(batch)
                for upd in batch:
                    if upd.kind in {
                        "entry_short", "entry_long",
                        "cancelled_late", "cancelled_opposite",
                    }:
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

    def _check_user_intent(self, watch: ScenarioWatch, price: float) -> list[ScenarioUpdate]:
        """Зона → короткое подтверждение → ENTRY; опоздание / противоположный пробой → cancel."""
        if watch.entry_fired:
            return []
        intent = watch.user_intent
        move = (price - watch.enroll_price) / watch.enroll_price * 100.0 if watch.enroll_price > 0 else 0.0

        # Противоположный сильный ход — сценарий сломан
        if intent == "short" and watch.breakout_level and price >= watch.breakout_level * (
            1.0 + watch.opposite_cancel_pct / 100.0
        ):
            return [ScenarioUpdate(
                watch=watch, kind="cancelled_opposite", price=price,
                move_pct=move, reference_price=watch.breakout_level,
            )]
        if intent == "long" and watch.breakdown_level and price <= watch.breakdown_level * (
            1.0 - watch.opposite_cancel_pct / 100.0
        ):
            return [ScenarioUpdate(
                watch=watch, kind="cancelled_opposite", price=price,
                move_pct=move, reference_price=watch.breakdown_level,
            )]

        # Опоздали: цена уже далеко за триггером
        if intent == "short" and watch.breakdown_level and watch.breakdown_level > 0:
            past = (watch.breakdown_level - price) / watch.breakdown_level * 100.0
            if past >= watch.late_cancel_pct:
                return [ScenarioUpdate(
                    watch=watch, kind="cancelled_late", price=price,
                    move_pct=move, reference_price=watch.breakdown_level,
                )]
        if intent == "long" and watch.breakout_level and watch.breakout_level > 0:
            past = (price - watch.breakout_level) / watch.breakout_level * 100.0
            if past >= watch.late_cancel_pct:
                return [ScenarioUpdate(
                    watch=watch, kind="cancelled_late", price=price,
                    move_pct=move, reference_price=watch.breakout_level,
                )]

        return self._check_entry_ready(watch, price, intent_side=intent)

    def _check_entry_ready(
        self,
        watch: ScenarioWatch,
        price: float,
        *,
        intent_side: str | None = None,
    ) -> list[ScenarioUpdate]:
        if watch.entry_fired:
            return []

        side = (intent_side or watch.user_intent or watch.side or "").lower()
        buf = max(0.02, float(watch.confirm_buffer_pct)) / 100.0
        out: list[ScenarioUpdate] = []
        strict = bool(intent_side or watch.is_user_watch)

        def _fire(kind: str, lvl: float) -> list[ScenarioUpdate]:
            watch.entry_fired = True
            move = (price - watch.enroll_price) / watch.enroll_price * 100.0
            return [ScenarioUpdate(
                watch=watch, kind=kind, price=price,  # type: ignore[arg-type]
                move_pct=move, reference_price=lvl,
            )]

        # SHORT
        if (not strict or side == "short") and watch.breakdown_level:
            lvl = watch.breakdown_level
            thresh = lvl * (1.0 - buf) if strict else lvl
            if price <= thresh:
                return _fire("entry_short", lvl)

        # LONG
        if (not strict or side == "long") and watch.breakout_level:
            lvl = watch.breakout_level
            thresh = lvl * (1.0 + buf) if strict else lvl
            if price >= thresh:
                return _fire("entry_long", lvl)

        # Зона без жёсткого уровня (только user intent)
        if strict and side == "short" and watch.zone_low and price <= watch.zone_low:
            return _fire("entry_short", watch.zone_low)
        if strict and side == "long" and watch.zone_high and price >= watch.zone_high:
            return _fire("entry_long", watch.zone_high)
        return out

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
