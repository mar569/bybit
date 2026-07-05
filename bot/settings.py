from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
SETTINGS_VERSION = 23

# Сохраняем при миграции на профессиональный пресет (tier + liq-cascade + все монеты).
PRESERVE_ON_MIGRATE = frozenset({
    "signals_enabled",
    "enabled_binance",
    "enabled_bybit",
    "telegram_max_per_minute",
    "liquidation_alerts_enabled",
    "liquidation_min_usd",
    "liquidation_burst_window_seconds",
    "liquidation_cooldown_seconds",
    "liquidation_all_symbols",
    "liquidation_show_reversal_hint",
    "analysis_enabled",
    "analysis_min_liq_usd",
    "analysis_delay_seconds",
    "analysis_min_confidence",
    "analysis_cooldown_seconds",
})

LIQUIDATION_PRESERVE_KEYS = frozenset({
    "liquidation_alerts_enabled",
    "liquidation_min_usd",
    "liquidation_burst_window_seconds",
    "liquidation_cooldown_seconds",
    "liquidation_all_symbols",
    "liquidation_show_reversal_hint",
})

ANALYSIS_PRESERVE_KEYS = frozenset({
    "analysis_enabled",
    "analysis_min_liq_usd",
    "analysis_delay_seconds",
    "analysis_min_confidence",
    "analysis_cooldown_seconds",
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

    # Основной LONG/SHORT-профиль (база для tier: мейджоры ниже, альты выше)
    oi_period_minutes: int = 10
    long_period_minutes: int = 10
    short_period_minutes: int = 10
    oi_rise_percent: float = 2.0
    oi_drop_percent: float = 2.0
    price_rise_percent: float = 1.0
    price_drop_percent: float = 1.0

    # Ранний пульс — чувствительнее основного (respect_global_floors=False)
    pulse_period_minutes: int = 5
    pulse_oi_rise_percent: float = 1.2
    pulse_oi_drop_percent: float = 1.2
    pulse_price_rise_percent: float = 0.55
    pulse_price_drop_percent: float = 0.55

    # Мега-пампы: 5–100% за 5–10 минут
    flash_enabled: bool = True
    flash_window_minutes: tuple[int, ...] = (5, 10)
    flash_price_tiers: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0)
    flash_min_oi_rise_percent: float = 1.0
    flash_min_oi_drop_percent: float = 1.0
    flash_bypass_oi_tier_pct: float = 15.0

    # Качество сигнала: деньги в OI, не просто цена
    min_oi_change_usd: float = 100_000.0
    short_squeeze_min_price: float = 3.5
    short_squeeze_max_oi_change: float = -0.8
    require_oi_for_price_only: bool = True
    require_both_oi_and_price: bool = True
    respect_global_floors: bool = False
    mega_cooldown_seconds: int = 45

    # Вертикальный памп/слив: флет → импульс (tier снижает % для BTC/ETH)
    breakout_enabled: bool = True
    breakout_bypass_top_n: bool = True
    breakout_consolidation_minutes: int = 20
    breakout_spike_minutes: int = 3
    breakout_max_flat_percent: float = 1.5
    breakout_min_spike_percent: float = 1.0
    breakout_min_dump_percent: float = 1.0
    breakout_velocity_multiplier: float = 2.8
    breakout_min_liquidity_oi_usd: float = 500_000.0
    breakout_cooldown_seconds: int = 150

    # Резкий разворот: памп → слив (или дамп → отскок)
    reversal_enabled: bool = True
    reversal_bypass_top_n: bool = True
    reversal_window_minutes: int = 10
    reversal_spike_minutes: int = 3
    reversal_peak_max_age_minutes: int = 6
    reversal_min_prior_move_pct: float = 1.2
    reversal_min_reversal_pct: float = 0.85
    reversal_min_liquidity_oi_usd: float = 400_000.0
    reversal_cooldown_seconds: int = 120
    reversal_block_long_after_dump: bool = True
    reversal_block_dump_window_minutes: int = 30
    reversal_block_min_dump_pct: float = 5.0

    # Импульс: кумулятивное движение за 15–30 мин (ловит затяжные pump/dump как DBR)
    impulse_enabled: bool = True
    impulse_bypass_top_n: bool = True
    impulse_window_minutes: tuple[int, ...] = (15, 30)
    impulse_price_tiers: tuple[float, ...] = (5.0, 8.0, 12.0)
    impulse_min_liquidity_oi_usd: float = 250_000.0
    impulse_cooldown_seconds: int = 120
    major_impulse_price_multiplier: float = 0.6
    alt_impulse_price_multiplier: float = 1.2

    # Tier: мейджоры / топ / альты — разные пороги из одной базы
    tier_enabled: bool = True
    major_symbols: tuple[str, ...] = (
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
    )
    major_price_multiplier: float = 0.5
    major_oi_multiplier: float = 0.55
    major_min_open_interest: float = 3_000_000.0
    major_min_oi_change_usd: float = 150_000.0
    major_min_probability_percent: float = 65.0
    major_min_signal_score: float = 1.0
    major_breakout_min_spike_percent: float = 0.55
    major_breakout_min_dump_percent: float = 0.55
    major_reversal_min_prior_pct: float = 0.9
    major_reversal_min_leg_pct: float = 0.65
    alt_price_multiplier: float = 1.25
    alt_oi_multiplier: float = 1.15
    alt_min_open_interest: float = 750_000.0
    alt_min_oi_change_usd: float = 120_000.0
    alt_min_probability_percent: float = 76.0
    alt_min_signal_score: float = 3.0
    standard_min_signal_score: float = 2.0

    # Liq-cascade: крупные ликвидации + движение цены (ловит ETH −0.7% + $194K liq)
    liq_cascade_enabled: bool = True
    liq_cascade_window_minutes: int = 5
    liq_cascade_min_usd: float = 80_000.0
    liq_cascade_min_price_percent: float = 0.45
    liq_cascade_imbalance_min: float = 0.60
    major_liq_cascade_min_usd: float = 120_000.0
    major_liq_cascade_min_price_percent: float = 0.35
    liq_cascade_cooldown_seconds: int = 120

    min_open_interest: float = 500_000.0
    min_volume: float = 0.0
    enabled_binance: bool = True
    enabled_bybit: bool = True
    scan_interval_seconds: int = 1
    signal_cooldown_seconds: int = 180
    volume_spike_multiplier: float = 4.0
    price_pump_threshold_pct: float = 8.0
    price_pump_window_minutes: int = 5
    cvd_divergence_threshold: float = -0.1
    min_signal_score: float = 1.0
    top_n_symbols: int | None = None
    priority_score_max: int = 5
    signals_enabled: bool = True
    price_only_min_percent: float = 3.0
    telegram_max_per_minute: int = 10
    telegram_min_interval_seconds: float = 2.0

    min_probability_percent: float = 72.0
    probability_filter_enabled: bool = True
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
    liquidation_min_usd: float = 50_000.0
    liquidation_burst_window_seconds: float = 2.0
    liquidation_sliding_window_seconds: float = 300.0
    liquidation_tier_enabled: bool = True
    liquidation_alt_max_oi_usd: float = 500_000.0
    liquidation_alt_min_usd: float = 20_000.0
    liquidation_mid_max_oi_usd: float = 2_000_000.0
    liquidation_mid_min_usd: float = 35_000.0
    liquidation_cooldown_seconds: int = 60
    liquidation_all_symbols: bool = True
    liquidation_show_reversal_hint: bool = True

    # Аномалии (volume/OI/funding/pump-dump) → TELEGRAM_ANOMALY_CHAT_ID или analysis
    anomaly_enabled: bool = False
    anomaly_min_oi_change_pct: float = 3.0
    anomaly_min_price_change_pct: float = 4.0
    anomaly_funding_abs_min: float = 0.0015
    anomaly_pump_dump_window_minutes: int = 60
    anomaly_pump_min_pct: float = 5.0
    anomaly_dump_min_pct: float = 5.0
    anomaly_volume_spike_multiplier: float = 8.0
    anomaly_volume_spike_enabled: bool = False
    anomaly_volume_spike_min_price_pct: float = 1.5
    anomaly_types_enabled: tuple[str, ...] = (
        "pump_dump", "oi_spike", "funding_extreme",
    )
    anomaly_cooldown_seconds: int = 300
    anomaly_max_per_minute: int = 3
    anomaly_batch_interval_seconds: int = 60
    anomaly_symbol_cooldown_seconds: int = 1800
    anomaly_min_importance: float = 55.0

    # Аналитический чат — только крупные ликвидации с толком (не все монеты)
    analysis_enabled: bool = True
    analysis_min_liq_usd: float = 40_000.0
    analysis_major_min_liq_usd: float = 50_000.0
    analysis_alt_min_liq_usd: float = 35_000.0
    analysis_skip_alt_tier: bool = False
    analysis_min_oi_usd: float = 0.0
    analysis_min_price_move_pct: float = 0.0
    analysis_min_trend_pct: float = 3.0
    analysis_require_trend: bool = True
    analysis_force_liq_usd: float = 60_000.0
    analysis_max_per_hour: int = 3
    analysis_signal_trigger_enabled: bool = True
    analysis_signal_min_liq_usd: float = 30_000.0
    analysis_delay_seconds: int = 90
    analysis_min_confidence: float = 55.0
    analysis_min_confidence_directional: float = 68.0
    analysis_min_cluster_events: int = 2
    analysis_single_event_min_usd: float = 55_000.0
    analysis_cooldown_seconds: int = 3600
    analysis_outcome_tracking_enabled: bool = True
    analysis_chart_enabled: bool = True
    analysis_chart_interval_minutes: int = 5

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
    def _parse_str_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
        if value is None:
            return default
        if isinstance(value, (list, tuple)):
            return tuple(str(v).upper() for v in value)
        if isinstance(value, str):
            parts = [p.strip().upper() for p in value.split(",") if p.strip()]
            return tuple(parts) if parts else default
        return default

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
            respect_global_floors=bool(base.get("respect_global_floors", False)),
            mega_cooldown_seconds=int(base["mega_cooldown_seconds"]),
            breakout_enabled=bool(base.get("breakout_enabled", True)),
            breakout_bypass_top_n=bool(base.get("breakout_bypass_top_n", True)),
            breakout_consolidation_minutes=int(base.get("breakout_consolidation_minutes", 20)),
            breakout_spike_minutes=int(base.get("breakout_spike_minutes", 3)),
            breakout_max_flat_percent=float(base.get("breakout_max_flat_percent", 1.5)),
            breakout_min_spike_percent=float(base.get("breakout_min_spike_percent", 1.0)),
            breakout_min_dump_percent=float(base.get("breakout_min_dump_percent", 1.0)),
            breakout_velocity_multiplier=float(base.get("breakout_velocity_multiplier", 2.8)),
            breakout_min_liquidity_oi_usd=float(base.get("breakout_min_liquidity_oi_usd", 500_000.0)),
            breakout_cooldown_seconds=int(base.get("breakout_cooldown_seconds", 150)),
            reversal_enabled=bool(base.get("reversal_enabled", True)),
            reversal_bypass_top_n=bool(base.get("reversal_bypass_top_n", True)),
            reversal_window_minutes=int(base.get("reversal_window_minutes", 10)),
            reversal_spike_minutes=int(base.get("reversal_spike_minutes", 3)),
            reversal_peak_max_age_minutes=int(base.get("reversal_peak_max_age_minutes", 6)),
            reversal_min_prior_move_pct=float(base.get("reversal_min_prior_move_pct", 1.2)),
            reversal_min_reversal_pct=float(base.get("reversal_min_reversal_pct", 0.85)),
            reversal_min_liquidity_oi_usd=float(base.get("reversal_min_liquidity_oi_usd", 400_000.0)),
            reversal_cooldown_seconds=int(base.get("reversal_cooldown_seconds", 120)),
            reversal_block_long_after_dump=bool(base.get("reversal_block_long_after_dump", True)),
            reversal_block_dump_window_minutes=int(
                base.get("reversal_block_dump_window_minutes", 30)
            ),
            reversal_block_min_dump_pct=float(base.get("reversal_block_min_dump_pct", 5.0)),
            impulse_enabled=bool(base.get("impulse_enabled", True)),
            impulse_bypass_top_n=bool(base.get("impulse_bypass_top_n", True)),
            impulse_window_minutes=cls._parse_int_tuple(
                base.get("impulse_window_minutes"), (15, 30)
            ),
            impulse_price_tiers=cls._parse_float_tuple(
                base.get("impulse_price_tiers"), (5.0, 8.0, 12.0)
            ),
            impulse_min_liquidity_oi_usd=float(
                base.get("impulse_min_liquidity_oi_usd", 250_000.0)
            ),
            impulse_cooldown_seconds=int(base.get("impulse_cooldown_seconds", 120)),
            major_impulse_price_multiplier=float(
                base.get("major_impulse_price_multiplier", 0.6)
            ),
            alt_impulse_price_multiplier=float(
                base.get("alt_impulse_price_multiplier", 1.2)
            ),
            tier_enabled=bool(base.get("tier_enabled", True)),
            major_symbols=cls._parse_str_tuple(
                base.get("major_symbols"),
                (
                    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
                ),
            ),
            major_price_multiplier=float(base.get("major_price_multiplier", 0.5)),
            major_oi_multiplier=float(base.get("major_oi_multiplier", 0.55)),
            major_min_open_interest=float(base.get("major_min_open_interest", 3_000_000.0)),
            major_min_oi_change_usd=float(base.get("major_min_oi_change_usd", 150_000.0)),
            major_min_probability_percent=float(base.get("major_min_probability_percent", 65.0)),
            major_min_signal_score=float(base.get("major_min_signal_score", 1.0)),
            major_breakout_min_spike_percent=float(
                base.get("major_breakout_min_spike_percent", 0.55)
            ),
            major_breakout_min_dump_percent=float(
                base.get("major_breakout_min_dump_percent", 0.55)
            ),
            major_reversal_min_prior_pct=float(base.get("major_reversal_min_prior_pct", 0.9)),
            major_reversal_min_leg_pct=float(base.get("major_reversal_min_leg_pct", 0.65)),
            alt_price_multiplier=float(base.get("alt_price_multiplier", 1.25)),
            alt_oi_multiplier=float(base.get("alt_oi_multiplier", 1.15)),
            alt_min_open_interest=float(base.get("alt_min_open_interest", 750_000.0)),
            alt_min_oi_change_usd=float(base.get("alt_min_oi_change_usd", 120_000.0)),
            alt_min_probability_percent=float(base.get("alt_min_probability_percent", 76.0)),
            alt_min_signal_score=float(base.get("alt_min_signal_score", 3.0)),
            standard_min_signal_score=float(base.get("standard_min_signal_score", 2.0)),
            liq_cascade_enabled=bool(base.get("liq_cascade_enabled", True)),
            liq_cascade_window_minutes=int(base.get("liq_cascade_window_minutes", 5)),
            liq_cascade_min_usd=float(base.get("liq_cascade_min_usd", 80_000.0)),
            liq_cascade_min_price_percent=float(base.get("liq_cascade_min_price_percent", 0.45)),
            liq_cascade_imbalance_min=float(base.get("liq_cascade_imbalance_min", 0.60)),
            major_liq_cascade_min_usd=float(base.get("major_liq_cascade_min_usd", 120_000.0)),
            major_liq_cascade_min_price_percent=float(
                base.get("major_liq_cascade_min_price_percent", 0.35)
            ),
            liq_cascade_cooldown_seconds=int(base.get("liq_cascade_cooldown_seconds", 120)),
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
            priority_score_max=int(base.get("priority_score_max", 5)),
            signals_enabled=bool(base.get("signals_enabled", True)),
            price_only_min_percent=float(base.get("price_only_min_percent", 3.0)),
            telegram_max_per_minute=int(base.get("telegram_max_per_minute", 10)),
            telegram_min_interval_seconds=float(base.get("telegram_min_interval_seconds", 2.0)),
            min_probability_percent=float(base.get("min_probability_percent", 72.0)),
            probability_filter_enabled=bool(base.get("probability_filter_enabled", True)),
            outcome_tracking_enabled=bool(base.get("outcome_tracking_enabled", True)),
            market_structure_enabled=bool(base.get("market_structure_enabled", True)),
            market_structure_hours=int(base.get("market_structure_hours", 5)),
            signal_chart_enabled=bool(base.get("signal_chart_enabled", True)),
            signal_chart_source=str(base.get("signal_chart_source", "tradingview")),
            signal_chart_hours=int(base.get("signal_chart_hours", 5)),
            signal_chart_interval_minutes=int(base.get("signal_chart_interval_minutes", 5)),
            signal_message_compact=bool(base.get("signal_message_compact", True)),
            scan_interval_seconds=int(base.get("scan_interval_seconds", 1)),
            signal_cooldown_seconds=int(base.get("signal_cooldown_seconds", 180)),
            liquidation_alerts_enabled=bool(base.get("liquidation_alerts_enabled", True)),
            liquidation_min_usd=float(base.get("liquidation_min_usd", 50_000.0)),
            liquidation_burst_window_seconds=float(
                base.get("liquidation_burst_window_seconds", 2.0)
            ),
            liquidation_sliding_window_seconds=float(
                base.get("liquidation_sliding_window_seconds", 300.0)
            ),
            liquidation_tier_enabled=bool(base.get("liquidation_tier_enabled", True)),
            liquidation_alt_max_oi_usd=float(base.get("liquidation_alt_max_oi_usd", 500_000.0)),
            liquidation_alt_min_usd=float(base.get("liquidation_alt_min_usd", 20_000.0)),
            liquidation_mid_max_oi_usd=float(base.get("liquidation_mid_max_oi_usd", 2_000_000.0)),
            liquidation_mid_min_usd=float(base.get("liquidation_mid_min_usd", 35_000.0)),
            liquidation_cooldown_seconds=int(base.get("liquidation_cooldown_seconds", 60)),
            liquidation_all_symbols=bool(base.get("liquidation_all_symbols", True)),
            liquidation_show_reversal_hint=bool(
                base.get("liquidation_show_reversal_hint", True)
            ),
            anomaly_enabled=bool(base.get("anomaly_enabled", False)),
            anomaly_min_oi_change_pct=float(base.get("anomaly_min_oi_change_pct", 3.0)),
            anomaly_min_price_change_pct=float(base.get("anomaly_min_price_change_pct", 4.0)),
            anomaly_funding_abs_min=float(base.get("anomaly_funding_abs_min", 0.0015)),
            anomaly_pump_dump_window_minutes=int(
                base.get("anomaly_pump_dump_window_minutes", 60)
            ),
            anomaly_pump_min_pct=float(base.get("anomaly_pump_min_pct", 5.0)),
            anomaly_dump_min_pct=float(base.get("anomaly_dump_min_pct", 5.0)),
            anomaly_volume_spike_multiplier=float(
                base.get("anomaly_volume_spike_multiplier", 8.0)
            ),
            anomaly_volume_spike_enabled=bool(
                base.get("anomaly_volume_spike_enabled", False)
            ),
            anomaly_volume_spike_min_price_pct=float(
                base.get("anomaly_volume_spike_min_price_pct", 1.5)
            ),
            anomaly_types_enabled=cls._parse_str_tuple(
                base.get("anomaly_types_enabled"),
                ("pump_dump", "oi_spike", "funding_extreme"),
            ),
            anomaly_cooldown_seconds=int(base.get("anomaly_cooldown_seconds", 300)),
            anomaly_max_per_minute=int(base.get("anomaly_max_per_minute", 3)),
            anomaly_batch_interval_seconds=int(
                base.get("anomaly_batch_interval_seconds", 60)
            ),
            anomaly_symbol_cooldown_seconds=int(
                base.get("anomaly_symbol_cooldown_seconds", 1800)
            ),
            anomaly_min_importance=float(base.get("anomaly_min_importance", 55.0)),
            analysis_enabled=bool(base.get("analysis_enabled", True)),
            analysis_min_liq_usd=float(base.get("analysis_min_liq_usd", 40_000.0)),
            analysis_major_min_liq_usd=float(
                base.get("analysis_major_min_liq_usd", 50_000.0)
            ),
            analysis_alt_min_liq_usd=float(
                base.get("analysis_alt_min_liq_usd", 35_000.0)
            ),
            analysis_skip_alt_tier=bool(base.get("analysis_skip_alt_tier", False)),
            analysis_min_oi_usd=float(base.get("analysis_min_oi_usd", 0.0)),
            analysis_min_price_move_pct=float(
                base.get("analysis_min_price_move_pct", 0.0)
            ),
            analysis_min_trend_pct=float(base.get("analysis_min_trend_pct", 3.0)),
            analysis_require_trend=bool(base.get("analysis_require_trend", True)),
            analysis_force_liq_usd=float(base.get("analysis_force_liq_usd", 60_000.0)),
            analysis_max_per_hour=int(base.get("analysis_max_per_hour", 3)),
            analysis_signal_trigger_enabled=bool(
                base.get("analysis_signal_trigger_enabled", True)
            ),
            analysis_signal_min_liq_usd=float(
                base.get("analysis_signal_min_liq_usd", 30_000.0)
            ),
            analysis_delay_seconds=int(base.get("analysis_delay_seconds", 90)),
            analysis_min_confidence=float(base.get("analysis_min_confidence", 55.0)),
            analysis_min_confidence_directional=float(
                base.get("analysis_min_confidence_directional", 68.0)
            ),
            analysis_min_cluster_events=int(base.get("analysis_min_cluster_events", 2)),
            analysis_single_event_min_usd=float(
                base.get("analysis_single_event_min_usd", 55_000.0)
            ),
            analysis_cooldown_seconds=int(base.get("analysis_cooldown_seconds", 3600)),
            analysis_outcome_tracking_enabled=bool(
                base.get("analysis_outcome_tracking_enabled", True)
            ),
            analysis_chart_enabled=bool(base.get("analysis_chart_enabled", True)),
            analysis_chart_interval_minutes=int(
                base.get("analysis_chart_interval_minutes", 5)
            ),
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
            for key in LIQUIDATION_PRESERVE_KEYS:
                if key in data:
                    merged[key] = data[key]
            for key in ANALYSIS_PRESERVE_KEYS:
                if key in data:
                    merged[key] = data[key]
            # v3: кнопки бота управляют глобальными порогами — убираем биржевые override
            if version < 3:
                for override_key in EXCHANGE_OVERRIDE_KEYS:
                    merged[override_key] = None
            # v10+: сброс биржевых override при миграции
            if version >= 3:
                for override_key in EXCHANGE_OVERRIDE_KEYS:
                    merged[override_key] = None
            # v16: сканируем все монеты (не top-150), снимаем жёсткие кнопочные пороги
            merged["top_n_symbols"] = None
            merged["respect_global_floors"] = False
            # v17: починка анализа ликвидаций — пороги как у REKT-алертов, conf не режется в ноль
            if version < 17:
                merged["analysis_min_liq_usd"] = min(
                    float(merged.get("analysis_min_liq_usd", 80_000.0)),
                    float(merged.get("liquidation_min_usd", 80_000.0)),
                )
                merged["analysis_min_confidence"] = min(
                    float(merged.get("analysis_min_confidence", 58.0)),
                    58.0,
                )
                merged["analysis_delay_seconds"] = int(merged.get("analysis_delay_seconds", 90))
            # v18: impulse + tier liq + ниже пороги для альтов
            if version < 18:
                if float(merged.get("liquidation_min_usd", 80_000.0)) >= 80_000.0:
                    merged["liquidation_min_usd"] = 50_000.0
                if float(merged.get("analysis_min_liq_usd", 80_000.0)) >= 80_000.0:
                    merged["analysis_min_liq_usd"] = 40_000.0
                merged.setdefault("liquidation_alt_min_usd", 20_000.0)
                merged.setdefault("liquidation_mid_min_usd", 35_000.0)
                merged.setdefault("liquidation_sliding_window_seconds", 300.0)
                merged.setdefault("anomaly_enabled", True)
            # v19: антиспам аномалий — очередь, лимит, без volume_spike по умолчанию
            if version < 19:
                merged["anomaly_volume_spike_enabled"] = False
                merged["anomaly_max_per_minute"] = 3
                merged["anomaly_batch_interval_seconds"] = 60
                merged["anomaly_symbol_cooldown_seconds"] = 1800
                merged["anomaly_min_importance"] = 55.0
                merged["anomaly_types_enabled"] = [
                    "pump_dump", "oi_spike", "funding_extreme",
                ]
            # v20: analysis — только крупные liq, без альт-мусора
            if version < 20:
                merged["analysis_min_liq_usd"] = max(
                    float(merged.get("analysis_min_liq_usd", 40_000.0)), 100_000.0
                )
                merged["analysis_min_confidence"] = max(
                    float(merged.get("analysis_min_confidence", 58.0)), 68.0
                )
                merged.setdefault("analysis_skip_alt_tier", True)
                merged.setdefault("analysis_min_oi_usd", 1_500_000.0)
                merged.setdefault("analysis_major_min_liq_usd", 80_000.0)
                merged.setdefault("analysis_alt_min_liq_usd", 250_000.0)
                merged.setdefault("analysis_min_price_move_pct", 0.4)
                merged["analysis_cooldown_seconds"] = max(
                    int(merged.get("analysis_cooldown_seconds", 1800)), 3600
                )
            # v21: тренд + liq + OI/CVD анализ; без funding-аномалий; фикс liq-порогов
            if version < 21:
                merged["anomaly_enabled"] = False
                merged["analysis_min_liq_usd"] = 30_000.0
                merged["analysis_major_min_liq_usd"] = 50_000.0
                merged["analysis_alt_min_liq_usd"] = 35_000.0
                merged["analysis_skip_alt_tier"] = False
                merged["analysis_min_oi_usd"] = 500_000.0
                merged["analysis_min_confidence"] = 62.0
                merged["analysis_min_price_move_pct"] = 0.0
                merged.setdefault("analysis_min_trend_pct", 2.0)
                merged.setdefault("analysis_require_trend", True)
                merged.setdefault("analysis_force_liq_usd", 80_000.0)
                merged.setdefault("analysis_max_per_hour", 4)
                merged["anomaly_types_enabled"] = ["pump_dump", "oi_spike"]
            # v22: анализ чаще — триггер от сигналов, пороги как у liq-алертов
            if version < 22:
                merged["analysis_min_liq_usd"] = 20_000.0
                merged["analysis_major_min_liq_usd"] = 35_000.0
                merged["analysis_alt_min_liq_usd"] = 20_000.0
                merged["analysis_min_oi_usd"] = 0.0
                merged["analysis_min_confidence"] = 58.0
                merged["analysis_min_trend_pct"] = 1.5
                merged["analysis_force_liq_usd"] = 50_000.0
                merged["analysis_max_per_hour"] = 6
                merged.setdefault("analysis_signal_trigger_enabled", True)
                merged.setdefault("analysis_signal_min_liq_usd", 10_000.0)
            # v23: меньше ложных long — строже тренд/OI, выжидание по умолчанию
            if version < 23:
                merged["analysis_min_liq_usd"] = 40_000.0
                merged["analysis_major_min_liq_usd"] = 50_000.0
                merged["analysis_alt_min_liq_usd"] = 35_000.0
                merged["analysis_min_trend_pct"] = 3.0
                merged["analysis_force_liq_usd"] = 60_000.0
                merged["analysis_max_per_hour"] = 3
                merged["analysis_signal_min_liq_usd"] = 30_000.0
                merged["analysis_min_confidence"] = 55.0
                merged.setdefault("analysis_min_confidence_directional", 68.0)
                merged.setdefault("analysis_min_cluster_events", 2)
                merged.setdefault("analysis_single_event_min_usd", 55_000.0)
            merged["settings_version"] = SETTINGS_VERSION
            settings = ScannerSettings.from_dict(merged)
            self.save(settings)
            preserved = sorted(
                (PRESERVE_ON_MIGRATE | LIQUIDATION_PRESERVE_KEYS | ANALYSIS_PRESERVE_KEYS) & data.keys()
            )
            logger.info(
                "Settings migrated v%d → v%d (v23: stricter analysis scenarios; preserved: %s)",
                version,
                SETTINGS_VERSION,
                ", ".join(preserved),
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
