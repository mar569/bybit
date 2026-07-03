from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
SETTINGS_VERSION = 2

# Сохраняем при миграции на новый пресет (остальное — идеальные значения).
PRESERVE_ON_MIGRATE = frozenset({
    "signals_enabled",
    "enabled_binance",
    "enabled_bybit",
    "top_n_symbols",
})


@dataclass
class ExchangeThresholds:
    long_period_minutes: int
    short_period_minutes: int
    oi_rise_percent: float
    oi_drop_percent: float
    price_rise_percent: float
    price_drop_percent: float


@dataclass
class ScannerSettings:
    settings_version: int = SETTINGS_VERSION

    # Основной LONG-профиль (OI + цена, качественный приток)
    oi_period_minutes: int = 10
    long_period_minutes: int = 10
    short_period_minutes: int = 10
    oi_rise_percent: float = 2.5
    oi_drop_percent: float = 3.0
    price_rise_percent: float = 0.8
    price_drop_percent: float = 0.8

    # Ранний пульс — ловим старт движения на альтах
    pulse_period_minutes: int = 5
    pulse_oi_rise_percent: float = 0.8
    pulse_oi_drop_percent: float = 0.8
    pulse_price_rise_percent: float = 0.4
    pulse_price_drop_percent: float = 0.4

    # Мега-пампы: 5–100% за 5–10 минут
    flash_enabled: bool = True
    flash_window_minutes: tuple[int, ...] = (5, 10)
    flash_price_tiers: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0)
    flash_min_oi_rise_percent: float = 1.0
    flash_min_oi_drop_percent: float = 1.0
    flash_bypass_oi_tier_pct: float = 15.0

    # Качество сигнала: деньги в OI, не просто цена
    min_oi_change_usd: float = 25_000.0
    short_squeeze_min_price: float = 4.0
    short_squeeze_max_oi_change: float = -0.8
    require_oi_for_price_only: bool = True
    mega_cooldown_seconds: int = 30

    min_open_interest: float = 75_000.0
    min_volume: float = 0.0
    enabled_binance: bool = True
    enabled_bybit: bool = True
    scan_interval_seconds: int = 1
    signal_cooldown_seconds: int = 90
    volume_spike_multiplier: float = 4.0
    price_pump_threshold_pct: float = 8.0
    price_pump_window_minutes: int = 5
    cvd_divergence_threshold: float = -0.1
    min_signal_score: float = 1.0
    top_n_symbols: int | None = 150
    priority_score_max: int = 3
    signals_enabled: bool = True
    price_only_min_percent: float = 3.0
    telegram_max_per_minute: int = 10
    telegram_min_interval_seconds: float = 2.0
    pin_in_private_chat: bool = True

    # Per-exchange: Bybit альты агрессивнее, Binance чуть строже
    binance_oi_period_minutes: int | None = None
    binance_long_period_minutes: int | None = None
    binance_short_period_minutes: int | None = None
    binance_oi_rise_percent: float | None = 3.0
    binance_oi_drop_percent: float | None = None
    binance_price_rise_percent: float | None = 0.8
    binance_price_drop_percent: float | None = None
    bybit_oi_period_minutes: int | None = None
    bybit_long_period_minutes: int | None = None
    bybit_short_period_minutes: int | None = None
    bybit_oi_rise_percent: float | None = 2.0
    bybit_oi_drop_percent: float | None = None
    bybit_price_rise_percent: float | None = 0.6
    bybit_price_drop_percent: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def for_exchange(self, exchange: str) -> ExchangeThresholds:
        prefix = "bybit" if "bybit" in exchange.lower() else "binance"
        legacy_period = int(
            getattr(self, f"{prefix}_oi_period_minutes") or self.oi_period_minutes
        )
        long_period = int(
            getattr(self, f"{prefix}_long_period_minutes")
            or getattr(self, f"{prefix}_oi_period_minutes")
            or self.long_period_minutes
            or legacy_period
        )
        short_period = int(
            getattr(self, f"{prefix}_short_period_minutes")
            or getattr(self, f"{prefix}_oi_period_minutes")
            or self.short_period_minutes
            or legacy_period
        )
        return ExchangeThresholds(
            long_period_minutes=long_period,
            short_period_minutes=short_period,
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

    @staticmethod
    def _parse_int_tuple(value: object, default: tuple[int, ...]) -> tuple[int, ...]:
        if value is None:
            return default
        if isinstance(value, (list, tuple)):
            return tuple(int(v) for v in value)
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return tuple(int(float(p)) for p in parts) if parts else default
        return default

    @staticmethod
    def _parse_float_tuple(value: object, default: tuple[float, ...]) -> tuple[float, ...]:
        if value is None:
            return default
        if isinstance(value, (list, tuple)):
            return tuple(float(v) for v in value)
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return tuple(float(p) for p in parts) if parts else default
        return default

    @classmethod
    def default(cls) -> "ScannerSettings":
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScannerSettings":
        base = cls.default().to_dict()
        base.update(data)

        def opt_int(key: str) -> int | None:
            if key not in base or base[key] is None:
                return None
            return int(base[key])

        def opt_float(key: str) -> float | None:
            if key not in base or base[key] is None:
                return None
            return float(base[key])

        top_n = base.get("top_n_symbols")
        return cls(
            settings_version=int(base.get("settings_version", SETTINGS_VERSION)),
            oi_period_minutes=int(base["oi_period_minutes"]),
            long_period_minutes=int(base.get("long_period_minutes", base["oi_period_minutes"])),
            short_period_minutes=int(base.get("short_period_minutes", base["oi_period_minutes"])),
            oi_rise_percent=float(base["oi_rise_percent"]),
            oi_drop_percent=float(base["oi_drop_percent"]),
            price_rise_percent=float(base["price_rise_percent"]),
            price_drop_percent=float(base["price_drop_percent"]),
            pulse_period_minutes=int(base["pulse_period_minutes"]),
            pulse_oi_rise_percent=float(base["pulse_oi_rise_percent"]),
            pulse_oi_drop_percent=float(base["pulse_oi_drop_percent"]),
            pulse_price_rise_percent=float(base["pulse_price_rise_percent"]),
            pulse_price_drop_percent=float(base["pulse_price_drop_percent"]),
            flash_enabled=bool(base.get("flash_enabled", True)),
            flash_window_minutes=cls._parse_int_tuple(
                base.get("flash_window_minutes"), (5, 10)
            ),
            flash_price_tiers=cls._parse_float_tuple(
                base.get("flash_price_tiers"),
                (5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0),
            ),
            flash_min_oi_rise_percent=float(base["flash_min_oi_rise_percent"]),
            flash_min_oi_drop_percent=float(base["flash_min_oi_drop_percent"]),
            flash_bypass_oi_tier_pct=float(base["flash_bypass_oi_tier_pct"]),
            min_oi_change_usd=float(base["min_oi_change_usd"]),
            short_squeeze_min_price=float(base["short_squeeze_min_price"]),
            short_squeeze_max_oi_change=float(base["short_squeeze_max_oi_change"]),
            require_oi_for_price_only=bool(base.get("require_oi_for_price_only", True)),
            mega_cooldown_seconds=int(base["mega_cooldown_seconds"]),
            min_open_interest=float(base["min_open_interest"]),
            min_volume=float(base["min_volume"]),
            enabled_binance=bool(base.get("enabled_binance", True)),
            enabled_bybit=bool(base.get("enabled_bybit", True)),
            volume_spike_multiplier=float(base.get("volume_spike_multiplier", 4.0)),
            price_pump_threshold_pct=float(base.get("price_pump_threshold_pct", 8.0)),
            price_pump_window_minutes=int(base.get("price_pump_window_minutes", 5)),
            cvd_divergence_threshold=float(base.get("cvd_divergence_threshold", -0.1)),
            min_signal_score=float(base.get("min_signal_score", 1.0)),
            top_n_symbols=(int(top_n) if top_n is not None else None),
            priority_score_max=int(base.get("priority_score_max", 3)),
            signals_enabled=bool(base.get("signals_enabled", True)),
            price_only_min_percent=float(base.get("price_only_min_percent", 3.0)),
            telegram_max_per_minute=int(base.get("telegram_max_per_minute", 10)),
            telegram_min_interval_seconds=float(base.get("telegram_min_interval_seconds", 2.0)),
            pin_in_private_chat=bool(base.get("pin_in_private_chat", True)),
            scan_interval_seconds=int(base.get("scan_interval_seconds", 1)),
            signal_cooldown_seconds=int(base.get("signal_cooldown_seconds", 90)),
            binance_oi_period_minutes=opt_int("binance_oi_period_minutes"),
            binance_long_period_minutes=opt_int("binance_long_period_minutes"),
            binance_short_period_minutes=opt_int("binance_short_period_minutes"),
            binance_oi_rise_percent=opt_float("binance_oi_rise_percent"),
            binance_oi_drop_percent=opt_float("binance_oi_drop_percent"),
            binance_price_rise_percent=opt_float("binance_price_rise_percent"),
            binance_price_drop_percent=opt_float("binance_price_drop_percent"),
            bybit_oi_period_minutes=opt_int("bybit_oi_period_minutes"),
            bybit_long_period_minutes=opt_int("bybit_long_period_minutes"),
            bybit_short_period_minutes=opt_int("bybit_short_period_minutes"),
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
        ideal = ScannerSettings.default()

        if not self.path.exists():
            self.save(ideal)
            return ideal

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            self.save(ideal)
            return ideal

        version = int(data.get("settings_version", 1))
        if version < SETTINGS_VERSION:
            merged = ideal.to_dict()
            for key in PRESERVE_ON_MIGRATE:
                if key in data:
                    merged[key] = data[key]
            merged["settings_version"] = SETTINGS_VERSION
            settings = ScannerSettings.from_dict(merged)
            self.save(settings)
            logger.info(
                "Settings migrated v%d → v%d (ideal preset applied, preserved: %s)",
                version,
                SETTINGS_VERSION,
                ", ".join(sorted(PRESERVE_ON_MIGRATE & data.keys())),
            )
            return settings

        return ScannerSettings.from_dict(data)

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
