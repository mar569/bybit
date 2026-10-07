"""Консолидация с рабочего TF + M15/H1 — для разбора «боковик 176–179» на 1h."""
from __future__ import annotations

from dataclasses import replace

from .bybit_klines import KlineBar
from .ta_analysis import ConsolidationZone, detect_local_consolidation


def _zone_from_bars(
    bars: list[KlineBar] | None,
    tf_label: str,
    *,
    lookback: int,
    max_range_pct: float,
) -> ConsolidationZone | None:
    if not bars or len(bars) < 12:
        return None
    lb = min(lookback, len(bars))
    raw = detect_local_consolidation(bars, lookback=lb, max_range_pct=max_range_pct)
    if raw is None:
        return None
    return replace(
        raw,
        label=f"{tf_label}: {raw.label}",
    )


def apply_consolidation_trigger_levels(
    consolidation: ConsolidationZone | None,
    *,
    breakdown: float | None,
    breakout: float | None,
    current: float,
) -> tuple[float | None, float | None]:
    """Триггеры пробоя/закрепа у границ выбранного диапазона (как «под 176»)."""
    if consolidation is None or current <= 0:
        return breakdown, breakout
    top, bot = float(consolidation.top), float(consolidation.bottom)
    if top <= bot:
        return breakdown, breakout
    bd = breakdown
    bo = breakout
    if bot > 0 and current >= bot * 0.992:
        want = bot * 0.9995
        if bd is None or abs(float(bd) - bot) / bot > 0.03:
            bd = want
    if top > 0 and current <= top * 1.008:
        want = top * 1.0005
        if bo is None or abs(float(bo) - top) / top > 0.03:
            bo = want
    return bd, bo


def resolve_multi_tf_consolidation(
    current: float,
    *,
    ltf: ConsolidationZone | None,
    mid_bars: list[KlineBar] | None,
    htf_bars: list[KlineBar] | None,
    macro_bars: list[KlineBar] | None = None,
    mid_interval_minutes: int = 15,
    htf_interval_minutes: int = 60,
    macro_interval_minutes: int = 240,
) -> ConsolidationZone | None:
    """Выбирает диапазон M5/M15/H1/H4 — что лучше описывает «где стоим»."""
    if current <= 0:
        return ltf

    mid_label = "M15" if mid_interval_minutes >= 15 else f"{mid_interval_minutes}m"
    if htf_interval_minutes >= 240:
        htf_label = "H4"
    elif htf_interval_minutes == 60:
        htf_label = "H1"
    else:
        htf_label = f"{max(1, htf_interval_minutes // 60)}h"
    h4_label = "H4" if macro_interval_minutes >= 240 else f"{macro_interval_minutes // 60}h"

    mid = _zone_from_bars(
        list(mid_bars) if mid_bars else None,
        mid_label,
        lookback=88,
        max_range_pct=14.0,
    )
    htf = _zone_from_bars(
        list(htf_bars) if htf_bars else None,
        htf_label,
        lookback=56,
        max_range_pct=20.0,
    )
    h4 = _zone_from_bars(
        list(macro_bars) if macro_bars else None,
        h4_label,
        lookback=42,
        max_range_pct=24.0,
    )

    def _score(zone: ConsolidationZone | None, tf_weight: int) -> float:
        if zone is None:
            return -1.0
        top, bot = float(zone.top), float(zone.bottom)
        if top <= bot:
            return -1.0
        mid_p = (top + bot) / 2.0
        width_pct = (top - bot) / mid_p * 100.0
        inside = bot <= current <= top
        dist_edge = min(abs(current - top), abs(current - bot)) / current * 100.0
        s = float(tf_weight)
        if inside:
            s += 45.0
        elif dist_edge <= 2.5:
            s += 28.0
        elif dist_edge <= 5.0:
            s += 12.0
        if 1.2 <= width_pct <= 16.0:
            s += 18.0
        elif width_pct <= 22.0:
            s += 8.0
        # Слишком узкий LTF-боковик на пампе — уступаем старшему
        if tf_weight == 1 and width_pct < 2.5 and (mid or htf or h4):
            s -= 15.0
        if tf_weight >= 4:
            s += 4.0
        return s

    candidates = [
        (ltf, _score(ltf, 1)),
        (mid, _score(mid, 2)),
        (htf, _score(htf, 3)),
        (h4, _score(h4, 4)),
    ]
    best_zone, best_score = max(candidates, key=lambda x: x[1])
    if best_score < 20.0:
        return ltf or mid or htf or h4
    return best_zone
