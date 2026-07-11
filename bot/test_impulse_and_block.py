"""Тест: затяжный dump (как DBR) → impulse_dump; reversal_pump LONG блокируется."""
from __future__ import annotations

import asyncio
import time

from .scanner_engine import SignalEngine
from .settings import SettingsManager


async def _feed_dbr_like_dump(engine: SignalEngine, now: float) -> None:
    """Памп к 0.01773, затем слив к 0.0157 за ~25 мин."""
    oi = 17_600_000.0
    oi_usd = 280_000.0
    points = [
        (now - 28 * 60, 0.01620),
        (now - 22 * 60, 0.01773),
        (now - 15 * 60, 0.01680),
        (now - 8 * 60, 0.01600),
        (now, 0.01570),
    ]
    for ts, price in points:
        await engine.update_snapshot(
            "Bybit",
            "DBRUSDT",
            price=price,
            open_interest=oi,
            volume_24h=12_000_000.0,
            bid_price=price * 0.999,
            ask_price=price * 1.001,
            timestamp=ts,
            additional={"open_interest_value": oi_usd},
        )


async def test_impulse_dump_on_sustained_move() -> None:
    settings = SettingsManager()
    settings.update(
        signals_enabled=True,
        impulse_enabled=True,
        reversal_enabled=True,
        probability_filter_enabled=False,
        signal_cooldown_seconds=0,
        impulse_cooldown_seconds=0,
        reversal_cooldown_seconds=0,
    )

    sent: list = []
    engine = SignalEngine(settings, sent.append)
    now = time.time()
    await _feed_dbr_like_dump(engine, now)
    await engine._evaluate_signals("Bybit:DBRUSDT", "Bybit", "DBRUSDT")

    assert sent, "impulse_dump должен сработать на −10% за 25–30м"
    assert sent[0].signal_type == "impulse_dump"
    assert sent[0].price_change_percent <= -5.0


async def test_reversal_pump_blocked_after_dump() -> None:
    settings = SettingsManager()
    settings.update(
        signals_enabled=True,
        impulse_enabled=False,
        reversal_enabled=True,
        reversal_block_long_after_dump=True,
        reversal_block_min_dump_pct=5.0,
        probability_filter_enabled=False,
        signal_cooldown_seconds=0,
        reversal_cooldown_seconds=0,
    )

    sent: list = []
    engine = SignalEngine(settings, sent.append)
    now = time.time()
    await _feed_dbr_like_dump(engine, now)
    # микроотскок как в реальном алерте
    await engine.update_snapshot(
        "Bybit",
        "DBRUSDT",
        price=0.01586,
        open_interest=17_600_000.0,
        volume_24h=12_100_000.0,
        bid_price=0.01585,
        ask_price=0.01587,
        timestamp=now + 60,
        additional={"open_interest_value": 280_000.0},
    )
    await engine._evaluate_signals("Bybit:DBRUSDT", "Bybit", "DBRUSDT")

    reversal_long = [s for s in sent if s.signal_type == "reversal_pump"]
    assert not reversal_long, "reversal_pump LONG должен блокироваться после −5%+ дампа"


async def test_mega_dump_flash_on_vertical_crash() -> None:
    """Вертикальный слив −12% за 5м (как CAP) → mega_dump, даже без 15m истории."""
    settings = SettingsManager()
    settings.update(
        signals_enabled=True,
        flash_enabled=True,
        impulse_enabled=True,
        probability_filter_enabled=False,
        signal_cooldown_seconds=0,
        tier_enabled=True,
    )

    sent: list = []
    engine = SignalEngine(settings, sent.append)
    now = time.time()
    oi_usd = 560_000.0
    await engine.update_snapshot(
        "Bybit", "CAPUSDT",
        price=0.01820, open_interest=33_000_000.0,
        volume_24h=2_800_000.0,
        timestamp=now - 5 * 60,
        additional={"open_interest_value": oi_usd},
    )
    await engine.update_snapshot(
        "Bybit", "CAPUSDT",
        price=0.01600, open_interest=33_000_000.0,
        volume_24h=2_900_000.0,
        timestamp=now,
        additional={"open_interest_value": oi_usd * 0.88},
    )
    await engine._evaluate_signals("Bybit:CAPUSDT", "Bybit", "CAPUSDT")

    assert sent, "mega_dump должен сработать на −12% за 5м"
    assert sent[0].signal_type == "mega_dump"
    assert sent[0].price_change_percent <= -10.0


if __name__ == "__main__":
    asyncio.run(test_impulse_dump_on_sustained_move())
    asyncio.run(test_reversal_pump_blocked_after_dump())
    asyncio.run(test_mega_dump_flash_on_vertical_crash())
    print("OK: impulse + reversal block tests passed")
