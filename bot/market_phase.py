"""Накопление vs распределение (не путать фазы)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .bybit_klines import KlineBar


@dataclass(frozen=True)
class AccumulationDistribution:
    phase: str  # accumulation | distribution | none | unclear
    label_ru: str
    confidence: int


def classify_accumulation_distribution(
    bars: Sequence[KlineBar],
    *,
    structure_label: str = "",
    post_pump: bool = False,
    compression: bool = False,
) -> AccumulationDistribution:
    if len(bars) < 24:
        return AccumulationDistribution("unclear", "мало данных для фазы базы", 0)
    struct = (structure_label or "").lower()
    window = bars[-20:]
    highs = [b.high for b in window]
    lows = [b.low for b in window]
    span = max(highs) - min(lows)
    mid = (max(highs) + min(lows)) / 2.0
    if mid <= 0:
        return AccumulationDistribution("unclear", "", 0)
    tight = span / mid * 100.0 < 3.5
    last_h = window[-1].high
    prev_max = max(highs[:-3]) if len(highs) > 3 else last_h
    failed_high = last_h < prev_max * 0.998 and post_pump

    if tight and compression:
        if "медв" in struct or "lh" in struct or failed_high:
            return AccumulationDistribution(
                "distribution",
                "сжатие после роста + слабое обновление хая → распределение",
                7 if failed_high else 5,
            )
        if "быч" in struct or "hh" in struct:
            return AccumulationDistribution(
                "accumulation",
                "сжатие в бычьем контексте → накопление",
                6,
            )
    if failed_high and post_pump:
        return AccumulationDistribution(
            "distribution",
            "памп + неудачный хай → распределение",
            7,
        )
    return AccumulationDistribution("none", "", 0)
