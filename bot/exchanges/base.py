from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class MarketSnapshot:
    exchange: str
    symbol: str
    price: float | None = None
    price_change_percent: float | None = None
    volume_24h: float | None = None
    open_interest: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    timestamp: float | None = None
    additional: dict[str, Any] = field(default_factory=dict)

class ExchangeScanner(ABC):
    @abstractmethod
    async def load_symbols(self) -> list[str]:
        ...

    @abstractmethod
    async def run(self) -> None:
        ...

    @abstractmethod
    def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        ...

    @abstractmethod
    def get_symbols(self) -> list[str]:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        return symbol.upper().strip()
