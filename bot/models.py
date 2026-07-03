from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass
class SnapshotPoint:
    timestamp: float
    price: float | None
    open_interest: float | None
    volume_24h: float | None
    bid_price: float | None
    ask_price: float | None
    additional: dict[str, Any] = field(default_factory=dict)

@dataclass
class Signal:
    exchange: str
    symbol: str
    signal_type: str
    oi_period_minutes: int
    oi_change_percent: float
    oi_change_value: float
    oi_direction: str
    price_change_percent: float | None
    price_change_value: float | None
    price_direction: str | None
    volume_change_percent: float | None
    trade_count: int | None
    spread: float | None
    funding_rate: float | None
    liquidation_estimate: float | None
    vwap: float | None
    atr: float | None
    rsi: float | None
    ema_short: float | None
    ema_long: float | None
    volume_24h: float | None
    volume_speed: float | None
    signal_score: int
    current_price: float | None
    current_open_interest: float | None
    link: str
    details: dict[str, Any] = field(default_factory=dict)
