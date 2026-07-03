from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"

@dataclass
class ScannerSettings:
    oi_period_minutes: int = 15
    oi_rise_percent: float = 5.0
    oi_drop_percent: float = 5.0
    price_rise_percent: float = 1.0
    price_drop_percent: float = 1.0
    min_open_interest: float = 100_000.0
    min_volume: float = 0.0
    enabled_binance: bool = True
    enabled_bybit: bool = True
    scan_interval_seconds: int = 1
    signal_cooldown_seconds: int = 60
    # Advanced thresholds
    volume_spike_multiplier: float = 5.0
    price_pump_threshold_pct: float = 8.0
    price_pump_window_minutes: int = 5
    cvd_divergence_threshold: float = -0.1
    min_signal_score: float = 2.0
    top_n_symbols: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScannerSettings":
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
            min_signal_score=float(data.get("min_signal_score", 2.0)),
            top_n_symbols=(int(data.get("top_n_symbols")) if data.get("top_n_symbols") is not None else None),
            scan_interval_seconds=int(data.get("scan_interval_seconds", 1)),
            signal_cooldown_seconds=int(data.get("signal_cooldown_seconds", 60)),
        )

class SettingsManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_SETTINGS_FILE
        self.settings = self.load()

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
        return self.settings
