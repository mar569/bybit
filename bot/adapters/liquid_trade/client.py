"""Liquid.trade unified markets (future). Not liquid.com legacy API."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class LiquidQuote:
    symbol: str
    last: float
    asset_class: str  # crypto | equity | prediction


@dataclass
class MarketSession:
    symbol: str
    asset_class: str
    is_open: bool
    session_label: str


class LiquidTradeClient(Protocol):
    def get_quote(self, symbol: str) -> LiquidQuote | None: ...

    def market_session(self, symbol: str, *, at: datetime | None = None) -> MarketSession | None: ...


class MockLiquidTradeClient:
    def get_quote(self, symbol: str) -> LiquidQuote | None:
        sym = symbol.strip().upper()
        if not sym:
            return None
        return LiquidQuote(symbol=sym, last=0.0, asset_class="crypto")

    def market_session(self, symbol: str, *, at: datetime | None = None) -> MarketSession | None:
        sym = symbol.strip().upper()
        if not sym:
            return None
        _ = at or datetime.now(timezone.utc)
        return MarketSession(
            symbol=sym,
            asset_class="crypto",
            is_open=True,
            session_label="24/7",
        )
