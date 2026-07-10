"""Детектор паттерна «тренд → коррекция/перегрев → слив» (VELVET и альты)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import SnapshotPoint

if TYPE_CHECKING:
    from .settings import ScannerSettings
    from .symbol_tiers import TierThresholds


@dataclass(frozen=True)
class TrendExhaustionHit:
    signal_type: str  # trend_dump | trend_pump
    period_minutes: int
    earlier: SnapshotPoint
    price_change_percent: float
    oi_change_percent: float
    meta: dict[str, float]


@dataclass(frozen=True)
class TrendExhaustionRisk:
    """Превентивный WATCH: перегрев у хая после тренда, слив ещё не подтверждён."""
    kind: str  # dump_risk | pump_risk
    price: float
    meta: dict[str, float]
    detail: str


@dataclass
class _TrendCtx:
    prior_up: float
    prior_down: float
    spike_pct: float
    dump_from_peak: float
    bounce_from_trough: float
    peak_age_min: float
    trough_age_min: float
    range_pos: float
    oi_chg: float
    oi_spike: float
    peak_price: float
    trough_price: float
    spike_earlier: SnapshotPoint


def _point_at_cutoff(history: deque[SnapshotPoint], cutoff: float) -> SnapshotPoint | None:
    for point in history:
        if point.timestamp >= cutoff:
            return point
    return history[0] if history else None


def _pct_change(old: float, new: float) -> float:
    if old <= 0:
        return 0.0
    return (new - old) / old * 100.0


def _window_points(
    history: deque[SnapshotPoint],
    start_ts: float,
    end_ts: float | None = None,
) -> list[SnapshotPoint]:
    out: list[SnapshotPoint] = []
    for p in history:
        if p.timestamp < start_ts:
            continue
        if end_ts is not None and p.timestamp > end_ts:
            continue
        if p.price is not None and p.price > 0:
            out.append(p)
    return out


def _build_trend_context(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    *,
    settings: ScannerSettings,
) -> _TrendCtx | None:
    if current.price is None or current.open_interest is None:
        return None
    if len(history) < 20:
        return None

    now = current.timestamp
    trend_min = int(getattr(settings, "trend_exhaustion_trend_window_minutes", 60))
    spike_min = int(getattr(settings, "trend_exhaustion_spike_minutes", 5))
    trend_start = now - trend_min * 60

    if history[0].timestamp > trend_start:
        return None

    trend_pts = _window_points(history, trend_start, now)
    if len(trend_pts) < 12:
        return None

    trend_earlier = _point_at_cutoff(history, trend_start)
    spike_earlier = _point_at_cutoff(history, now - spike_min * 60)
    if trend_earlier is None or spike_earlier is None:
        return None
    if trend_earlier.price is None or spike_earlier.price is None:
        return None

    peak_pt = max(trend_pts, key=lambda p: p.price or 0.0)
    trough_pt = min(trend_pts, key=lambda p: p.price or float("inf"))
    peak_price = peak_pt.price or 0.0
    trough_price = trough_pt.price or 0.0
    if peak_price <= 0 or trough_price <= 0:
        return None

    range_span = peak_price - trough_price
    range_pos = (current.price - trough_price) / range_span if range_span > 0 else 0.5

    oi_at_peak = peak_pt.open_interest or current.open_interest
    oi_now = current.open_interest
    oi_chg = _pct_change(oi_at_peak, oi_now) if oi_at_peak and oi_now else 0.0
    oi_spike = _pct_change(spike_earlier.open_interest or oi_now, oi_now) if oi_now else 0.0

    return _TrendCtx(
        prior_up=_pct_change(trend_earlier.price, peak_price),
        prior_down=_pct_change(trend_earlier.price, trough_price),
        spike_pct=_pct_change(spike_earlier.price, current.price),
        dump_from_peak=_pct_change(peak_price, current.price),
        bounce_from_trough=_pct_change(trough_price, current.price),
        peak_age_min=(now - peak_pt.timestamp) / 60.0,
        trough_age_min=(now - trough_pt.timestamp) / 60.0,
        range_pos=range_pos,
        oi_chg=oi_chg,
        oi_spike=oi_spike,
        peak_price=peak_price,
        trough_price=trough_price,
        spike_earlier=spike_earlier,
    )


def detect_trend_exhaustion_risk(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    *,
    settings: ScannerSettings,
    tier: TierThresholds,
    liq_long_usd: float = 0.0,
    liq_short_usd: float = 0.0,
) -> TrendExhaustionRisk | None:
    """
    WATCH до слива: тренд вверх, цена у хая, признаки перегрева.
    Золотая середина — только при ≥2 факторах, cooldown в scanner.
    """
    if not getattr(settings, "trend_exhaustion_risk_enabled", True):
        return None
    if not getattr(settings, "trend_exhaustion_enabled", True):
        return None

    ctx = _build_trend_context(history, current, settings=settings)
    if ctx is None:
        return None

    min_prior = float(
        getattr(tier, "trend_exhaustion_min_prior_pct", None)
        or getattr(settings, "trend_exhaustion_min_prior_trend_pct", 6.0)
    )
    min_dump = float(
        getattr(tier, "trend_exhaustion_min_dump_pct", None)
        or getattr(settings, "trend_exhaustion_min_dump_pct", 2.0)
    )
    peak_max_age = float(
        getattr(tier, "trend_exhaustion_peak_max_age_minutes", None)
        or getattr(settings, "trend_exhaustion_peak_max_age_minutes", 18.0)
    )
    liq_boost = float(getattr(settings, "trend_exhaustion_liq_boost_usd", 22_000.0))
    min_range = float(getattr(settings, "trend_exhaustion_risk_min_range_position", 0.76))
    max_pullback = float(getattr(settings, "trend_exhaustion_risk_max_pullback_pct", 1.8))
    min_confluence = int(getattr(settings, "trend_exhaustion_risk_min_confluence", 2))

    # Уже подтверждённый слив — risk не нужен
    if ctx.dump_from_peak <= -min_dump and ctx.spike_pct <= -float(
        getattr(settings, "trend_exhaustion_min_spike_pct", 0.9)
    ):
        return None

    # --- dump_risk после восходящего тренда ---
    if (
        ctx.prior_up >= min_prior * 0.92
        and ctx.range_pos >= min_range
        and ctx.dump_from_peak >= -max_pullback
        and ctx.peak_age_min <= peak_max_age * 1.35
    ):
        factors: list[str] = []
        score = 0
        if ctx.oi_chg <= -0.25 or (ctx.oi_spike <= -0.15 and ctx.spike_pct <= 0.4):
            score += 1
            factors.append("OI↓")
        liq_soft = liq_boost * 0.55
        if liq_long_usd >= liq_soft:
            score += 1
            factors.append(f"liq ${liq_long_usd:,.0f}")
        if -0.9 <= ctx.spike_pct <= 0.35:
            score += 1
            factors.append("затухание импульса")
        if 0.4 <= ctx.dump_from_peak <= max_pullback:
            score += 1
            factors.append(f"откат −{abs(ctx.dump_from_peak):.1f}% от хая")
        if ctx.range_pos >= 0.84:
            score += 1
            factors.append("у хая range")

        if score >= min_confluence:
            off_peak = abs(ctx.dump_from_peak)
            detail = (
                f"тренд +{ctx.prior_up:.1f}% → у хая "
                f"(−{off_peak:.1f}% от пика) · {' · '.join(factors[:3])}"
            )
            return TrendExhaustionRisk(
                kind="dump_risk",
                price=current.price or 0.0,
                detail=detail,
                meta={
                    "trend_prior_pct": round(ctx.prior_up, 2),
                    "off_peak_pct": round(off_peak, 2),
                    "range_position": round(ctx.range_pos, 3),
                    "peak_age_min": round(ctx.peak_age_min, 1),
                    "oi_chg_pct": round(ctx.oi_chg, 2),
                    "liq_long_usd": round(liq_long_usd, 0),
                    "risk_score": float(score),
                },
            )

    # --- pump_risk после нисходящего тренда (симметрия) ---
    if (
        ctx.prior_down <= -min_prior * 0.92
        and 0.02 <= ctx.range_pos <= (1.0 - min_range)
        and ctx.bounce_from_trough <= max_pullback
        and ctx.trough_age_min <= peak_max_age * 1.35
    ):
        factors = []
        score = 0
        if ctx.oi_chg >= -0.25 or (ctx.oi_spike >= 0.15 and ctx.spike_pct >= -0.4):
            score += 1
            factors.append("OI↑")
        if liq_short_usd >= liq_boost * 0.55:
            score += 1
            factors.append(f"liq ${liq_short_usd:,.0f}")
        if -0.35 <= ctx.spike_pct <= 0.9:
            score += 1
            factors.append("затухание сливa")
        if score >= min_confluence:
            detail = (
                f"тренд {ctx.prior_down:.1f}% → у дна · {' · '.join(factors[:3])}"
            )
            return TrendExhaustionRisk(
                kind="pump_risk",
                price=current.price or 0.0,
                detail=detail,
                meta={
                    "trend_prior_pct": round(ctx.prior_down, 2),
                    "range_position": round(ctx.range_pos, 3),
                    "risk_score": float(score),
                },
            )

    return None


def detect_trend_exhaustion(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    *,
    settings: ScannerSettings,
    tier: TierThresholds,
    liq_long_usd: float = 0.0,
    liq_short_usd: float = 0.0,
) -> TrendExhaustionHit | None:
    """
    Паттерн альтов: затяжный тренд → у хая/дна → слив/отскок с OI-unwind и liq.

    trend_dump: рост → перегрев → падение (как VELVET).
    trend_pump: падение → дно → отскок.
    """
    if not getattr(settings, "trend_exhaustion_enabled", True):
        return None

    ctx = _build_trend_context(history, current, settings=settings)
    if ctx is None:
        return None

    min_prior = float(
        getattr(tier, "trend_exhaustion_min_prior_pct", None)
        or getattr(settings, "trend_exhaustion_min_prior_trend_pct", 6.0)
    )
    min_dump = float(
        getattr(tier, "trend_exhaustion_min_dump_pct", None)
        or getattr(settings, "trend_exhaustion_min_dump_pct", 2.0)
    )
    min_spike = float(getattr(settings, "trend_exhaustion_min_spike_pct", 0.9))
    peak_max_age = float(
        getattr(tier, "trend_exhaustion_peak_max_age_minutes", None)
        or getattr(settings, "trend_exhaustion_peak_max_age_minutes", 18.0)
    )
    liq_boost = float(getattr(settings, "trend_exhaustion_liq_boost_usd", 22_000.0))

    has_long_liq = liq_long_usd >= liq_boost
    has_short_liq = liq_short_usd >= liq_boost

    dump_ok = ctx.dump_from_peak <= -min_dump
    if has_long_liq and ctx.dump_from_peak <= -(min_dump * 0.65):
        dump_ok = True
    recent_dump = ctx.spike_pct <= -min_spike
    big_dump = ctx.dump_from_peak <= -8.0
    peak_fresh = ctx.peak_age_min <= peak_max_age
    extended_dump = big_dump and recent_dump

    if (
        ctx.prior_up >= min_prior
        and dump_ok
        and (recent_dump or extended_dump)
        and (peak_fresh or extended_dump)
        and ctx.range_pos >= 0.35
    ):
        oi_unwind = ctx.oi_chg <= 0.5 or (ctx.oi_spike <= 0 and ctx.spike_pct < 0)
        score_boost = 0.0
        if oi_unwind:
            score_boost += 1.0
        if has_long_liq:
            score_boost += 1.5
        if big_dump:
            score_boost += 1.0
        meta = {
            "trend_prior_pct": round(ctx.prior_up, 2),
            "trend_leg_pct": round(ctx.dump_from_peak, 2),
            "trend_peak_age_min": round(ctx.peak_age_min, 1),
            "spike_percent": round(ctx.spike_pct, 2),
            "range_position": round(ctx.range_pos, 3),
            "oi_unwind": 1.0 if oi_unwind else 0.0,
            "liq_long_usd": round(liq_long_usd, 0),
            "trend_exhaustion_score": score_boost,
        }
        return TrendExhaustionHit(
            signal_type="trend_dump",
            period_minutes=int(getattr(settings, "trend_exhaustion_spike_minutes", 5)),
            earlier=ctx.spike_earlier,
            price_change_percent=round(ctx.spike_pct, 2),
            oi_change_percent=round(ctx.oi_spike, 2),
            meta=meta,
        )

    pump_ok = ctx.bounce_from_trough >= min_dump
    if has_short_liq and ctx.bounce_from_trough >= min_dump * 0.65:
        pump_ok = True
    recent_pump = ctx.spike_pct >= min_spike
    big_pump = ctx.bounce_from_trough >= 8.0
    trough_fresh = ctx.trough_age_min <= peak_max_age
    extended_pump = big_pump and recent_pump

    if (
        ctx.prior_down <= -min_prior
        and pump_ok
        and (recent_pump or extended_pump)
        and (trough_fresh or extended_pump)
        and ctx.range_pos <= 0.65
    ):
        oi_build = ctx.oi_chg >= -0.5 or (ctx.oi_spike >= 0 and ctx.spike_pct > 0)
        meta = {
            "trend_prior_pct": round(ctx.prior_down, 2),
            "trend_leg_pct": round(ctx.bounce_from_trough, 2),
            "trend_peak_age_min": round(ctx.trough_age_min, 1),
            "spike_percent": round(ctx.spike_pct, 2),
            "range_position": round(ctx.range_pos, 3),
            "oi_unwind": 1.0 if oi_build else 0.0,
            "liq_short_usd": round(liq_short_usd, 0),
        }
        return TrendExhaustionHit(
            signal_type="trend_pump",
            period_minutes=int(getattr(settings, "trend_exhaustion_spike_minutes", 5)),
            earlier=ctx.spike_earlier,
            price_change_percent=round(ctx.spike_pct, 2),
            oi_change_percent=round(ctx.oi_spike, 2),
            meta=meta,
        )

    return None
