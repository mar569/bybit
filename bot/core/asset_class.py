"""Asset class detection + feature flags (Ed Terminal multi-market)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

AssetClass = Literal["crypto_perp", "crypto_spot", "equity", "prediction", "commodity", "unknown"]


def _env_on(name: str, *, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def detect_asset_class(symbol: str, *, exchange: str = "") -> AssetClass:
    sym = (symbol or "").strip().upper()
    ex = (exchange or "").strip().lower()
    if not sym:
        return "unknown"
    if sym in {
        "WTIUSDT",
        "BRENTUSDT",
        "BZUSDT",
        "CLUSDT",
        "XAUUSDT",
        "XAGUSDT",
        "UKOUSD",
        "UKOUSD.S",
    } or sym.startswith(("WTI", "BRENT", "XAU", "XAG", "UKO")):
        return "commodity"
    if sym.endswith("USDT") or sym.endswith("USDC") or sym.endswith("USD"):
        if ex in {"bybit", "binance", "okx"} or not ex:
            return "crypto_perp"
        return "crypto_spot"
    if len(sym) <= 5 and sym.isalpha():
        return "equity"
    if sym.endswith("-PRED") or "PRED" in sym:
        return "prediction"
    return "crypto_perp"


@dataclass(frozen=True)
class AssetFeatureFlags:
    asset_class: AssetClass
    playbook_enabled: bool
    chart_spec_layers: bool
    quiver_intel: bool
    liquid_trade_quotes: bool


def resolve_asset_flags(symbol: str, *, exchange: str = "") -> AssetFeatureFlags:
    cls = detect_asset_class(symbol, exchange=exchange)
    crypto = cls in {"crypto_perp", "crypto_spot", "commodity", "unknown"}
    equity = cls == "equity"
    return AssetFeatureFlags(
        asset_class=cls,
        playbook_enabled=crypto or _env_on("ED_PLAYBOOK_EQUITY", default="0"),
        chart_spec_layers=crypto or _env_on("ED_CHART_SPEC_EQUITY", default="0"),
        quiver_intel=equity or _env_on("ED_QUIVER_CRYPTO", default="0"),
        liquid_trade_quotes=crypto or equity or _env_on("ED_LIQUID_PREDICTION", default="0"),
    )
