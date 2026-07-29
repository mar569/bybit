"""Тесты WaveLevelWatcher."""
from __future__ import annotations

from collections import deque
from types import SimpleNamespace

from bot.models import SnapshotPoint
from bot.settings import ScannerSettings
from bot.wave_alerts import WaveEvent
from bot.wave_watcher import WaveLevelWatcher


class _FakeScanner:
    def __init__(self, price: float) -> None:
        self.history = {
            "Bybit:AKEUSDT": deque([
                SnapshotPoint(
                    timestamp=1.0,
                    price=price,
                    open_interest=1e6,
                    volume_24h=1e6,
                    bid_price=price,
                    ask_price=price,
                ),
            ]),
        }


def test_wave_watch_entry_hit():
    watcher = WaveLevelWatcher()
    settings = ScannerSettings.default()
    event = WaveEvent(
        exchange="Bybit",
        symbol="AKEUSDT",
        timestamp=1.0,
        price=1.05,
        side="long",
        setup_kind="wave4_zone",
        phase="impulse_4",
        label_ru="",
        detail="",
        importance=70.0,
        entry_price=1.00,
        stop_price=0.95,
        invalidation=0.95,
    )
    assert watcher.try_enroll(event, settings) is True
    updates = watcher.tick(_FakeScanner(0.999), settings)
    kinds = [u.kind for u in updates]
    assert "entry_hit" in kinds
    assert watcher.active_count == 0


def test_wave_watch_invalidation():
    watcher = WaveLevelWatcher()
    settings = ScannerSettings.default()
    event = WaveEvent(
        exchange="Bybit",
        symbol="AKEUSDT",
        timestamp=1.0,
        price=1.05,
        side="long",
        setup_kind="wave2_zone",
        phase="impulse_2",
        label_ru="",
        detail="",
        importance=70.0,
        entry_price=1.02,
        invalidation=0.98,
    )
    assert watcher.try_enroll(event, settings) is True
    updates = watcher.tick(_FakeScanner(0.97), settings)
    assert any(u.kind == "invalidation" for u in updates)
