"""Тест: ETH −0.7% + $200K long liq → liq_cascade_dump (как на скрине Coinglass)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .bybit_liquidations import LiquidationStats
from .scanner_engine import SignalEngine
from .settings import SettingsManager


@dataclass
class _MockLiqTracker:
    """Подставляет статистику ликвидаций без WebSocket."""

    long_usd: float
    short_usd: float

    def get_stats(self, symbol: str, window_minutes: int = 5) -> LiquidationStats:
        return LiquidationStats(
            symbol=symbol,
            window_minutes=window_minutes,
            long_liq_usd=self.long_usd,
            short_liq_usd=self.short_usd,
            event_count=12,
        )


async def test_eth_liq_cascade_dump() -> None:
    settings = SettingsManager()
    settings.update(
        settings_version=16,
        top_n_symbols=None,
        respect_global_floors=False,
        liq_cascade_enabled=True,
        signals_enabled=True,
        signal_cooldown_seconds=0,
        liq_cascade_cooldown_seconds=0,
        probability_filter_enabled=False,
    )

    sent: list = []

    async def on_signal(signal) -> None:
        sent.append(signal)

    engine = SignalEngine(settings, on_signal)
    engine.attach_liquidation_tracker(_MockLiqTracker(long_usd=210_000.0, short_usd=8_000.0))

    now = time.time()
    window = settings.settings.liq_cascade_window_minutes * 60

    # ETH ~1768 → ~1756 (−0.68%)
    await engine.update_snapshot(
        "Bybit", "ETHUSDT",
        price=1768.0, open_interest=705_000.0, volume_24h=50_000_000.0,
        bid_price=1767.9, ask_price=1768.1,
        timestamp=now - window - 30,
        additional={"open_interest_value": 1_246_440_000.0},
    )
    await engine.update_snapshot(
        "Bybit", "ETHUSDT",
        price=1756.0, open_interest=695_000.0, volume_24h=50_006_000.0,
        bid_price=1755.9, ask_price=1756.1,
        timestamp=now,
        additional={"open_interest_value": 1_220_420_000.0},
    )

    await engine._evaluate_signals("Bybit:ETHUSDT", "Bybit", "ETHUSDT")

    assert sent, "ETH liq-cascade dump должен сработать при −0.7% и $210K long liq"
    sig = sent[0]
    assert sig.signal_type == "liq_cascade_dump"
    assert sig.symbol == "ETHUSDT"
    assert sig.price_change_percent <= -0.35


if __name__ == "__main__":
    asyncio.run(test_eth_liq_cascade_dump())
    print("OK: ETH liq-cascade test passed")
