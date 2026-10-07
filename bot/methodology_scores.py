"""Веса групп из ТЗ (§400–427) — независимые голоса, не сумма индикаторов."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .market_state import MarketState

WEIGHT_HTF = 0.22
WEIGHT_STRUCTURE = 0.16
WEIGHT_LOCATION = 0.16
WEIGHT_TRIGGER = 0.14
WEIGHT_IMPULSE = 0.10
WEIGHT_FLOW = 0.12
WEIGHT_PATTERN = 0.06


@dataclass(frozen=True)
class MethodologyScore:
    htf: float
    structure: float
    location: float
    trigger: float
    impulse: float
    flow: float
    pattern: float
    penalties: float
    total: float
    grade: str  # A | B | C | F
    votes: int
    label_ru: str

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "htf": round(self.htf, 3),
            "structure": round(self.structure, 3),
            "location": round(self.location, 3),
            "trigger": round(self.trigger, 3),
            "impulse": round(self.impulse, 3),
            "flow": round(self.flow, 3),
            "pattern": round(self.pattern, 3),
            "penalties": round(self.penalties, 3),
            "total": round(self.total, 3),
            "grade": self.grade,
            "votes": self.votes,
        }


def _grade(total: float, votes: int, exhaustion: bool) -> str:
    if exhaustion or total < 0.28:
        return "F"
    if total >= 0.72 and votes >= 4:
        return "A"
    if total >= 0.52 and votes >= 3:
        return "B"
    return "C"


def score_methodology(
    state: "MarketState",
    *,
    flow_continuation: int = 50,
    flow_correction: int = 50,
    accept_pattern: bool = True,
    post_pump: bool = False,
) -> MethodologyScore:
    htf = 0.0
    if state.htf_structure in {"bullish", "bearish"}:
        htf = WEIGHT_HTF
    elif state.htf_structure == "sideways":
        htf = WEIGHT_HTF * 0.35

    structure = 0.0
    smc = state.smc_checklist
    if smc.structure_break:
        structure = WEIGHT_STRUCTURE * (1.0 if smc.break_kind == "mss" else 0.85)
    elif smc.sweep:
        structure = WEIGHT_STRUCTURE * 0.5

    location = 0.0
    valid_zones = [z for z in state.zones if z.valid or z.kind.startswith("ob_")]
    if valid_zones:
        location = WEIGHT_LOCATION * min(1.0, len(valid_zones) * 0.35)
    elif any(z.kind.startswith("sr_") for z in state.zones):
        location = WEIGHT_LOCATION * 0.45

    trigger = 0.0
    if state.entry_quality == "ideal":
        trigger = WEIGHT_TRIGGER
    elif state.entry_quality == "good":
        trigger = WEIGHT_TRIGGER * 0.75
    elif state.retests:
        trigger = WEIGHT_TRIGGER * 0.35

    impulse = 0.0
    if state.compression and state.compression.weak_pullback:
        impulse = WEIGHT_IMPULSE
    elif state.compression:
        impulse = WEIGHT_IMPULSE * 0.4
    touch = getattr(state, "touch_compression", None)
    if touch and getattr(touch, "active", False):
        impulse = max(impulse, WEIGHT_IMPULSE * 0.85)

    flow = 0.0
    dom = max(flow_continuation, flow_correction)
    if dom >= 62:
        flow = WEIGHT_FLOW * min(1.0, (dom - 50) / 35.0)
    elif abs(flow_continuation - flow_correction) <= 6:
        flow = WEIGHT_FLOW * 0.25

    pattern = WEIGHT_PATTERN if accept_pattern else 0.0

    penalties = 0.0
    if state.exhaustion.active:
        penalties += 0.18
    if post_pump and state.entry_quality == "weak":
        penalties += 0.08
    if smc.score < 2 and not smc.sweep:
        penalties += 0.05

    raw = htf + structure + location + trigger + impulse + flow + pattern
    total = max(0.0, min(1.0, raw - penalties))
    votes = sum(
        1
        for x in (htf, structure, location, trigger, impulse, flow, pattern)
        if x >= 0.08
    )
    grade = _grade(total, votes, state.exhaustion.active)
    label = f"метод {grade} · {total:.0%} · голосов {votes}/7"
    if state.exhaustion.active:
        label += " · exhaustion"

    return MethodologyScore(
        htf=htf,
        structure=structure,
        location=location,
        trigger=trigger,
        impulse=impulse,
        flow=flow,
        pattern=pattern,
        penalties=penalties,
        total=total,
        grade=grade,
        votes=votes,
        label_ru=label,
    )
