from __future__ import annotations

import time

from bot.models import SnapshotPoint
from bot.settings import ScannerSettings
from bot.symbol_tiers import SymbolTier, TierThresholds, tier_thresholds
from bot.settings import ExchangeThresholds
from bot.trend_exhaustion import detect_trend_exhaustion, detect_trend_exhaustion_risk


def _pt(ts: float, price: float, oi: float = 1_000_000.0) -> SnapshotPoint:
    return SnapshotPoint(
        timestamp=ts,
        price=price,
        open_interest=oi,
        volume_24h=5_000_000.0,
        bid_price=price * 0.999,
        ask_price=price * 1.001,
        additional={},
    )


def _alt_tier() -> TierThresholds:
    base = ExchangeThresholds(
        long_period_minutes=60,
        short_period_minutes=15,
        oi_rise_percent=2.0,
        oi_drop_percent=2.0,
        price_rise_percent=2.0,
        price_drop_percent=2.0,
    )
    settings = ScannerSettings(
        min_open_interest=80_000.0,
        min_volume=0.0,
        oi_period_minutes=15,
        long_period_minutes=60,
        short_period_minutes=15,
        oi_rise_percent=2.0,
        price_rise_percent=2.0,
        oi_drop_percent=2.0,
        price_drop_percent=2.0,
        scan_interval_seconds=1,
        signal_cooldown_seconds=120,
        min_signal_score=1.0,
    )
    return tier_thresholds("VELVETUSDT", settings, base, in_top_n=False)


def test_trend_dump_velvet_like_pattern() -> None:
    """Рост ~40% за час → слив −12% с OI-unwind и liq."""
    from collections import deque

    settings = ScannerSettings(
        min_open_interest=80_000.0,
        min_volume=0.0,
        oi_period_minutes=15,
        long_period_minutes=60,
        short_period_minutes=15,
        oi_rise_percent=2.0,
        price_rise_percent=2.0,
        oi_drop_percent=2.0,
        price_drop_percent=2.0,
        scan_interval_seconds=1,
        signal_cooldown_seconds=120,
        min_signal_score=1.0,
        trend_exhaustion_enabled=True,
        trend_exhaustion_trend_window_minutes=60,
        trend_exhaustion_spike_minutes=5,
    )
    tier = _alt_tier()
    assert tier.tier == SymbolTier.ALT

    base_ts = time.time() - 3700
    history: deque[SnapshotPoint] = deque(maxlen=4000)
    price = 0.50
    for i in range(60):
        price *= 1.006
        history.append(_pt(base_ts + i * 60, price, oi=2_000_000 + i * 1000))
    peak = price
    for i in range(8):
        wobble = peak * (1.0 - 0.002 * (i % 3))
        history.append(_pt(base_ts + 3600 + i * 60, wobble, oi=2_500_000))
    dump_price = peak * 0.88
    for i in range(5):
        p = peak * (0.99 - i * 0.022)
        oi = 2_500_000 - i * 80_000
        history.append(_pt(base_ts + 3600 + 480 + i * 60, p, oi=oi))
    current = history[-1]

    hit = detect_trend_exhaustion(
        history,
        current,
        settings=settings,
        tier=tier,
        liq_long_usd=28_000.0,
    )
    assert hit is not None, "ожидали trend_dump на VELVET-подобном сценарии"
    assert hit.signal_type == "trend_dump"
    assert hit.meta["trend_prior_pct"] >= 6.0
    assert hit.meta["trend_leg_pct"] <= -2.0


def test_trend_dump_skips_flat_market() -> None:
    from collections import deque

    settings = ScannerSettings(
        min_open_interest=80_000.0,
        min_volume=0.0,
        oi_period_minutes=15,
        long_period_minutes=60,
        short_period_minutes=15,
        oi_rise_percent=2.0,
        price_rise_percent=2.0,
        oi_drop_percent=2.0,
        price_drop_percent=2.0,
        scan_interval_seconds=1,
        signal_cooldown_seconds=120,
        min_signal_score=1.0,
        trend_exhaustion_enabled=True,
    )
    tier = _alt_tier()
    base_ts = time.time() - 3700
    history: deque[SnapshotPoint] = deque(maxlen=4000)
    for i in range(70):
        p = 1.0 + (i % 5) * 0.001
        history.append(_pt(base_ts + i * 60, p))
    hit = detect_trend_exhaustion(history, history[-1], settings=settings, tier=tier)
    assert hit is None


def _velvet_uptrend_history():
    from collections import deque

    base_ts = time.time() - 3700
    history: deque[SnapshotPoint] = deque(maxlen=4000)
    price = 0.50
    for i in range(60):
        price *= 1.006
        history.append(_pt(base_ts + i * 60, price, oi=2_000_000 + i * 1000))
    peak = price
    return history, peak, base_ts


def test_dump_risk_at_peak_before_confirmed_dump() -> None:
    """У хая после тренда: OI↓ + затухание — WATCH до trend_dump."""
    from collections import deque

    settings = ScannerSettings(
        min_open_interest=80_000.0,
        min_volume=0.0,
        oi_period_minutes=15,
        long_period_minutes=60,
        short_period_minutes=15,
        oi_rise_percent=2.0,
        price_rise_percent=2.0,
        oi_drop_percent=2.0,
        price_drop_percent=2.0,
        scan_interval_seconds=1,
        signal_cooldown_seconds=120,
        min_signal_score=1.0,
        trend_exhaustion_enabled=True,
        trend_exhaustion_risk_enabled=True,
        trend_exhaustion_trend_window_minutes=60,
        trend_exhaustion_spike_minutes=5,
    )
    tier = _alt_tier()
    history, peak, base_ts = _velvet_uptrend_history()
    for i in range(6):
        wobble = peak * (1.0 - 0.003 * (i % 2))
        oi = 2_600_000 - i * 45_000
        history.append(_pt(base_ts + 3600 + i * 60, wobble, oi=oi))
    current = history[-1]

    risk = detect_trend_exhaustion_risk(
        history,
        current,
        settings=settings,
        tier=tier,
        liq_long_usd=14_000.0,
    )
    assert risk is not None, "ожидали dump_risk у хая до слива"
    assert risk.kind == "dump_risk"
    assert risk.meta["risk_score"] >= 2.0

    confirmed = detect_trend_exhaustion(
        history, current, settings=settings, tier=tier, liq_long_usd=14_000.0,
    )
    assert confirmed is None


def test_dump_risk_skips_when_dump_confirmed() -> None:
    from collections import deque

    settings = ScannerSettings(
        min_open_interest=80_000.0,
        min_volume=0.0,
        oi_period_minutes=15,
        long_period_minutes=60,
        short_period_minutes=15,
        oi_rise_percent=2.0,
        price_rise_percent=2.0,
        oi_drop_percent=2.0,
        price_drop_percent=2.0,
        scan_interval_seconds=1,
        signal_cooldown_seconds=120,
        min_signal_score=1.0,
        trend_exhaustion_enabled=True,
        trend_exhaustion_risk_enabled=True,
    )
    tier = _alt_tier()
    history, peak, base_ts = _velvet_uptrend_history()
    for i in range(5):
        p = peak * (0.99 - i * 0.022)
        history.append(_pt(base_ts + 3600 + i * 60, p, oi=2_500_000 - i * 80_000))
    current = history[-1]

    risk = detect_trend_exhaustion_risk(
        history, current, settings=settings, tier=tier, liq_long_usd=25_000.0,
    )
    assert risk is None
    hit = detect_trend_exhaustion(
        history, current, settings=settings, tier=tier, liq_long_usd=28_000.0,
    )
    assert hit is not None
    assert hit.signal_type == "trend_dump"
