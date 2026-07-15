"""Детектор «потенциала тренда» (AKE-модель): флет → пробой + OI↑ + CVD↑, не late chase."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import SnapshotPoint

if TYPE_CHECKING:
    from .settings import ScannerSettings
    from .symbol_tiers import TierThresholds


@dataclass(frozen=True)
class TrendSeedHit:
    signal_type: str  # trend_seed
    period_minutes: int
    earlier: SnapshotPoint
    price_change_percent: float
    oi_change_percent: float
    urgency: int
    meta: dict[str, float]


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
        if end_ts is not None and p.timestamp >= end_ts:
            continue
        if p.price is not None and p.price > 0:
            out.append(p)
    return out


def _tier_price_mult(tier: TierThresholds | None, settings: ScannerSettings) -> float:
    if tier is None:
        return 1.0
    name = getattr(getattr(tier, "tier", None), "value", "") or str(getattr(tier, "tier", ""))
    if name == "major":
        return float(getattr(settings, "major_price_multiplier", 0.5))
    if name == "alt":
        return float(getattr(settings, "alt_price_multiplier", 1.15))
    return 1.0


def detect_trend_seed(
    history: deque[SnapshotPoint],
    current: SnapshotPoint,
    *,
    settings: ScannerSettings,
    tier: TierThresholds | None = None,
    cvd_ratio: float | None = None,
) -> TrendSeedHit | None:
    """Ранний long: сжатие базы → пробой вверх с подтверждением OI (и CVD если есть)."""
    if not getattr(settings, "trend_seed_enabled", True):
        return None
    if current.price is None or current.open_interest is None:
        return None
    if len(history) < 16:
        return None

    now = current.timestamp
    base_min = int(getattr(settings, "trend_seed_base_minutes", 25))
    break_min = int(getattr(settings, "trend_seed_break_minutes", 5))
    base_min = max(15, min(base_min, 45))
    break_min = max(3, min(break_min, 8))

    break_start = now - break_min * 60
    flat_start = now - (base_min + break_min) * 60
    if history[0].timestamp > flat_start:
        return None

    flat_pts = _window_points(history, flat_start, break_start)
    if len(flat_pts) < 8:
        return None

    flat_prices = [p.price for p in flat_pts if p.price]
    if len(flat_prices) < 8:
        return None
    flat_low = min(flat_prices)
    flat_high = max(flat_prices)
    if flat_low <= 0:
        return None

    flat_mid = (flat_low + flat_high) / 2.0
    flat_range_pct = (flat_high - flat_low) / flat_low * 100.0

    price_mult = _tier_price_mult(tier, settings)
    max_flat = float(getattr(settings, "trend_seed_max_flat_percent", 3.0)) * max(price_mult, 0.85)
    min_break = float(getattr(settings, "trend_seed_min_break_percent", 1.2)) * price_mult
    max_ext = float(getattr(settings, "trend_seed_max_extension_percent", 10.0))
    min_oi_pct = float(getattr(settings, "trend_seed_min_oi_rise_percent", 0.8))
    min_oi_usd = float(getattr(settings, "trend_seed_min_oi_change_usd", 25_000.0))
    cvd_min = float(getattr(settings, "trend_seed_cvd_min_ratio", 0.55))
    require_cvd = bool(getattr(settings, "trend_seed_require_cvd", False))

    if flat_range_pct > max_flat:
        return None

    break_earlier = _point_at_cutoff(history, break_start)
    if break_earlier is None or break_earlier.price is None:
        return None

    # Пробой: цена над хаем базы + минимальный ход за окно пробоя
    break_above_base = _pct_change(flat_high, current.price)
    break_window_pct = _pct_change(break_earlier.price, current.price)
    if break_above_base < min_break * 0.55 and break_window_pct < min_break:
        return None
    if current.price < flat_high * (1.0 + min_break / 200.0):
        # Нужен хотя бы мягкий выход над хаем базы
        return None
    if break_above_base < min_break * 0.35:
        return None

    # Anti-late: ход от середины базы
    extension_pct = _pct_change(flat_mid, current.price)
    if extension_pct > max_ext:
        return None
    range_span = flat_high - flat_low
    range_pos = (
        (current.price - flat_low) / range_span if range_span > 0 else 1.0
    )
    # Уже далеко выше базы → не seed
    if range_pos > 4.0 and extension_pct > max_ext * 0.85:
        return None

    # OI confirm на окне пробоя (+ мягко от середины базы)
    oi_earlier = break_earlier
    if oi_earlier.open_interest is None or oi_earlier.open_interest <= 0:
        return None
    oi_chg = _pct_change(oi_earlier.open_interest, current.open_interest)
    oi_usd = None
    if current.price and oi_earlier.open_interest is not None:
        oi_delta = current.open_interest - oi_earlier.open_interest
        oi_usd = oi_delta * current.price

    soft_oi_ok = oi_chg >= min_oi_pct * 0.65 and (
        oi_usd is None or oi_usd >= min_oi_usd * 0.5
    )
    hard_oi_ok = oi_chg >= min_oi_pct or (
        oi_usd is not None and oi_usd >= min_oi_usd and oi_chg >= min_oi_pct * 0.4
    )
    if not (hard_oi_ok or soft_oi_ok):
        return None
    if oi_chg < 0:
        return None

    # CVD: если есть — должен подтверждать покупки; без CVD — слабее (флаг в meta)
    cvd_missing = 1.0 if cvd_ratio is None else 0.0
    if cvd_ratio is not None:
        if cvd_ratio < cvd_min:
            return None
    elif require_cvd:
        return None

    # Urgency: раньше / с CVD = выше приоритет (ниже score в боте = earlier)
    urgency = 8
    if cvd_ratio is not None and cvd_ratio >= cvd_min + 0.05:
        urgency = 9
    if extension_pct <= max_ext * 0.45:
        urgency = min(10, urgency + 1)
    if cvd_missing:
        urgency = max(6, urgency - 2)

    meta: dict[str, float] = {
        "seed_flat_range_pct": round(flat_range_pct, 3),
        "seed_break_above_base_pct": round(break_above_base, 3),
        "seed_break_window_pct": round(break_window_pct, 3),
        "seed_extension_pct": round(extension_pct, 3),
        "seed_range_position": round(range_pos, 3),
        "seed_oi_rise_pct": round(oi_chg, 3),
        "seed_base_minutes": float(base_min),
        "seed_break_minutes": float(break_min),
        "seed_cvd_missing": cvd_missing,
        "flat_range_percent": round(flat_range_pct, 3),
        "spike_percent": round(break_above_base, 3),
        "consolidation_minutes": float(base_min),
    }
    if oi_usd is not None:
        meta["seed_oi_change_usd"] = round(oi_usd, 1)
    if cvd_ratio is not None:
        meta["seed_cvd_ratio"] = round(float(cvd_ratio), 4)

    return TrendSeedHit(
        signal_type="trend_seed",
        period_minutes=break_min,
        earlier=break_earlier,
        price_change_percent=round(break_window_pct, 3),
        oi_change_percent=round(oi_chg, 3),
        urgency=urgency,
        meta=meta,
    )
