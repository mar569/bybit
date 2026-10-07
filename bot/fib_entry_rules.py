"""Fib 0.382 vs 0.5–0.71 по силе тренда + чеклист с OB."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .zone_model import TradingZone, zones_near_price


@dataclass(frozen=True)
class FibEntryPlan:
    prefer_ratios: tuple[float, ...]
    rule_ru: str
    in_preferred_zone: bool
    near_ob: bool
    checklist_ok: bool
    label_ru: str


def _strong_trend(htf_structure: str, wave_confidence: int, htf_change_pct: float) -> bool:
    s = (htf_structure or "").lower()
    if s in {"bullish", "bearish"} and wave_confidence >= 6:
        return True
    return abs(htf_change_pct) >= 4.0 and s in {"bullish", "bearish"}


def preferred_fib_ratios(*, strong_trend: bool) -> tuple[float, ...]:
    if strong_trend:
        return (0.382, 0.5, 0.618)
    return (0.5, 0.618, 0.705, 0.71)


def evaluate_fib_entry(
    *,
    current: float,
    fib_levels: Sequence,
    wave_phase: str,
    htf_structure: str = "",
    wave_confidence: int = 0,
    htf_change_pct: float = 0.0,
    zones: Sequence[TradingZone],
    accept_fib: bool,
) -> FibEntryPlan:
    strong = _strong_trend(htf_structure, wave_confidence, htf_change_pct)
    ratios = preferred_fib_ratios(strong_trend=strong)
    rule = (
        "сильный HTF → допускаем 0.382; иначе только 0.5–0.71 (+ OB)"
        if strong
        else "слабый/неясный HTF → golden 0.5–0.71, не 0.382"
    )
    if not accept_fib or current <= 0 or not fib_levels:
        return FibEntryPlan(
            prefer_ratios=ratios,
            rule_ru=rule,
            in_preferred_zone=False,
            near_ob=False,
            checklist_ok=False,
            label_ru="Fib не готов",
        )

    prices = {float(getattr(lv, "ratio", 0)): float(getattr(lv, "price", 0)) for lv in fib_levels}
    in_zone = False
    for r in ratios:
        p = prices.get(r)
        if p and abs(current - p) / current * 100.0 <= 0.45:
            in_zone = True
            break
    if not in_zone and wave_phase == "fib_golden_zone":
        in_zone = True

    near_ob = bool(
        zones_near_price(
            [z for z in zones if z.kind.startswith("ob_") or z.kind.startswith("breaker_")],
            current,
            pct=2.0,
        )
    )
    checklist_ok = in_zone and (near_ob or wave_phase in {"fib_golden_zone", "shallow_pullback"})
    if strong and prices.get(0.382) and abs(current - prices[0.382]) / current * 100.0 <= 0.35:
        checklist_ok = checklist_ok or near_ob

    label = rule
    if in_zone:
        label += " · цена в рабочей Fib-зоне"
    if near_ob:
        label += " · confluence с OB/breaker"
    if checklist_ok:
        label += " ✓"

    return FibEntryPlan(
        prefer_ratios=ratios,
        rule_ru=rule,
        in_preferred_zone=in_zone,
        near_ob=near_ob,
        checklist_ok=checklist_ok,
        label_ru=label,
    )


def fib_plan_to_dict(plan: FibEntryPlan | None) -> dict[str, object]:
    if plan is None:
        return {}
    return {
        "checklist_ok": plan.checklist_ok,
        "in_zone": plan.in_preferred_zone,
        "near_ob": plan.near_ob,
        "rule": plan.rule_ru,
        "label": plan.label_ru,
        "ratios": list(plan.prefer_ratios),
    }


def fib_blocks_market_entry(ta: object, side: str) -> str:
    """Блок market-ENTRY при погоне / late Fib / вне рабочей зоны (PDF 0.382 vs 0.5–0.71)."""
    side = (side or "").lower()
    if side not in {"long", "short"}:
        return ""

    accept = bool(getattr(ta, "reading_accept_fib", True))
    status = (getattr(ta, "fib_status", "") or "").lower()
    reject = (getattr(ta, "fib_reject_reason", "") or "").strip()

    if status in {"late_impulse", "broken", "no_impulse", "empty"}:
        return f"Fib: {reject or status} — без отката не входим"
    if not accept and reject:
        return f"Fib: {reject[:110]}"
    if not accept and status and status not in {"ready", "chart_only"}:
        return f"Fib не готов ({status})"

    metrics = getattr(ta, "market_metrics", None) or {}
    fp = metrics.get("fib_plan") if isinstance(metrics, dict) else None
    grade = (getattr(ta, "setup_grade", "") or "").upper()
    if isinstance(fp, dict) and grade not in {"A", "B"}:
        if not fp.get("checklist_ok") and not fp.get("in_zone"):
            rule = str(fp.get("rule") or fp.get("label") or "")[:90]
            if rule:
                return f"Цена вне рабочей Fib-зоны — {rule}"
    return ""
