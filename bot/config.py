from __future__ import annotations

from dotenv import load_dotenv
from pydantic import BaseSettings, Field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH)

class Config(BaseSettings):
    telegram_token: str = Field(..., env="TELEGRAM_TOKEN")
    telegram_admin_id: int = Field(..., env="TELEGRAM_ADMIN_ID")

    binance_api_key: str | None = Field(None, env="BINANCE_API_KEY")
    binance_api_secret: str | None = Field(None, env="BINANCE_API_SECRET")
    bybit_api_key: str | None = Field(None, env="BYBIT_API_KEY")
    bybit_api_secret: str | None = Field(None, env="BYBIT_API_SECRET")

    scan_interval_seconds: int = Field(1, env="SCAN_INTERVAL_SECONDS")
    default_oi_period: int = Field(15, env="DEFAULT_OI_PERIOD")
    default_oi_rise_percent: float = Field(5.0, env="DEFAULT_OI_PERCENT")
    default_oi_drop_percent: float = Field(5.0, env="DEFAULT_OI_PERCENT")
    default_price_rise_percent: float = Field(1.0, env="DEFAULT_PRICE_PERCENT")
    default_price_drop_percent: float = Field(1.0, env="DEFAULT_PRICE_PERCENT")
    default_min_oi: float = Field(100000.0, env="DEFAULT_MIN_OI")
    default_min_volume: float = Field(0.0, env="DEFAULT_MIN_VOLUME")
    default_binance_enabled: bool = Field(True, env="DEFAULT_BINANCE_ENABLED")
    default_bybit_enabled: bool = Field(True, env="DEFAULT_BYBIT_ENABLED")
    signal_cooldown_seconds: int = Field(60, env="SIGNAL_COOLDOWN_SECONDS")

    class Config:
        env_file = ENV_PATH
        env_file_encoding = "utf-8"

    @classmethod
    def load(cls) -> "Config":
        return cls()
