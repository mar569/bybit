"""Exhaustion: новый HH/LL без подтверждения объёмом (BNB-кейс)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .bybit_klines import KlineBar
from .market_reading import ParticipationDivergence, detect_participation_divergence


@dataclass(frozen=True)
class ExhaustionSignal:
    active: bool
    kind: str  # weak_high | weak_low
    label_ru: str
    block_continuation: bool
    volume_ratio: float = 1.0
    oi_ratio: float | None = None


def detect_exhaustion_at_extreme(
    bars: Sequence[KlineBar],
    *,
    swings: Sequence | None = None,
    oi_bars: Sequence[KlineBar] | None = None,
    divergence: ParticipationDivergence | None = None,
) -> ExhaustionSignal:
    div = divergence
    if div is None and bars:
        if swings is None:
            from .ta_analysis import find_swing_points

            swings = find_swing_points(list(bars))
        div = detect_participation_divergence(bars, swings, oi_bars=oi_bars)
    if div is None:
        return ExhaustionSignal(
            active=False,
            kind="none",
            label_ru="",
            block_continuation=False,
        )
    active = div.kind in {"weak_high", "weak_low"}
    label = div.label_ru or (
        "хай обновлён — объём слабее прошлого пика"
        if div.kind == "weak_high"
        else "лоу обновлён — объём слабее прошлого минимума"
    )
    if div.oi_ratio is not None and div.oi_ratio < 0.92:
        label += f" · OI {div.oi_ratio:.0%} от пред. экстремума"
    return ExhaustionSignal(
        active=active,
        kind=div.kind,
        label_ru=label,
        block_continuation=div.kind == "weak_high",
        volume_ratio=div.volume_ratio,
        oi_ratio=div.oi_ratio,
    )
