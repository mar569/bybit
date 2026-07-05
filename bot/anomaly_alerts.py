from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .liquidation_alerts import coinglass_url, exchange_trade_url, base_ticker
from .models import SnapshotPoint
from .settings import ScannerSettings
from .symbol_tiers import TierThresholds


@dataclass(frozen=True)
class AnomalyEvent:
    exchange: str
    symbol: str
    anomaly_type: str
    timestamp: float
    price: float
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)


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


def detect_anomaly(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    settings: ScannerSettings,
    tier: TierThresholds,
) -> AnomalyEvent | None:
    if not settings.anomaly_enabled or current.price is None:
        return None

    candidates: list[tuple[int, AnomalyEvent]] = []

    pump_dump = _detect_pump_dump(history, current, settings)
    if pump_dump is not None:
        candidates.append((0, pump_dump))

    vol = _detect_volume_spike(history, current, settings)
    if vol is not None:
        candidates.append((1, vol))

    oi = _detect_oi_spike(history, current, settings)
    if oi is not None:
        candidates.append((2, oi))

    funding = _detect_funding_extreme(current, settings)
    if funding is not None:
        candidates.append((3, funding))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


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
    return AnomalyEvent(
        exchange=exchange,
        symbol=symbol.upper(),
        anomaly_type=event.anomaly_type,
        timestamp=event.timestamp,
        price=event.price,
        detail=event.detail,
        meta=event.meta,
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
    if len(history) < 12:
        return None
    deltas: list[float] = []
    prev = None
    for point in history:
        if (
            prev is not None
            and point.volume_24h is not None
            and prev.volume_24h is not None
        ):
            delta = point.volume_24h - prev.volume_24h
            if delta > 0:
                deltas.append(delta)
        prev = point
    if len(deltas) < 5:
        return None
    avg_delta = sum(deltas[:-1]) / max(len(deltas) - 1, 1)
    last_delta = deltas[-1]
    if avg_delta <= 0:
        return None
    if last_delta < settings.anomaly_volume_spike_multiplier * avg_delta:
        return None

    earlier = _point_at_cutoff(history, current.timestamp - 5 * 60)
    price_pct = 0.0
    if earlier is not None and earlier.price and current.price:
        price_pct = _percent_change(earlier.price, current.price)

    return AnomalyEvent(
        exchange="",
        symbol="",
        anomaly_type="volume_spike",
        timestamp=current.timestamp,
        price=current.price or 0.0,
        detail=(
            f"всплеск объёма ×{last_delta / avg_delta:.1f} · цена {price_pct:+.1f}% за 5м"
        ),
        meta={"volume_ratio": round(last_delta / avg_delta, 2), "price_5m_pct": round(price_pct, 2)},
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
    if abs(price_pct) < settings.anomaly_min_price_change_pct * 0.5:
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

    return (
        f"<b>{label}</b>\n"
        f'{exchange_emoji} <a href="{ex_url}">{exchange_name}</a> '
        f'<a href="{cg_url}">#{ticker}</a> · ${event.price:.6g}\n'
        f"{event.detail}\n"
        f"<i>{ts} UTC · ранний радар, не вход</i>"
    )
