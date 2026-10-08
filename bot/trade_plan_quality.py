"""Проверка плана входа перед отчётом / Telegram."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult

from .human_trade_brief import preferred_trade_side
from .pro_invariants import ensure_minimum_targets, entry_reference_price


@dataclass(frozen=True)
class PlanQualityResult:
    ok: bool
    reason_ru: str = ""
    risk_pct: float = 0.0
    reward_pct: float = 0.0
    rr: float = 0.0


def validate_trade_plan(ta: "TAAnalysisResult") -> PlanQualityResult:
    side = preferred_trade_side(ta)
    v = (getattr(ta, "verdict", "") or "").upper()
    if side not in {"long", "short"}:
        if v == "LONG":
            side = "long"
        elif v == "SHORT":
            side = "short"
        else:
            return PlanQualityResult(ok=True)

    current = float(getattr(ta, "current_price", 0) or 0)
    if current <= 0:
        return PlanQualityResult(ok=True)

    entry_zone = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        lo, hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
        if hi > lo:
            entry_zone = (lo, hi)

    stop = getattr(ta, "invalidation_price", None) or getattr(ta, "setup_stop", None)
    if not stop or float(stop) <= 0:
        return PlanQualityResult(ok=True)

    raw_targets = list(getattr(ta, "target_prices", None) or []) or list(getattr(ta, "setup_tps", None) or [])
    targets = ensure_minimum_targets(
        side, current, raw_targets, entry_zone=entry_zone, min_reward_pct=0.008,
    )
    if not targets:
        return PlanQualityResult(
            ok=False,
            reason_ru="нет валидной цели в сторону сделки",
        )

    ref = entry_reference_price(entry_zone, current, side)
    tp1 = float(targets[0])
    stop_f = float(stop)
    risk_pct = abs(ref - stop_f) / ref * 100.0
    reward_pct = abs(tp1 - ref) / ref * 100.0
    if risk_pct <= 0:
        return PlanQualityResult(ok=False, reason_ru="нулевой риск — стоп на входе")
    rr = reward_pct / risk_pct
    if reward_pct < 0.75:
        return PlanQualityResult(
            ok=False,
            reason_ru=f"цель слишком близко ({reward_pct:.2f}% от входа)",
            risk_pct=risk_pct,
            reward_pct=reward_pct,
            rr=rr,
        )
    if rr < 0.75:
        return PlanQualityResult(
            ok=False,
            reason_ru=f"слабое соотношение риск/прибыль ~1:{rr:.2f}",
            risk_pct=risk_pct,
            reward_pct=reward_pct,
            rr=rr,
        )
    return PlanQualityResult(ok=True, risk_pct=risk_pct, reward_pct=reward_pct, rr=rr)
