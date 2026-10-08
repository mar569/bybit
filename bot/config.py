from __future__ import annotations

from dotenv import load_dotenv
from pydantic import BaseSettings, Field, validator
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH)

from .env_ru import apply_env_ru_aliases

apply_env_ru_aliases()

class Config(BaseSettings):
    telegram_token: str = Field(..., env="TELEGRAM_TOKEN")
    telegram_admin_id: int = Field(..., env="TELEGRAM_ADMIN_ID")

    binance_api_key: str | None = Field(None, env="BINANCE_API_KEY")
    binance_api_secret: str | None = Field(None, env="BINANCE_API_SECRET")
    bybit_api_key: str | None = Field(None, env="BYBIT_API_KEY")
    bybit_api_secret: str | None = Field(None, env="BYBIT_API_SECRET")

    gemini_api_key: str | None = Field(None, env="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-3.6-flash", env="GEMINI_MODEL")
    groq_api_key: str | None = Field(None, env="GROQ_API_KEY")
    groq_model: str = Field("llama-3.3-70b-versatile", env="GROQ_MODEL")
    relay_api_key: str | None = Field(None, env="RELAY_API_KEY")
    relay_model: str = Field("gemini-3.6-flash", env="RELAY_MODEL")
    relay_base_url: str = Field(
        "https://api.relaymodels.com/v1",
        env="RELAY_BASE_URL",
    )
    ai_provider_order: str = Field("gemini,relay,groq", env="AI_PROVIDER_ORDER")
    # X API Bearer (console.x.com). Если пусто — RSSHub fallback для oil X-ленты.
    x_bearer_token: str | None = Field(None, env="X_BEARER_TOKEN")

    telegram_alert_chat_id: int | None = Field(None, env="TELEGRAM_ALERT_CHAT_ID")
    telegram_analysis_chat_id: int | None = Field(None, env="TELEGRAM_ANALYSIS_CHAT_ID")
    telegram_manual_ta_chat_id: int | None = Field(None, env="TELEGRAM_MANUAL_TA_CHAT_ID")

    @validator("telegram_manual_ta_chat_id", pre=True)
    def empty_manual_ta_chat_id(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @validator("telegram_alert_chat_id", pre=True)
    def empty_alert_chat_id(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @validator("telegram_analysis_chat_id", pre=True)
    def empty_analysis_chat_id(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @validator("gemini_api_key", pre=True)
    def empty_gemini_api_key(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @validator("groq_api_key", pre=True)
    def empty_groq_api_key(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @validator("relay_api_key", pre=True)
    def empty_relay_api_key(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @validator("x_bearer_token", pre=True)
    def empty_x_bearer_token(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def relay_configured(self) -> bool:
        return bool(self.relay_api_key)

    @property
    def x_configured(self) -> bool:
        return bool(self.x_bearer_token)

    @property
    def ai_configured(self) -> bool:
        return bool(
            self.gemini_api_key or self.groq_api_key or self.relay_api_key
        )

    @property
    def ai_keys_label(self) -> str:
        parts: list[str] = []
        if self.gemini_configured:
            parts.append("Gemini")
        if self.groq_configured:
            parts.append("Groq")
        if self.relay_configured:
            parts.append("Relay")
        return "+".join(parts) if parts else "нет"

    @property
    def notification_chat_id(self) -> int:
        """Куда слать сигналы: группа/канал или личка админу (не оба сразу)."""
        if self.telegram_alert_chat_id is not None:
            return self.telegram_alert_chat_id
        return self.telegram_admin_id

    @property
    def effective_analysis_chat_id(self) -> int | None:
        """Чат разборов: ANALYSIS_CHAT, иначе ALERT/admin (чтобы ON в настройках не молчал)."""
        if self.telegram_analysis_chat_id is not None:
            return self.telegram_analysis_chat_id
        if self.telegram_alert_chat_id is not None:
            return self.telegram_alert_chat_id
        return self.telegram_admin_id

    @property
    def analysis_chat_configured(self) -> bool:
        """Разборы можно слать, если есть любой целевой чат (analysis или alert/admin)."""
        return self.effective_analysis_chat_id is not None

    @property
    def analysis_chat_is_fallback(self) -> bool:
        return (
            self.telegram_analysis_chat_id is None
            and self.effective_analysis_chat_id is not None
        )

    @property
    def anomaly_chat_id(self) -> int | None:
        """Аномалии используют общий чат анализа, без отдельного канала."""
        return self.effective_analysis_chat_id

    @property
    def anomaly_chat_configured(self) -> bool:
        return self.anomaly_chat_id is not None

    @property
    def manual_ta_chat_configured(self) -> bool:
        return self.telegram_manual_ta_chat_id is not None

    telegram_oil_news_chat_id: int | None = Field(None, env="TELEGRAM_OIL_NEWS_CHAT_ID")

    @validator("telegram_oil_news_chat_id", pre=True)
    def empty_oil_news_chat_id(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @property
    def oil_news_chat_id(self) -> int | None:
        """Только отдельный oil-чат — не смешиваем с crypto analysis."""
        return self.telegram_oil_news_chat_id

    @property
    def oil_news_chat_configured(self) -> bool:
        return self.telegram_oil_news_chat_id is not None

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
