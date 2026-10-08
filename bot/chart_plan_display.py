"""План на PNG: читаемый R:R — не растягивать Y на весь swing low."""
from __future__ import annotations

from dataclasses import dataclass

from .ta_analysis import fmt_price


@dataclass(frozen=True)
class DisplayPlan:
    side: str
    entry: float
    entry_lo: float
    entry_hi: float
    stop: float
    tp: float
    tp_label: str
    stop_label: str


def clamp_tp_for_chart(
    *,
    side: str,
    entry: float,
    tp: float,
    stop: float,
    max_reward_pct: float = 0.11,
    max_risk_pct: float = 0.045,
) -> tuple[float, float, str]:
    """TP/SL для отрисовки — не дальше max_reward_pct от entry."""
    if entry <= 0:
        return tp, stop, fmt_price(tp)
    if side == "short":
        near_tp = entry * (1.0 - max_reward_pct)
        tp_disp = tp if tp >= near_tp else near_tp
        stop_disp = stop if stop > entry else entry * (1.0 + max_risk_pct * 0.6)
        stop_disp = min(stop_disp, entry * (1.0 + max_risk_pct))
        label = fmt_price(tp)
        if abs(tp - tp_disp) / entry > 0.012:
            label = f"{fmt_price(tp_disp)} (далее {fmt_price(tp)})"
        return tp_disp, stop_disp, label
    tp_disp = min(tp, entry * (1.0 + max_reward_pct))
    stop_disp = max(stop, entry * (1.0 - max_risk_pct))
    label = fmt_price(tp)
    if abs(tp - tp_disp) / entry > 0.015:
        label = f"{fmt_price(tp_disp)} → {fmt_price(tp)}"
    return tp_disp, stop_disp, label


def build_display_plan(
    *,
    side: str,
    entry: float,
    entry_lo: float,
    entry_hi: float,
    stop: float,
    tp: float,
) -> DisplayPlan:
    tp_d, stop_d, tp_lbl = clamp_tp_for_chart(
        side=side, entry=entry, tp=tp, stop=stop,
    )
    return DisplayPlan(
        side=side,
        entry=entry,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop_d,
        tp=tp_d,
        tp_label=tp_lbl,
        stop_label=fmt_price(stop_d),
    )
