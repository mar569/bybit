from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"


@dataclass
class ExchangeThresholds:
    oi_period_minutes: int
    oi_rise_percent: float
    oi_drop_percent: float
    price_rise_percent: float
    price_drop_percent: float


@dataclass
class ScannerSettings:
    oi_period_minutes: int = 15
    oi_rise_percent: float = 3.0
    oi_drop_percent: float = 3.0
    price_rise_percent: float = 0.5
    price_drop_percent: float = 0.5
    min_open_interest: float = 50_000.0
    min_volume: float = 0.0
    enabled_binance: bool = True
    enabled_bybit: bool = True
    scan_interval_seconds: int = 1
    signal_cooldown_seconds: int = 120
    volume_spike_multiplier: float = 5.0
    price_pump_threshold_pct: float = 8.0
    price_pump_window_minutes: int = 5
    cvd_divergence_threshold: float = -0.1
    min_signal_score: float = 1.0
    top_n_symbols: int | None = None
    priority_score_max: int = 2
    signals_enabled: bool = True
    price_only_min_percent: float = 2.0
    telegram_max_per_minute: int = 12
    telegram_min_interval_seconds: float = 1.5
    pin_in_private_chat: bool = True

    # Per-exchange overrides (None = use global)
    binance_oi_period_minutes: int | None = None
    binance_oi_rise_percent: float | None = None
    binance_oi_drop_percent: float | None = None
    binance_price_rise_percent: float | None = None
    binance_price_drop_percent: float | None = None
    bybit_oi_period_minutes: int | None = None
    bybit_oi_rise_percent: float | None = None
    bybit_oi_drop_percent: float | None = None
    bybit_price_rise_percent: float | None = None
    bybit_price_drop_percent: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def for_exchange(self, exchange: str) -> ExchangeThresholds:
        prefix = "bybit" if "bybit" in exchange.lower() else "binance"
        return ExchangeThresholds(
            oi_period_minutes=int(
                getattr(self, f"{prefix}_oi_period_minutes") or self.oi_period_minutes
            ),
            oi_rise_percent=float(
                getattr(self, f"{prefix}_oi_rise_percent") or self.oi_rise_percent
            ),
            oi_drop_percent=float(
                getattr(self, f"{prefix}_oi_drop_percent") or self.oi_drop_percent
            ),
            price_rise_percent=float(
                getattr(self, f"{prefix}_price_rise_percent") or self.price_rise_percent
            ),
            price_drop_percent=float(
                getattr(self, f"{prefix}_price_drop_percent") or self.price_drop_percent
            ),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScannerSettings":
        def opt_int(key: str) -> int | None:
            if key not in data or data[key] is None:
                return None
            return int(data[key])

        def opt_float(key: str) -> float | None:
            if key not in data or data[key] is None:
                return None
            return float(data[key])

        top_n = data.get("top_n_symbols")
        return cls(
            oi_period_minutes=int(data.get("oi_period_minutes", 15)),
            oi_rise_percent=float(data.get("oi_rise_percent", 5.0)),
            oi_drop_percent=float(data.get("oi_drop_percent", 5.0)),
            price_rise_percent=float(data.get("price_rise_percent", 1.0)),
            price_drop_percent=float(data.get("price_drop_percent", 1.0)),
            min_open_interest=float(data.get("min_open_interest", 100000.0)),
            min_volume=float(data.get("min_volume", 0.0)),
            enabled_binance=bool(data.get("enabled_binance", True)),
            enabled_bybit=bool(data.get("enabled_bybit", True)),
            volume_spike_multiplier=float(data.get("volume_spike_multiplier", 5.0)),
            price_pump_threshold_pct=float(data.get("price_pump_threshold_pct", 8.0)),
            price_pump_window_minutes=int(data.get("price_pump_window_minutes", 5)),
            cvd_divergence_threshold=float(data.get("cvd_divergence_threshold", -0.1)),
            min_signal_score=float(data.get("min_signal_score", 1.0)),
            top_n_symbols=(int(top_n) if top_n is not None else None),
            priority_score_max=int(data.get("priority_score_max", 2)),
            signals_enabled=bool(data.get("signals_enabled", True)),
            price_only_min_percent=float(data.get("price_only_min_percent", 2.0)),
            telegram_max_per_minute=int(data.get("telegram_max_per_minute", 12)),
            telegram_min_interval_seconds=float(data.get("telegram_min_interval_seconds", 1.5)),
            pin_in_private_chat=bool(data.get("pin_in_private_chat", True)),
            scan_interval_seconds=int(data.get("scan_interval_seconds", 1)),
            signal_cooldown_seconds=int(data.get("signal_cooldown_seconds", 60)),
            binance_oi_period_minutes=opt_int("binance_oi_period_minutes"),
            binance_oi_rise_percent=opt_float("binance_oi_rise_percent"),
            binance_oi_drop_percent=opt_float("binance_oi_drop_percent"),
            binance_price_rise_percent=opt_float("binance_price_rise_percent"),
            binance_price_drop_percent=opt_float("binance_price_drop_percent"),
            bybit_oi_period_minutes=opt_int("bybit_oi_period_minutes"),
            bybit_oi_rise_percent=opt_float("bybit_oi_rise_percent"),
            bybit_oi_drop_percent=opt_float("bybit_oi_drop_percent"),
            bybit_price_rise_percent=opt_float("bybit_price_rise_percent"),
            bybit_price_drop_percent=opt_float("bybit_price_drop_percent"),
        )


class SettingsManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_SETTINGS_FILE
        self.settings = self.load()
        self._listeners: list[Callable[[ScannerSettings], None]] = []

    def add_listener(self, callback: Callable[[ScannerSettings], None]) -> None:
        self._listeners.append(callback)

    def reload(self) -> ScannerSettings:
        self.settings = self.load()
        return self.settings

    def load(self) -> ScannerSettings:
        if not self.path.exists():
            settings = ScannerSettings()
            self.save(settings)
            return settings

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return ScannerSettings.from_dict(data)
        except Exception:
            settings = ScannerSettings()
            self.save(settings)
            return settings

    def save(self, settings: ScannerSettings | None = None) -> None:
        if settings is not None:
            self.settings = settings
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.settings.to_dict(), handle, indent=2)

    def update(self, **kwargs: Any) -> ScannerSettings:
        data = self.settings.to_dict()
        data.update(kwargs)
        self.settings = ScannerSettings.from_dict(data)
        self.save()
        logger.info("Settings applied: %s", kwargs)
        for listener in self._listeners:
            try:
                listener(self.settings)
            except Exception:
                logger.exception("Settings listener failed")
        return self.settings
