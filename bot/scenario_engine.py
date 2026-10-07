"""Scenario engine (контур C): 5 сценариев из ТЗ."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .market_state import MarketState


SCENARIO_CONTINUATION = "continuation"
SCENARIO_REVERSAL = "reversal"
SCENARIO_RANGE_FADE = "range_fade"
SCENARIO_BREAKOUT_COMPRESSION = "breakout_compression"
SCENARIO_EXHAUSTION = "exhaustion_warning"


@dataclass(frozen=True)
class ScenarioPick:
    scenario_id: str
    title_ru: str
    action: str  # WATCH | LONG | SHORT | SKIP
    quality: str  # A | B | C | F
    reason_ru: str


def pick_trading_scenario(state: "MarketState", *, verdict: str = "WAIT") -> ScenarioPick:
    from .market_state import MarketState  # noqa: F401

    ex = state.exhaustion
    if ex.active and ex.block_continuation:
        return ScenarioPick(
            SCENARIO_EXHAUSTION,
            "Exhaustion — участие не подтверждает хай",
            "WATCH",
            "F",
            ex.label_ru,
        )

    comp = state.compression
    smc = state.smc_checklist
    eq = state.entry_quality
    htf = (state.htf_structure or "").lower()

    if smc.ready and smc.break_kind in {"mss", "bos"} and smc.sweep:
        return ScenarioPick(
            SCENARIO_REVERSAL,
            "Reversal (свип + BOS/MSS + Fib/OB)",
            verdict if verdict in {"LONG", "SHORT"} else "WATCH",
            "B" if eq in {"ideal", "good"} else "C",
            smc.label_ru,
        )

    touch = getattr(state, "touch_compression", None)
    if touch and touch.active:
        rp = float(state.range_position or 0.5)
        if rp >= 0.58:
            bias = "LONG" if htf == "bullish" else "WATCH"
        elif rp <= 0.42:
            bias = "SHORT" if htf == "bearish" else "WATCH"
        else:
            bias = "WATCH"
        return ScenarioPick(
            SCENARIO_BREAKOUT_COMPRESSION,
            "Breakout-after-compression — касания у границы",
            bias,
            "B" if eq in {"ideal", "good"} else "C",
            touch.label_ru,
        )

    if comp and comp.weak_pullback and htf in {"bullish", "bearish"}:
        side = "LONG" if htf == "bullish" else "SHORT"
        return ScenarioPick(
            SCENARIO_CONTINUATION,
            "Continuation — слабый откат в тренде HTF",
            side if eq != "weak" else "WATCH",
            "B" if eq == "good" else "C",
            comp.label_ru,
        )

    if state.accumulation.phase == "distribution" and state.range_position > 0.65:
        return ScenarioPick(
            SCENARIO_RANGE_FADE,
            "Range fade / распределение у верхней границы",
            "WATCH",
            "C",
            state.accumulation.label_ru,
        )

    if state.accumulation.phase == "accumulation" and state.range_position < 0.35:
        return ScenarioPick(
            SCENARIO_BREAKOUT_COMPRESSION,
            "Breakout-after-compression (накопление)",
            "WATCH",
            "C",
            state.accumulation.label_ru,
        )

    if htf == "bullish" and eq in {"ideal", "good"}:
        return ScenarioPick(
            SCENARIO_CONTINUATION,
            "Continuation long",
            "LONG" if verdict == "LONG" else "WATCH",
            "B",
            state.entry_quality_note,
        )
    if htf == "bearish" and eq in {"ideal", "good"}:
        return ScenarioPick(
            SCENARIO_CONTINUATION,
            "Continuation short",
            "SHORT" if verdict == "SHORT" else "WATCH",
            "B",
            state.entry_quality_note,
        )

    return ScenarioPick(
        SCENARIO_CONTINUATION,
        "Нет сценария C — только наблюдение",
        "WATCH",
        "C",
        "мало confluence для входа",
    )
