from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .liquidation_alerts import coinglass_url, exchange_trade_url, base_ticker
from .models import SnapshotPoint
from .settings import ScannerSettings
from .symbol_tiers import SymbolTier, TierThresholds

logger = logging.getLogger(__name__)

DEFAULT_ANOMALY_TYPES = ("PUMP_DUMP", "OI_SPIKE")

ANOMALY_TYPE_PRIORITY: dict[str, int] = {
    "pump_dump": 0,
    "oi_spike": 1,
    "funding_extreme": 2,
    "volume_spike": 3,
}


@dataclass(frozen=True)
class AnomalyEvent:
    exchange: str
    symbol: str
    anomaly_type: str
    timestamp: float
    price: float
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.0


def _percent_change(previous: float, current: float) -> float:
    if previous == 0.0:
        return 0.0
    return (current - previous) / abs(previous) * 100.0


def _point_at_cutoff(
    history: deque[SnapshotPoint],
    cutoff: float,
) -> SnapshotPoint | None:
    for point in reversed(history):
        if point.timestamp <= cutoff and point.price is not None:
            return point
    return None


def _oi_percent_change(earlier: SnapshotPoint, current: SnapshotPoint) -> float:
    if (
        earlier.open_interest is None
        or current.open_interest is None
        or earlier.open_interest <= 0
    ):
        return 0.0
    return _percent_change(earlier.open_interest, current.open_interest)


def _enabled_types(settings: ScannerSettings) -> frozenset[str]:
    raw = getattr(settings, "anomaly_types_enabled", DEFAULT_ANOMALY_TYPES)
    if raw is None:
        return frozenset(t.lower() for t in DEFAULT_ANOMALY_TYPES)
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
        return frozenset(parts) if parts else frozenset(t.lower() for t in DEFAULT_ANOMALY_TYPES)
    return frozenset(str(t).lower() for t in raw)


def compute_anomaly_importance(
    event: AnomalyEvent,
    settings: ScannerSettings,
    tier: TierThresholds,
) -> float:
    """Чем выше — тем важнее. Используется для сортировки очереди."""
    base = {
        "pump_dump": 88.0,
        "oi_spike": 72.0,
        "funding_extreme": 58.0,
        "volume_spike": 40.0,
    }.get(event.anomaly_type, 30.0)

    meta = event.meta or {}
    if event.anomaly_type == "pump_dump":
        base += min(abs(float(meta.get("dump_pct", 0) or 0)) * 1.8, 25.0)
        base += min(abs(float(meta.get("pump_pct", 0) or 0)) * 0.8, 12.0)
    elif event.anomaly_type == "oi_spike":
        base += min(abs(float(meta.get("oi_pct", 0) or 0)) * 2.5, 20.0)
        base += min(abs(float(meta.get("price_pct", 0) or 0)) * 1.5, 15.0)
    elif event.anomaly_type == "funding_extreme":
        base += min(abs(float(meta.get("funding_rate", 0) or 0)) * 4000.0, 22.0)
    elif event.anomaly_type == "volume_spike":
        base += min(float(meta.get("volume_ratio", 0) or 0) * 1.5, 25.0)
        base += min(abs(float(meta.get("price_5m_pct", 0) or 0)) * 2.0, 18.0)

    if tier.tier == SymbolTier.MAJOR:
        base += 6.0
    elif tier.tier == SymbolTier.ALT:
        base -= 4.0

    return round(base, 1)


def detect_anomaly(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    settings: ScannerSettings,
    tier: TierThresholds,
) -> AnomalyEvent | None:
    if not settings.anomaly_enabled or current.price is None:
        return None

    enabled = _enabled_types(settings)
    candidates: list[AnomalyEvent] = []

    if "pump_dump" in enabled:
        event = _detect_pump_dump(history, current, settings)
        if event is not None:
            candidates.append(event)

    if "oi_spike" in enabled:
        event = _detect_oi_spike(history, current, settings)
        if event is not None:
            candidates.append(event)

    if "funding_extreme" in enabled:
        event = _detect_funding_extreme(current, settings)
        if event is not None:
            candidates.append(event)

    if getattr(settings, "anomaly_volume_spike_enabled", False) and "volume_spike" in enabled:
        event = _detect_volume_spike(history, current, settings)
        if event is not None:
            candidates.append(event)

    if not candidates:
        return None

    candidates.sort(
        key=lambda e: (
            ANOMALY_TYPE_PRIORITY.get(e.anomaly_type, 9),
            -compute_anomaly_importance(
                AnomalyEvent(
                    exchange="", symbol="", anomaly_type=e.anomaly_type,
                    timestamp=e.timestamp, price=e.price, detail=e.detail, meta=e.meta,
                ),
                settings,
                tier,
            ),
        ),
    )
    return candidates[0]


def detect_anomaly_for_symbol(
    exchange: str,
    symbol: str,
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    settings: ScannerSettings,
    tier: TierThresholds,
) -> AnomalyEvent | None:
    event = detect_anomaly(history, current, settings, tier)
    if event is None:
        return None
    full = AnomalyEvent(
        exchange=exchange,
        symbol=symbol.upper(),
        anomaly_type=event.anomaly_type,
        timestamp=event.timestamp,
        price=event.price,
        detail=event.detail,
        meta=event.meta,
    )
    importance = compute_anomaly_importance(full, settings, tier)
    return AnomalyEvent(
        exchange=full.exchange,
        symbol=full.symbol,
        anomaly_type=full.anomaly_type,
        timestamp=full.timestamp,
        price=full.price,
        detail=full.detail,
        meta=full.meta,
        importance=importance,
    )


def _detect_pump_dump(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    settings: ScannerSettings,
) -> AnomalyEvent | None:
    window_sec = settings.anomaly_pump_dump_window_minutes * 60
    cutoff = current.timestamp - window_sec
    if history[0].timestamp > cutoff:
        return None

    window_points = [
        p for p in history
        if p.timestamp >= cutoff and p.price is not None and p.price > 0
    ]
    if len(window_points) < 8:
        return None

    peak = max(window_points, key=lambda p: p.price or 0.0)
    trough_before_peak = min(
        (p for p in window_points if p.timestamp <= peak.timestamp),
        key=lambda p: p.price or float("inf"),
        default=window_points[0],
    )
    peak_price = peak.price or 0.0
    trough_price = trough_before_peak.price or 0.0
    current_price = current.price or 0.0
    if peak_price <= 0 or trough_price <= 0:
        return None

    pump_pct = _percent_change(trough_price, peak_price)
    dump_pct = _percent_change(peak_price, current_price)
    if (
        pump_pct >= settings.anomaly_pump_min_pct
        and dump_pct <= -settings.anomaly_dump_min_pct
        and peak.timestamp < current.timestamp
    ):
        return AnomalyEvent(
            exchange="",
            symbol="",
            anomaly_type="pump_dump",
            timestamp=current.timestamp,
            price=current_price,
            detail=(
                f"памп +{pump_pct:.1f}% → слив {dump_pct:.1f}% "
                f"за {settings.anomaly_pump_dump_window_minutes}м"
            ),
            meta={
                "pump_pct": round(pump_pct, 2),
                "dump_pct": round(dump_pct, 2),
                "peak_price": peak_price,
            },
        )
    return None


def _detect_volume_spike(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    settings: ScannerSettings,
) -> AnomalyEvent | None:
    """Только при одновременном движении цены — иначе шум на 400+ парах."""
    if len(history) < 30:
        return None

    cutoff = current.timestamp - 5 * 60
    deltas: list[float] = []
    prev = None
    for point in history:
        if point.timestamp < cutoff:
            prev = point
            continue
        if (
            prev is not None
            and point.volume_24h is not None
            and prev.volume_24h is not None
        ):
            delta = point.volume_24h - prev.volume_24h
            if delta > 0:
                deltas.append(delta)
        prev = point

    if len(deltas) < 8:
        return None

    baseline = sorted(deltas[:-1])
    mid = baseline[len(baseline) // 2]
    if mid <= 0:
        return None

    last_delta = deltas[-1]
    ratio = last_delta / mid
    if ratio < settings.anomaly_volume_spike_multiplier:
        return None

    earlier = _point_at_cutoff(history, current.timestamp - 5 * 60)
    price_pct = 0.0
    if earlier is not None and earlier.price and current.price:
        price_pct = _percent_change(earlier.price, current.price)

    min_price = float(getattr(settings, "anomaly_volume_spike_min_price_pct", 1.5))
    if abs(price_pct) < min_price:
        return None

    return AnomalyEvent(
        exchange="",
        symbol="",
        anomaly_type="volume_spike",
        timestamp=current.timestamp,
        price=current.price or 0.0,
        detail=(
            f"всплеск объёма ×{ratio:.1f} · цена {price_pct:+.1f}% за 5м"
        ),
        meta={
            "volume_ratio": round(ratio, 2),
            "price_5m_pct": round(price_pct, 2),
        },
    )


def _detect_oi_spike(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    settings: ScannerSettings,
) -> AnomalyEvent | None:
    earlier = _point_at_cutoff(history, current.timestamp - 10 * 60)
    if earlier is None:
        return None
    oi_pct = _oi_percent_change(earlier, current)
    price_pct = 0.0
    if earlier.price and current.price:
        price_pct = _percent_change(earlier.price, current.price)
    if abs(oi_pct) < settings.anomaly_min_oi_change_pct:
        return None
    if abs(price_pct) < settings.anomaly_min_price_change_pct:
        return None
    return AnomalyEvent(
        exchange="",
        symbol="",
        anomaly_type="oi_spike",
        timestamp=current.timestamp,
        price=current.price or 0.0,
        detail=f"OI {oi_pct:+.1f}% · цена {price_pct:+.1f}% за 10м",
        meta={"oi_pct": round(oi_pct, 2), "price_pct": round(price_pct, 2)},
    )


def _detect_funding_extreme(
    current: SnapshotPoint,
    settings: ScannerSettings,
) -> AnomalyEvent | None:
    raw = current.additional.get("funding_rate")
    if raw is None:
        return None
    try:
        funding = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(funding) < settings.anomaly_funding_abs_min:
        return None
    pct = funding * 100.0
    bias = "шорты переплачивают" if funding < 0 else "лонги переплачивают"
    return AnomalyEvent(
        exchange="",
        symbol="",
        anomaly_type="funding_extreme",
        timestamp=current.timestamp,
        price=current.price or 0.0,
        detail=f"funding {pct:+.3f}% · {bias}",
        meta={"funding_rate": funding},
    )


class AnomalyBatcher:
    """Собирает кандидатов и шлёт только топ-N важных за интервал."""

    def __init__(
        self,
        on_dispatch: Callable[[AnomalyEvent], Awaitable[None]],
    ) -> None:
        self._on_dispatch = on_dispatch
        self._best_per_symbol: dict[str, AnomalyEvent] = {}
        self._symbol_cooldown_until: dict[str, float] = {}
        self._dispatch_times: deque[float] = deque(maxlen=64)
        self._last_flush = 0.0
        self._lock = asyncio.Lock()

    async def offer(self, event: AnomalyEvent, settings: ScannerSettings) -> None:
        min_imp = float(getattr(settings, "anomaly_min_importance", 55.0))
        if event.importance < min_imp:
            return

        sym = event.symbol.upper()
        now = time.time()
        symbol_cd = int(getattr(settings, "anomaly_symbol_cooldown_seconds", 1800))

        async with self._lock:
            if now < self._symbol_cooldown_until.get(sym, 0.0):
                return
            prev = self._best_per_symbol.get(sym)
            if prev is None or event.importance > prev.importance:
                self._best_per_symbol[sym] = event

    async def flush(self, settings: ScannerSettings) -> int:
        now = time.time()
        interval = float(getattr(settings, "anomaly_batch_interval_seconds", 60.0))
        if now - self._last_flush < interval:
            return 0
        self._last_flush = now

        max_per_min = int(getattr(settings, "anomaly_max_per_minute", 3))
        while self._dispatch_times and self._dispatch_times[0] < now - 60.0:
            self._dispatch_times.popleft()
        slots = max(0, max_per_min - len(self._dispatch_times))
        if slots <= 0:
            return 0

        async with self._lock:
            if not self._best_per_symbol:
                return 0
            ranked = sorted(
                self._best_per_symbol.values(),
                key=lambda e: (-e.importance, ANOMALY_TYPE_PRIORITY.get(e.anomaly_type, 9)),
            )
            to_send = ranked[:slots]
            sent_syms = {e.symbol.upper() for e in to_send}
            self._best_per_symbol = {
                k: v for k, v in self._best_per_symbol.items() if k not in sent_syms
            }

        symbol_cd = int(getattr(settings, "anomaly_symbol_cooldown_seconds", 1800))
        for event in to_send:
            await self._on_dispatch(event)
            async with self._lock:
                ts = time.time()
                self._dispatch_times.append(ts)
                self._symbol_cooldown_until[event.symbol.upper()] = ts + symbol_cd
            logger.info(
                "Anomaly sent %s %s %s (importance %.0f)",
                event.exchange,
                event.symbol,
                event.anomaly_type,
                event.importance,
            )

        return len(to_send)


ANOMALY_LABELS: dict[str, str] = {
    "pump_dump": "🔄 PUMP → DUMP",
    "volume_spike": "📊 ВСПЛЕСК ОБЪЁМА",
    "oi_spike": "📈 АНОМАЛИЯ OI",
    "funding_extreme": "💸 ЭКСТРЕМАЛЬНЫЙ FUNDING",
}


def format_anomaly_alert(event: AnomalyEvent) -> str:
    exchange_key = "bybit" if "bybit" in event.exchange.lower() else "binance"
    if exchange_key == "bybit":
        exchange_emoji, exchange_name = "⚫", "ByBit"
    else:
        exchange_emoji, exchange_name = "🟡", "Binance"

    label = ANOMALY_LABELS.get(event.anomaly_type, "⚠️ АНОМАЛИЯ")
    ticker = base_ticker(event.symbol)
    cg_url = coinglass_url(event.symbol, event.exchange)
    ex_url = exchange_trade_url(event.symbol, event.exchange)
    ts = datetime.fromtimestamp(event.timestamp, tz=timezone.utc).strftime("%H:%M")
    imp = f" · важн. <b>{event.importance:.0f}</b>" if event.importance else ""

    return (
        f"<b>{label}</b>{imp}\n"
        f'{exchange_emoji} <a href="{ex_url}">{exchange_name}</a> '
        f'<a href="{cg_url}">#{ticker}</a> · ${event.price:.6g}\n'
        f"{event.detail}\n"
        f"<i>{ts} UTC · ранний радар, не вход</i>"
    )
