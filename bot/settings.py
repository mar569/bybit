from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
SETTINGS_VERSION = 7

# Сохраняем при миграции на новый пресет (остальное — идеальные значения).
PRESERVE_ON_MIGRATE = frozenset({
    "signals_enabled",
    "enabled_binance",
    "enabled_bybit",
    "top_n_symbols",
    "telegram_max_per_minute",
})

# При изменении глобального порога сбрасываем биржевые override — иначе кнопки в боте «не работают».
GLOBAL_CLEARS_EXCHANGE_OVERRIDES: dict[str, tuple[str, ...]] = {
    "oi_period_minutes": (
        "binance_oi_period_minutes",
        "bybit_oi_period_minutes",
        "binance_long_period_minutes",
        "bybit_long_period_minutes",
        "binance_short_period_minutes",
        "bybit_short_period_minutes",
    ),
    "long_period_minutes": (
        "binance_long_period_minutes",
        "bybit_long_period_minutes",
    ),
    "short_period_minutes": (
        "binance_short_period_minutes",
        "bybit_short_period_minutes",
    ),
    "oi_rise_percent": ("binance_oi_rise_percent", "bybit_oi_rise_percent"),
    "oi_drop_percent": ("binance_oi_drop_percent", "bybit_oi_drop_percent"),
    "price_rise_percent": ("binance_price_rise_percent", "bybit_price_rise_percent"),
    "price_drop_percent": ("binance_price_drop_percent", "bybit_price_drop_percent"),
}

EXCHANGE_OVERRIDE_KEYS = frozenset({
    "binance_oi_period_minutes",
    "binance_long_period_minutes",
    "binance_short_period_minutes",
    "binance_oi_rise_percent",
    "binance_oi_drop_percent",
    "binance_price_rise_percent",
    "binance_price_drop_percent",
    "bybit_oi_period_minutes",
    "bybit_long_period_minutes",
    "bybit_short_period_minutes",
    "bybit_oi_rise_percent",
    "bybit_oi_drop_percent",
    "bybit_price_rise_percent",
    "bybit_price_drop_percent",
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

    # Основной профиль — сбалансированный: OI+цена вместе, умеренный приток
    oi_period_minutes: int = 10
    long_period_minutes: int = 10
    short_period_minutes: int = 10
    oi_rise_percent: float = 2.0
    oi_drop_percent: float = 2.0
    price_rise_percent: float = 1.2
    price_drop_percent: float = 1.2

    # Ранний пульс — только при сильном совпадении с глобальными порогами
    pulse_enabled: bool = True
    pulse_period_minutes: int = 5
    pulse_oi_rise_percent: float = 2.5
    pulse_oi_drop_percent: float = 2.5
    pulse_price_rise_percent: float = 0.9
    pulse_price_drop_percent: float = 0.9

    # Мега-пампы: только крупные движения (≥8%)
    flash_enabled: bool = True
    flash_window_minutes: tuple[int, ...] = (5, 10)
    flash_price_tiers: tuple[float, ...] = (8.0, 12.0, 18.0, 25.0, 35.0, 50.0, 100.0)
    flash_min_oi_rise_percent: float = 2.0
    flash_min_oi_drop_percent: float = 2.0
    flash_bypass_oi_tier_pct: float = 20.0

    # Качество сигнала: реальный приток капитала в OI
    min_oi_change_usd: float = 20_000.0
    short_squeeze_min_price: float = 5.0
    short_squeeze_max_oi_change: float = -1.2
    require_oi_for_price_only: bool = True
    require_both_oi_and_price: bool = True
    respect_global_floors: bool = True
    mega_cooldown_seconds: int = 300

    # Вертикальный памп: флет → взлёт (вне порогов OI/цены из кнопок)
    breakout_enabled: bool = True
    breakout_bypass_top_n: bool = True
    breakout_consolidation_minutes: int = 25
    breakout_spike_minutes: int = 3
    breakout_max_flat_percent: float = 1.8
    breakout_min_spike_percent: float = 2.5
    breakout_min_dump_percent: float = 2.5
    breakout_velocity_multiplier: float = 4.0
    breakout_min_liquidity_oi_usd: float = 80_000.0
    breakout_cooldown_seconds: int = 300

    # Резкий разворот: памп → слив (или дамп → отскок) без 25м флета
    reversal_enabled: bool = True
    reversal_bypass_top_n: bool = True
    reversal_window_minutes: int = 10
    reversal_spike_minutes: int = 3
    reversal_peak_max_age_minutes: int = 5
    reversal_min_prior_move_pct: float = 3.0
    reversal_min_reversal_pct: float = 2.0
    reversal_min_liquidity_oi_usd: float = 60_000.0
    reversal_cooldown_seconds: int = 300

    min_open_interest: float = 100_000.0
    min_volume: float = 0.0
    enabled_binance: bool = True
    enabled_bybit: bool = True
    scan_interval_seconds: int = 1
    signal_cooldown_seconds: int = 300
    volume_spike_multiplier: float = 5.0
    price_pump_threshold_pct: float = 10.0
    price_pump_window_minutes: int = 5
    cvd_divergence_threshold: float = -0.1
    min_signal_score: float = 1.0
    max_signal_score: int | None = 4
    max_signals_per_symbol_per_day: int = 2
    top_n_symbols: int | None = 250
    priority_score_max: int = 2
    signals_enabled: bool = True
    price_only_min_percent: float = 4.0
    telegram_max_per_minute: int = 5
    telegram_min_interval_seconds: float = 3.0

    min_probability_percent: float = 50.0
    probability_filter_enabled: bool = True
    probability_strict: bool = False
    min_probability_factors_passed: int = 1
    outcome_tracking_enabled: bool = True

    # Мульти-часовой контекст (Bybit: свечи 5m + OI-бары)
    market_structure_enabled: bool = True
    market_structure_hours: int = 5

    # График к сигналу: tradingview = реальный TV (как Bybit), coinglass, generated
    signal_chart_enabled: bool = True
    signal_chart_source: str = "tradingview"
    signal_chart_hours: int = 5
    signal_chart_interval_minutes: int = 5

    # Компактное уведомление (для фото+caption ≤1024 символов)
    signal_message_compact: bool = True

    # Алерты по крупным ликвидациям (REKT-style) → TELEGRAM_ALERT_CHAT_ID
    liquidation_alerts_enabled: bool = True
    liquidation_min_usd: float = 40_000.0
    liquidation_burst_window_seconds: float = 2.0
    liquidation_cooldown_seconds: int = 60
    liquidation_all_symbols: bool = True
    liquidation_show_reversal_hint: bool = True

    # Per-exchange override (None = использовать глобальные пороги из бота)
    binance_oi_period_minutes: int | None = None
    binance_long_period_minutes: int | None = None
    binance_short_period_minutes: int | None = None
    binance_oi_rise_percent: float | None = None
    binance_oi_drop_percent: float | None = None
    binance_price_rise_percent: float | None = None
    binance_price_drop_percent: float | None = None
    bybit_oi_period_minutes: int | None = None
    bybit_long_period_minutes: int | None = None
    bybit_short_period_minutes: int | None = None
    bybit_oi_rise_percent: float | None = None
    bybit_oi_drop_percent: float | None = None
    bybit_price_rise_percent: float | None = None
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
        defaults = cls.default().to_dict()
        base = {**defaults, **data}

        def opt_int(key: str) -> int | None:
            if key not in data:
                return None
            value = data[key]
            if value is None:
                return None
            return int(value)

        def opt_float(key: str) -> float | None:
            if key not in data:
                return None
            value = data[key]
            if value is None:
                return None
            return float(value)

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
            pulse_enabled=bool(base.get("pulse_enabled", True)),
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
            require_both_oi_and_price=bool(base.get("require_both_oi_and_price", True)),
            respect_global_floors=bool(base.get("respect_global_floors", True)),
            mega_cooldown_seconds=int(base["mega_cooldown_seconds"]),
            breakout_enabled=bool(base.get("breakout_enabled", True)),
            breakout_bypass_top_n=bool(base.get("breakout_bypass_top_n", True)),
            breakout_consolidation_minutes=int(base.get("breakout_consolidation_minutes", 25)),
            breakout_spike_minutes=int(base.get("breakout_spike_minutes", 3)),
            breakout_max_flat_percent=float(base.get("breakout_max_flat_percent", 2.0)),
            breakout_min_spike_percent=float(base.get("breakout_min_spike_percent", 1.8)),
            breakout_min_dump_percent=float(base.get("breakout_min_dump_percent", 1.8)),
            breakout_velocity_multiplier=float(base.get("breakout_velocity_multiplier", 3.5)),
            breakout_min_liquidity_oi_usd=float(base.get("breakout_min_liquidity_oi_usd", 30_000.0)),
            breakout_cooldown_seconds=int(base.get("breakout_cooldown_seconds", 120)),
            reversal_enabled=bool(base.get("reversal_enabled", True)),
            reversal_bypass_top_n=bool(base.get("reversal_bypass_top_n", True)),
            reversal_window_minutes=int(base.get("reversal_window_minutes", 10)),
            reversal_spike_minutes=int(base.get("reversal_spike_minutes", 3)),
            reversal_peak_max_age_minutes=int(base.get("reversal_peak_max_age_minutes", 6)),
            reversal_min_prior_move_pct=float(base.get("reversal_min_prior_move_pct", 2.0)),
            reversal_min_reversal_pct=float(base.get("reversal_min_reversal_pct", 1.5)),
            reversal_min_liquidity_oi_usd=float(base.get("reversal_min_liquidity_oi_usd", 25_000.0)),
            reversal_cooldown_seconds=int(base.get("reversal_cooldown_seconds", 90)),
            min_open_interest=float(base["min_open_interest"]),
            min_volume=float(base["min_volume"]),
            enabled_binance=bool(base.get("enabled_binance", True)),
            enabled_bybit=bool(base.get("enabled_bybit", True)),
            volume_spike_multiplier=float(base.get("volume_spike_multiplier", 4.0)),
            price_pump_threshold_pct=float(base.get("price_pump_threshold_pct", 8.0)),
            price_pump_window_minutes=int(base.get("price_pump_window_minutes", 5)),
            cvd_divergence_threshold=float(base.get("cvd_divergence_threshold", -0.1)),
            min_signal_score=float(base.get("min_signal_score", 1.0)),
            max_signal_score=(
                int(base["max_signal_score"])
                if base.get("max_signal_score") is not None
                else None
            ),
            max_signals_per_symbol_per_day=int(
                base.get("max_signals_per_symbol_per_day", 2)
            ),
            top_n_symbols=(int(top_n) if top_n is not None else None),
            priority_score_max=int(base.get("priority_score_max", 3)),
            signals_enabled=bool(base.get("signals_enabled", True)),
            price_only_min_percent=float(base.get("price_only_min_percent", 3.0)),
            telegram_max_per_minute=int(base.get("telegram_max_per_minute", 10)),
            telegram_min_interval_seconds=float(base.get("telegram_min_interval_seconds", 2.0)),
            min_probability_percent=float(base.get("min_probability_percent", 60.0)),
            probability_filter_enabled=bool(base.get("probability_filter_enabled", True)),
            probability_strict=bool(base.get("probability_strict", False)),
            min_probability_factors_passed=int(
                base.get("min_probability_factors_passed", 3)
            ),
            outcome_tracking_enabled=bool(base.get("outcome_tracking_enabled", True)),
            market_structure_enabled=bool(base.get("market_structure_enabled", True)),
            market_structure_hours=int(base.get("market_structure_hours", 5)),
            signal_chart_enabled=bool(base.get("signal_chart_enabled", True)),
            signal_chart_source=str(base.get("signal_chart_source", "tradingview")),
            signal_chart_hours=int(base.get("signal_chart_hours", 5)),
            signal_chart_interval_minutes=int(base.get("signal_chart_interval_minutes", 5)),
            signal_message_compact=bool(base.get("signal_message_compact", True)),
            liquidation_alerts_enabled=bool(base.get("liquidation_alerts_enabled", True)),
            liquidation_min_usd=float(base.get("liquidation_min_usd", 40_000.0)),
            liquidation_burst_window_seconds=float(
                base.get("liquidation_burst_window_seconds", 2.0)
            ),
            liquidation_cooldown_seconds=int(base.get("liquidation_cooldown_seconds", 60)),
            liquidation_all_symbols=bool(base.get("liquidation_all_symbols", True)),
            liquidation_show_reversal_hint=bool(
                base.get("liquidation_show_reversal_hint", True)
            ),
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
            # v3: кнопки бота управляют глобальными порогами — убираем биржевые override
            if version < 3:
                for override_key in EXCHANGE_OVERRIDE_KEYS:
                    merged[override_key] = None
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
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.settings.to_dict(), handle, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to save settings to %s", self.path)
            raise

    def update(self, **kwargs: Any) -> ScannerSettings:
        data = self.settings.to_dict()
        for key, value in kwargs.items():
            data[key] = value
            for override_key in GLOBAL_CLEARS_EXCHANGE_OVERRIDES.get(key, ()):
                data[override_key] = None
        data["settings_version"] = SETTINGS_VERSION
        self.settings = ScannerSettings.from_dict(data)
        self.save()
        logger.info("Settings applied: %s", kwargs)
        for listener in self._listeners:
            try:
                listener(self.settings)
            except Exception:
                logger.exception("Settings listener failed")
        return self.settings
