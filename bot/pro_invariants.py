"""PRO-инварианты: цели/стопы/path всегда согласованы с направлением.

Жёсткие правила (график + текст + AI):
1. LONG-цель строго выше цены; SHORT-цель строго ниже.
2. TP не равен триггеру входа (нужен запас за уровнем).
3. Цель фигуры берётся только от паттерна в сторону bias.
4. На WAIT подпись TP/path = bias-сценарий, не «чужая» сторона.
"""
from __future__ import annotations

from typing import Any, Sequence


def bias_side(verdict: str = "", action_priority: str = "", path_bias: str = "") -> str:
    v = (verdict or "").upper()
    if v == "LONG":
        return "long"
    if v == "SHORT":
        return "short"
    ap = (action_priority or "").lower()
    if ap in {"long", "short"}:
        return ap
    pb = (path_bias or "").lower()
    if pb in {"long", "short"}:
        return pb
    return "neutral"


def target_matches_side(
    side: str,
    current: float,
    target: float,
    *,
    min_pct: float = 0.001,
) -> bool:
    if current <= 0 or target <= 0 or side not in {"long", "short"}:
        return False
    if side == "long":
        return target > current * (1.0 + min_pct)
    return target < current * (1.0 - min_pct)


def stop_matches_side(
    side: str,
    current: float,
    stop: float,
    *,
    min_pct: float = 0.0005,
) -> bool:
    if current <= 0 or stop <= 0 or side not in {"long", "short"}:
        return False
    if side == "long":
        return stop < current * (1.0 - min_pct)
    return stop > current * (1.0 + min_pct)


def sanitize_targets(
    side: str,
    current: float,
    targets: Sequence[float],
    *,
    trigger: float | None = None,
    min_pct: float = 0.001,
) -> list[float]:
    """Оставить только цели в сторону side; TP за триггером, без дублей."""
    out: list[float] = []
    seen: set[float] = set()
    for raw in targets:
        try:
            tp = float(raw)
        except (TypeError, ValueError):
            continue
        if not target_matches_side(side, current, tp, min_pct=min_pct):
            continue
        if trigger and trigger > 0:
            if side == "long" and tp <= trigger * (1.0 + min_pct):
                continue
            if side == "short" and tp >= trigger * (1.0 - min_pct):
                continue
        key = round(tp, 8)
        if key in seen:
            continue
        seen.add(key)
        out.append(tp)
    if side == "long":
        out.sort()
    elif side == "short":
        out.sort(reverse=True)
    return out


def sanitize_path_prices(
    side: str,
    current: float,
    prices: Sequence[float],
    labels: Sequence[str] | None = None,
) -> tuple[list[float], list[str]]:
    """Path может зигзагом, но финальная точка обязана быть в сторону bias."""
    labs = list(labels or [])
    clean_p = [float(p) for p in prices if p is not None]
    if len(clean_p) < 2 or side not in {"long", "short"}:
        return clean_p, labs[: len(clean_p)]
    while len(labs) < len(clean_p):
        labs.append("")
    labs = labs[: len(clean_p)]
    final = clean_p[-1]
    if target_matches_side(side, current, final, min_pct=0.0005):
        return clean_p, labs
    # обрезать до последней валидной точки в сторону bias
    keep = 1
    for i in range(1, len(clean_p)):
        if target_matches_side(side, current, clean_p[i], min_pct=0.0005):
            keep = i + 1
    if keep < 2:
        return [], []
    return clean_p[:keep], labs[:keep]


def pick_directional_tp(
    *,
    side: str,
    current: float,
    target_prices: Sequence[float] | None = None,
    scenario_targets: Sequence[float] | None = None,
    trigger: float | None = None,
) -> float | None:
    """TP1 для плана/подписи: сначала scenario, потом target_prices."""
    for pool in (scenario_targets, target_prices):
        cleaned = sanitize_targets(side, current, pool or [], trigger=trigger)
        if cleaned:
            return cleaned[0]
    return None


def pattern_target_ok(
    direction: str,
    current: float,
    target: float | None,
) -> bool:
    if target is None or target <= 0:
        return False
    side = "long" if direction == "bullish" else "short" if direction == "bearish" else "neutral"
    return target_matches_side(side, current, float(target), min_pct=0.001)


def resolve_wait_plan_levels(ta: Any) -> tuple[float | None, float | None, float | None]:
    """Для WAIT: (stop/отмена, TP1, trigger) в сторону action_priority."""
    current = float(getattr(ta, "current_price", 0) or 0)
    side = bias_side(
        getattr(ta, "verdict", "") or "",
        getattr(ta, "action_priority", "") or "",
    )
    stop = getattr(ta, "invalidation_price", None)
    trigger = None
    scenario_tps: list[float] = []
    if side == "short":
        bear = getattr(ta, "bearish_scenario", None)
        trigger = getattr(ta, "breakdown_level", None) or (
            getattr(bear, "trigger_price", None) if bear else None
        )
        if bear:
            scenario_tps = list(getattr(bear, "target_prices", None) or [])
            if stop is None:
                stop = getattr(bear, "stop_price", None)
    elif side == "long":
        bull = getattr(ta, "bullish_scenario", None)
        trigger = getattr(ta, "breakout_level", None) or (
            getattr(bull, "trigger_price", None) if bull else None
        )
        if bull:
            scenario_tps = list(getattr(bull, "target_prices", None) or [])
            if stop is None:
                stop = getattr(bull, "stop_price", None)

    tp1 = pick_directional_tp(
        side=side,
        current=current,
        target_prices=list(getattr(ta, "target_prices", None) or []),
        scenario_targets=scenario_tps,
        trigger=float(trigger) if trigger else None,
    )
    if stop is not None and current > 0 and side in {"long", "short"}:
        if not stop_matches_side(side, current, float(stop)):
            stop = None
    return (
        float(stop) if stop else None,
        tp1,
        float(trigger) if trigger else None,
    )
