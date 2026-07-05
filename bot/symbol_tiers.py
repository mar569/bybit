from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .settings import ExchangeThresholds, ScannerSettings

DEFAULT_MAJOR_SYMBOLS: frozenset[str] = frozenset({
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "SUIUSDT",
})


class SymbolTier(str, Enum):
    MAJOR = "major"
    STANDARD = "standard"
    ALT = "alt"


@dataclass(frozen=True)
class TierThresholds:
    tier: SymbolTier
    oi_rise_percent: float
    oi_drop_percent: float
    price_rise_percent: float
    price_drop_percent: float
    min_open_interest_usd: float
    min_oi_change_usd: float
    min_probability_percent: float
    breakout_min_spike_percent: float
    breakout_min_dump_percent: float
    reversal_min_prior_move_pct: float
    reversal_min_reversal_pct: float
    liq_cascade_min_usd: float
    liq_cascade_min_price_percent: float
    min_signal_score: float


def _major_symbols(settings: ScannerSettings) -> frozenset[str]:
    raw = getattr(settings, "major_symbols", None) or DEFAULT_MAJOR_SYMBOLS
    return frozenset(s.upper() for s in raw)


def classify_symbol(symbol: str, settings: ScannerSettings, *, in_top_n: bool) -> SymbolTier:
    if not getattr(settings, "tier_enabled", True):
        return SymbolTier.STANDARD
    if symbol.upper() in _major_symbols(settings):
        return SymbolTier.MAJOR
    if in_top_n:
        return SymbolTier.STANDARD
    return SymbolTier.ALT


def tier_thresholds(
    symbol: str,
    settings: ScannerSettings,
    base: ExchangeThresholds,
    *,
    in_top_n: bool = True,
) -> TierThresholds:
    tier = classify_symbol(symbol, settings, in_top_n=in_top_n)

    if tier == SymbolTier.MAJOR:
        price_mult = float(getattr(settings, "major_price_multiplier", 0.5))
        oi_mult = float(getattr(settings, "major_oi_multiplier", 0.55))
        return TierThresholds(
            tier=tier,
            oi_rise_percent=base.oi_rise_percent * oi_mult,
            oi_drop_percent=base.oi_drop_percent * oi_mult,
            price_rise_percent=base.price_rise_percent * price_mult,
            price_drop_percent=base.price_drop_percent * price_mult,
            min_open_interest_usd=float(getattr(settings, "major_min_open_interest", 3_000_000.0)),
            min_oi_change_usd=float(getattr(settings, "major_min_oi_change_usd", 150_000.0)),
            min_probability_percent=float(getattr(settings, "major_min_probability_percent", 65.0)),
            breakout_min_spike_percent=float(
                getattr(settings, "major_breakout_min_spike_percent", 0.55)
            ),
            breakout_min_dump_percent=float(
                getattr(settings, "major_breakout_min_dump_percent", 0.55)
            ),
            reversal_min_prior_move_pct=float(
                getattr(settings, "major_reversal_min_prior_pct", 0.9)
            ),
            reversal_min_reversal_pct=float(
                getattr(settings, "major_reversal_min_leg_pct", 0.65)
            ),
            liq_cascade_min_usd=float(getattr(settings, "major_liq_cascade_min_usd", 120_000.0)),
            liq_cascade_min_price_percent=float(
                getattr(settings, "major_liq_cascade_min_price_percent", 0.35)
            ),
            min_signal_score=float(getattr(settings, "major_min_signal_score", 1.0)),
        )

    if tier == SymbolTier.ALT:
        price_mult = float(getattr(settings, "alt_price_multiplier", 1.25))
        oi_mult = float(getattr(settings, "alt_oi_multiplier", 1.15))
        return TierThresholds(
            tier=tier,
            oi_rise_percent=base.oi_rise_percent * oi_mult,
            oi_drop_percent=base.oi_drop_percent * oi_mult,
            price_rise_percent=base.price_rise_percent * price_mult,
            price_drop_percent=base.price_drop_percent * price_mult,
            min_open_interest_usd=float(getattr(settings, "alt_min_open_interest", 750_000.0)),
            min_oi_change_usd=float(getattr(settings, "alt_min_oi_change_usd", 120_000.0)),
            min_probability_percent=float(getattr(settings, "alt_min_probability_percent", 76.0)),
            breakout_min_spike_percent=settings.breakout_min_spike_percent * 1.15,
            breakout_min_dump_percent=settings.breakout_min_dump_percent * 1.15,
            reversal_min_prior_move_pct=settings.reversal_min_prior_move_pct * 1.1,
            reversal_min_reversal_pct=settings.reversal_min_reversal_pct * 1.1,
            liq_cascade_min_usd=float(getattr(settings, "liq_cascade_min_usd", 100_000.0)),
            liq_cascade_min_price_percent=float(
                getattr(settings, "liq_cascade_min_price_percent", 0.55)
            ),
            min_signal_score=float(getattr(settings, "alt_min_signal_score", 3.0)),
        )

    return TierThresholds(
        tier=tier,
        oi_rise_percent=base.oi_rise_percent,
        oi_drop_percent=base.oi_drop_percent,
        price_rise_percent=base.price_rise_percent,
        price_drop_percent=base.price_drop_percent,
        min_open_interest_usd=settings.min_open_interest,
        min_oi_change_usd=settings.min_oi_change_usd,
        min_probability_percent=settings.min_probability_percent,
        breakout_min_spike_percent=settings.breakout_min_spike_percent,
        breakout_min_dump_percent=settings.breakout_min_dump_percent,
        reversal_min_prior_move_pct=settings.reversal_min_prior_move_pct,
        reversal_min_reversal_pct=settings.reversal_min_reversal_pct,
        liq_cascade_min_usd=float(getattr(settings, "liq_cascade_min_usd", 80_000.0)),
        liq_cascade_min_price_percent=float(
            getattr(settings, "liq_cascade_min_price_percent", 0.45)
        ),
        min_signal_score=float(getattr(settings, "standard_min_signal_score", 2.0)),
    )


def apply_tier_to_exchange_thresholds(
    base: ExchangeThresholds,
    tier: TierThresholds,
) -> ExchangeThresholds:
    return ExchangeThresholds(
        long_period_minutes=base.long_period_minutes,
        short_period_minutes=base.short_period_minutes,
        oi_rise_percent=tier.oi_rise_percent,
        oi_drop_percent=tier.oi_drop_percent,
        price_rise_percent=tier.price_rise_percent,
        price_drop_percent=tier.price_drop_percent,
    )
