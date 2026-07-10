from __future__ import annotations

from bot.scanner_engine import _evaluate_oi_flow
from bot.settings import ScannerSettings
from bot.symbol_tiers import SymbolTier, TierThresholds


def _tier(**kwargs) -> TierThresholds:
    base = dict(
        tier=SymbolTier.STANDARD,
        oi_rise_percent=2.5,
        oi_drop_percent=2.5,
        price_rise_percent=1.5,
        price_drop_percent=1.5,
        min_open_interest_usd=80_000.0,
        min_oi_change_usd=45_000.0,
        min_probability_percent=66.0,
        breakout_min_spike_percent=1.0,
        breakout_min_dump_percent=1.0,
        reversal_min_prior_move_pct=1.2,
        reversal_min_reversal_pct=0.85,
        liq_cascade_min_usd=80_000.0,
        liq_cascade_min_price_percent=0.45,
        min_signal_score=2.0,
        impulse_price_tiers=(5.0, 8.0, 12.0),
    )
    base.update(kwargs)
    return TierThresholds(**base)


def test_oi_flow_full_threshold_passes() -> None:
    settings = ScannerSettings.default()
    ok, weak = _evaluate_oi_flow(
        oi_change_usd=50_000.0,
        price_change_percent=2.0,
        tier=_tier(),
        settings=settings,
        signal_type="pump",
    )
    assert ok and not weak


def test_oi_flow_soft_range_with_strong_price() -> None:
    settings = ScannerSettings.default()
    ok, weak = _evaluate_oi_flow(
        oi_change_usd=25_000.0,
        price_change_percent=2.2,
        tier=_tier(),
        settings=settings,
        signal_type="pump",
    )
    assert ok and weak


def test_oi_flow_below_soft_rejected() -> None:
    settings = ScannerSettings.default()
    ok, weak = _evaluate_oi_flow(
        oi_change_usd=12_000.0,
        price_change_percent=3.0,
        tier=_tier(),
        settings=settings,
        signal_type="pump",
    )
    assert not ok and not weak


def test_trend_hunter_defaults() -> None:
    s = ScannerSettings.default()
    assert s.long_period_minutes == 5
    assert s.price_rise_percent == 1.5
    assert s.min_open_interest == 80_000.0
    assert s.alt_min_open_interest == 180_000.0
    assert s.min_oi_change_soft_usd == 20_000.0
    assert s.top_n_symbols == 150
    assert s.priority_score_max == 3
