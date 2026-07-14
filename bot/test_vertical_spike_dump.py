"""Тест vertical spike dump: wick/хай → мгновенный crash (как MAGMA)."""
from __future__ import annotations

import time
from collections import deque
from unittest.mock import MagicMock

from bot.models import SnapshotPoint
from bot.scanner_engine import SignalEngine
from bot.settings import ExchangeThresholds, ScannerSettings, SettingsManager
from bot.symbol_tiers import tier_thresholds


def _pt(ts: float, price: float, oi: float = 2_000_000.0) -> SnapshotPoint:
    return SnapshotPoint(
        timestamp=ts,
        price=price,
        open_interest=oi,
        volume_24h=5_000_000.0,
        bid_price=price * 0.999,
        ask_price=price * 1.001,
        additional={},
    )


def test_peak_crash_detects_magma_style_dump() -> None:
    """Хай 0.308 → crash 0.256 за минуты = vertical_dump peak_crash."""
    now = time.time()
    history: deque[SnapshotPoint] = deque(maxlen=500)
    # спокойная база
    for i in range(20):
        history.append(_pt(now - 600 + i * 20, 0.275 + i * 0.0003))
    # spike к хаю
    history.append(_pt(now - 90, 0.286))
    history.append(_pt(now - 70, 0.295))
    history.append(_pt(now - 50, 0.308))  # peak
    # вертикальный слив
    history.append(_pt(now - 35, 0.290))
    history.append(_pt(now - 20, 0.270))
    history.append(_pt(now - 8, 0.260))
    current = _pt(now, 0.256)
    history.append(current)

    sm = MagicMock(spec=SettingsManager)
    settings = ScannerSettings()
    sm.settings = settings
    engine = SignalEngine(sm, on_signal=None)

    thresholds = settings.for_exchange("Bybit")
    tier = tier_thresholds("MAGMAUSDT", settings, thresholds, in_top_n=True)
    cand = engine._detect_vertical_spike_dump(history, current, settings, tier)
    assert cand is not None
    assert cand.signal_type == "vertical_dump"
    assert cand.price_change_percent is not None
    assert cand.price_change_percent <= -8.0
    assert (cand.breakout_meta or {}).get("vertical_mode") == "peak_crash"


def test_flash_peak_to_now_mega_dump() -> None:
    now = time.time()
    history: deque[SnapshotPoint] = deque(maxlen=500)
    for i in range(15):
        history.append(_pt(now - 400 + i * 20, 0.280))
    history.append(_pt(now - 80, 0.310))
    history.append(_pt(now - 40, 0.290))
    current = _pt(now, 0.265)
    history.append(current)

    sm = MagicMock(spec=SettingsManager)
    settings = ScannerSettings(flash_enabled=True)
    sm.settings = settings
    engine = SignalEngine(sm, on_signal=None)
    thresholds = ExchangeThresholds(
        long_period_minutes=5,
        short_period_minutes=5,
        oi_rise_percent=2.5,
        oi_drop_percent=2.5,
        price_rise_percent=1.5,
        price_drop_percent=1.5,
    )
    cand = engine._detect_flash_candidate(history, current, settings, thresholds)
    assert cand is not None
    assert cand.signal_type == "mega_dump"
    assert cand.price_change_percent <= -8.0
