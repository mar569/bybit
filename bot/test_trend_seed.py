"""Синтетические тесты trend_seed (AKE: флет → пробой + OI)."""
from __future__ import annotations

import time
from collections import deque

from bot.models import SnapshotPoint
from bot.settings import ExchangeThresholds, ScannerSettings
from bot.symbol_tiers import SymbolTier, tier_thresholds
from bot.trend_seed import detect_trend_seed


def _pt(ts: float, price: float, oi: float = 500_000.0) -> SnapshotPoint:
    return SnapshotPoint(
        timestamp=ts,
        price=price,
        open_interest=oi,
        volume_24h=3_000_000.0,
        bid_price=price * 0.999,
        ask_price=price * 1.001,
        additional={},
    )


def _settings(**kwargs) -> ScannerSettings:
    base = dict(
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
        trend_seed_enabled=True,
        trend_seed_base_minutes=25,
        trend_seed_break_minutes=5,
        trend_seed_max_flat_percent=3.0,
        trend_seed_min_break_percent=1.2,
        trend_seed_max_extension_percent=10.0,
        trend_seed_min_oi_rise_percent=0.8,
        trend_seed_min_oi_change_usd=20_000.0,
        trend_seed_cvd_min_ratio=0.55,
        trend_seed_require_cvd=False,
    )
    base.update(kwargs)
    return ScannerSettings(**base)


def _alt_tier(settings: ScannerSettings | None = None):
    settings = settings or _settings()
    exch = ExchangeThresholds(
        long_period_minutes=60,
        short_period_minutes=15,
        oi_rise_percent=2.0,
        oi_drop_percent=2.0,
        price_rise_percent=2.0,
        price_drop_percent=2.0,
    )
    return tier_thresholds("AKEUSDT", settings, exch, in_top_n=False)


def _build_flat_then_break(
    *,
    flat_range_pct: float = 1.5,
    break_pct: float = 2.0,
    oi_rise_pct: float = 1.5,
    extension_extra: float = 0.0,
) -> tuple[deque[SnapshotPoint], SnapshotPoint]:
    """Флет ~25м + пробой 5м с ростом OI."""
    now = time.time()
    history: deque[SnapshotPoint] = deque(maxlen=4000)
    base_price = 1.0
    flat_low = base_price
    flat_high = base_price * (1.0 + flat_range_pct / 100.0)
    oi0 = 400_000.0

    # База: 25 минут, точки каждые 60с
    for i in range(26):
        # лёгкий зигзаг внутри диапазона
        frac = (i % 5) / 4.0
        p = flat_low + (flat_high - flat_low) * frac
        history.append(_pt(now - (30 - i) * 60, p, oi=oi0))

    # Пробой: 5 минут вверх + OI
    break_start_price = flat_high
    final_price = break_start_price * (1.0 + (break_pct + extension_extra) / 100.0)
    oi1 = oi0 * (1.0 + oi_rise_pct / 100.0)
    for i in range(1, 6):
        t = i / 5.0
        p = break_start_price + (final_price - break_start_price) * t
        oi = oi0 + (oi1 - oi0) * t
        history.append(_pt(now - (5 - i) * 60, p, oi=oi))
    current = history[-1]
    return history, current


def test_trend_seed_flat_break_with_oi_passes() -> None:
    settings = _settings()
    tier = _alt_tier(settings)
    assert tier.tier == SymbolTier.ALT
    history, current = _build_flat_then_break(break_pct=2.0, oi_rise_pct=1.5)
    hit = detect_trend_seed(
        history, current, settings=settings, tier=tier, cvd_ratio=0.58,
    )
    assert hit is not None, "ожидали trend_seed на флет→пробой+OI+CVD"
    assert hit.signal_type == "trend_seed"
    assert hit.meta["seed_oi_rise_pct"] > 0
    assert hit.meta.get("seed_cvd_missing", 1) == 0.0


def test_trend_seed_rejects_late_extension() -> None:
    settings = _settings(trend_seed_max_extension_percent=10.0)
    tier = _alt_tier(settings)
    # Уже +15% от mid базы — не seed
    history, current = _build_flat_then_break(
        flat_range_pct=1.2, break_pct=14.0, oi_rise_pct=2.0, extension_extra=2.0,
    )
    hit = detect_trend_seed(
        history, current, settings=settings, tier=tier, cvd_ratio=0.60,
    )
    assert hit is None, "late extension должен отсекаться"


def test_trend_seed_rejects_without_oi_rise() -> None:
    settings = _settings()
    tier = _alt_tier(settings)
    history, current = _build_flat_then_break(break_pct=2.2, oi_rise_pct=-0.5)
    # пересобрать с падающим OI на пробое
    now = current.timestamp
    oi_start = 400_000.0
    for i, p in enumerate(list(history)[-5:]):
        p.open_interest = oi_start * (1.0 - 0.01 * i)
    current = history[-1]
    hit = detect_trend_seed(
        history, current, settings=settings, tier=tier, cvd_ratio=0.60,
    )
    assert hit is None, "без роста OI seed не должен срабатывать"


def test_trend_seed_weak_without_cvd_still_ok() -> None:
    settings = _settings(trend_seed_require_cvd=False)
    tier = _alt_tier(settings)
    history, current = _build_flat_then_break(break_pct=2.0, oi_rise_pct=1.4)
    hit = detect_trend_seed(
        history, current, settings=settings, tier=tier, cvd_ratio=None,
    )
    assert hit is not None
    assert hit.meta["seed_cvd_missing"] == 1.0
    assert hit.urgency < 9


def test_trend_seed_rejects_bad_cvd_when_present() -> None:
    settings = _settings()
    tier = _alt_tier(settings)
    history, current = _build_flat_then_break(break_pct=2.0, oi_rise_pct=1.5)
    hit = detect_trend_seed(
        history, current, settings=settings, tier=tier, cvd_ratio=0.40,
    )
    assert hit is None


def test_settings_v39_defaults() -> None:
    s = ScannerSettings.default()
    assert s.settings_version >= 39 or hasattr(s, "trend_seed_enabled")
    assert s.trend_seed_enabled is True
    assert s.trend_seed_max_flat_percent == 3.0
    assert s.trend_seed_min_break_percent == 1.2
    assert s.trend_seed_cooldown_seconds == 150
    assert s.trend_seed_scan_limit == 15
    assert s.trend_seed_scan_cooldown_seconds == 45


def test_rank_trend_seed_hits_order_and_limit() -> None:
    from bot.trend_seed import TrendSeedScanRow, rank_trend_seed_hits

    rows = [
        TrendSeedScanRow(
            exchange="Bybit", symbol="LATE", break_pct=3.0, oi_pct=2.0,
            extension_pct=9.0, urgency=7, cvd_ratio=0.56, base_minutes=25,
            flat_range_pct=1.5,
        ),
        TrendSeedScanRow(
            exchange="Bybit", symbol="EARLY", break_pct=1.5, oi_pct=1.0,
            extension_pct=2.0, urgency=9, cvd_ratio=0.60, base_minutes=25,
            flat_range_pct=1.2,
        ),
        TrendSeedScanRow(
            exchange="Bybit", symbol="MID", break_pct=2.0, oi_pct=3.0,
            extension_pct=4.0, urgency=9, cvd_ratio=None, base_minutes=25,
            flat_range_pct=1.8,
        ),
    ]
    ranked = rank_trend_seed_hits(rows, limit=2)
    assert len(ranked) == 2
    assert ranked[0].symbol == "EARLY"  # urgency 9, меньший extension
    assert ranked[1].symbol == "MID"    # urgency 9, больше OI чем LATE отсечён limit
